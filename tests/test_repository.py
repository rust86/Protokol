import hashlib
import os

import pytest

from protocol_archive.db import Protocol, ProtocolFilter


# ----- документы -----------------------------------------------------------

def test_add_document_stores_path_and_metadata(repo, make_file):
    f = make_file("Протокол 1.PDF", b"abc")
    doc = repo.add_document(f)
    assert doc.path == str(f.resolve())
    assert doc.file_name == "Протокол 1.PDF"
    assert doc.extension == "pdf"
    assert doc.size_bytes == 3
    assert doc.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert doc.status == "new"


def test_original_is_not_modified(repo, make_file):
    f = make_file("a.pdf", b"original")
    before = (f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_size)
    repo.add_document(f)
    repo.add_document(f)
    assert (f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_size) == before


def test_original_is_not_copied(repo, make_file, tmp_path):
    f = make_file("a.pdf")
    repo.add_document(f)
    files = [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".sqlite3"
             and "-wal" not in p.name and "-shm" not in p.name]
    assert files == [f]


def test_add_same_path_twice_does_not_duplicate(repo, make_file):
    f = make_file("a.pdf")
    d1 = repo.add_document(f)
    d2 = repo.add_document(f)
    assert d1.id == d2.id
    assert repo.count_documents() == 1


def test_changed_content_resets_status_to_new(repo, make_file):
    f = make_file("a.pdf", b"v1")
    doc = repo.add_document(f)
    repo.set_document_status(doc.id, "processed")
    f.write_bytes(b"v2")
    again = repo.add_document(f)
    assert again.status == "new"
    assert again.sha256 == hashlib.sha256(b"v2").hexdigest()


def test_unchanged_content_keeps_status(repo, make_file):
    f = make_file("a.pdf")
    doc = repo.add_document(f)
    repo.set_document_status(doc.id, "processed")
    assert repo.add_document(f).status == "processed"


def test_add_missing_file_raises(repo, tmp_path):
    with pytest.raises(FileNotFoundError):
        repo.add_document(tmp_path / "nope.pdf")


def test_find_by_hash_finds_duplicates_under_different_paths(repo, make_file):
    a = repo.add_document(make_file("a.pdf", b"same"))
    b = repo.add_document(make_file("b.pdf", b"same"))
    assert {d.id for d in repo.find_documents_by_hash(a.sha256)} == {a.id, b.id}


def test_list_documents_name_filter_is_case_insensitive_for_cyrillic(repo, make_file):
    repo.add_document(make_file("Протокол_Иванов.pdf"))
    repo.add_document(make_file("other.pdf"))
    found = repo.list_documents(name_contains="ПРОТОКОЛ")
    assert [d.file_name for d in found] == ["Протокол_Иванов.pdf"]


def test_status_validation(repo, make_file):
    doc = repo.add_document(make_file("a.pdf"))
    with pytest.raises(ValueError):
        repo.set_document_status(doc.id, "bogus")
    with pytest.raises(KeyError):
        repo.set_document_status(9999, "processed")


def test_check_missing_documents(repo, make_file):
    f = make_file("a.pdf")
    doc = repo.add_document(f)
    f.unlink()
    assert repo.check_missing_documents() == 1
    assert repo.get_document(doc.id).status == "missing"
    assert repo.check_missing_documents() == 0  # повторно не считается


def test_file_reappearing_resets_missing(repo, make_file):
    f = make_file("a.pdf")
    doc = repo.add_document(f)
    f.unlink()
    repo.check_missing_documents()
    f.write_bytes(b"test-content")
    assert repo.add_document(f).status == "new"


# ----- протоколы ------------------------------------------------------------

@pytest.fixture
def doc(repo, make_file):
    return repo.add_document(make_file("a.pdf"))


def test_add_and_get_protocol(repo, doc):
    saved = repo.add_protocol(Protocol(
        document_id=doc.id, protocol_number=" ТЕСТ-001 ", protocol_date="2025-03-05",
        article="12.9", offender_name="Тестов Тест Тестович"))
    assert saved.id is not None
    assert saved.protocol_number == "ТЕСТ-001"  # пробелы обрезаны
    assert saved.status == "manual"
    got = repo.get_protocol(saved.id)
    assert got.protocol_date == "2025-03-05"
    assert got.offender_type is None  # тип лица не угадывается
    assert got.location is None  # ничего не выдумываем


