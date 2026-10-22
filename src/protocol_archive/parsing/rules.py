"""Детерминированные правила извлечения реквизитов протокола из текста.

Принципы:
* только явные правила на регулярных выражениях, никаких предположений;
* значение либо найдено по правилу, либо поле остаётся пустым с указанием причины;
* для каждого найденного значения сохраняется фрагмент текста (evidence) и имя правила;
* значения не «исправляются»: если в документе дата несуществующая — поля не будет.

Дата нарушения принимается равной дате контрольных действий; место нарушения — адреса вида
«ул. Мира д. 95» из блока после слова «выразившиеся/выразившееся»; нарушенные пункты ГОСТ — из
фраз «в нарушение п. N ГОСТ …» и «нарушены требования … п. N ГОСТ … выразившиеся» (решения
пользователя).

Правила написаны по форме протокола «в отношении юридического лица» (ГИБДД,
ст. 12.34 КоАП РФ). Для других форм (в том числе «в отношении должностного лица»)
правила могут не сработать — тогда поля останутся пустыми; это не ошибка.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# Латинские буквы, неотличимые на скане от кириллических (применяется только к серии номера).
_LATIN_TO_CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")

_FLAGS = re.IGNORECASE


@dataclass
class ExtractedField:
    name: str       # имя поля Protocol: protocol_number, protocol_date, ...
    value: str      # ISO-дата для дат, иначе текст
    evidence: str   # фрагмент исходного текста
    rule: str       # идентификатор правила


@dataclass
class ParseResult:
    fields: Dict[str, ExtractedField] = field(default_factory=dict)
    missing: Dict[str, str] = field(default_factory=dict)   # поле -> причина
    warnings: List[str] = field(default_factory=list)

    def value(self, name: str) -> Optional[str]:
        f = self.fields.get(name)
        return f.value if f else None


def _squash(s: str, limit: int = 300) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)]


# ----- номер -----------------------------------------------------------------------

_NUMBER = re.compile(
    r"^[ \t]*ПРОТОКОЛ[ \t]*(?:№[ \t]*)?(\d{2})[ \t]*([A-Za-zА-Яа-яЁё]{2})[ \t]*(\d{5,7})\b",
    _FLAGS | re.MULTILINE)


def _find_number(text: str, res: ParseResult):
    m = _NUMBER.search(text)
    if not m:
        res.missing["protocol_number"] = "в тексте нет заголовка «ПРОТОКОЛ НN СС ЧЧЧЧЧЧ»"
        return None
    region, series, digits = m.groups()
    raw_series = series.upper()
    series_cyr = raw_series.translate(_LATIN_TO_CYR)
    if series_cyr != raw_series:
        res.warnings.append("В серии номера протокола латинские буквы заменены на кириллические (вероятно, OCR).")
    if not re.fullmatch(r"[А-ЯЁ]{2}", series_cyr):
        res.missing["protocol_number"] = f"серия номера {series!r} содержит недопустимые символы"
        return None
    res.fields["protocol_number"] = ExtractedField(
        "protocol_number", f"{region} {series_cyr} {digits}", _squash(m.group(0)), "number.header")
    return m


# ----- дата протокола -----------------------------------------------------------------

_DATE_WORDS = re.compile(
    r"(\d{1,2})[ \t]+(" + "|".join(MONTHS) + r")[ \t]+(\d{4})[ \t]*г(?:ода|\.)?", _FLAGS)


def _find_date(text: str, start: int, res: ParseResult):
    m = _DATE_WORDS.search(text, start, start + 600)
    if not m:
        res.missing["protocol_date"] = "после заголовка нет даты вида «06 февраля 2026 г.»"
        return None
    d, month, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    try:
        iso = date(y, month, d).isoformat()
    except ValueError:
        res.missing["protocol_date"] = f"в тексте несуществующая дата: {m.group(0)!r}"
        return m
    res.fields["protocol_date"] = ExtractedField(
        "protocol_date", iso, _squash(_line_at(text, m.start())), "date.header")
    return m


# ----- тип лица ---------------------------------------------------------------------------

_TYPE = re.compile(r"в[ \t]+отношении[ \t]+(юридического|должностного)[ \t]+лица", _FLAGS)


def _find_type(text: str, start: int, res: ParseResult) -> None:
    m = _TYPE.search(text, start, start + 400)
    if not m:
        res.missing["offender_type"] = "в заголовке нет «в отношении юридического/должностного лица»"
        return
    kind = "legal_entity" if m.group(1).lower() == "юридического" else "official"
    res.fields["offender_type"] = ExtractedField(
        "offender_type", kind, _squash(m.group(0)), "type.header")
    if kind == "official":
        res.warnings.append(
            "Тип «должностное лицо» определён по заголовку, но правила для остальных полей "
            "этой формы не проверялись на образцах — проверьте результат по оригиналу.")


# ----- статья ---------------------------------------------------------------------------------

_ARTICLE = re.compile(
    r"нарушени\w*,?[ \t\n]+предусмотренн\w+[ \t\n]+"
    r"(?:ч(?:асть|асти)?\.?[ \t]*(?P<part>\d+(?:\.\d+)?)[ \t]*)?"
    r"ст(?:атья|атьи|атьей|атьёй)?\.?[ \t]*(?P<art>\d+(?:\.\d+)?)"
    r"(?P<code>[ \t]*КоАП[ \t]*РФ)?", _FLAGS)


def _find_article(text: str, res: ParseResult):
    m = _ARTICLE.search(text)
    if not m:
        res.missing["article"] = "нет фразы «нарушение, предусмотренное ч. N ст. N.N КоАП РФ»"
        return None
    value = (f"ч. {m.group('part')} " if m.group("part") else "") + f"ст. {m.group('art')}"
    if m.group("code"):
        value += " КоАП РФ"
    res.fields["article"] = ExtractedField("article", value, _squash(m.group(0)), "article.phrase")
    return m


# ----- лицо (наименование/ФИО) ---------------------------------------------------------------------

_OFFENDER_AFTER = re.compile(r"в[ \t]+отношении[ \t]*:[ \t]*", _FLAGS)
_STOP_LINE = re.compile(r"^\s*(расположен\w*|огрн|инн|по[ \t]+адресу|адрес)\b", _FLAGS)


def _find_offender(text: str, start: int, res: ParseResult) -> None:
    m = _OFFENDER_AFTER.search(text, start, start + 200)
    if not m:
        res.missing["offender_name"] = "после статьи нет «в отношении:»"
        return
    lines = text[m.end():].split("\n")
    taken: List[str] = []
    for ln in lines[:6]:
        if _STOP_LINE.match(ln) or (taken and not ln.strip()):
            break
        if ln.strip():
            taken.append(ln.strip())
    if not taken:
        res.missing["offender_name"] = "после «в отношении:» нет наименования или ФИО"
        return
    value = _squash(" ".join(taken), limit=500)
    res.fields["offender_name"] = ExtractedField("offender_name", value, value, "offender.after_colon")


# ----- орган / составитель -------------------------------------------------------------------------

_AUTHORITY_END = re.compile(r"^\s*(в[ \t]+присутствии|составил)\b", _FLAGS)


def _find_authority(text: str, date_match, res: ParseResult) -> None:
    if date_match is None:
        res.missing["authority"] = "не найдена дата, от которой отсчитывается блок составителя"
        return
    nl = text.find("\n", date_match.end())
    rest = text[nl + 1:].split("\n") if nl != -1 else []
    taken: List[str] = []
    for ln in rest[:12]:
        if _AUTHORITY_END.match(ln):
            if taken:
                value = _squash(" ".join(taken), limit=500)
                res.fields["authority"] = ExtractedField("authority", value, value, "authority.block")
                return
            break
        if ln.strip():
            taken.append(ln.strip())
    res.missing["authority"] = "после даты не найден блок, оканчивающийся на «в присутствии» / «составил»"


# ----- дата нарушения = дата контрольных действий ------------------------------------------------

_ACTION_DATE = re.compile(
    r"провед[её]нн\w+[ \t\n]+с[ \t\n]+\d{1,2}[ \t]*ч\.?[ \t]*\d{1,2}[ \t]*мин\.?[ \t\n]+"
    r"по[ \t\n]+\d{1,2}[ \t]*ч\.?[ \t]*\d{1,2}[ \t]*мин\.?[ \t\n]+"
    r"(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<y>\d{4})", _FLAGS)


def _find_violation_date(text: str, start: int, res: ParseResult) -> None:
    """Дата нарушения принимается равной дате контрольных действий:
    «…проведённых с 11 ч. 34 мин. по 16 ч. 10 мин. 05.02.2026» → 2026-02-05."""
    m = _ACTION_DATE.search(text, start)
    if not m:
        res.missing["violation_date"] = ("нет фразы «проведённых с … ч. … мин. по … ч. … мин. "
                                         "ДД.ММ.ГГГГ»")
        return
    try:
        iso = date(int(m.group("y")), int(m.group("m")), int(m.group("d"))).isoformat()
    except ValueError:
        res.missing["violation_date"] = f"в тексте несуществующая дата: {m.group(0)!r}"
        return
    res.fields["violation_date"] = ExtractedField(
        "violation_date", iso, _squash(m.group(0)), "violation_date.action_date")


# ----- место нарушения = адреса «ул. Название д. N» из блока после «выразившиеся» ------------------------

_EXPRESSED = re.compile(r"выразивш\w+[ \t]*:?[ \t]*", _FLAGS)
_LOCATION_STOP = re.compile(r"^\s*(по[ \t]+итогам|в[ \t]+соответствии)\b", _FLAGS)
_LOCATION_MAX_LINES = 10

# (шаблон типа улицы, как записывается в поле)
_STREET_TYPES = [
    (r"ул(?:\.|ица|ице|ицы|ицу)", "ул."),
    (r"(?:пр-кт|просп\.|проспект\w*|пр\.)", "пр-кт"),
    (r"(?:пер\.|переул\w+)", "пер."),
    (r"проезд\w*", "проезд"),
    (r"(?:б-р|бульвар\w*)", "б-р"),
    (r"(?:ш\.|шоссе)", "ш."),
    (r"(?:наб\.|набережн\w+)", "наб."),
]
_ADDRESS = re.compile(
    r"(?<!\w)(?P<type>" + "|".join(p for p, _ in _STREET_TYPES) + r")[ \t]*"
    r"(?P<name>[А-ЯЁ0-9][^\n,;:()—–]{0,50}?)[ \t]+(?:д\.|дом)[ \t]*"
    r"(?P<num>\d+(?:[ \t]*/[ \t]*\d+)?[А-Яа-я]?)(?!\w)", _FLAGS)
_INTERSECTION = re.compile(r"пересечени\w*[ \t]+улиц[^;\n]*", _FLAGS)


def _canonical_street_type(raw: str) -> str:
    for pattern, canon in _STREET_TYPES:
        if re.fullmatch(pattern, raw, _FLAGS):
            return canon
    return raw


def _addresses(block: str) -> List[str]:
    """Адреса вида «ул. Мира д. 95» в порядке появления, без повторов.

    Название улицы остаётся в той форме, как записано в документе (падеж не меняется)."""
    out: List[str] = []
    for m in _ADDRESS.finditer(block):
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        num = re.sub(r"\s+", "", m.group("num"))
        item = f"{_canonical_street_type(m.group('type'))} {name} д. {num}"
        if item not in out:
            out.append(item)
    return out


def _find_location(text: str, start: int, res: ParseResult) -> None:
    """Блок после «выразившиеся/выразившееся» до абзаца «По итогам…» (или «В соответствии…»,
    пустой строки); из него берутся адреса «ул. Название д. N». Места без номера дома
    (пересечения улиц) в поле не вносятся, о них выдаётся предупреждение."""
    m = _EXPRESSED.search(text, start)
    if not m:
        res.missing["location"] = "нет слова «выразившиеся/выразившееся»"
        return
    lines = text[m.end():].split("\n")
    taken: List[str] = []
    closed = False
    for ln in lines[:_LOCATION_MAX_LINES + 1]:
        if _LOCATION_STOP.match(ln) or (taken and not ln.strip()):
            closed = True
            break
        if ln.strip():
            taken.append(ln.strip())
    if not taken:
        res.missing["location"] = "после «выразившиеся» нет текста"
        return
    if not closed:
        res.missing["location"] = ("не найден конец блока (абзац «По итогам…»): "
                                   "граница места нарушения неясна")
        return
    block = " ".join(taken)
    skipped = [re.sub(r"\s+", " ", i.group(0)).strip(" .;") for i in _INTERSECTION.finditer(block)]
    if skipped:
        res.warnings.append("В описании нарушения есть места без номера дома (пересечения улиц); "
                            "в «Место нарушения» они не внесены: " + "; ".join(skipped)[:300])
    addresses = _addresses(block)
    if not addresses:
        res.missing["location"] = "в блоке нет адресов вида «ул. Название д. N»"
        return
    res.fields["location"] = ExtractedField(
        "location", "; ".join(addresses), _squash(block, limit=800), "location.addresses_after_expressed")


# ----- нарушенные пункты ГОСТ ----------------------------------------------------------------------------

_GOST = r"ГОСТ(?:[ \t]+Р)?[ \t]+\d{4,5}(?:\.\d+)?-\d{2,4}"
_POINT = r"\d+(?:\.\d+)*"
_REF = re.compile(
    r"(?:пп?\.|пункт\w*|подпункт\w*)[ \t]*(?P<pts>" + _POINT +
    r"(?:[ \t]*(?:,|;|и)[ \t]*" + _POINT + r")*)[ \t]*(?P<gost>" + _GOST + ")", _FLAGS)
_IN_VIOLATION = re.compile(r"в[ \t]+нарушени\w*", _FLAGS)
_REQUIREMENTS = re.compile(r"нарушен\w*[ \t\n]+требовани\w*", _FLAGS)


def _refs_in(window: str):
    """(пункт, ГОСТ, фрагмент) для каждой ссылки вида «п. 8.1, 8.8 ГОСТ Р 50597-2017»."""
    for r in _REF.finditer(window):
        gost = re.sub(r"\s+", " ", r.group("gost")).upper()
        for point in re.findall(_POINT, r.group("pts")):
            yield point, gost, re.sub(r"\s+", " ", r.group(0))


def _find_gost(text: str, start: int, res: ParseResult) -> None:
    """Пункты ГОСТ, названные нарушенными. Ссылки в пояснительной части («в соответствии с
    п. 3.18 ГОСТ…», «согласно п. 8.8 ГОСТ…») не учитываются.

    * «в нарушение п. 5.2.4 ГОСТ Р 50597-2017» — до конца строки или «;»;
    * «нарушены требования …, п. 8.1, 8.8 ГОСТ Р 50597-2017, выразившиеся» — до слова «выразившиеся».
    """
    found: List[tuple] = []   # (пункт, ГОСТ, подсказка-контекст)

    def add(point, gost, lead, frag):
        if (point, gost) not in [(p, g) for p, g, _ in found]:
            found.append((point, gost, f"{lead} … {frag}"))

    for m in _IN_VIOLATION.finditer(text, start):
        window = re.split(r"[;\n]", text[m.end(): m.end() + 400], maxsplit=1)[0]
        for point, gost, frag in _refs_in(window):
            add(point, gost, _squash(m.group(0)), frag)
    for m in _REQUIREMENTS.finditer(text, start):
        stop = _EXPRESSED.search(text, m.end(), m.end() + 2000)
        end = stop.start() if stop else (text.find("\n", m.end()) if text.find("\n", m.end()) != -1 else len(text))
        for point, gost, frag in _refs_in(text[m.end(): end]):
            add(point, gost, _squash(m.group(0)), frag)

    if not found:
        res.missing["gost_norms"] = ("нет ссылок вида «в нарушение п. N ГОСТ …» или «нарушены "
                                     "требования … п. N ГОСТ … выразившиеся»")
        return
    value = "; ".join(f"п. {p} {g}" for p, g, _ in found)
    evidence = " | ".join(ctx for _, _, ctx in found)
    res.fields["gost_norms"] = ExtractedField(
        "gost_norms", value, _squash(evidence, limit=600), "gost.violated_points")


# ----- вход ---------------------------------------------------------------------------------------------

def parse_protocol(text: str) -> ParseResult:
    res = ParseResult()
    num = _find_number(text, res)
    if num is None:  # нет заголовка — не похоже на протокол этой формы; остальное не угадываем
        for name in ("protocol_date", "violation_date", "offender_type", "article",
                     "offender_name", "authority", "location", "gost_norms"):
            res.missing.setdefault(name, "документ не распознан как протокол: нет заголовка с номером")
        return res
    date_m = _find_date(text, num.end(), res)
    _find_type(text, num.end(), res)
    art = _find_article(text, res)
    _find_offender(text, art.end() if art else num.end(), res)
    _find_authority(text, date_m, res)
    _find_violation_date(text, num.end(), res)
    _find_location(text, num.end(), res)
    _find_gost(text, num.end(), res)
    return res
