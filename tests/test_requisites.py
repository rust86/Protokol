import sqlite3
from pathlib import Path

import pytest

import samples
from protocol_archive.db import Protocol, connect, Repository
from protocol_archive.db.schema import SCHEMA_VERSION
from protocol_archive.extract import extract_text
from protocol_archive.parsing import extract_requisites, parse_protocol
from protocol_archive.processing import process_document

SAMPLE = (Path(__file__).parent / "data" / "protocol_legal_entity.txt").read_text(encoding="utf-8")


@pytest.fixture
def doc_with_text(repo, make_file):
    d = repo.add_document(make_file("p.pdf"))
    repo.set_document_text(d.id, SAMPLE, "pdf-text")
    return d


def test_creates_extracted_protocol_with_evidence(repo, doc_with_text):
    out = extract_requisites(repo, doc_with_text.id)
    assert out.status == "created" and out.ok and out.missing == {}
    p = repo.get_protocol(out.protocol_id)
    assert (p.status, p.protocol_number, p.protocol_date) == ("extracted", "86 НН 000001", "2026-02-06")
    assert p.offender_type == "legal_entity" and p.article == "ч. 1 ст. 12.34 КоАП РФ"
    assert p.violation_date == "2026-02-05"
    assert p.location == "ул. Тестовой д. 8"
    assert p.gost_norms == "п. 8.1 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"
    ev = {f: (v, e, r) for f, v, e, r in repo.get_evidence(p.id)}
    assert set(ev) == {"protocol_number", "protocol_date", "violation_date", "offender_type",
                       "article", "offender_name", "authority", "location", "gost_norms"}
    assert ev["article"][1].startswith("нарушение, предусмотренное")


def test_search_finds_the_extracted_protocol(repo, doc_with_text):
    extract_requisites(repo, doc_with_text.id)
    from protocol_archive.db import ProtocolFilter
    rows = repo.search_protocols(ProtocolFilter(article="12.34", offender_type="legal_entity",
                                                date_from="2026-02-01", date_to="2026-02-28"))
    assert [r.protocol.protocol_number for r in rows] == ["86 НН 000001"]


def test_without_text_nothing_is_created(repo, make_file):
    d = repo.add_document(make_file("p.pdf"))
    out = extract_requisites(repo, d.id)
    assert out.status == "no_text" and repo.count_protocols() == 0


def test_unknown_form_creates_nothing(repo, make_file):
    d = repo.add_document(make_file("p.pdf"))
    repo.set_document_text(d.id, "Обычное письмо без реквизитов", "docx")
    out = extract_requisites(repo, d.id)
    assert out.status == "nothing_found" and repo.count_protocols() == 0 and out.missing


def test_manual_protocol_is_never_overwritten(repo, doc_with_text):
    mine = repo.add_protocol(Protocol(document_id=doc_with_text.id, protocol_number="РУЧНОЙ-1"))
    out = extract_requisites(repo, doc_with_text.id)
    assert out.status == "skipped"
    assert repo.get_protocol(mine.id).protocol_number == "РУЧНОЙ-1" and repo.count_protocols() == 1
    assert repo.get_evidence(mine.id) == []


def test_verified_protocol_is_never_overwritten(repo, doc_with_text):
    out = extract_requisites(repo, doc_with_text.id)
    p = repo.get_protocol(out.protocol_id)
    p.status, p.article = "verified", "ч. 2 ст. 12.34 КоАП РФ"   # человек исправил и подтвердил
    repo.update_protocol(p)
    again = extract_requisites(repo, doc_with_text.id)
    assert again.status == "skipped"
    assert repo.get_protocol(p.id).article == "ч. 2 ст. 12.34 КоАП РФ"


def test_rerun_updates_only_automatic_record(repo, doc_with_text):
    first = extract_requisites(repo, doc_with_text.id)
    repo.set_document_text(doc_with_text.id, SAMPLE.replace("86 НН 000001", "86 НН 000002"), "pdf-text")
    second = extract_requisites(repo, doc_with_text.id)
    assert second.status == "updated" and second.protocol_id == first.protocol_id
    assert repo.get_protocol(first.protocol_id).protocol_number == "86 НН 000002"
    assert repo.count_protocols() == 1
    assert len(repo.get_evidence(first.protocol_id)) == 9          # заменены, не накоплены


