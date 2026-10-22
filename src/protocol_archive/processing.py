"""Обработка документов: извлечение текста и сохранение его в базу.

Оригиналы только читаются. Функции не зависят от Qt и годятся для запуска из потока.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

from .db import Repository, connect
from .extract import (
    ExtractionCancelled, ExtractionError, OcrOptions, extract_text,
)


@dataclass
class ProcessOutcome:
    doc_id: int
    file_name: str
    ok: bool
    message: str = ""           # причина ошибки или пояснение
    source: str = ""            # pdf-text / ocr / docx / ...
    chars: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class ProcessSummary:
    outcomes: List[ProcessOutcome] = field(default_factory=list)
    cancelled: bool = False

    @property
    def processed(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)


def process_document(repo: Repository, doc_id: int, options: OcrOptions = OcrOptions(),
                     cancel: Optional[threading.Event] = None,
                     page_progress: Optional[Callable[[int, int], None]] = None) -> ProcessOutcome:
    """Извлекает текст одного документа и обновляет статус.

    Перед чтением метаданные файла обновляются: если содержимое изменилось с момента
    добавления, это учитывается (хэш, статус). Прерывание (ExtractionCancelled)
    пробрасывается наружу, статус при этом не меняется.
    """
    before = repo.get_document(doc_id)
    if before is None:
        raise KeyError(f"Документ {doc_id} не найден")
    try:
        doc = repo.add_document(before.path)
    except FileNotFoundError:
        repo.set_document_status(doc_id, "missing")
        return ProcessOutcome(doc_id, before.file_name, False, "Файл не найден")
    content_changed = doc.sha256 != before.sha256

    def fail(message: str) -> ProcessOutcome:
        repo.set_document_status(doc_id, "error", message)
        if content_changed:  # прежний текст относился к другой версии файла
            repo.delete_document_text(doc_id)
        return ProcessOutcome(doc_id, doc.file_name, False, message)

    try:
        result = extract_text(doc.path, options, page_progress, cancel)
    except ExtractionCancelled:
        raise
    except ExtractionError as exc:
        return fail(str(exc))
    except Exception as exc:  # непредвиденная ошибка не должна останавливать пакет
        return fail(f"Непредвиденная ошибка: {type(exc).__name__}: {exc}")

    if not result.text.strip():
        return fail("Текст не извлечён: документ пустой или нечитаемый")

    repo.set_document_text(doc_id, result.text, result.source)
    repo.set_document_status(doc_id, "processed")
    return ProcessOutcome(doc_id, doc.file_name, True, source=result.source,
                          chars=len(result.text), warnings=result.warnings)


def process_documents(db_path: Union[str, Path], doc_ids: Sequence[int],
                      options: OcrOptions = OcrOptions(),
                      on_progress: Optional[Callable[[int, int, str], None]] = None,
                      cancel: Optional[threading.Event] = None) -> ProcessSummary:
    """Обрабатывает документы по очереди. Открывает собственное соединение с базой,
    поэтому безопасно вызывается из рабочего потока."""
    conn = connect(db_path)
    summary = ProcessSummary()
    try:
        repo = Repository(conn)
        total = len(doc_ids)
        for n, doc_id in enumerate(doc_ids):
            if cancel is not None and cancel.is_set():
                summary.cancelled = True
                break
            doc = repo.get_document(doc_id)
            if on_progress:
                on_progress(n, total, doc.file_name if doc else str(doc_id))
            if doc is None:
                continue
            try:
                summary.outcomes.append(process_document(repo, doc_id, options, cancel))
            except ExtractionCancelled:
                summary.cancelled = True
                break
        if on_progress and not summary.cancelled:
            on_progress(total, total, "")
    finally:
        conn.close()
    return summary
