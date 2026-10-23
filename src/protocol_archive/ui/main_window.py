from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from PySide6.QtCore import QThread, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QComboBox, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QDialog,
)

from .. import APP_NAME, __version__
from ..config import default_backup_dir
from ..dates import to_display, to_iso
from ..db import (
    BackupError, ProtocolFilter, Repository, connect, create_backup, list_backups,
)
from ..envcheck import check_all, format_report
from ..extract import OcrOptions
from ..parsing import FIELD_LABELS, extract_requisites
from ..processing import ProcessSummary
from .dialogs import OFFENDER_TYPE_LABELS, STATUS_LABELS, ProtocolDialog
from .worker import ProcessWorker

DOC_STATUS_LABELS = {
    "new": "Добавлен, не обработан",
    "processed": "Обработан",
    "error": "Ошибка обработки",
    "missing": "Файл не найден",
}
SUPPORTED_EXTENSIONS = "Документы (*.pdf *.docx *.odt *.doc);;Все файлы (*)"
NOT_IMPLEMENTED_NOTE = (
    "Вкладка «Документы»: «Обработать» извлекает текст (PDF, сканы через OCR, DOCX, ODT, DOC), "
    "«Извлечь реквизиты» заполняет номер, даты, статью, тип и наименование лица, орган, место "
    "нарушения и нарушенные пункты ГОСТ по правилам; всё извлечённое проверяйте по оригиналу."
)


MAX_COLUMN_WIDTH = 380  # начальная ширина колонки не больше этой; дальше длинный текст переносится


def _fit_table(table: QTableWidget) -> None:
    """Подгоняет колонки под содержимое (но не шире MAX_COLUMN_WIDTH) и высоту строк под перенос."""
    table.resizeColumnsToContents()
    for col in range(table.columnCount()):
        if table.columnWidth(col) > MAX_COLUMN_WIDTH:
            table.setColumnWidth(col, MAX_COLUMN_WIDTH)
    table.resizeRowsToContents()


def _item(text: str, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text or "")
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


