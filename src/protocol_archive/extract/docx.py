"""DOCX: абзацы и таблицы в порядке следования в документе."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .result import ExtractionError, ExtractionResult, normalize_text


def _table_lines(table) -> List[str]:
    lines: List[str] = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            if id(cell._tc) in seen:  # объединённые ячейки python-docx повторяет
                continue
            seen.add(id(cell._tc))
            cells.append(" ".join(l.strip() for l in _block_lines(cell._tc, cell) if l.strip()))
        line = "\t".join(cells).rstrip("\t")
        if line.strip():
            lines.append(line)
    return lines


def _block_lines(element, parent) -> List[str]:
    """Строки блока (тело документа или ячейка) в порядке следования абзацев и таблиц."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: List[str] = []
    for child in element.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, parent).text)
        elif tag == "tbl":
            lines.extend(_table_lines(Table(child, parent)))
    return lines


def extract_docx(path: Path, source: str = "docx") -> ExtractionResult:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("Не установлен python-docx") from exc
    try:
        document = docx.Document(str(path))
        lines = _block_lines(document.element.body, document)
    except Exception as exc:
        raise ExtractionError(f"DOCX не читается: {exc}") from exc
    return ExtractionResult(
        normalize_text("\n".join(lines)), source,
        warnings=["Колонтитулы, текстовые надписи и сноски пока не извлекаются."])
