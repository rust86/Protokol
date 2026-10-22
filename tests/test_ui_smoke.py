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


# ----- этап 2: обработка документов через окно ---------------------------------------

import time  # noqa: E402

import samples  # noqa: E402
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit  # noqa: E402


def _wait_processing(window, qapp, timeout=60):
    deadline = time.time() + timeout
    while window.is_processing() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert not window.is_processing(), "обработка не завершилась вовремя"


@pytest.fixture
def quiet(monkeypatch):
    """Подавляет модальные окна и запоминает их тексты."""
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
    return shown


def test_processing_in_background_thread_updates_window(window, qapp, tmp_path, quiet):
    good = samples.make_docx(tmp_path / "a.docx")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    window.add_documents([str(good), str(bad)])

    window.process_pending()
    _wait_processing(window, qapp)

    statuses = {window.document_table.item(r, 0).text(): window.document_table.item(r, 3).text()
                for r in range(window.document_table.rowCount())}
    assert statuses == {"a.docx": "Обработан", "bad.pdf": "Ошибка обработки"}
    messages = {window.document_table.item(r, 0).text(): window.document_table.item(r, 4).text()
                for r in range(window.document_table.rowCount())}
    assert messages["bad.pdf"] and messages["a.docx"] == ""
    assert window.last_summary.processed == 1 and window.last_summary.failed == 1
    assert "С ошибками: 1" in quiet[-1] and "bad.pdf" in quiet[-1]


def test_text_search_on_documents_tab(window, qapp, tmp_path, quiet):
    window.add_documents([str(samples.make_docx(tmp_path / "a.docx")),
                          str(samples.make_odt(tmp_path / "b.odt"))])
    window.process_pending()
    _wait_processing(window, qapp)
    window.doc_query.setText("объединённая")
    window.refresh_documents()
    assert [window.document_table.item(r, 0).text() for r in range(window.document_table.rowCount())] == ["a.docx"]
    window.doc_query.setText("b.odt")           # поиск по имени файла
    window.refresh_documents()
    assert [window.document_table.item(r, 0).text() for r in range(window.document_table.rowCount())] == ["b.odt"]
    window.reset_document_search()
    assert window.document_table.rowCount() == 2


def test_process_pending_with_nothing_to_do(window, quiet):
    window.process_pending()
    assert "Нет документов" in quiet[-1] and not window.is_processing()


def test_show_text_requires_processed_document(window, tmp_path, quiet, monkeypatch):
    window.add_documents([str(samples.make_docx(tmp_path / "a.docx"))])
    window.document_table.selectRow(0)
    window.show_document_text()
    assert "ещё не извлечён" in quiet[-1]


def test_show_text_dialog_after_processing(window, qapp, tmp_path, quiet, monkeypatch):
    window.add_documents([str(samples.make_docx(tmp_path / "a.docx"))])
    window.document_table.selectRow(0)
    window.process_selected()
    _wait_processing(window, qapp)
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    window.document_table.selectRow(0)
    window.show_document_text()
    assert len(opened) == 1
    assert "ТЕСТ-001" in opened[0].findChild(QPlainTextEdit).toPlainText()


def test_window_does_not_modify_originals_during_processing(window, qapp, tmp_path, quiet):
    f = samples.make_docx(tmp_path / "a.docx")
    before = (f.read_bytes(), f.stat().st_mtime_ns)
    window.add_documents([str(f)])
    window.process_pending()
    _wait_processing(window, qapp)
    assert (f.read_bytes(), f.stat().st_mtime_ns) == before
