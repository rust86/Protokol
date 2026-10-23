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
    r"^[ \t]*ПРОТОКОЛ[ \t]*(?:№[ \t]*)?(\d{2})[ \t]*"
    r"([A-Za-zА-Яа-яЁё])[ \t]*([A-Za-zА-Яа-яЁё])[ \t]*(\d{5,7})\b",
    _FLAGS | re.MULTILINE)


def _find_number(text: str, res: ParseResult):
    m = _NUMBER.search(text)
    if not m:
        res.missing["protocol_number"] = "в тексте нет заголовка «ПРОТОКОЛ НN СС ЧЧЧЧЧЧ»"
        return None
    region, s1, s2, digits = m.groups()
    series = s1 + s2
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
    r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\s*г(?:ода|\.)?", _FLAGS)


_DATE_DIGITS = re.compile(r"(?<![\d.])(\d{2})\.(\d{2})\.(\d{4})(?![\d.])")


def _find_date(text: str, start: int, res: ParseResult):
    """Дата протокола: «06 февраля 2026 г.» (приоритет); если такой нет — первая дата ДД.ММ.ГГГГ
    в пределах 300 символов под заголовком."""
    m = _DATE_WORDS.search(text, start, start + 600)
    rule = "date.header"
    if m:
        d, month, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    else:
        m = _DATE_DIGITS.search(text, start, start + 300)
        if not m:
            res.missing["protocol_date"] = ("после заголовка нет даты вида «06 февраля 2026 г.» "
                                            "или «06.02.2026»")
            return None
        d, month, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        rule = "date.header_numeric"
    try:
        iso = date(y, month, d).isoformat()
    except ValueError:
        res.missing["protocol_date"] = f"в тексте несуществующая дата: {m.group(0)!r}"
        return m
    res.fields["protocol_date"] = ExtractedField(
        "protocol_date", iso, _squash(_line_at(text, m.start())), rule)
    return m


# ----- тип лица ---------------------------------------------------------------------------

_TYPE = re.compile(r"в\s+отношении\s+(юридического|должностного)\s+лица", _FLAGS)


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
    r"нарушени\w*,?\s+предусмотренн\w+\s+"
    r"(?:ч(?:асть|асти)?\.?\s*(?P<part>\d+(?:\.\d+)?)\s*)?"
    r"ст(?:атья|атьи|атьей|атьёй)?\.?\s*(?P<art>\d+(?:\.\d+)?)"
    r"(?P<code>\s*КоАП\s*РФ)?", _FLAGS)


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

_OFFENDER_AFTER = re.compile(r"в\s+отношении\s*:\s*", _FLAGS)
_STOP_LINE = re.compile(r"^\s*(расположен\w*|огрн|инн|по\s+адресу|адрес)\b", _FLAGS)


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

_AUTHORITY_END = re.compile(r"^\s*(в\s+присутствии|составил)\b", _FLAGS)


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

_TIME = r"(?:\d{1,2}:\d{2}|\d{1,2}\s*ч\.?(?:\s*\d{1,2}\s*мин\.?)?)"
_DATE_NUM = r"(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<y>\d{4})"
_INTERVAL = r"с\s+" + _TIME + r"\s+(?:по|до)\s+" + _TIME
# «…по 10 ч. 45 мин. 04.05.2026» — дата сразу после интервала
_ACTION_AFTER = re.compile(r"(?<!\w)" + _INTERVAL + r"\s*,?\s*" + _DATE_NUM, _FLAGS)
# «…проведённого 06.07.2026 с 14:10 по 14:14» — дата сразу перед интервалом
_ACTION_BEFORE = re.compile(r"(?<![\d.])" + _DATE_NUM + r"\s*,?\s*" + _INTERVAL, _FLAGS)
# слова, говорящие, что речь о контрольных действиях (а не, например, о дате решения)
_ACTION_CONTEXT = re.compile(r"провед|период|осмотр|обследован|рейд|мероприят|контрольн", _FLAGS)