class MainWindow(QMainWindow):
    PROTOCOL_COLUMNS = ["№ протокола", "Дата протокола", "Дата нарушения", "Статья", "Тип лица",
                        "ФИО / наименование", "Место нарушения", "Нарушенные пункты ГОСТ",
                        "Статус записи", "Документ"]
    DOCUMENT_COLUMNS = ["Файл", "Тип", "Размер, байт", "Статус", "Сообщение", "Добавлен", "Путь"]

    def __init__(self, db_path: Path):
        super().__init__()
        self.db_path = Path(db_path)
        self.conn = connect(self.db_path)
        self.repo = Repository(self.conn)

        self.setWindowTitle(APP_NAME)
        self.resize(1200, 700)
        self._build_menu()
        self._build_ui()
        self.refresh_all()

    # ----- построение интерфейса -------------------------------------------

    def _build_menu(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("&Файл")
        self._act(m_file, "Открыть или создать базу…", self.choose_database, QKeySequence.Open)
        self._act(m_file, "Добавить документы…", self.add_documents_dialog, "Ctrl+D")
        m_file.addSeparator()
        self._act(m_file, "Создать резервную копию базы", self.make_backup, "Ctrl+B")
        m_file.addSeparator()
        self._act(m_file, "Выход", self.close, QKeySequence.Quit)

        m_help = bar.addMenu("&Справка")
        self._act(m_help, "Проверка зависимостей…", self.show_dependencies)
        self._act(m_help, "О программе", self.show_about)

    def _act(self, menu, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        note = QLabel(NOT_IMPLEMENTED_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("background:#fff4d6; border:1px solid #e0c36a; padding:6px;")
        root.addWidget(note)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_protocols_tab(), "Протоколы")
        self.tabs.addTab(self._build_documents_tab(), "Документы")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)
        self._thread = None
        self._worker = None
        self._progress = None

    def _build_search_box(self) -> QGroupBox:
        box = QGroupBox("Поиск протоколов")
        grid = QGridLayout(box)
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText("слова из извлечённого текста документа")
        self.f_number = QLineEdit()
        self.f_article = QLineEdit()
        self.f_offender = QLineEdit()
        self.f_type = QComboBox()
        self.f_type.addItem("любой", "")
        for key, label in OFFENDER_TYPE_LABELS.items():
            self.f_type.addItem(label, key)
        self.f_location = QLineEdit()
        self.f_gost = QLineEdit()
        self.f_gost.setPlaceholderText("например, 8.8 или 50597")
        self.f_from = QLineEdit()
        self.f_from.setPlaceholderText("ДД.ММ.ГГГГ")
        self.f_to = QLineEdit()
        self.f_to.setPlaceholderText("ДД.ММ.ГГГГ")
        # Сетка: пары «подпись / поле» в колонках (0,1), (2,3), (4,5).
        grid.addWidget(QLabel("Текст документа:"), 0, 0)
        grid.addWidget(self.f_text, 0, 1, 1, 5)
        rows = [
            [("№ протокола:", self.f_number), ("Статья:", self.f_article), ("ФИО / наименование:", self.f_offender)],
            [("Тип лица:", self.f_type), ("Место:", self.f_location), ("Пункт ГОСТ:", self.f_gost)],
            [("Дата протокола с:", self.f_from), ("по:", self.f_to)],
        ]
        for r, pairs in enumerate(rows, start=1):
            for k, (label, widget) in enumerate(pairs):
                grid.addWidget(QLabel(label), r, 2 * k)
                grid.addWidget(widget, r, 2 * k + 1)
        for w in (self.f_text, self.f_number, self.f_article, self.f_offender,
                  self.f_location, self.f_gost, self.f_from, self.f_to):
            w.returnPressed.connect(self.refresh_protocols)
        self.f_type.currentIndexChanged.connect(lambda _i: self.refresh_protocols())
        btn_find = QPushButton("Найти")
        btn_find.clicked.connect(self.refresh_protocols)
        btn_reset = QPushButton("Сбросить")
        btn_reset.clicked.connect(self.reset_search)
        grid.addWidget(btn_find, 3, 4)
        grid.addWidget(btn_reset, 3, 5)
        for col in (1, 3, 5):
            grid.setColumnStretch(col, 1)
        return box

    @staticmethod
    def _make_table(columns: List[str]) -> QTableWidget:
        t = QTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setStretchLastSection(True)
        t.setSortingEnabled(False)
        # Длинный текст переносится по ширине колонки (а не обрезается «…»); при изменении
        # ширины колонки или окна высота строк пересчитывается.
        t.setWordWrap(True)
        t.setTextElideMode(Qt.ElideNone)
        timer = QTimer(t)
        timer.setSingleShot(True)
        timer.setInterval(30)
        timer.timeout.connect(t.resizeRowsToContents)
        t.horizontalHeader().sectionResized.connect(lambda *_: timer.start())
        t._relayout_timer = timer
        return t

    def _build_protocols_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._build_search_box())
        self.protocol_table = self._make_table(self.PROTOCOL_COLUMNS)
        self.protocol_table.doubleClicked.connect(lambda _i: self.edit_protocol())
        lay.addWidget(self.protocol_table)
        row = QHBoxLayout()
        for text, slot in (
            ("Добавить протокол вручную…", self.add_protocol),
            ("Изменить…", self.edit_protocol),
            ("Удалить запись", self.delete_protocol),
            ("Открыть оригинал", self.open_protocol_original),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _build_documents_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Найти документы по тексту или имени файла:"))
        self.doc_query = QLineEdit()
        self.doc_query.setPlaceholderText("слова из текста документа или часть имени файла")
        self.doc_query.returnPressed.connect(self.refresh_documents)
        find_row.addWidget(self.doc_query, 1)
        b_find = QPushButton("Найти")
        b_find.clicked.connect(self.refresh_documents)
        b_clear = QPushButton("Сбросить")
        b_clear.clicked.connect(self.reset_document_search)
        find_row.addWidget(b_find)
        find_row.addWidget(b_clear)
        lay.addLayout(find_row)
        self.document_table = self._make_table(self.DOCUMENT_COLUMNS)
        self.document_table.doubleClicked.connect(lambda _i: self.show_document_text())
        lay.addWidget(self.document_table)
        row = QHBoxLayout()
        for text, slot in (
            ("Добавить документы…", self.add_documents_dialog),
            ("Обработать выбранный", self.process_selected),
            ("Обработать все новые и с ошибками", self.process_pending),
            ("Показать текст", self.show_document_text),
            ("Извлечь реквизиты", self.extract_selected_requisites),
            ("Извлечь реквизиты у всех обработанных", self.extract_pending_requisites),
            ("Удалить из базы", self.delete_document),
            ("Проверить наличие файлов", self.check_missing),
            ("Открыть оригинал", self.open_document_original),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("«Удалить из базы» удаляет только запись и связанные протоколы; "
                      "файл-оригинал не затрагивается.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    # ----- обновление данных -----------------------------------------------

    def current_filter(self) -> ProtocolFilter:
        return ProtocolFilter(
            text=self.f_text.text(),
            protocol_number=self.f_number.text(),
            article=self.f_article.text(),
            offender_name=self.f_offender.text(),
            offender_type=self.f_type.currentData() or "",
            location=self.f_location.text(),
            gost=self.f_gost.text(),
            date_from=to_iso(self.f_from.text()),
            date_to=to_iso(self.f_to.text()),
        )

    def refresh_all(self) -> None:
        self.refresh_protocols()
        self.refresh_documents()

    def refresh_protocols(self) -> None:
        try:
            rows = self.repo.search_protocols(self.current_filter())
        except ValueError as exc:
            QMessageBox.warning(self, "Поиск", str(exc))
            return
        t = self.protocol_table
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            p = r.protocol
            values = [p.protocol_number, to_display(p.protocol_date),
                      to_display(p.violation_date), p.article,
                      OFFENDER_TYPE_LABELS.get(p.offender_type or "", ""), p.offender_name,
                      p.location, p.gost_norms, STATUS_LABELS.get(p.status, p.status),
                      r.document_name]
            for c, v in enumerate(values):
                t.setItem(i, c, _item(v or "", p.id if c == 0 else None))
            t.item(i, 0).setData(Qt.UserRole + 1, r.document_path)
        _fit_table(t)
        self._update_status()

    def refresh_documents(self) -> None:
        query = self.doc_query.text().strip()
        docs = self.repo.list_documents(text=query) if query else self.repo.list_documents()
        if query:  # слова из запроса могут быть и частью имени файла
            seen = {d.id for d in docs}
            docs += [d for d in self.repo.list_documents(name_contains=query) if d.id not in seen]
        t = self.document_table
        t.setRowCount(len(docs))
        for i, d in enumerate(docs):
            values = [d.file_name, d.extension.upper(), str(d.size_bytes),
                      DOC_STATUS_LABELS.get(d.status, d.status), d.error_message or "",
                      d.added_at, d.path]
            for c, v in enumerate(values):
                t.setItem(i, c, _item(v, d.id if c == 0 else None))
        _fit_table(t)
        self._update_status()

    def _update_status(self) -> None:
        self.status_label.setText(
            f"База: {self.db_path}   |   документов: {self.repo.count_documents()}, "
            f"протоколов: {self.repo.count_protocols()}, показано: {self.protocol_table.rowCount()}"
        )

    def reset_search(self) -> None:
        self.f_type.blockSignals(True)
        self.f_type.setCurrentIndex(0)
        self.f_type.blockSignals(False)
        for w in (self.f_text, self.f_number, self.f_article, self.f_offender,
                  self.f_location, self.f_gost, self.f_from, self.f_to):
            w.clear()
        self.refresh_protocols()

    # ----- действия: документы ---------------------------------------------

    def add_documents_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Добавить документы", "",
                                                SUPPORTED_EXTENSIONS)
        if paths:
            self.add_documents(paths)

    last_summary = None

    def add_documents(self, paths: Iterable[str]) -> List[str]:
        """Регистрирует файлы. Возвращает список сообщений об ошибках (пустой при успехе)."""
        errors: List[str] = []
        added = 0
        for p in paths:
            try:
                self.repo.add_document(p)
                added += 1
            except (OSError, ValueError) as exc:
                errors.append(f"{p}: {exc}")
        self.refresh_all()
        self.statusBar().showMessage(f"Добавлено документов: {added}", 5000)
        if errors:
            QMessageBox.warning(self, "Добавление документов", "\n".join(errors))
        return errors

    def _selected_id(self, table: QTableWidget) -> Optional[int]:
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        return table.item(rows[0].row(), 0).data(Qt.UserRole)

    def reset_document_search(self) -> None:
        self.doc_query.clear()
        self.refresh_documents()

    # ----- обработка документов (фоновый поток) --------------------------------

    def process_selected(self) -> None:
        doc_id = self._selected_id(self.document_table)
        if doc_id is None:
            QMessageBox.information(self, "Обработка", "Выберите документ в таблице.")
            return
        self.start_processing([doc_id])

    def process_pending(self) -> None:
        ids = self.repo.list_document_ids(("new", "error"))
        if not ids:
            QMessageBox.information(self, "Обработка", "Нет документов, ожидающих обработки.")
            return
        self.start_processing(ids)

    def is_processing(self) -> bool:
        return self._thread is not None

    def start_processing(self, doc_ids, options: Optional[OcrOptions] = None) -> None:
        if self.is_processing():
            return
        worker = ProcessWorker(self.db_path, doc_ids, options or OcrOptions())
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_processing_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        dlg = QProgressDialog("Подготовка…", "Отмена", 0, len(doc_ids), self)
        dlg.setWindowTitle("Обработка документов")
        dlg.setWindowModality(Qt.ApplicationModal)  # пока идёт обработка, правки блокируются
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(worker.cancel_event.set)
        worker.progress.connect(self._on_processing_progress)

        self._thread, self._worker, self._progress = thread, worker, dlg
        dlg.show()
        thread.start()

    def _on_processing_progress(self, done: int, total: int, name: str) -> None:
        if self._progress is not None:
            self._progress.setMaximum(total)
            self._progress.setValue(done)
            if name:
                self._progress.setLabelText(f"Обрабатывается ({done + 1} из {total}):\n{name}\n"
                                            "Сканы распознаются медленно — это нормально.")

    def _on_processing_finished(self, result) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress.deleteLater()
        thread = self._thread
        self._progress = self._worker = self._thread = None
        if thread is not None:
            thread.wait(5000)
        self.refresh_all()
        if isinstance(result, Exception):
            QMessageBox.critical(self, "Обработка", f"Обработка прервана ошибкой:\n{result}")
            return
        self.last_summary = result
        self._show_summary(result)

    def _show_summary(self, s: ProcessSummary) -> None:
        lines = [f"Обработано успешно: {s.processed}", f"С ошибками: {s.failed}"]
        if s.cancelled:
            lines.append("Обработка отменена пользователем; необработанные остались в очереди.")
        for o in [o for o in s.outcomes if not o.ok][:10]:
            lines.append(f"• {o.file_name}: {o.message}")
        ocr = [o for o in s.outcomes if o.ok and o.source in ("ocr", "pdf-text+ocr")]
        if ocr:
            lines.append(f"Распознано OCR: {len(ocr)} док. Текст OCR может содержать ошибки — "
                         "проверяйте реквизиты по оригиналу.")
        QMessageBox.information(self, "Обработка завершена", "\n".join(lines))

    # ----- извлечение реквизитов -------------------------------------------------

    def extract_selected_requisites(self) -> None:
        doc_id = self._selected_id(self.document_table)
        if doc_id is None:
            QMessageBox.information(self, "Реквизиты", "Выберите документ в таблице.")
            return
        self._run_requisites([doc_id])

    def extract_pending_requisites(self) -> None:
        ids = self.repo.list_document_ids_for_extraction()
        if not ids:
            QMessageBox.information(self, "Реквизиты",
                                    "Нет обработанных документов, для которых можно извлечь "
                                    "реквизиты (протоколы, внесённые вручную или проверенные, "
                                    "не пересчитываются).")
            return
        self._run_requisites(ids)

    def _run_requisites(self, doc_ids) -> None:
        outcomes = [extract_requisites(self.repo, i) for i in doc_ids]
        self.refresh_all()
        self.last_requisites = outcomes
        created = sum(1 for o in outcomes if o.status == "created")
        updated = sum(1 for o in outcomes if o.status == "updated")
        lines = [f"Создано протоколов: {created}, обновлено: {updated}, "
                 f"не удалось: {len(outcomes) - created - updated} (из {len(outcomes)} документов).",
                 "Записи имеют статус «Извлечён автоматически» — сверьте их с оригиналом. "
                 "Значения, изменённые вами вручную, при пересчёте сохраняются."]
        for o in outcomes:
            if not o.ok:
                lines.append(f"• {o.file_name}: {o.message}")
            else:
                if o.missing:
                    names = ", ".join(FIELD_LABELS.get(k, k) for k in o.missing)
                    lines.append(f"• {o.file_name}: не найдено — {names}")
                lines += [f"• {o.file_name}: {w}" for w in o.warnings]
        lines.append("Дата нарушения взята как дата контрольных действий; место — адреса вида "
                     "«ул. … д. N» из описания нарушения; пункты ГОСТ — из фраз «в нарушение…» "
                     "и «нарушены требования…».")
        QMessageBox.information(self, "Извлечение реквизитов", "\n".join(lines))

    last_requisites = None

    def show_document_text(self) -> None:
        doc_id = self._selected_id(self.document_table)
        info = self.repo.get_document_text_info(doc_id) if doc_id is not None else None
        if doc_id is None:
            QMessageBox.information(self, "Текст документа", "Выберите документ в таблице.")
            return
        if info is None:
            QMessageBox.information(self, "Текст документа",
                                    "Для этого документа текст ещё не извлечён. Нажмите «Обработать».")
            return
        text, source, extracted_at = info
        doc = self.repo.get_document(doc_id)
        pages = text.split("\f")
        body = "\n\n".join(f"──── страница {i} ────\n{p}" for i, p in enumerate(pages, 1)) \
            if len(pages) > 1 else text
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Текст: {doc.file_name}")
        dlg.resize(820, 600)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"Источник: {source}    Извлечён: {extracted_at}"))
        view = QPlainTextEdit(body)
        view.setReadOnly(True)
        lay.addWidget(view)
        close = QPushButton("Закрыть")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close)
        dlg.exec()

    def delete_document(self) -> None:
        doc_id = self._selected_id(self.document_table)
        if doc_id is None:
            QMessageBox.information(self, "Документы", "Выберите документ в таблице.")
            return
        ok = QMessageBox.question(
            self, "Удалить из базы",
            "Удалить запись о документе и связанные протоколы из базы?\n"
            "Файл-оригинал останется на месте.")
        if ok == QMessageBox.Yes:
            self.repo.delete_document(doc_id)
            self.refresh_all()

    def check_missing(self) -> None:
        n = self.repo.check_missing_documents()
        self.refresh_documents()
        QMessageBox.information(self, "Проверка файлов",
                                f"Не найдено файлов: {n}" if n else "Все файлы на месте.")

    def open_document_original(self) -> None:
        doc_id = self._selected_id(self.document_table)
        doc = self.repo.get_document(doc_id) if doc_id is not None else None
        if doc is None:
            QMessageBox.information(self, "Документы", "Выберите документ в таблице.")
            return
        self._open_path(doc.path)

    # ----- действия: протоколы ---------------------------------------------

    def add_protocol(self) -> None:
        docs = self.repo.list_documents()
        if not docs:
            QMessageBox.information(self, "Протокол",
                                    "Сначала добавьте документ-оригинал на вкладке «Документы».")
            return
        dlg = ProtocolDialog(docs, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_protocol:
            try:
                self.repo.add_protocol(dlg.result_protocol)
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(self, "Протокол", str(exc))
                return
            self.refresh_all()

    def edit_protocol(self) -> None:
        pid = self._selected_id(self.protocol_table)
        protocol = self.repo.get_protocol(pid) if pid is not None else None
        if protocol is None:
            QMessageBox.information(self, "Протокол", "Выберите протокол в таблице.")
            return
        dlg = ProtocolDialog(self.repo.list_documents(), protocol, parent=self,
                             evidence=self.repo.get_evidence(protocol.id))
        if dlg.exec() == QDialog.Accepted and dlg.result_protocol:
            try:
                self.repo.update_protocol(dlg.result_protocol)
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(self, "Протокол", str(exc))
                return
            self.refresh_protocols()

    def delete_protocol(self) -> None:
        pid = self._selected_id(self.protocol_table)
        if pid is None:
            QMessageBox.information(self, "Протокол", "Выберите протокол в таблице.")
            return
        if QMessageBox.question(self, "Удалить запись",
                                "Удалить запись о протоколе из базы?\n"
                                "Документ-оригинал не затрагивается.") == QMessageBox.Yes:
            self.repo.delete_protocol(pid)
            self.refresh_all()

    def open_protocol_original(self) -> None:
        rows = self.protocol_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Протокол", "Выберите протокол в таблице.")
            return
        self._open_path(self.protocol_table.item(rows[0].row(), 0).data(Qt.UserRole + 1))

    def _open_path(self, path: str) -> None:
        if not Path(path).exists():
            QMessageBox.warning(self, "Оригинал", f"Файл не найден:\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, "Оригинал", f"Не удалось открыть файл:\n{path}")

    # ----- база, копии, справка --------------------------------------------

    def choose_database(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Открыть или создать базу", str(self.db_path),
            "База SQLite (*.sqlite3 *.db);;Все файлы (*)",
            options=QFileDialog.DontConfirmOverwrite)
        if path:
            self.open_database(Path(path))

    def open_database(self, path: Path) -> None:
        if self.is_processing():
            QMessageBox.information(self, "База данных", "Дождитесь окончания обработки документов.")
            return
        try:
            new_conn = connect(path)
        except Exception as exc:
            QMessageBox.critical(self, "База данных", f"Не удалось открыть базу:\n{exc}")
            return
        self.conn.close()
        self.conn, self.db_path = new_conn, Path(path)
        self.repo = Repository(self.conn)
        self.refresh_all()

    def make_backup(self) -> Optional[Path]:
        target_dir = default_backup_dir(self.db_path)
        try:
            dest = create_backup(self.conn, target_dir)
        except (BackupError, OSError) as exc:
            QMessageBox.critical(self, "Резервная копия", f"Копия не создана:\n{exc}")
            return None
        QMessageBox.information(
            self, "Резервная копия",
            f"Копия создана и проверена:\n{dest}\n\nВсего копий в папке: {len(list_backups(target_dir))}")
        return dest

    def show_dependencies(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report = format_report(check_all())
        finally:
            QApplication.restoreOverrideCursor()
        dlg = QDialog(self)
        dlg.setWindowTitle("Проверка зависимостей")
        dlg.resize(760, 420)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit(report)
        view.setReadOnly(True)
        view.setStyleSheet("font-family: monospace;")
        lay.addWidget(view)
        close = QPushButton("Закрыть")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close)
        dlg.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self, "О программе",
            f"{APP_NAME}, версия {__version__} (MVP, этап 3)\n\n"
            "Локальный архив протоколов об административных правонарушениях.\n"
            "Работает без облачных сервисов и без ИИ-моделей. Оригиналы не копируются "
            "и не изменяются; в базе хранятся извлечённые сведения и пути к файлам.\n\n"
            + NOT_IMPLEMENTED_NOTE)

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10000)
        try:
            self.conn.close()
        finally:
            super().closeEvent(event)
