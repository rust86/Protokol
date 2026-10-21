import sqlite3

import pytest

from protocol_archive.db import SchemaVersionError, connect
from protocol_archive.db.connection import has_fts
from protocol_archive.db.schema import SCHEMA_VERSION


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table')")}


def test_creates_all_tables_and_version(conn):
    assert {"documents", "protocols", "document_texts"} <= _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_fts_table_created_when_available(conn):
    assert has_fts(conn)


def test_foreign_keys_enabled(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_reopen_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "db.sqlite3"
    c1 = connect(path)
    c1.execute("INSERT INTO documents(path,file_name,extension,size_bytes,mtime_ns,sha256,"
               "added_at,updated_at) VALUES('/x/a.pdf','a.pdf','pdf',1,1,'h','t','t')")
    c1.commit()
    c1.close()
    c2 = connect(path)
    assert c2.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    c2.close()


def test_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "db.sqlite3"
    connect(path).close()
    assert path.exists()


def test_newer_schema_is_rejected(tmp_path):
    path = tmp_path / "db.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()
    with pytest.raises(SchemaVersionError):
        connect(path)


def test_check_constraints_reject_bad_status(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO documents(path,file_name,extension,size_bytes,mtime_ns,"
                     "sha256,status,added_at,updated_at) "
                     "VALUES('/p','p','pdf',1,1,'h','bogus','t','t')")


def test_path_is_unique(conn):
    sql = ("INSERT INTO documents(path,file_name,extension,size_bytes,mtime_ns,sha256,"
           "added_at,updated_at) VALUES('/p','p','pdf',1,1,'h','t','t')")
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_protocol_requires_existing_document(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO protocols(document_id,created_at,updated_at) "
                     "VALUES(999,'t','t')")


def test_cascade_delete_removes_protocols_and_text(repo, make_file):
    from protocol_archive.db import Protocol
    doc = repo.add_document(make_file("a.pdf"))
    repo.add_protocol(Protocol(document_id=doc.id, protocol_number="T-1"))
    repo.set_document_text(doc.id, "тестовый текст", "pdf-text")
    repo.delete_document(doc.id)
    assert repo.count_protocols() == 0
    assert repo.get_document_text(doc.id) is None
