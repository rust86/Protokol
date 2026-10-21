from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox,
    QPlainTextEdit, QVBoxLayout,
)

from ..dates import to_display, to_iso
from ..db import Document, Protocol

OFFENDER_TYPE_LABELS = {
    "official": "Должностное лицо",
    "legal_entity": "Юридическое лицо",
}

STATUS_LABELS = {
    "manual": "Внесён вручную",
    "extracted": "Извлечён автоматически",
    "verified": "Проверен",
}


class ProtocolDialog(QDialog):
    """Ручной ввод и правка реквизитов протокола."""

    def __init__(self, documents: List[Document], protocol: Optional[Protocol] = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Протокол" if protocol else "Новый протокол")
        self.setMinimumWidth(520)
        self._protocol = protocol

        self.doc_box = QComboBox()
        for d in documents:
            self.doc_box.addItem(d.file_name, d.id)
        if protocol:
            idx = self.doc_box.findData(protocol.document_id)
            if idx >= 0:
                self.doc_box.setCurrentIndex(idx)
            self.doc_box.setEnabled(False)

        self.number = QLineEdit()
        self.p_date = QLineEdit()
        self.p_date.setPlaceholderText("ДД.ММ.ГГГГ")
        self.v_date = QLineEdit()
        self.v_date.setPlaceholderText("ДД.ММ.ГГГГ")
        self.article = QLineEdit()
        self.offender = QLineEdit()
        self.offender_type = QComboBox()
        self.offender_type.addItem("— не указан —", None)
        for key, label in OFFENDER_TYPE_LABELS.items():
            self.offender_type.addItem(label, key)
        self.location = QLineEdit()
        self.authority = QLineEdit()
        self.status = QComboBox()
        for key, label in STATUS_LABELS.items():
            self.status.addItem(label, key)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(70)

        form = QFormLayout()
        form.addRow("Документ-оригинал:", self.doc_box)
        form.addRow("Номер протокола:", self.number)
        form.addRow("Дата протокола:", self.p_date)
        form.addRow("Дата нарушения:", self.v_date)
        form.addRow("Статья:", self.article)
        form.addRow("Тип лица:", self.offender_type)
        form.addRow("ФИО должностного лица / наименование юрлица:", self.offender)
        form.addRow("Место нарушения:", self.location)
        form.addRow("Орган, составивший протокол:", self.authority)
        form.addRow("Статус записи:", self.status)
        form.addRow("Примечание:", self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if protocol:
            self.number.setText(protocol.protocol_number or "")
            self.p_date.setText(to_display(protocol.protocol_date))
            self.v_date.setText(to_display(protocol.violation_date))
            self.article.setText(protocol.article or "")
            self.offender.setText(protocol.offender_name or "")
            self.offender_type.setCurrentIndex(max(0, self.offender_type.findData(protocol.offender_type)))
            self.location.setText(protocol.location or "")
            self.authority.setText(protocol.authority or "")
            self.notes.setPlainText(protocol.notes or "")
            self.status.setCurrentIndex(max(0, self.status.findData(protocol.status)))

        self.result_protocol: Optional[Protocol] = None

    def _accept(self) -> None:
        if self.doc_box.currentData() is None:
            QMessageBox.warning(self, "Протокол", "Сначала добавьте документ-оригинал.")
            return
        try:
            p = Protocol(
                id=self._protocol.id if self._protocol else None,
                document_id=int(self.doc_box.currentData()),
                protocol_number=self.number.text(),
                protocol_date=to_iso(self.p_date.text()),
                violation_date=to_iso(self.v_date.text()),
                article=self.article.text(),
                offender_name=self.offender.text(),
                offender_type=self.offender_type.currentData(),
                location=self.location.text(),
                authority=self.authority.text(),
                notes=self.notes.toPlainText(),
                status=self.status.currentData(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Протокол", str(exc))
            return
        self.result_protocol = p
        self.accept()
