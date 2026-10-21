"""Проверка главного окна в режиме offscreen (без экрана)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from protocol_archive.db import Protocol  # noqa: E402
from protocol_archive.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    w = MainWindow(tmp_path / "ui.sqlite3")
    yield w
    w.close()


def test_window_has_russian_ui(window):
    assert window.windowTitle() == "Архив протоколов"
    assert [window.tabs.tabText(i) for i in range(2)] == ["Протоколы", "Документы"]
    assert window.protocol_table.horizontalHeaderItem(0).text() == "№ протокола"
    menus = [a.text() for a in window.menuBar().actions()]
    assert menus == ["&Файл", "&Справка"]


def test_add_documents_and_search_through_window(window, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    assert window.add_documents([str(f)]) == []
    assert window.document_table.rowCount() == 1
    doc = window.repo.list_documents()[0]
    window.repo.add_protocol(Protocol(document_id=doc.id, protocol_number="ТЕСТ-777",
                                      protocol_date="2025-04-01"))
    window.refresh_all()
    assert window.protocol_table.rowCount() == 1
    assert window.protocol_table.item(0, 1).text() == "01.04.2025"

    window.f_type.setCurrentIndex(window.f_type.findData("legal_entity"))
    assert window.protocol_table.rowCount() == 0   # у протокола тип лица не указан
    window.f_type.setCurrentIndex(0)
    assert window.protocol_table.rowCount() == 1
    window.f_number.setText("нет-такого")
    window.refresh_protocols()
    assert window.protocol_table.rowCount() == 0
    window.reset_search()
    assert window.protocol_table.rowCount() == 1


def test_original_untouched_by_window(window, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"abc")
    before = f.stat().st_mtime_ns
    window.add_documents([str(f)])
    assert f.read_bytes() == b"abc" and f.stat().st_mtime_ns == before


def test_add_missing_file_reports_error_without_crash(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    errors = window.add_documents([str(tmp_path / "нет.pdf")])
    assert len(errors) == 1


def test_backup_from_window(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setenv("PROTOCOL_ARCHIVE_BACKUPS", str(tmp_path / "bk"))
    dest = window.make_backup()
    assert dest is not None and dest.exists()


def test_open_another_database(window, tmp_path):
    other = tmp_path / "other.sqlite3"
    window.open_database(other)
    assert window.db_path == other and other.exists()


def test_no_vehicle_plate_in_ui(window):
    headers = [window.protocol_table.horizontalHeaderItem(i).text()
               for i in range(window.protocol_table.columnCount())]
    assert "Госномер" not in headers and "Тип лица" in headers
    assert not hasattr(window, "f_plate")
