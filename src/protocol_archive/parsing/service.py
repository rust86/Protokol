"""Извлечение реквизитов из сохранённого текста документа и создание записи протокола."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..db import Protocol, Repository
from .rules import ParseResult, parse_protocol

# Поля, которые программа умеет заполнять; остальные поля протокола остаются пустыми.
FIELD_LABELS = {
    "protocol_number": "Номер протокола",
    "protocol_date": "Дата протокола",
    "violation_date": "Дата нарушения",
    "offender_type": "Тип лица",
    "article": "Статья",
    "offender_name": "ФИО / наименование",
    "authority": "Орган / составитель",
    "location": "Место нарушения",
    "gost_norms": "Нарушенные пункты ГОСТ",
}


@dataclass
class RequisitesOutcome:
    doc_id: int
    file_name: str
    status: str                  # created | updated | skipped | no_text | nothing_found
    message: str = ""
    protocol_id: Optional[int] = None
    missing: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("created", "updated")


def extract_requisites(repo: Repository, doc_id: int) -> RequisitesOutcome:
    """Создаёт (или обновляет) запись протокола по тексту документа.

    * Нет текста — ничего не делаем (сначала нужна обработка документа).
    * Нашлось ноль полей — запись не создаётся.
    * У документа уже есть протокол, внесённый вручную или проверенный, — не трогаем его.
    * Ранее извлечённая автоматически запись (статус «extracted») обновляется заново.
    Статус «Проверен» программа сама никогда не ставит.
    """
    doc = repo.get_document(doc_id)
    if doc is None:
        raise KeyError(f"Документ {doc_id} не найден")
    info = repo.get_document_text_info(doc_id)
    if info is None:
        return RequisitesOutcome(doc_id, doc.file_name, "no_text",
                                 "текст не извлечён — сначала нажмите «Обработать»")
    text, source, _ = info
    result: ParseResult = parse_protocol(text)
    warnings = list(result.warnings)
    if source in ("ocr", "pdf-text+ocr"):
        warnings.append("Текст получен OCR: цифры и буквы могли распознаться неверно — "
                        "проверьте реквизиты по оригиналу.")
    if not result.fields:
        return RequisitesOutcome(doc_id, doc.file_name, "nothing_found",
                                 "реквизиты не найдены: форма документа не похожа на известную",
                                 missing=result.missing, warnings=warnings)

    existing = repo.list_protocols_for_document(doc_id)
    auto = [p for p in existing if p.status == "extracted"]
    if existing and not auto:
        return RequisitesOutcome(
            doc_id, doc.file_name, "skipped",
            "у документа уже есть протокол, внесённый вручную или проверенный; он не изменён",
            existing[0].id, result.missing, warnings)

    values = {name: f.value for name, f in result.fields.items()}
    if auto:
        protocol = auto[0]
        for name in FIELD_LABELS:
            setattr(protocol, name, values.get(name))
        protocol = repo.update_protocol(protocol)
        status = "updated"
    else:
        protocol = repo.add_protocol(Protocol(document_id=doc_id, status="extracted", **values))
        status = "created"
    repo.set_evidence(protocol.id, [(f.name, f.value, f.evidence, f.rule)
                                    for f in result.fields.values()])
    return RequisitesOutcome(doc_id, doc.file_name, status, protocol_id=protocol.id,
                             missing=result.missing, warnings=warnings)