def test_ocr_text_adds_warning(repo, make_file):
    d = repo.add_document(make_file("scan.pdf"))
    repo.set_document_text(d.id, SAMPLE, "ocr")
    out = extract_requisites(repo, d.id)
    assert any("OCR" in w for w in out.warnings)


def test_missing_fields_are_reported(repo, make_file):
    d = repo.add_document(make_file("p.pdf"))
    repo.set_document_text(d.id, "ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\n", "docx")
    out = extract_requisites(repo, d.id)
    assert out.status == "created" and "article" in out.missing and "protocol_date" in out.missing
    assert {"violation_date", "location", "gost_norms"} <= set(out.missing)
    p = repo.get_protocol(out.protocol_id)
    assert p.article is None and p.violation_date is None and p.location is None
    assert p.gost_norms is None


def test_deleting_protocol_deletes_evidence(repo, doc_with_text, conn):
    out = extract_requisites(repo, doc_with_text.id)
    repo.delete_protocol(out.protocol_id)
    assert conn.execute("SELECT COUNT(*) FROM protocol_evidence").fetchone()[0] == 0


def test_list_documents_without_protocol(repo, make_file, doc_with_text):
    other = repo.add_document(make_file("q.pdf"))
    repo.set_document_status(doc_with_text.id, "processed")
    repo.set_document_status(other.id, "processed")
    extract_requisites(repo, doc_with_text.id)
    assert repo.list_document_ids_without_protocol() == [other.id]


# ----- цепочка: ODT-файл → текст → реквизиты ----------------------------------------------------------

def test_full_chain_from_odt_file(repo, tmp_path):
    from odf.opendocument import OpenDocumentText
    from odf.text import P
    odt = OpenDocumentText()
    for line in SAMPLE.split("\n"):
        odt.text.addElement(P(text=line))
    path = tmp_path / "протокол.odt"
    odt.save(str(path))
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    doc = repo.add_document(path)
    assert process_document(repo, doc.id).ok
    out = extract_requisites(repo, doc.id)
    p = repo.get_protocol(out.protocol_id)
    assert (p.protocol_number, p.protocol_date, p.offender_type) == ("86 НН 000001", "2026-02-06", "legal_entity")
    assert p.offender_name == 'МБУ "Тестовое управление по дорожному хозяйству"'
    assert p.violation_date == "2026-02-05" and p.location == "ул. Тестовой д. 8"
    assert p.gost_norms.startswith("п. 8.1 ГОСТ Р 50597-2017")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_full_chain_from_scanned_pdf(repo, tmp_path):
    from protocol_archive.extract import ocr_status
    if not ocr_status()[0]:
        pytest.skip("OCR недоступен")
    lines = ["ПРОТОКОЛ 86 НН 000001", "об административном правонарушении", "в отношении юридического лица"]
    pdf = samples.make_scan_pdf(tmp_path / "scan.pdf", lines=lines)
    doc = repo.add_document(pdf)
    assert process_document(repo, doc.id).ok
    out = extract_requisites(repo, doc.id)
    p = repo.get_protocol(out.protocol_id)
    assert p.protocol_number == "86 НН 000001" and p.offender_type == "legal_entity"
    assert any("OCR" in w for w in out.warnings)


# ----- миграция v2 -> v3 --------------------------------------------------------------------------------------

@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35), reason="DROP COLUMN требует SQLite 3.35+")
def test_v2_database_migrates_to_current(tmp_path):
    path = tmp_path / "v2.sqlite3"
    c = connect(path)                       # актуальная схема, затем приводим к виду v2
    c.execute("DROP TABLE protocol_evidence")
    c.execute("ALTER TABLE protocols DROP COLUMN gost_norms")
    c.execute("PRAGMA user_version = 2")
    c.execute("INSERT INTO documents(path,file_name,extension,size_bytes,mtime_ns,sha256,added_at,updated_at)"
              " VALUES('/x/a.pdf','a.pdf','pdf',1,1,'h','t','t')")
    c.execute("INSERT INTO protocols(document_id,protocol_number,created_at,updated_at) VALUES(1,'КЕЙС-1','t','t')")
    c.commit()
    c.close()
    c2 = connect(path)
    try:
        assert c2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        repo = Repository(c2)
        assert repo.get_protocol(1).protocol_number == "КЕЙС-1"
        repo.set_evidence(1, [("article", "ст. 1", "фрагмент", "r")])
        assert repo.get_evidence(1) == [("article", "ст. 1", "фрагмент", "r")]
    finally:
        c2.close()


