"""Подключение к SQLite и инициализация схемы."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from .schema import FTS_SQL, MIGRATIONS, SCHEMA_VERSION, TABLES_SQL


class SchemaVersionError(RuntimeError):
    """База создана более новой версией программы."""


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Открывает (и при необходимости создаёт) базу, применяет схему."""
    if str(db_path) != ":memory:":
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        target = str(path)
    else:
        target = ":memory:"

    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # str.lower корректно работает с кириллицей, в отличие от встроенного lower().
    conn.create_function("ulower", 1, lambda s: s.lower() if s is not None else None,
                         deterministic=True)
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    initialize_schema(conn)
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"База имеет версию схемы {version}, программа поддерживает до {SCHEMA_VERSION}. "
            "Обновите программу."
        )
    if version == SCHEMA_VERSION:
        return
    if version == 0:  # новая база: сразу актуальная схема
        with conn:
            conn.executescript(TABLES_SQL)
            if fts5_available(conn):
                conn.executescript(FTS_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    # Существующая база старой версии: последовательные миграции.
    while version < SCHEMA_VERSION:
        script = MIGRATIONS.get(version)
        if script is None:
            raise SchemaVersionError(f"Нет миграции со схемы версии {version}.")
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        version = conn.execute("PRAGMA user_version").fetchone()[0]


def has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_texts_fts'"
    ).fetchone()
    return row is not None
