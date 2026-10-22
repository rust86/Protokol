"""Извлечение текста из PDF, DOCX, ODT и DOC. Только чтение оригиналов."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional, Union

from .ocr import OcrOptions, ocr_status
from .result import (
    ExtractionCancelled, ExtractionError, ExtractionResult, normalize_text,
)

SUPPORTED_EXTENSIONS = ("pdf", "docx", "odt", "doc")

__all__ = [
    "ExtractionCancelled", "ExtractionError", "ExtractionResult", "OcrOptions",
    "SUPPORTED_EXTENSIONS", "extract_text", "normalize_text", "ocr_status",
]


def extract_text(path: Union[str, Path], options: OcrOptions = OcrOptions(),
                 progress: Optional[Callable[[int, int], None]] = None,
                 cancel: Optional[threading.Event] = None) -> ExtractionResult:
    """Извлекает текст по расширению файла. Оригинал не изменяется."""
    p = Path(path)
    if not p.is_file():
        raise ExtractionError(f"Файл не найден: {p}")
    ext = p.suffix.lower().lstrip(".")
    if ext == "pdf":
        from .pdf import extract_pdf
        return extract_pdf(p, options, progress, cancel)
    if ext == "docx":
        from .docx import extract_docx
        return extract_docx(p)
    if ext == "odt":
        from .odt import extract_odt
        return extract_odt(p)
    if ext == "doc":
        from .doc import extract_doc
        return extract_doc(p)
    raise ExtractionError(
        f"Формат .{ext or '?'} не поддерживается (поддерживаются: {', '.join(SUPPORTED_EXTENSIONS)})")