def test_search_by_gost_point(repo, doc_with_text):
    from protocol_archive.db import ProtocolFilter
    extract_requisites(repo, doc_with_text.id)
    assert len(repo.search_protocols(ProtocolFilter(gost="8.8"))) == 1
    assert len(repo.search_protocols(ProtocolFilter(gost="50597"))) == 1
    assert repo.search_protocols(ProtocolFilter(gost="5.2.4")) == []


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35), reason="DROP COLUMN требует SQLite 3.35+")
def test_v3_database_migrates_to_gost_column(tmp_path):
    path = tmp_path / "v3.sqlite3"
    c = connect(path)
    c.execute("ALTER TABLE protocols DROP COLUMN gost_norms")
    c.execute("PRAGMA user_version = 3")
    c.execute("INSERT INTO documents(path,file_name,extension,size_bytes,mtime_ns,sha256,added_at,updated_at)"
              " VALUES('/x/a.pdf','a.pdf','pdf',1,1,'h','t','t')")
    c.execute("INSERT INTO protocols(document_id,protocol_number,created_at,updated_at) VALUES(1,'КЕЙС-3','t','t')")
    c.commit()
    c.close()
    c2 = connect(path)
    try:
        assert c2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        p = Repository(c2).get_protocol(1)
        assert p.protocol_number == "КЕЙС-3" and p.gost_norms is None
    finally:
        c2.close()


def test_wrapped_text_pdf_gives_every_field(repo, tmp_path):
    """Реальный PDF с переносами строк внутри фраз — прежние правила теряли дату и статью."""
    pdf = samples.make_wrapped_pdf(tmp_path / "wrapped.pdf")
    doc = repo.add_document(pdf)
    assert process_document(repo, doc.id).ok
    out = extract_requisites(repo, doc.id)
    assert out.missing == {}
    p = repo.get_protocol(out.protocol_id)
    assert p.protocol_number == "86 НН 000001" and p.protocol_date == "2026-02-06"
    assert p.violation_date == "2026-02-05"
    assert p.article == "ч. 1 ст. 12.34 КоАП РФ" and p.offender_name == 'МБУ "Тестовое управление"'
    assert p.location == "ул. Мира д. 95; ул. Образцовая д. 2"
    assert p.gost_norms == ("п. 8.1 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017; "
                            "п. 5.2.4 ГОСТ Р 50597-2017")   # порядок документа; п. 3.18 ГОСТ Р 59434-2021 — пояснение


def test_recompute_keeps_manual_edits_and_updates_the_rest(repo, doc_with_text):
    out = extract_requisites(repo, doc_with_text.id)
    p = repo.get_protocol(out.protocol_id)
    p.article = "ч. 2 ст. 12.34 КоАП РФ"           # исправлено вручную, статус остался «извлечён»
    p.violation_date = None                         # пусто — новое правило может заполнить
    repo.update_protocol(p)
    repo.set_document_text(doc_with_text.id, SAMPLE.replace("86 НН 000001", "86 НН 000002"), "pdf-text")

    again = extract_requisites(repo, doc_with_text.id)
    q = repo.get_protocol(out.protocol_id)
    assert again.status == "updated"
    assert q.protocol_number == "86 НН 000002"        # не тронутое вручную — обновилось
    assert q.article == "ч. 2 ст. 12.34 КоАП РФ"      # правка пользователя сохранена
    assert q.violation_date == "2026-02-05"            # пустое поле заполнено


def test_old_extraction_without_new_fields_is_filled_by_recompute(repo, doc_with_text):
    """Запись, созданная старой версией правил (без даты нарушения), дополняется при пересчёте."""
    out = extract_requisites(repo, doc_with_text.id)
    old = repo.get_protocol(out.protocol_id)
    old.violation_date = None
    old.gost_norms = None
    repo.update_protocol(old)
    repo.set_evidence(old.id, [e for e in repo.get_evidence(old.id)
                               if e[0] not in ("violation_date", "gost_norms")])
    extract_requisites(repo, doc_with_text.id)
    q = repo.get_protocol(old.id)
    assert q.violation_date == "2026-02-05" and q.gost_norms


