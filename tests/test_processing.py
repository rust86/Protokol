import threading

import pytest

import samples
from protocol_archive.db import ProtocolFilter, Protocol, connect, Repository
from protocol_archive.extract import ExtractionError, OcrOptions
from protocol_archive import processing
from protocol_archive.processing import process_document, process_documents


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "p.sqlite3"


@pytest.fixture
def repo2(db_path):
    c = connect(db_path)
    yield Repository(c)
    c.close()


def add(repo, path):
    return repo.add_document(path)


def test_success_stores_text_and_status(repo2, tmp_path):
    doc = add(repo2, samples.make_docx(tmp_path / "a.docx"))
    out = process_document(repo2, doc.id)
    assert out.ok and out.source == "docx" and out.chars > 0
    after = repo2.get_document(doc.id)
    assert after.status == "processed" and after.error_message is None
    text, source, _ = repo2.get_document_text_info(doc.id)
    assert source == "docx" and "ТЕСТ-001" in text


def test_text_becomes_searchable_for_documents_and_protocols(repo2, tmp_path):
    doc = add(repo2, samples.make_docx(tmp_path / "a.docx"))
    other = add(repo2, samples.make_odt(tmp_path / "b.odt"))
    process_document(repo2, doc.id)
    process_document(repo2, other.id)
    assert [d.id for d in repo2.list_documents(text="объединённая")] == [doc.id]
    assert [d.id for d in repo2.list_documents(text="ТЕСТ-002")] == [other.id]
    repo2.add_protocol(Protocol(document_id=doc.id, protocol_number="X-1"))
    rows = repo2.search_protocols(ProtocolFilter(text="абзац"))
    assert [r.protocol.protocol_number for r in rows] == ["X-1"]
    assert repo2.search_protocols(ProtocolFilter(text="ODT")) == []  # у документа нет протокола


def test_corrupt_file_marks_error_with_message(repo2, tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"%PDF-1.4 garbage")
    doc = add(repo2, f)
    out = process_document(repo2, doc.id)
    assert not out.ok
    after = repo2.get_document(doc.id)
    assert after.status == "error" and after.error_message
    assert repo2.get_document_text(doc.id) is None


def test_empty_document_is_an_error_not_silent_success(repo2, tmp_path):
    doc = add(repo2, samples.make_empty_docx(tmp_path / "empty.docx"))
    out = process_document(repo2, doc.id)
    assert not out.ok and "пуст" in out.message
    assert repo2.get_document(doc.id).status == "error"


def test_missing_file_is_marked_missing(repo2, tmp_path):
    f = samples.make_docx(tmp_path / "a.docx")
    doc = add(repo2, f)
    f.unlink()
    out = process_document(repo2, doc.id)
    assert not out.ok and repo2.get_document(doc.id).status == "missing"


def test_changed_file_is_reprocessed_and_text_replaced(repo2, tmp_path):
    import docx
    f = samples.make_docx(tmp_path / "a.docx")
    doc = add(repo2, f)
    process_document(repo2, doc.id)
    old_sha = repo2.get_document(doc.id).sha256
    assert "ТЕСТ-001" in repo2.get_document_text(doc.id)

    new = docx.Document()               # тот же путь, другое содержимое
    new.add_paragraph("Совсем новый текст")
    new.save(str(f))
    process_document(repo2, doc.id)

    after = repo2.get_document(doc.id)
    assert after.sha256 != old_sha and after.status == "processed"
    text = repo2.get_document_text(doc.id)
    assert text == "Совсем новый текст" and "ТЕСТ-001" not in text
    assert repo2.list_documents(text="ТЕСТ-001") == []   # индекс обновился


def test_content_change_under_same_path_drops_stale_text_on_failure(repo2, tmp_path):
    f = samples.make_docx(tmp_path / "a.docx")
    doc = add(repo2, f)
    process_document(repo2, doc.id)
    assert repo2.get_document_text(doc.id)
    f.write_bytes(b"now broken")  # то же имя, другое содержимое
    out = process_document(repo2, doc.id)
    assert not out.ok
    assert repo2.get_document_text(doc.id) is None  # старый текст относился к другой версии


