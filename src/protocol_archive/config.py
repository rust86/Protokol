"""Пути по умолчанию (XDG) и переопределение через окружение."""
from __future__ import annotations

import os
from pathlib import Path

ENV_DB_PATH = "PROTOCOL_ARCHIVE_DB"
ENV_BACKUP_DIR = "PROTOCOL_ARCHIVE_BACKUPS"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "protocol-archive"


def default_db_path() -> Path:
    override = os.environ.get(ENV_DB_PATH)
    return Path(override).expanduser() if override else data_dir() / "archive.sqlite3"


def default_backup_dir(db_path: Path) -> Path:
    override = os.environ.get(ENV_BACKUP_DIR)
    if override:
        return Path(override).expanduser()
    return Path(db_path).parent / "backups"
