from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List


class ExtractionError(RuntimeError):
    """Документ не удалось прочитать; сообщение показывается пользователю."""


class ExtractionCancelled(RuntimeError):
    """Обработка прервана пользователем."""


@dataclass
class ExtractionResult:
    text: str
    source: str                      # 'pdf-text', 'ocr', 'pdf-text+ocr', 'docx', 'odt', 'doc'
    pages: int = 0                   # для PDF; для остальных форматов 0
    ocr_pages: int = 0
    warnings: List[str] = field(default_factory=list)


_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"), None)
_BLANKS = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Детерминированная очистка текста; структура строк сохраняется.

    * Unicode NFC; неразрывные и прочие «широкие» пробелы → обычный пробел;
    * удаляются мягкие переносы и невидимые символы нулевой ширины;
    * CRLF/CR → LF; пробелы в конце строк обрезаются; серии пустых строк сжимаются;
    * символ разрыва страницы \\f сохраняется как разделитель страниц.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]+\f", "\f", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()
