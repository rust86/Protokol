from __future__ import annotations

import threading
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from ..extract import OcrOptions
from ..processing import ProcessSummary, process_documents


class ProcessWorker(QObject):
    """Обработка документов в отдельном потоке (со своим соединением с базой)."""

    progress = Signal(int, int, str)   # сделано, всего, имя текущего файла
    finished = Signal(object)          # ProcessSummary или Exception

    def __init__(self, db_path: Path, doc_ids: Sequence[int], options: OcrOptions):
        super().__init__()
        self.db_path = db_path
        self.doc_ids = list(doc_ids)
        self.options = options
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result: object = process_documents(
                self.db_path, self.doc_ids, self.options,
                on_progress=lambda d, t, n: self.progress.emit(d, t, n),
                cancel=self.cancel_event)
        except Exception as exc:  # показать пользователю, а не молча потерять
            result = exc
        self.finished.emit(result)