def test_documents_selected_for_extraction(repo, make_file, doc_with_text):
    repo.set_document_status(doc_with_text.id, "processed")
    manual_doc = repo.add_document(make_file("m.pdf"))
    repo.set_document_status(manual_doc.id, "processed")
    repo.add_protocol(Protocol(document_id=manual_doc.id, protocol_number="РУЧНОЙ"))
    fresh = repo.add_document(make_file("n.pdf"))
    repo.set_document_status(fresh.id, "processed")
    new_doc = repo.add_document(make_file("u.pdf"))          # не обработан — не берём

    assert repo.list_document_ids_for_extraction() == [doc_with_text.id, fresh.id]
    extract_requisites(repo, doc_with_text.id)                # теперь у него «извлечённый» протокол
    assert repo.list_document_ids_for_extraction() == [doc_with_text.id, fresh.id]   # пересчитывается
    p = repo.list_protocols_for_document(doc_with_text.id)[0]
    p.status = "verified"
    repo.update_protocol(p)
    assert repo.list_document_ids_for_extraction() == [fresh.id]   # проверенный — исключён
    assert new_doc.id not in repo.list_document_ids_for_extraction()


def test_scanned_wrapped_pdf_through_ocr(repo, tmp_path):
    """Скан того же документа: OCR + переносы строк + пустые строки между абзацами."""
    import pymupdf
    from protocol_archive.extract import ocr_status
    if not ocr_status()[0]:
        pytest.skip("OCR недоступен")
    src = samples.make_wrapped_pdf(tmp_path / "w.pdf", width=420)
    png = pymupdf.open(str(src))[0].get_pixmap(dpi=300).tobytes("png")
    scan = pymupdf.open()
    page = scan.new_page()
    page.insert_image(page.rect, stream=png)
    scan.save(str(tmp_path / "scan.pdf"))
    scan.close()

    doc = repo.add_document(tmp_path / "scan.pdf")
    assert process_document(repo, doc.id).ok
    out = extract_requisites(repo, doc.id)
    p = repo.get_protocol(out.protocol_id)
    assert p.protocol_number == "86 НН 000001" and p.violation_date == "2026-02-05"
    assert "ул. Мира д. 95" in p.location and p.location.count("д.") == 2   # оба пункта списка
    assert "п. 5.2.4 ГОСТ Р 50597-2017" in p.gost_norms and "п. 8.8 ГОСТ Р 50597-2017" in p.gost_norms
    assert "59434" not in p.gost_norms
    assert any("OCR" in w for w in out.warnings)


# ----- формы протоколов, описанные пользователем (DOCX, цепочка целиком) ------------------------------------------

def _docx_protocol(path, number, header_date, circumstances, tail):
    import docx
    d = docx.Document()
    for line in [
        f"ПРОТОКОЛ {number}", "об административном правонарушении", "в отношении юридического лица",
        f"{header_date}\tг. Тестоград, ул. Тестовая, 1",
        "Начальник отделения дорожного надзора отдела ГИБДД УМВД России по г. Тестограду, майор полиции Тестов Т. Т.",
        "в присутствии защитника юридического лица: не явился;",
        "составил настоящий протокол за нарушение, предусмотренное ч. 1 ст. 12.34 КоАП РФ в отношении:",
        'МБУ "Тестовое управление"', "расположенного по адресу: г. Тестоград, ул. Тестовая д. 2",
        "ОБСТОЯТЕЛЬСТВА ПРАВОНАРУШЕНИЯ:", circumstances, tail,
        "В соответствии с п. 3.18 ГОСТ Р 59434-2021 снежный вал – это слой снега.",
    ]:
        d.add_paragraph(line)
    d.save(str(path))
    return path


def _run_chain(repo, path):
    doc = repo.add_document(path)
    assert process_document(repo, doc.id).ok
    out = extract_requisites(repo, doc.id)
    return repo.get_protocol(out.protocol_id), out


def test_form_works_on_a_road_with_time_in_colon_format(repo, tmp_path):
    path = _docx_protocol(
        tmp_path / "a.docx", "86 НН 000011", "08 июля 2026 г. 15 ч. 00 мин.",
        "По результатам контрольных (надзорных) действий – осмотра, проведённого 06.07.2026 с 14:10 по 14:14, "
        "установлены данные, а именно нарушены требования п. 5.1.6 ГОСТ Р 52289-2019, выразившееся в проведении "
        "дорожных работ по демонтажу сборно-разборной искусственной неровности по адресу г. Тестоград "
        "ул. Мира д. 95 в нарушение п. 5.1.6 ГОСТ Р 52289-2019.",
        "Создаётся непосредственная угроза причинения вреда на ул. Чужой д. 1.")
    p, out = _run_chain(repo, path)
    assert out.missing == {}
    assert (p.protocol_date, p.violation_date) == ("2026-07-08", "2026-07-06")
    assert p.location == "ул. Мира д. 95"
    assert p.gost_norms == "п. 5.1.6 ГОСТ Р 52289-2019"          # без повтора, пояснение 3.18 не берётся


