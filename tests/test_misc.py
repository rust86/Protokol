import pytest

from protocol_archive import envcheck
from protocol_archive.config import default_backup_dir, default_db_path
from protocol_archive.dates import to_display, to_iso


def test_dates_roundtrip():
    assert to_iso("05.03.2025") == "2025-03-05"
    assert to_iso("2025-03-05") == "2025-03-05"
    assert to_iso("  ") is None
    assert to_display("2025-03-05") == "05.03.2025"
    assert to_display(None) == ""


@pytest.mark.parametrize("bad", ["31.02.2025", "5/3/2025", "abc"])
def test_dates_invalid(bad):
    with pytest.raises(ValueError):
        to_iso(bad)


def test_config_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("PROTOCOL_ARCHIVE_DB", raising=False)
    monkeypatch.delenv("PROTOCOL_ARCHIVE_BACKUPS", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db = default_db_path()
    assert db == tmp_path / "protocol-archive" / "archive.sqlite3"
    assert default_backup_dir(db) == tmp_path / "protocol-archive" / "backups"
    monkeypatch.setenv("PROTOCOL_ARCHIVE_DB", str(tmp_path / "x.sqlite3"))
    assert default_db_path() == tmp_path / "x.sqlite3"


def test_envcheck_runs_and_reports_python_and_sqlite():
    results = envcheck.check_all()
    names = {r.name for r in results}
    assert {"Python", "SQLite", "PySide6 (Qt)", "Tesseract OCR", "LibreOffice"} <= names
    assert next(r for r in results if r.name == "Python").ok
    assert "Python" in envcheck.format_report(results)


def test_envcheck_missing_module_is_reported_not_raised():
    r = envcheck._check_module("X", ["no_such_module_xyz"], "no-such-dist")
    assert not r.ok and "pip install" in r.hint


def test_envcheck_tesseract_absent(monkeypatch):
    monkeypatch.setattr(envcheck.shutil, "which", lambda _n: None)
    res = envcheck.check_tesseract()
    assert len(res) == 1 and not res[0].ok and not res[0].required