def _find_violation_date(text: str, start: int, res: ParseResult) -> None:
    """Дата нарушения принимается равной дате контрольных действий. Ищется интервал времени
    «с … по …» с датой рядом, в контексте слов «проведённого/проведённых», «в период времени»,
    «осмотра» и т. п.; время может быть записано «11 ч. 34 мин.» или «14:10»:

    * «проведённых с 11 ч. 34 мин. по 16 ч. 10 мин. 05.02.2026»  (дата после интервала)
    * «В период времени с 10 ч. 44 мин. по 10 ч. 45 мин. 04.05.2026»
    * «проведённого 06.07.2026 с 14:10 по 14:14»                    (дата перед интервалом)
    """
    found = []
    for pattern in (_ACTION_AFTER, _ACTION_BEFORE):
        for m in pattern.finditer(text, start):
            if _ACTION_CONTEXT.search(text, max(start, m.start() - 150), m.start()):
                found.append(m)
    found.sort(key=lambda m: m.start())
    bad = None
    for m in found:
        try:
            iso = date(int(m.group("y")), int(m.group("m")), int(m.group("d"))).isoformat()
        except ValueError:
            bad = bad or m
            continue
        res.fields["violation_date"] = ExtractedField(
            "violation_date", iso, _squash(m.group(0)), "violation_date.action_date")
        return
    if bad is not None:
        res.missing["violation_date"] = f"в тексте несуществующая дата: {bad.group(0)!r}"
    else:
        res.missing["violation_date"] = ("нет интервала времени «с … по …» с датой ДД.ММ.ГГГГ рядом "
                                         "(после слов «проведённого», «в период времени», «осмотра»)")


# ----- место нарушения = адреса «ул. Название д. N» из блока после «выразившиеся» ------------------------

_EXPRESSED = re.compile(r"выразивш\w+\s*:?\s*", _FLAGS)
_LOCATION_STOP = re.compile(r"^\s*(по\s+итогам|в\s+соответствии)\b", _FLAGS)
_LOCATION_MAX_LINES = 30

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
# номер дома: 95, 8А, 17 А, 60/5 (буква после пробела — только заглавная: «д. 95 в нарушение» — не «95в»)
_HOUSE = r"\d+(?:\s*/\s*\d+)?(?:[А-Яа-я]|(?-i:\s[А-Я])(?![а-яё]))?"
_TYPES_ALT = "|".join(p for p, _ in _STREET_TYPES)
_NAME_TOKEN = r"(?-i:[А-ЯЁ0-9])[^\s,.;:()—–]*"
_NAME_WORDS = r"(?P<name>" + _NAME_TOKEN + r"(?:\s+[^\s,.;:()—–]+){0,3}?)"
_NAME_END = r"(?=\s*[,.;:()]|\s+(?:и|а|в|на|по|с|у|за|при|для|около|г)\b|\s*$)"
# «ул. Мира д. 95»
_ADDRESS = re.compile(
    r"(?<!\w)(?P<type>" + _TYPES_ALT + r")\s*"
    r"(?P<name>(?-i:[А-ЯЁ0-9])[^\n,;:()—–]{0,50}?)\s+(?:д\.|дом)\s*(?P<num>" + _HOUSE + r")(?!\w)", _FLAGS)
# «в районе дома 45 по улице Лопарева» → «ул. Лопарева д. 45»
_ADDRESS_HOUSE_FIRST = re.compile(
    r"(?<!\w)(?:дом(?:а|у|е)?|д\.)\s*(?P<num>" + _HOUSE + r")(?!\w)\s*,?\s*"
    r"по\s+(?P<type>" + _TYPES_ALT + r")\s*" + _NAME_WORDS + _NAME_END, _FLAGS)
# «в районе д. 60/5 ул. Мира» → «ул. Мира д. 60/5»
_ADDRESS_HOUSE_STREET = re.compile(
    r"(?<!\w)(?:дом(?:а|у|е)?|д\.)\s*(?P<num>" + _HOUSE + r")(?!\w)\s*,?\s*"
    r"(?P<type>" + _TYPES_ALT + r")\s*" + _NAME_WORDS + _NAME_END, _FLAGS)
# улица без номера дома: «по ул. Дзержинского,»
_STREET_ONLY = re.compile(
    r"(?<!\w)(?P<type>" + _TYPES_ALT + r")\s*" + _NAME_WORDS + _NAME_END, _FLAGS)
_INTERSECTION = re.compile(r"пересечени\w*\s+улиц[^;\n]*", _FLAGS)