def test_form_with_house_before_street_and_hours_minutes(repo, tmp_path):
    path = _docx_protocol(
        tmp_path / "b.docx", "86 НН 000012", "07 мая 2026 г. 12 ч. 00 мин.",
        "В период времени с 10 ч. 44 мин. по 10 ч. 45 мин. 04.05.2026 в ходе осмотра установлено нарушение, "
        "выразившееся в наличии дефекта обочины на 14,5 см, в районе дома 45 по улице Лопарева. "
        "в нарушение п. 5.2.4 ГОСТ Р 50597-2017.",
        "Указанные обстоятельства создают угрозу.")
    p, out = _run_chain(repo, path)
    assert out.missing == {}
    assert p.violation_date == "2026-05-04"
    assert p.location == "ул. Лопарева д. 45"
    assert p.gost_norms == "п. 5.2.4 ГОСТ Р 50597-2017"


def test_form_with_two_standards(repo, tmp_path):
    path = _docx_protocol(
        tmp_path / "c.docx", "86 НН 000013", "30 июля 2026 г. 10 ч. 00 мин.",
        "По результатам осмотра, проведённого 29.07.2026 с 15:55 по 15:57, нарушения, выразившиеся в "
        "неисправности светофора по ул. Кузоваткина д. 14, в нарушение пунктов 6.1.1.1, 6.6.1 ГОСТ Р 58350-2019, "
        "5.9.29, 5.5.5 ГОСТ Р 52289-2019.",
        "Создаётся угроза.")
    p, out = _run_chain(repo, path)
    assert p.violation_date == "2026-07-29" and p.location == "ул. Кузоваткина д. 14"
    assert p.gost_norms == ("п. 6.1.1.1 ГОСТ Р 58350-2019; п. 6.6.1 ГОСТ Р 58350-2019; "
                            "п. 5.9.29 ГОСТ Р 52289-2019; п. 5.5.5 ГОСТ Р 52289-2019")


def test_form_with_numbered_violation_list_and_with_house_before_street(repo, tmp_path):
    path = _docx_protocol(
        tmp_path / "list.docx", "86 НН 000021", "03 июля 2026 г. 15 ч. 00 мин.",
        "По результатам осмотра, проведённого 01.07.2026 с 10:30 по 10:35, нарушены требования "
        "п. 13 ОПД ПДД РФ, выразившиеся в несоблюдении требований по ул. Тестовая д. 60 ст1:\n"
        "1) п. 6.3.1 ГОСТ 50597-2017 — отсутствие дорожной разметки 1.2;\n"
        "2) п. 8.1.17 ГОСТ 52289-2019 — отсутствие ограждения;\n"
        "3) п. 4.5.1.2 ГОСТ Р 52766-2007 — отсутствие тротуаров;",
        "Создаётся угроза.")
    p, out = _run_chain(repo, path)
    assert p.location == "ул. Тестовая д. 60"
    assert p.gost_norms == ("п. 6.3.1 ГОСТ 50597-2017; п. 8.1.17 ГОСТ 52289-2019; "
                            "п. 4.5.1.2 ГОСТ Р 52766-2007")
    assert p.violation_date == "2026-07-01"
    path2 = _docx_protocol(
        tmp_path / "valy.docx", "86 НН 000022", "03 февраля 2026 г. 15 ч. 00 мин.",
        "В период времени с 14 ч. 37 мин. по 14 ч. 38 мин. 30.01.2026 осмотр; нарушены требования "
        "п. 13 ОПД ПДД РФ, п. 8.8 ГОСТ 50597-2017, выразившиеся в формировании снежных валов на границе "
        "перехода по ул. Дзержинского, в районе д. 60/5 ул. Мира г. Тестограда.",
        "Создаётся угроза.")
    p2, out2 = _run_chain(repo, path2)
    assert p2.location == "ул. Мира д. 60/5" and p2.gost_norms == "п. 8.8 ГОСТ 50597-2017"
    assert any("ул. Дзержинского" in w for w in out2.warnings)
