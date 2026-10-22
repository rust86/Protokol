"""Схема SQLite. Версия схемы хранится в PRAGMA user_version.

В базе хранятся только извлечённые сведения и пути к оригиналам;
содержимое оригиналов не копируется.
"""
from __future__ import annotations

SCHEMA_VERSION = 4

# Допустимые значения статусов (проверяются CHECK-ограничениями).
DOCUMENT_STATUSES = ("new", "processed", "error", "missing")
PROTOCOL_STATUSES = ("manual", "extracted", "verified")
# Протоколы ведутся только в отношении должностных и юридических лиц.
OFFENDER_TYPES = ("official", "legal_entity")

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT    NOT NULL UNIQUE,          -- абсолютный путь к оригиналу
    file_name     TEXT    NOT NULL,
    extension     TEXT    NOT NULL,                 -- в нижнем регистре, без точки
    size_bytes    INTEGER NOT NULL,
    mtime_ns      INTEGER NOT NULL,
    sha256        TEXT    NOT NULL,                 -- хэш содержимого оригинала
    status        TEXT    NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new','processed','error','missing')),
    error_message TEXT,
    added_at      TEXT    NOT NULL,                 -- ISO 8601 UTC
    updated_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS protocols (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    protocol_number   TEXT,
    protocol_date     TEXT,                         -- YYYY-MM-DD
    violation_date    TEXT,                         -- YYYY-MM-DD
    article           TEXT,                         -- статья КоАП / закона субъекта
    offender_type     TEXT
                      CHECK (offender_type IS NULL OR offender_type IN ('official','legal_entity')),
    offender_name     TEXT,                         -- ФИО должностного лица или наименование юрлица
    location          TEXT,
    gost_norms        TEXT,                         -- нарушенные пункты ГОСТ, через «; »
    authority         TEXT,                         -- орган, составивший протокол
    notes             TEXT,
    status            TEXT NOT NULL DEFAULT 'manual'
                      CHECK (status IN ('manual','extracted','verified')),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_protocols_document ON protocols(document_id);
CREATE INDEX IF NOT EXISTS idx_protocols_number   ON protocols(protocol_number);
CREATE INDEX IF NOT EXISTS idx_protocols_date     ON protocols(protocol_date);
CREATE INDEX IF NOT EXISTS idx_protocols_article  ON protocols(article);

-- Откуда программа взяла автоматически извлечённые значения (для проверки по оригиналу).
CREATE TABLE IF NOT EXISTS protocol_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
    field       TEXT    NOT NULL,                   -- имя поля протокола
    value       TEXT    NOT NULL,                   -- извлечённое значение
    evidence    TEXT    NOT NULL,                   -- фрагмент исходного текста
    rule        TEXT    NOT NULL                    -- идентификатор правила
);
CREATE INDEX IF NOT EXISTS idx_evidence_protocol ON protocol_evidence(protocol_id);

-- Текст, извлечённый из оригинала (для полнотекстового поиска).
CREATE TABLE IF NOT EXISTS document_texts (
    document_id  INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    text         TEXT    NOT NULL,
    source       TEXT    NOT NULL,                  -- 'pdf-text','ocr','docx','odt','doc', ...
    extracted_at TEXT    NOT NULL
);
"""

# Полнотекстовый индекс. Требует SQLite, собранного с FTS5; tokenizer unicode61
# корректно приводит к одному регистру кириллицу.
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS document_texts_fts USING fts5(
    text,
    content='document_texts',
    content_rowid='document_id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS document_texts_ai AFTER INSERT ON document_texts BEGIN
    INSERT INTO document_texts_fts(rowid, text) VALUES (new.document_id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS document_texts_ad AFTER DELETE ON document_texts BEGIN
    INSERT INTO document_texts_fts(document_texts_fts, rowid, text)
    VALUES ('delete', old.document_id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS document_texts_au AFTER UPDATE ON document_texts BEGIN
    INSERT INTO document_texts_fts(document_texts_fts, rowid, text)
    VALUES ('delete', old.document_id, old.text);
    INSERT INTO document_texts_fts(rowid, text) VALUES (new.document_id, new.text);
END;
"""


# ----- миграции ---------------------------------------------------------------
# Ключ — версия, ИЗ которой выполняется миграция. Каждый скрипт атомарен
# (BEGIN … COMMIT) и сам выставляет user_version.

MIGRATIONS = {
    # v1 -> v2: убран госномер ТС (протоколы только в отношении должностных и
    # юридических лиц), добавлен тип лица. Для существующих записей тип остаётся
    # пустым (NULL): программа не угадывает его по имени.
    1: """
    BEGIN;
    CREATE TABLE protocols_new (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        protocol_number   TEXT,
        protocol_date     TEXT,
        violation_date    TEXT,
        article           TEXT,
        offender_type     TEXT
                          CHECK (offender_type IS NULL OR offender_type IN ('official','legal_entity')),
        offender_name     TEXT,
        location          TEXT,
        authority         TEXT,
        notes             TEXT,
        status            TEXT NOT NULL DEFAULT 'manual'
                          CHECK (status IN ('manual','extracted','verified')),
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    );
    INSERT INTO protocols_new(id, document_id, protocol_number, protocol_date, violation_date,
                              article, offender_name, location, authority, notes, status,
                              created_at, updated_at)
        SELECT id, document_id, protocol_number, protocol_date, violation_date,
               article, offender_name, location, authority, notes, status,
               created_at, updated_at
        FROM protocols;
    DROP TABLE protocols;
    ALTER TABLE protocols_new RENAME TO protocols;
    CREATE INDEX IF NOT EXISTS idx_protocols_document ON protocols(document_id);
    CREATE INDEX IF NOT EXISTS idx_protocols_number   ON protocols(protocol_number);
    CREATE INDEX IF NOT EXISTS idx_protocols_date     ON protocols(protocol_date);
    CREATE INDEX IF NOT EXISTS idx_protocols_article  ON protocols(article);
    PRAGMA user_version = 2;
    COMMIT;
    """,
    # v2 -> v3: таблица происхождения автоматически извлечённых значений.
    2: """
    BEGIN;
    CREATE TABLE IF NOT EXISTS protocol_evidence (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        protocol_id INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
        field       TEXT    NOT NULL,
        value       TEXT    NOT NULL,
        evidence    TEXT    NOT NULL,
        rule        TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_evidence_protocol ON protocol_evidence(protocol_id);
    PRAGMA user_version = 3;
    COMMIT;
    """,
    # v3 -> v4: нарушенные пункты ГОСТ.
    3: """
    BEGIN;
    ALTER TABLE protocols ADD COLUMN gost_norms TEXT;
    PRAGMA user_version = 4;
    COMMIT;
    """,
}