def _canonical_street_type(raw: str) -> str:
    for pattern, canon in _STREET_TYPES:
        if re.fullmatch(pattern, raw, _FLAGS):
            return canon
    return raw


def _scan_addresses(block: str):
    """(адреса, улицы без номера дома). Адрес — «ул. Мира д. 95»; понимаются три порядка записи:
    «ул. Мира д. 95», «дом 45 по улице Лопарева», «д. 60/5 ул. Мира». Название улицы остаётся в
    той форме, как записано в документе (падеж не меняется)."""
    def fmt(m, with_house=True):
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        name = re.sub(r"\s+(?:в\s+районе|около|вблизи|напротив)$", "", name, flags=re.IGNORECASE)
        base = f"{_canonical_street_type(m.group('type'))} {name}"
        return f"{base} д. {re.sub(chr(92) + 's+', '', m.group('num'))}" if with_house else base

    found, spans = [], []
    for m in _ADDRESS.finditer(block):
        found.append((m.start(), fmt(m)))
        spans.append(m.span())

    def free(m, group):          # эта часть текста ещё не вошла в найденный адрес
        return not any(a <= m.start(group) < b for a, b in spans)

    for pattern in (_ADDRESS_HOUSE_FIRST, _ADDRESS_HOUSE_STREET):
        for m in pattern.finditer(block):
            if free(m, "type") and free(m, "num"):
                found.append((m.start(), fmt(m)))
                spans.append(m.span())
    addresses: List[str] = []
    for _, item in sorted(found):
        if item not in addresses:
            addresses.append(item)
    streets: List[str] = []
    for m in _STREET_ONLY.finditer(block):
        if free(m, "type"):
            item = fmt(m, with_house=False)
            if item not in streets:
                streets.append(item)
    return addresses, streets


def _addresses(block: str) -> List[str]:
    return _scan_addresses(block)[0]


_ABBREVIATION = re.compile(r"(?:ул|д|г|п|пп|пр|пер|ш|наб|мкр|кв|корп|стр|им|т|тел|ст|ч)\.", _FLAGS)


def _ends_sentence(line: str) -> bool:
    """Строка заканчивается концом предложения (а не сокращением вроде «ул.» перед переносом)."""
    t = line.rstrip()
    if not t or t[-1] not in ".;":
        return False
    last = t.split()[-1]
    return not _ABBREVIATION.fullmatch(last)


_PLACE_ROOTS = (r"(?:автодорог|дорог|подъезд|участк|мост|путепровод|развязк|тротуар|площад|парковк|"
                r"остановк|территори|обочин|трасс|магистрал|набережн|проезд|микрорайон)")
_PLACE_START = re.compile(
    r"(?<!\w)(?:на|по|вдоль|около|вблизи|в\s+районе)\s+(?:[а-яё\-]+\s+){0,2}?" + _PLACE_ROOTS + r"\w*",
    _FLAGS)


def _sentence_end(s: str, start: int) -> int:
    """Конец предложения, начинающегося в позиции start: «;» или точка, которая не стоит внутри
    числа («1.2») и не завершает сокращение («г.», «ул.»)."""
    for m in re.finditer(r"[.;]", s[start:]):
        i = start + m.start()
        if s[i] == ";":
            return i
        if i + 1 < len(s) and not s[i + 1].isspace():
            continue
        tokens = s[start:i].split()
        if tokens and _ABBREVIATION.fullmatch(tokens[-1] + "."):
            continue
        return i
    return len(s)


def _descriptive_place(block: str) -> Optional[str]:
    """Место, описанное словами: «на подъездной дороге к СНТ «Мега-84» г. Нижневартовска».
    Берётся от предлога перед названием дороги/участка/территории до конца предложения.
    Пересечения улиц сюда не относятся."""
    m = _PLACE_START.search(block)
    if not m:
        return None
    end = _sentence_end(block, m.start())
    value = re.sub(r"\s+", " ", block[m.start():end]).strip(" ,")
    return value or None


_BLOCK_CUT = re.compile(r"(?<=[.;])\s+(?=(?:По\s+итогам|В\s+соответствии|Согласно)\b)")


