"""Резервное копирование базы через онлайн-API SQLite (безопасно при открытом соединении)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

BACKUP_PREFIX = "archive_"
BACKUP_SUFFIX = ".sqlite3"


class BackupError(RuntimeError):
    pass


@dataclass
class BackupInfo:
    path: Path
    size_bytes: int
    created: datetime


def create_backup(conn: sqlite3.Connection, backup_dir: Union[str, Path],
                  keep: Optional[int] = 10, now: Optional[datetime] = None) -> Path:
    """Создаёт согласованную копию базы, проверяет её и удаляет старые копии сверх `keep`.

    Копия сначала пишется во временный файл и переименовывается только после
    успешной проверки PRAGMA integrity_check.
    """
    bdir = Path(backup_dir).expanduser()
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    final = bdir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    n = 1
    while final.exists():  # две копии в одну секунду
        final = bdir / f"{BACKUP_PREFIX}{stamp}_{n}{BACKUP_SUFFIX}"
        n += 1
    tmp = final.with_name(final.name + ".part")

    dest = sqlite3.connect(str(tmp))
    try:
        with dest:
            conn.backup(dest)
        result = dest.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"Проверка целостности копии не пройдена: {result}")
    except Exception:
        dest.close()
        tmp.unlink(missing_ok=True)
        raise
    dest.close()
    tmp.replace(final)

    if keep is not None and keep > 0:
        for old in list_backups(bdir)[keep:]:
            old.path.unlink(missing_ok=True)
    return final


def list_backups(backup_dir: Union[str, Path]) -> List[BackupInfo]:
    """Копии от новых к старым (по имени файла, содержащему метку времени)."""
    bdir = Path(backup_dir).expanduser()
    if not bdir.is_dir():
        return []
    files = [p for p in bdir.iterdir()
             if p.is_file() and p.name.startswith(BACKUP_PREFIX) and p.name.endswith(BACKUP_SUFFIX)]
    files.sort(key=lambda p: p.name, reverse=True)
    return [BackupInfo(p, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime))
            for p in files]