def test_failure_on_unchanged_file_keeps_previous_text(repo2, tmp_path, monkeypatch):
    f = samples.make_docx(tmp_path / "a.docx")
    doc = add(repo2, f)
    process_document(repo2, doc.id)

    def boom(*a, **k):
        raise ExtractionError("временный сбой")
    monkeypatch.setattr(processing, "extract_text", boom)
    out = process_document(repo2, doc.id)
    assert not out.ok and repo2.get_document(doc.id).status == "error"
    assert "ТЕСТ-001" in repo2.get_document_text(doc.id)


def test_unexpected_exception_does_not_stop_the_batch(db_path, tmp_path, monkeypatch):
    repo = Repository(connect(db_path))
    a = add(repo, samples.make_docx(tmp_path / "a.docx"))
    b = add(repo, samples.make_odt(tmp_path / "b.odt"))
    real = processing.extract_text

    def flaky(path, *args, **kw):
        if str(path).endswith(".docx"):
            raise RuntimeError("что-то непредвиденное")
        return real(path, *args, **kw)
    monkeypatch.setattr(processing, "extract_text", flaky)
    s = process_documents(db_path, [a.id, b.id])
    assert (s.processed, s.failed) == (1, 1)
    assert "Непредвиденная ошибка" in repo.get_document(a.id).error_message


def test_batch_progress_and_summary(db_path, tmp_path):
    repo = Repository(connect(db_path))
    ids = [add(repo, samples.make_docx(tmp_path / "a.docx")).id,
           add(repo, samples.make_odt(tmp_path / "b.odt")).id]
    calls = []
    s = process_documents(db_path, ids, on_progress=lambda d, t, n: calls.append((d, t, n)))
    assert (s.processed, s.failed, s.cancelled) == (2, 0, False)
    assert calls[0][:2] == (0, 2) and calls[-1][:2] == (2, 2)
    assert {repo.get_document(i).status for i in ids} == {"processed"}


def test_batch_cancel_leaves_documents_pending(db_path, tmp_path):
    repo = Repository(connect(db_path))
    ids = [add(repo, samples.make_docx(tmp_path / "a.docx")).id]
    ev = threading.Event()
    ev.set()
    s = process_documents(db_path, ids, cancel=ev)
    assert s.cancelled and s.outcomes == []
    assert repo.get_document(ids[0]).status == "new"


def test_scan_without_ocr_marks_error_and_explains(repo2, tmp_path, monkeypatch):
    from protocol_archive.extract import pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, "ocr_status", lambda o: (False, "Не установлен языковой пакет Tesseract: rus"))
    doc = add(repo2, samples.make_scan_pdf(tmp_path / "scan.pdf"))
    out = process_document(repo2, doc.id)
    assert not out.ok and "rus" in repo2.get_document(doc.id).error_message


def test_list_documents_text_fallback_without_fts(repo2, tmp_path):
    doc = add(repo2, samples.make_docx(tmp_path / "a.docx"))
    process_document(repo2, doc.id)
    repo2.conn.executescript(
        "DROP TRIGGER document_texts_ai; DROP TRIGGER document_texts_ad;"
        "DROP TRIGGER document_texts_au; DROP TABLE document_texts_fts;")
    assert [d.id for d in repo2.list_documents(text="ОБЪЕДИНЁННАЯ")] == [doc.id]
    assert repo2.list_documents(text="нет такого слова") == []


def test_list_document_ids_by_status(repo2, tmp_path):
    a = add(repo2, samples.make_docx(tmp_path / "a.docx"))
    b = add(repo2, samples.make_odt(tmp_path / "b.odt"))
    process_document(repo2, b.id)
    assert repo2.list_document_ids(("new", "error")) == [a.id]