def _violation_block(text: str, start: int):
    """Блок описания нарушения после слова «выразившиеся/выразившееся».

    Возвращает (позиция слова, текст блока одной строкой, найден ли конец блока, причина-ошибки).
    Конец блока: абзац «По итогам…»/«В соответствии…»/«Согласно…» (в том числе в середине абзаца
    после законченного предложения), либо абзац с заглавной буквы после пустой строки или после
    законченного предложения. Пункты списка пишутся со строчной или с номером («1) п. 6.3.1…»);
    перенос «ул.»/«д.» в конце строки концом предложения не считается."""
    m = _EXPRESSED.search(text, start)
    if not m:
        return None, "", False, "нет слова «выразившиеся/выразившееся»"
    lines = text[m.end():].split("\n")
    taken: List[str] = []
    closed = False
    blank_seen = False
    for ln in lines[:_LOCATION_MAX_LINES + 1]:
        if not ln.strip():
            blank_seen = True
            continue
        if _LOCATION_STOP.match(ln) or (
                taken and ln.strip()[0].isupper() and (blank_seen or _ends_sentence(taken[-1]))):
            closed = True
            break
        taken.append(ln.strip())
        blank_seen = False
    if not taken:
        return m, "", False, "после «выразившиеся» нет текста"
    block = " ".join(taken)
    cut = _BLOCK_CUT.search(block)
    if cut:
        block, closed = block[:cut.start()], True
    if not closed:
        return m, block, False, ("не найден конец блока (абзац «По итогам…»): "
                                 "граница места нарушения неясна")
    return m, block, True, ""


def _find_location(text: str, start: int, res: ParseResult) -> None:
    """Место нарушения: адреса «ул. Название д. N» из блока после «выразившиеся/выразившееся».
    Если адресов нет, берётся место, описанное словами («на подъездной дороге к СНТ …»).
    Улицы без номера дома и пересечения улиц в поле не вносятся, о них выдаётся предупреждение."""
    _, block, closed, reason = _violation_block(text, start)
    if not closed:
        res.missing["location"] = reason
        return
    skipped = [re.sub(r"\s+", " ", i.group(0)).strip(" .;") for i in _INTERSECTION.finditer(block)]
    if skipped:
        res.warnings.append("В описании нарушения есть места без номера дома (пересечения улиц); "
                            "в «Место нарушения» они не внесены: " + "; ".join(skipped)[:300])
    addresses, streets = _scan_addresses(block)
    if addresses and streets:
        res.warnings.append("Улицы без номера дома в «Место нарушения» не внесены: "
                            + "; ".join(streets)[:300])
    if not addresses:
        place = _descriptive_place(block)
        if place is None:
            res.missing["location"] = ("в блоке нет адресов вида «ул. Название д. N» и описания места "
                                       "(«на … дороге», «на участке …», «на территории …»)")
            return
        res.warnings.append("Место нарушения описано без улицы и номера дома и записано как в тексте: "
                            f"«{place}». Проверьте по оригиналу.")
        res.fields["location"] = ExtractedField(
            "location", _squash(place, limit=300), _squash(block, limit=800), "location.descriptive_place")
        return
    res.fields["location"] = ExtractedField(
        "location", "; ".join(addresses), _squash(block, limit=800), "location.addresses_after_expressed")


# ----- нарушенные пункты ГОСТ ----------------------------------------------------------------------------

_GOST = r"ГОСТ(?:\s*Р)?\s*\d{4,5}(?:\.\d+)?-\d{2,4}"
_POINT = r"\d+(?:\.\d+)*"
_PTS = _POINT + r"(?:\s*(?:,|;|и)\s*" + _POINT + r")*"
_REF = re.compile(
    r"(?:пп?\.|пункт\w*|подпункт\w*)\s*(?P<pts>" + _PTS + r")\s*(?P<gost>" + _GOST + ")", _FLAGS)
# продолжение без слова «пункт»: «…ГОСТ Р 58350-2019, 5.9.29, 5.5.5 ГОСТ Р 52289-2019»
_BARE = re.compile(r"(?P<pts>" + _PTS + r")\s*(?P<gost>" + _GOST + ")", _FLAGS)
_IN_VIOLATION = re.compile(r"в\s+нарушени\w*\s+(?:(?:требовани\w+|положени\w+)\s+)?", _FLAGS)
_REF_SEP = re.compile(r"\s*(?:,|;|и)?\s*", _FLAGS)
_REQUIREMENTS = re.compile(r"нарушен\w*\s+требовани\w*", _FLAGS)


