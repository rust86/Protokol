import tempfile
import threading
from pathlib import Path

import pymupdf
import pytest

import samples
from protocol_archive.extract import (
    ExtractionCancelled, ExtractionError, OcrOptions, extract_text, normalize_text, ocr_status,
)
from protocol_archive.extract import pdf as pdf_mod

OCR_OK, OCR_WHY = ocr_status()
needs_ocr = pytest.mark.skipif(not OCR_OK, reason=f"OCR недоступен: {OCR_WHY}")
needs_soffice = pytest.mark.skipif(not samples.have_soffice(), reason="LibreOffice не установлен")


@pytest.fixture
def d(tmp_path):
    return tmp_path


def snapshot(p: Path):
    return p.read_bytes(), p.stat().st_mtime_ns


# ----- нормализация ---------------------------------------------------------------

def test_normalize_text():
    raw = "  Ст\u00adатья\u00a012.9 \r\n\r\n\r\n\r\nстрока\u200b два \t\n\fстр. 2  "
    assert normalize_text(raw) == "Статья 12.9\n\nстрока два\n\fстр. 2"


def test_normalize_is_idempotent():
    s = normalize_text("а\u00a0б\r\n\r\n\r\nв")
    assert normalize_text(s) == s


# ----- PDF: текстовый слой -------------------------------------------------------------

def test_pdf_text_layer(d):
    f = samples.make_text_pdf(d / "a.pdf")
    r = extract_text(f)
    assert r.source == "pdf-text" and r.pages == 1 and r.ocr_pages == 0
    assert r.text == "Протокол № ТЕСТ-001\nСтатья 12.9, превышение скорости"


def test_pdf_pages_are_separated_by_form_feed(d):
    f = samples.make_text_pdf(d / "a.pdf", pages=(["Первая страница"], ["Вторая страница"]))
    r = extract_text(f)
    assert r.pages == 2 and r.text.split("\f") == ["Первая страница", "Вторая страница"]