def test_empty_strings_become_null(repo, doc):
    saved = repo.add_protocol(Protocol(document_id=doc.id, protocol_number="  ", article=""))
    assert saved.protocol_number is None and saved.article is None


@pytest.mark.parametrize("bad", ["05.03.2025", "2025-13-01", "2025-02-30", "abc"])
def test_invalid_dates_rejected(repo, doc, bad):
    with pytest.raises(ValueError):
        repo.add_protocol(Protocol(document_id=doc.id, protocol_date=bad))


def test_invalid_protocol_status_rejected(repo, doc):
    with pytest.raises(ValueError):
        repo.add_protocol(Protocol(document_id=doc.id, status="bogus"))


def test_protocol_for_unknown_document_rejected(repo):
    with pytest.raises(KeyError):
        repo.add_protocol(Protocol(document_id=12345))


def test_update_protocol(repo, doc):
    p = repo.add_protocol(Protocol(document_id=doc.id, protocol_number="A"))
    p.protocol_number = "B"
    p.status = "verified"
    updated = repo.update_protocol(p)
    assert updated.protocol_number == "B" and updated.status == "verified"
    assert repo.count_protocols() == 1


def test_update_unknown_protocol(repo, doc):
    with pytest.raises(KeyError):
        repo.update_protocol(Protocol(document_id=doc.id, id=777))


def test_delete_protocol_keeps_document(repo, doc):
    p = repo.add_protocol(Protocol(document_id=doc.id))
    repo.delete_protocol(p.id)
    assert repo.count_protocols() == 0
    assert repo.get_document(doc.id) is not None


# ----- поиск ----------------------------------------------------------------

@pytest.fixture
def populated(repo, make_file):
    d1 = repo.add_document(make_file("one.pdf", b"1"))
    d2 = repo.add_document(make_file("two.pdf", b"2"))
    d3 = repo.add_document(make_file("three.pdf", b"3"))
    repo.add_protocol(Protocol(
        document_id=d1.id, protocol_number="ТЕСТ-001", protocol_date="2025-01-10",
        article="12.9", offender_type="official", offender_name="Тестов Аркадий",
        location="ул. Тестовая, 1"))
    repo.add_protocol(Protocol(
        document_id=d2.id, protocol_number="ТЕСТ-002", protocol_date="2025-02-20",
        article="12.15", offender_type="legal_entity", offender_name="ООО «Образец»",
        location="пр. Образцовый, 5"))
    repo.add_protocol(Protocol(
        document_id=d3.id, protocol_number="ТЕСТ-003", protocol_date="2025-03-30",
        article="12.9", offender_name="Пробин Пётр"))
    repo.set_document_text(d1.id, "Превышение скорости на участке дороги", "pdf-text")
    repo.set_document_text(d2.id, "Нарушение правил остановки и стоянки", "ocr")
    return repo


def numbers(rows):
    return sorted(r.protocol.protocol_number for r in rows)


def test_search_without_filter_returns_all(populated):
    assert len(populated.search_protocols()) == 3


def test_search_by_number_substring(populated):
    assert numbers(populated.search_protocols(ProtocolFilter(protocol_number="001"))) == ["ТЕСТ-001"]


def test_search_is_case_insensitive_for_cyrillic(populated):
    assert numbers(populated.search_protocols(ProtocolFilter(offender_name="ооо «обРАЗец»"))) == ["ТЕСТ-002"]


def test_search_by_article(populated):
    assert numbers(populated.search_protocols(ProtocolFilter(article="12.9"))) == ["ТЕСТ-001", "ТЕСТ-003"]


def test_search_by_date_range_inclusive(populated):
    rows = populated.search_protocols(ProtocolFilter(date_from="2025-02-20", date_to="2025-03-30"))
    assert numbers(rows) == ["ТЕСТ-002", "ТЕСТ-003"]


