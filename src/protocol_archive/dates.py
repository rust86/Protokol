"""Преобразование дат между видом для пользователя (ДД.ММ.ГГГГ) и хранимым ISO (ГГГГ-ММ-ДД)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def to_iso(text: str) -> Optional[str]:
    """'05.03.2025' или '2025-03-05' → '2025-03-05'; пустая строка → None. Иначе ValueError."""
    s = (text or "").strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Не удалось разобрать дату {s!r}. Ожидается ДД.ММ.ГГГГ.")


def to_display(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso
