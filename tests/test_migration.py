"""Миграция базы схемы v1 (с госномером) на актуальную."""
import sqlite3

import pytest

from protocol_archive.db import Protocol, ProtocolFilter, Repository, connect
from protocol_archive.db.schema import SCHEMA_VERSION

# Схема версии 1 ровно в том виде, в каком её создавала первая версия программы.
V1_DDL = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, file_name TEXT NOT NULL,
    extension TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','processed','error','missing')),
    error_message TEXT, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_documents_sha256 ON documents(sha256);
CREATE INDEX idx_documents_status ON documents(status);
CREATE TABLE protocols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    protocol_number TEXT, protocol_date TEXT, violation_date TEXT, article TEXT,
    offender_name TEXT, vehicle_plate TEXT, location TEXT, authority TEXT, notes TEXT,
    status TEXT NOT NULL DEFAULT 'manual' CHECK (status IN ('manual','extracted','verified')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_protocols_document ON protocols(document_id);
CREATE INDEX idx_protocols_number ON protocols(protocol_number);
CREATE INDEX idx_protocols_date ON protocols(protocol_date);
CREATE INDEX idx_protocols_article ON protocols(article);
CREATE TABLE document_texts (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    text TEXT NOT NULL, source TEXT NOT NULL, extracted_at TEXT NOT NULL);
CREATE VIRTUAL TABLE document_texts_fts USING fts5(
    text, content='document_texts', content_rowid='document_id',
    tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER document_texts_ai AFTER INSERT ON document_texts BEGIN
    INSERT INTO document_texts_fts(rowid, text) VALUES (new.document_id, new.text); END;
CREATE TRIGGER document_texts_ad AFTER DELETE ON document_texts BEGIN
    INSERT INTO document_texts_fts(document_texts_fts, rowid, text)
    VALUES ('delete', old.document_id, old.text); END;
CREATE TRIGGER document_texts_au AFTER UPDATE ON document_texts BEGIN
    INSERT INTO document_texts_fts(document_texts_fts, rowid, text)
    VALUES ('delete', old.document_id, old.text);
    INSERT INTO document_texts_fts(rowid, text) VALUES (new.document_id, new.text); END;
PRAGMA user_version = 1;
"""


@pytest.fixture
def v1_path(tmp_path):
    path = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(path)
    raw.executescript(V1_DDL)
    raw.execute("INSERT INTO documents VALUES(1,'/x/a.pdf','a.pdf','pdf',1,1,'h','new',NULL,'t','t')")
    raw.execute("INSERT INTO documents VALUES(2,'/x/b.pdf','b.pdf','pdf',2,2,'h2','processed',NULL,'t','t')")
    raw.execute("INSERT INTO protocols(id,document_id,protocol_number,protocol_date,article,"
                "offender_name,vehicle_plate,location,authority,notes,status,created_at,updated_at)"
                " VALUES(10,1,'ТЕСТ-001','2025-01-10','12.9','ООО «Образец»','А001АА86',"
                "'ул. Тестовая','Орган','прим','verified','c','u')")
    raw.execute("INSERT INTO document_texts VALUES(1,'текст про скорость','pdf-text','t')")
    raw.commit()
    raw.close()
    return path


def test_v1_database_is_migrated_with_data_intact(v1_path):
    conn = connect(v1_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = [r[1] for r in conn.execute("PRAGMA table_info(protocols)")]
        assert "vehicle_plate" not in cols and "offender_type" in cols

        p = Repository(conn).get_protocol(10)
        assert (p.protocol_number, p.protocol_date, p.article) == ("ТЕСТ-001", "2025-01-10", "12.9")
        assert (p.offender_name, p.location, p.authority, p.notes) == (
            "ООО «Образец»", "ул. Тестовая", "Орган", "прим")
        assert p.status == "verified" and p.created_at == "c" and p.updated_at == "u"
        assert p.offender_type is None  # тип не угадывается по названию
        assert Repository(conn).count_documents() == 2
    finally:
        conn.close()


def test_migrated_database_is_fully_functional(v1_path, tmp_path):
    conn = connect(v1_path)
    try:
        repo = Repository(conn)
        # полнотекстовый поиск и индексы пережили миграцию
        assert [r.protocol.id for r in repo.search_protocols(ProtocolFilter(text="скорость"))] == [10]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_protocols_document", "idx_protocols_number",
                "idx_protocols_date", "idx_protocols_article"} <= names
        # новые записи получают следующий id, тип лица работает
        doc = repo.get_document(2)
        new = repo.add_protocol(Protocol(document_id=doc.id, offender_type="official"))
        assert new.id == 11
        # внешние ключи по-прежнему каскадно удаляют
        repo.delete_document(1)
        assert repo.get_protocol(10) is None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_migration_is_idempotent_on_reopen(v1_path):
    connect(v1_path).close()
    conn = connect(v1_path)
    assert conn.execute("SELECT COUNT(*) FROM protocols").fetchone()[0] == 1
    conn.close()


def test_failed_migration_rolls_back(v1_path, monkeypatch):
    from protocol_archive.db import connection, schema
    broken = dict(schema.MIGRATIONS)
    broken[1] = broken[1].replace("DROP TABLE protocols;", "DROP TABLE no_such_table;")
    monkeypatch.setattr(connection, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        connect(v1_path)
    raw = sqlite3.connect(v1_path)
    cols = [r[1] for r in raw.execute("PRAGMA table_info(protocols)")]
    assert "vehicle_plate" in cols                     # старая таблица на месте
    assert raw.execute("SELECT COUNT(*) FROM protocols").fetchone()[0] == 1
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "protocols_new" not in {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    raw.close()