def test_pdf_progress_callback(d):
    f = samples.make_text_pdf(d / "a.pdf", pages=(["а"], ["б"], ["в"]))
    calls = []
    extract_text(f, progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_blank_page_without_images_does_not_need_ocr(d, monkeypatch):
    monkeypatch.setattr(pdf_mod, "ocr_status", lambda o: (False, "нет OCR"))
    f = samples.make_blank_pdf(d / "blank.pdf")
    r = extract_text(f)  # не падает: читать нечего, OCR не требуется
    assert r.text == "" and r.ocr_pages == 0


def test_page_needs_ocr_rules():
    o = OcrOptions()
    assert pdf_mod.page_needs_ocr("", True, o)                 # скан
    assert not pdf_mod.page_needs_ocr("", False, o)            # пустая страница
    assert not pdf_mod.page_needs_ocr("Нормальный текст страницы", True, o)
    assert pdf_mod.page_needs_ocr("\ufffd" * 30 + "abc", False, o)  # испорченный текстовый слой


def test_corrupt_pdf_gives_clear_error(d):
    f = d / "bad.pdf"
    f.write_bytes(b"%PDF-1.4\nthis is not a real pdf")
    with pytest.raises(ExtractionError):
        extract_text(f)


def test_encrypted_pdf_gives_clear_error(d):
    src = samples.make_text_pdf(d / "plain.pdf")
    doc = pymupdf.open(str(src))
    enc = d / "enc.pdf"
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    doc.close()
    with pytest.raises(ExtractionError, match="парол"):
        extract_text(enc)


def test_cancel_stops_pdf_extraction(d):
    f = samples.make_text_pdf(d / "a.pdf", pages=(["а"], ["б"]))
    ev = threading.Event()
    ev.set()
    with pytest.raises(ExtractionCancelled):
        extract_text(f, cancel=ev)


# ----- PDF: OCR ----------------------------------------------------------------------

@needs_ocr
def test_scanned_pdf_is_recognized(d):
    f = samples.make_scan_pdf(d / "scan.pdf")
    r = extract_text(f)
    assert r.source == "ocr" and r.ocr_pages == 1 and r.pages == 1
    low = r.text.lower()
    assert "протокол" in low and "12.9" in low and "скорости" in low
    assert any("OCR" in w for w in r.warnings)  # пользователь предупреждён


@needs_ocr
def test_mixed_pdf_marks_source(d):
    f = samples.make_scan_pdf(d / "mix.pdf", mixed_with_text=["Текстовая страница"])
    r = extract_text(f)
    assert r.source == "pdf-text+ocr" and r.pages == 2 and r.ocr_pages == 1
    assert r.text.split("\f")[0] == "Текстовая страница"


def test_scan_without_ocr_fails_explicitly(d, monkeypatch):
    f = samples.make_scan_pdf(d / "scan.pdf")
    monkeypatch.setattr(pdf_mod, "ocr_status", lambda o: (False, "Tesseract не найден в PATH"))
    with pytest.raises(ExtractionError, match="нужен OCR.*Tesseract не найден"):
        extract_text(f)


def test_ocr_status_reports_missing_language():
    ok, why = ocr_status(OcrOptions(lang="zzz"))
    assert not ok and "zzz" in why


# ----- DOCX / ODT / DOC --------------------------------------------------------------------

def test_docx_paragraphs_and_tables_in_order(d):
    r = extract_text(samples.make_docx(d / "a.docx"))
    assert r.source == "docx"
    assert r.text == "Заголовок документа\nНомер\tТЕСТ-001\nОбъединённая ячейка\nПоследний абзац"


def test_docx_corrupt(d):
    f = d / "bad.docx"
    f.write_bytes(b"not a zip")
    with pytest.raises(ExtractionError):
        extract_text(f)


def test_odt_paragraphs_and_tables(d):
    r = extract_text(samples.make_odt(d / "a.odt"))
    assert r.source == "odt" and r.text == "Абзац ODT\nНомер\tТЕСТ-002"


def test_odt_corrupt(d):
    f = d / "bad.odt"
    f.write_bytes(b"not a zip")
    with pytest.raises(ExtractionError):
        extract_text(f)


@needs_soffice
def test_doc_via_libreoffice_leaves_no_traces(d):
    src_dir = d / "originals"
    src_dir.mkdir()
    docx_path = samples.make_docx(src_dir / "tmp.docx")
    doc = samples.make_doc(src_dir / "a.doc", docx_path)
    docx_path.unlink()
    before_listing = sorted(p.name for p in src_dir.iterdir())
    before = snapshot(doc)
    tmp_before = {p.name for p in Path(tempfile.gettempdir()).glob("protocol-archive-doc-*")}

    r = extract_text(doc)

    assert r.source == "doc"
    assert r.text == "Заголовок документа\nНомер\tТЕСТ-001\nОбъединённая ячейка\nПоследний абзац"
    assert snapshot(doc) == before                                   # оригинал не изменён
    assert sorted(p.name for p in src_dir.iterdir()) == before_listing  # ни lock-файлов, ни копий
    tmp_after = {p.name for p in Path(tempfile.gettempdir()).glob("protocol-archive-doc-*")}
    assert tmp_after == tmp_before                                   # временные файлы удалены


def test_doc_without_libreoffice_fails_explicitly(d, monkeypatch):
    from protocol_archive.extract import doc as doc_mod
    monkeypatch.setattr(doc_mod.shutil, "which", lambda _n: None)
    f = d / "a.doc"
    f.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="LibreOffice"):
        extract_text(f)


# ----- общее -----------------------------------------------------------------------------

def test_unsupported_and_missing(d):
    f = d / "a.txt"
    f.write_text("x")
    with pytest.raises(ExtractionError, match="не поддерживается"):
        extract_text(f)
    with pytest.raises(ExtractionError, match="не найден"):
        extract_text(d / "нет.pdf")


@pytest.mark.parametrize("maker,name", [
    (samples.make_text_pdf, "a.pdf"), (samples.make_docx, "a.docx"), (samples.make_odt, "a.odt"),
])
def test_originals_are_never_modified(d, maker, name):
    f = maker(d / name)
    before = snapshot(f)
    listing = sorted(p.name for p in d.iterdir())
    extract_text(f)
    assert snapshot(f) == before and sorted(p.name for p in d.iterdir()) == listing
