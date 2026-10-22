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