def _normalize_gost(raw: str) -> str:
    g = re.sub(r"\s+", " ", raw).upper()
    if re.match(r"^ГОСТ ?Р", g):
        return re.sub(r"^ГОСТ ?Р ?", "ГОСТ Р ", g)      # «ГОСТР 50597-2017» → «ГОСТ Р 50597-2017»
    return re.sub(r"^ГОСТ ?", "ГОСТ ", g)


def _ref_items(m):
    gost = _normalize_gost(m.group("gost"))
    frag = re.sub(r"\s+", " ", m.group(0))
    return [(point, gost, frag) for point in re.findall(_POINT, m.group("pts"))]


def _chain(text: str, pos: int):
    """Ссылка «п. N … ГОСТ …» с позиции pos и сразу за ней продолжения («, N, N ГОСТ …» — другой
    стандарт). Возвращает (список (пункт, ГОСТ, фрагмент), позиция конца)."""
    r = _REF.match(text, pos)
    if not r:
        return [], pos
    items, j = _ref_items(r), r.end()
    while True:
        k = _REF_SEP.match(text, j).end()
        nxt = _REF.match(text, k) or _BARE.match(text, k)
        if not nxt:
            return items, j
        items += _ref_items(nxt)
        j = nxt.end()


def _find_gost(text: str, start: int, res: ParseResult) -> None:
    """Пункты ГОСТ, названные нарушенными. Ссылки в пояснительной части («в соответствии с
    п. 3.18 ГОСТ…», «согласно п. 8.8 ГОСТ…») не учитываются.

    * «в нарушение п. 5.2.4 ГОСТ Р 50597-2017» — ссылки, идущие сразу за фразой (через «,», «;», «и»);
    * «нарушены требования …, п. 8.1, 8.8 ГОСТ Р 50597-2017, выразившиеся» — до слова «выразившиеся»;
    * «выразившиеся в …: 1) п. 6.3.1 ГОСТ 50597-2017 — …; 2) п. 8.1.17 ГОСТ 52289-2019 — …» — ссылки
      внутри блока описания нарушения (список после слова «выразившиеся»).
    """
    raw: List[tuple] = []     # (позиция в тексте, пункт, ГОСТ, подсказка-контекст)

    def add(pos, point, gost, lead, frag):
        raw.append((pos, point, gost, f"{lead} … {frag}"))

    for m in _IN_VIOLATION.finditer(text, start):
        items, _ = _chain(text, m.end())      # ссылки, идущие сразу за «в нарушение»
        for point, gost, frag in items:
            add(m.start(), point, gost, _squash(m.group(0)), frag)
    for m in _REQUIREMENTS.finditer(text, start):
        stop = _EXPRESSED.search(text, m.end(), m.end() + 2000)
        nl = text.find("\n", m.end())
        end = stop.start() if stop else (nl if nl != -1 else len(text))
        i = m.end()
        while i < end:
            r = _REF.search(text, i, end)
            if not r:
                break
            items, j = _chain(text, r.start())
            for point, gost, frag in items:
                add(m.start(), point, gost, _squash(m.group(0)), frag)
            i = max(j, r.end())

    # нумерованный список после «выразившиеся…:» — «1) п. 6.3.1 ГОСТ 50597-2017 — отсутствие разметки»
    em, block, closed, _ = _violation_block(text, start)
    if closed and em is not None:
        i = 0
        while i < len(block):
            r = _REF.search(block, i)
            if not r:
                break
            items, j = _chain(block, r.start())
            for point, gost, frag in items:
                add(em.start(), point, gost, "выразившиеся", frag)
            i = max(j, r.end())

    found: List[tuple] = []   # (пункт, ГОСТ, контекст) — в порядке появления в документе, без повторов
    seen = set()
    for _pos, point, gost, ctx in sorted(raw, key=lambda t: t[0]):
        if (point, gost) not in seen:
            seen.add((point, gost))
            found.append((point, gost, ctx))

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
