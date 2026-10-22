"""ODT: абзацы, заголовки, списки и таблицы через odfpy."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .result import ExtractionError, ExtractionResult, normalize_text


def _qname(node) -> str:
    return getattr(node, "qname", (None, ""))[1]


def _walk(node, out: List[str]) -> None:
    from odf import teletype

    for child in node.childNodes:
        name = _qname(child)
        if name in ("p", "h"):
            out.append(teletype.extractText(child))
        elif name == "table":
            _table(child, out)
        elif name in ("note", "annotation"):  # сноски и примечания не извлекаем
            continue
        elif getattr(child, "childNodes", None):
            _walk(child, out)


def _rows(table_node):
    for child in table_node.childNodes:
        name = _qname(child)
        if name == "table-row":
            yield child
        elif name in ("table-header-rows", "table-row-group", "table-rows"):
            yield from _rows(child)


def _table(table_node, out: List[str]) -> None:
    for row in _rows(table_node):
        cells = []
        for cell in row.childNodes:
            if _qname(cell) != "table-cell":
                continue  # covered-table-cell — часть объединённой ячейки
            parts: List[str] = []
            _walk(cell, parts)
            cells.append(" ".join(p.strip() for p in parts if p.strip()))
        line = "\t".join(cells).rstrip("\t")
        if line.strip():
            out.append(line)


def extract_odt(path: Path) -> ExtractionResult:
    try:
        from odf.opendocument import load
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("Не установлен odfpy") from exc
    try:
        doc = load(str(path))
        lines: List[str] = []
        _walk(doc.text, lines)
    except Exception as exc:
        raise ExtractionError(f"ODT не читается: {exc}") from exc
    return ExtractionResult(
        normalize_text("\n".join(lines)), "odt",
        warnings=["Колонтитулы, текстовые рамки и сноски пока не извлекаются."])
