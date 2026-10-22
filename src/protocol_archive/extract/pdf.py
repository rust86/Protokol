"""PDF: текстовый слой через PyMuPDF; страницы без текста — через OCR."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from .ocr import OcrOptions, ocr_png, ocr_status
from .result import ExtractionCancelled, ExtractionError, ExtractionResult, normalize_text

try:  # у PyMuPDF с версии 1.24.3 основное имя модуля — pymupdf; fitz — прежнее
    import pymupdf as _mu
except ImportError:  # pragma: no cover - зависит от установленной версии
    import fitz as _mu  # type: ignore

PAGE_SEPARATOR = "\f"


def page_needs_ocr(text: str, has_images: bool, options: OcrOptions) -> bool:
    """Нужна ли странице распознавание.

    * текста почти нет, но на странице есть изображение — это скан;
    * заметная доля символов подстановки U+FFFD — текстовый слой испорчен;
    * пустая страница без изображений — читать нечего, OCR не нужен.
    """
    visible = [c for c in text if not c.isspace()]
    if len(visible) < options.min_text_chars:
        return has_images
    return visible.count("\ufffd") / len(visible) > 0.2


def extract_pdf(path: Path, options: OcrOptions = OcrOptions(),
                progress: Optional[Callable[[int, int], None]] = None,
                cancel: Optional[threading.Event] = None) -> ExtractionResult:
    try:
        doc = _mu.open(str(path))
    except Exception as exc:
        raise ExtractionError(f"PDF не открывается: {exc}") from exc
    try:
        if doc.needs_pass:
            raise ExtractionError("PDF защищён паролем")
        total = doc.page_count
        parts, ocr_pages, warnings = [], 0, []
        ocr_checked = None  # (доступен, пояснение) — проверяется один раз при первой нужде
        for i in range(total):
            if cancel is not None and cancel.is_set():
                raise ExtractionCancelled()
            page = doc.load_page(i)
            text = page.get_text("text", sort=True)
            has_images = bool(page.get_images(full=True))
            if page_needs_ocr(text, has_images, options):
                if ocr_checked is None:
                    ocr_checked = ocr_status(options)
                if not ocr_checked[0]:
                    raise ExtractionError(
                        f"Страница {i + 1} — скан, нужен OCR, но он недоступен: {ocr_checked[1]}")
                png = page.get_pixmap(dpi=options.dpi).tobytes("png")
                text = ocr_png(png, options)
                ocr_pages += 1
            parts.append(text)
            if progress:
                progress(i + 1, total)
    except ExtractionError:
        raise
    except ExtractionCancelled:
        raise
    except Exception as exc:  # повреждённый файл и т. п.
        raise ExtractionError(f"Ошибка чтения PDF: {exc}") from exc
    finally:
        doc.close()

    if ocr_pages == 0:
        source = "pdf-text"
    elif ocr_pages == total:
        source = "ocr"
    else:
        source = "pdf-text+ocr"
    if ocr_pages:
        warnings.append("Текст распознан OCR и может содержать ошибки; проверяйте реквизиты по оригиналу.")
    text = normalize_text(PAGE_SEPARATOR.join(normalize_text(p) for p in parts))
    return ExtractionResult(text, source, pages=total, ocr_pages=ocr_pages, warnings=warnings)
