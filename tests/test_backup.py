import sqlite3
from datetime import datetime, timedelta

from protocol_archive.db import Protocol, Repository, create_backup, list_backups
from protocol_archive.db.schema import SCHEMA_VERSION


def test_backup_is_consistent_copy(repo, conn, make_file, tmp_path):
    doc = repo.add_document(make_file("a.pdf"))
    repo.add_protocol(Protocol(document_id=doc.id, protocol_number="ТЕСТ-001"))
    dest = create_backup(conn, tmp_path / "backups")
    assert dest.exists() and dest.suffix == ".sqlite3"

    copy = sqlite3.connect(dest)
    copy.row_factory = sqlite3.Row
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT protocol_number FROM protocols").fetchone()[0] == "ТЕСТ-001"
    assert copy.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    copy.close()


def test_backup_does_not_touch_original_database(repo, conn, make_file, tmp_path):
    repo.add_document(make_file("a.pdf"))
    create_backup(conn, tmp_path / "backups")
    assert repo.count_documents() == 1


def test_backup_leaves_no_partial_files(conn, tmp_path):
    create_backup(conn, tmp_path / "backups")
    assert not list((tmp_path / "backups").glob("*.part"))


def test_two_backups_in_same_second_do_not_overwrite(conn, tmp_path):
    t = datetime(2025, 1, 1, 12, 0, 0)
    a = create_backup(conn, tmp_path / "b", now=t)
    b = create_backup(conn, tmp_path / "b", now=t)
    assert a != b and a.exists() and b.exists()


def test_rotation_keeps_newest(conn, tmp_path):
    base = datetime(2025, 1, 1, 12, 0, 0)
    for i in range(5):
        create_backup(conn, tmp_path / "b", keep=3, now=base + timedelta(minutes=i))
    names = [b.path.name for b in list_backups(tmp_path / "b")]
    assert len(names) == 3
    assert names[0] == "archive_20250101_120400.sqlite3"  # новые первыми
    assert "archive_20250101_120000.sqlite3" not in names


def test_rotation_ignores_foreign_files(conn, tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    (d / "notes.txt").write_text("не трогать")
    create_backup(conn, d, keep=1)
    create_backup(conn, d, keep=1, now=datetime(2030, 1, 1))
    assert (d / "notes.txt").exists()


def test_list_backups_empty_for_missing_dir(tmp_path):
    assert list_backups(tmp_path / "none") == []