def test_search_combines_conditions_with_and(populated):
    rows = populated.search_protocols(ProtocolFilter(article="12.9", location="тестовая"))
    assert numbers(rows) == ["ТЕСТ-001"]


def test_like_wildcards_are_escaped(populated):
    assert populated.search_protocols(ProtocolFilter(protocol_number="%")) == []
    assert populated.search_protocols(ProtocolFilter(protocol_number="_")) == []


def test_full_text_search_in_document_text(populated):
    assert numbers(populated.search_protocols(ProtocolFilter(text="скорости"))) == ["ТЕСТ-001"]


def test_full_text_search_is_case_insensitive_and_prefix(populated):
    assert numbers(populated.search_protocols(ProtocolFilter(text="ОСТАНОВК"))) == ["ТЕСТ-002"]


def test_full_text_search_requires_all_words(populated):
    assert populated.search_protocols(ProtocolFilter(text="скорости стоянки")) == []


def test_full_text_query_with_special_characters_does_not_crash(populated):
    populated.search_protocols(ProtocolFilter(text='" OR * ( NEAR'))
    assert populated.search_protocols(ProtocolFilter(text='"""')) == []


def test_full_text_index_follows_text_updates(populated):
    doc = populated.get_document_by_path(populated.list_documents(name_contains="one")[0].path)
    populated.set_document_text(doc.id, "совсем другой текст", "ocr")
    assert populated.search_protocols(ProtocolFilter(text="скорости")) == []
    assert numbers(populated.search_protocols(ProtocolFilter(text="другой"))) == ["ТЕСТ-001"]


def test_search_rows_carry_original_path(populated):
    row = populated.search_protocols(ProtocolFilter(protocol_number="002"))[0]
    assert row.document_name == "two.pdf"
    assert row.document_path.endswith(os.path.join("originals", "two.pdf"))


def test_search_limit_and_offset(populated):
    first = populated.search_protocols(limit=2)
    rest = populated.search_protocols(limit=2, offset=2)
    assert len(first) == 2 and len(rest) == 1


def test_invalid_search_date_raises(populated):
    with pytest.raises(ValueError):
        populated.search_protocols(ProtocolFilter(date_from="05.03.2025"))


def test_text_search_fallback_without_fts(populated):
    """Запасной путь, когда FTS5 недоступен (имитируем отсутствие индекса)."""
    populated.conn.executescript(
        "DROP TRIGGER document_texts_ai; DROP TRIGGER document_texts_ad;"
        "DROP TRIGGER document_texts_au; DROP TABLE document_texts_fts;")
    assert numbers(populated.search_protocols(ProtocolFilter(text="СКОРОСТИ"))) == ["ТЕСТ-001"]
    assert populated.search_protocols(ProtocolFilter(text="скорости стоянки")) == []


# ----- тип лица ---------------------------------------------------------------

def test_search_by_offender_type(populated):
    assert numbers(populated.search_protocols(ProtocolFilter(offender_type="official"))) == ["ТЕСТ-001"]
    assert numbers(populated.search_protocols(ProtocolFilter(offender_type="legal_entity"))) == ["ТЕСТ-002"]
    assert len(populated.search_protocols(ProtocolFilter(offender_type=""))) == 3  # любой


def test_invalid_offender_type_rejected_on_save(repo, doc):
    with pytest.raises(ValueError):
        repo.add_protocol(Protocol(document_id=doc.id, offender_type="citizen"))


def test_invalid_offender_type_rejected_in_filter(populated):
    with pytest.raises(ValueError):
        populated.search_protocols(ProtocolFilter(offender_type="citizen"))


def test_empty_offender_type_is_stored_as_null(repo, doc):
    assert repo.add_protocol(Protocol(document_id=doc.id, offender_type="")).offender_type is None


def test_database_rejects_unknown_offender_type(conn, repo, doc):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO protocols(document_id,offender_type,created_at,updated_at)"
                     " VALUES(?, 'citizen','t','t')", (doc.id,))


def test_no_vehicle_plate_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(protocols)")]
    assert "vehicle_plate" not in cols and "offender_type" in cols
