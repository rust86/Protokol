from pathlib import Path

import pytest

from protocol_archive.parsing import parse_protocol

SAMPLE = (Path(__file__).parent / "data" / "protocol_legal_entity.txt").read_text(encoding="utf-8")


def parsed(text=SAMPLE):
    return parse_protocol(text)


# ----- образец: юридическое лицо, ст. 12.34 --------------------------------------------

def test_sample_extracts_all_fields():
    r = parsed()
    assert r.missing == {}
    assert len(r.warnings) == 1 and "пересечения улиц" in r.warnings[0]
    assert r.value("protocol_number") == "86 НН 000001"
    assert r.value("protocol_date") == "2026-02-06"
    assert r.value("violation_date") == "2026-02-05"
    assert r.value("location") == "ул. Тестовой д. 8"
    assert r.value("gost_norms") == "п. 8.1 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"
    assert r.value("offender_type") == "legal_entity"
    assert r.value("article") == "ч. 1 ст. 12.34 КоАП РФ"
    assert r.value("offender_name") == 'МБУ "Тестовое управление по дорожному хозяйству"'
    assert r.value("authority") == (
        "Начальник отделения дорожного надзора отдела ГИБДД УМВД России "
        "по г. Тестограду, майор полиции Тестов Т. Т.")


def test_every_field_has_evidence_from_the_text_and_a_rule():
    flat = " ".join(SAMPLE.split())
    for f in parsed().fields.values():
        assert f.rule and f.evidence
        for part in f.evidence.replace(" … ", " | ").split(" | "):
            assert part.rstrip("…") in flat, f"{f.name}: фрагмент не из текста: {part!r}"


def test_traps_in_the_sample_are_not_picked():
    r = parsed()
    assert "25.5" not in r.value("article") and "51" not in r.value("article")   # права, Конституция
    assert "3.1" not in r.value("article")
    assert r.value("protocol_date") != "2026-02-05"      # дата осмотра — не дата протокола
    assert "ул. Тестовая, 1" not in r.value("authority")  # место составления — не орган
    assert "ОГРН" not in r.value("offender_name") and "ИНН" not in r.value("offender_name")


def test_violation_date_is_the_action_date_not_other_dates():
    r = parsed()
    assert r.value("violation_date") == "2026-02-05"
    assert r.value("violation_date") != r.value("protocol_date")
    # 02.02.2026 (решение о рейде) и 05.02.2026 в «извещении» не должны подменять дату
    assert "11 ч. 34 мин. по 16 ч. 10 мин. 05.02.2026" in r.fields["violation_date"].evidence


def test_location_comes_only_from_the_block_after_expressed():
    r = parsed()
    assert r.value("location") == "ул. Тестовой д. 8"           # только адрес, без описания
    assert "Индустриальная" not in r.value("location")         # адреса лица/составления не берутся
    assert "ул. Тестовая д. 2" not in r.value("location")


def test_violation_date_variants_and_failures():
    base = "ПРОТОКОЛ 86 НН 000001\nпроведённых с 9 ч. 05 мин. по 18 ч. 00 мин. {}, в рамках\n"
    assert parse_protocol(base.format("01.03.2026")).value("violation_date") == "2026-03-01"
    bad = parse_protocol(base.format("31.02.2026"))
    assert bad.value("violation_date") is None and "несуществующая" in bad.missing["violation_date"]
    assert "violation_date" in parse_protocol("ПРОТОКОЛ 86 НН 000001\nвыявлено 05.02.2026\n").missing


def test_location_needs_a_clear_end_of_block():
    long = "\n".join(f"в пункте {i}" for i in range(20))
    r = parse_protocol(f"ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\n{long}\n")
    assert r.value("location") is None and "граница" in r.missing["location"]
    ok = parse_protocol("ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\nв наличии ямы по ул. Мира д. 95;\n\nдалее\n")
    assert ok.value("location") == "ул. Мира д. 95"
    assert "location" in parse_protocol("ПРОТОКОЛ 86 НН 000001\nнет такого слова\n").missing


def test_parsing_is_deterministic():
    a, b = parsed(), parsed()
    assert {k: v.value for k, v in a.fields.items()} == {k: v.value for k, v in b.fields.items()}


# ----- реальная шапка протокола (структура из ODT пользователя) ------------------------------

def test_real_world_header_layout():
    text = ("ПРОТОКОЛ 86 НН 122990\nоб административном правонарушении\n"
            "в отношении юридического лица\n\n"
            "06 февраля 2026 г. 15 ч. 30 мин.\tг. Нижневартовск, ул. Индустриальная, 68\n\n"
            "Начальник отделения\nпо г. Нижневартовску, майор полиции Аюпов Р. И.\n\n"
            "в присутствии защитника\n\n"
            "составил настоящий протокол за нарушение, предусмотренное ч. 1 ст. 12.34 КоАП РФ в отношении:\n"
            'МБУ "Управление по дорожному хозяйству и благоустройству г. Нижневартовска"\n'
            "расположенного по адресу: г. Нижневартовск\nОГРН 1178617005489 от 30.03.2017, ИНН 8603227648\n")
    r = parse_protocol(text)
    assert r.value("protocol_number") == "86 НН 122990"
    assert r.value("offender_name").startswith('МБУ "Управление по дорожному хозяйству')
    assert r.value("authority").endswith("Аюпов Р. И.")


# ----- номер ---------------------------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("ПРОТОКОЛ 86 НН 122990", "86 НН 122990"),
    ("Протокол 86нн105320", "86 НН 105320"),          # слитно и в нижнем регистре
    ("ПРОТОКОЛ № 86  НН  122990", "86 НН 122990"),
    ("ПРОТОКОЛ 86 HH 122990", "86 НН 122990"),         # латинские H на скане
])
def test_number_variants(header, expected):
    r = parse_protocol(header + "\nоб административном правонарушении\n")
    assert r.value("protocol_number") == expected


def test_latin_letters_in_series_produce_a_warning():
    r = parse_protocol("ПРОТОКОЛ 86 HH 122990\n")
    assert any("латинские" in w for w in r.warnings)


def test_number_is_taken_from_header_not_from_body():
    text = "Письмо\nдалее составлен 1 протокол осмотра 86 НН 111111\n"
    assert parse_protocol(text).value("protocol_number") is None


# ----- дата ------------------------------------------------------------------------------------

@pytest.mark.parametrize("line,iso", [
    ("06 февраля 2026 г.", "2026-02-06"), ("1 марта 2025 года", "2025-03-01"),
    ("31 декабря 2024 г.", "2024-12-31"),
])
def test_date_words(line, iso):
    r = parse_protocol(f"ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\n{line}\n")
    assert r.value("protocol_date") == iso


def test_impossible_date_is_not_corrected():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\n31 февраля 2026 г.\n")
    assert r.value("protocol_date") is None
    assert "несуществующая" in r.missing["protocol_date"]


def test_numeric_date_is_not_accepted_as_protocol_date():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\n06.02.2026\n")
    assert r.value("protocol_date") is None and "protocol_date" in r.missing


# ----- тип лица, статья, лицо ------------------------------------------------------------------------

def test_official_type_is_recognized_with_a_caution():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\n"
                       "в отношении должностного лица\n")
    assert r.value("offender_type") == "official"
    assert any("не проверялись" in w for w in r.warnings)


def test_article_without_part_and_code():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\nсоставил протокол за нарушение, предусмотренное ст. 12.9 в отношении:\nООО \"Тест\"\n")
    assert r.value("article") == "ст. 12.9"


def test_article_only_from_the_violation_phrase():
    text = ("ПРОТОКОЛ 86 НН 000001\nразъяснены права, предусмотренные статьей 25.5 КоАП РФ\n")
    assert parse_protocol(text).value("article") is None


def test_offender_name_on_same_line_and_multiline():
    base = "ПРОТОКОЛ 86 НН 000001\nза нарушение, предусмотренное ч. 1 ст. 12.34 КоАП РФ в отношении: {}\n"
    r = parse_protocol(base.format('ООО "Тест"') + "ИНН 0000000000\n")
    assert r.value("offender_name") == 'ООО "Тест"'
    r2 = parse_protocol(base.format("") + 'Общество с ограниченной\nответственностью "Тест"\nрасположенное по адресу: х\n')
    assert r2.value("offender_name") == 'Общество с ограниченной ответственностью "Тест"'


# ----- незнакомые документы: ничего не выдумываем -------------------------------------------------------

def test_unknown_document_yields_no_fields():
    r = parse_protocol("Письмо в администрацию города\nПросим рассмотреть обращение 12.34\n")
    assert r.fields == {}
    assert "protocol_number" in r.missing and r.missing["article"]


def test_empty_text():
    assert parse_protocol("").fields == {}


def test_other_form_keeps_what_it_can_and_reports_the_rest():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\nнет остальных данных\n")
    assert r.value("protocol_number") == "86 НН 000001"
    assert {"protocol_date", "violation_date", "offender_type", "article", "offender_name",
            "authority", "location", "gost_norms"} <= set(r.missing)


# ----- место нарушения: формат «ул. Мира д. 95» ---------------------------------------------------------------

def loc(block):
    return parse_protocol(f"ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\n{block}\n\nконец\n")


@pytest.mark.parametrize("block,expected", [
    ("в наличии ям по ул. Мира д. 95;", "ул. Мира д. 95"),
    ("по улице Мира д.95", "ул. Мира д. 95"),                       # написание нормализуется
    ("по ул. Мира дом 95А, и по пр. Победы д. 3/1;", "ул. Мира д. 95А; пр-кт Победы д. 3/1"),
    ("ул. Мира д. 95; ул. Мира д. 95", "ул. Мира д. 95"),           # без повторов
    ("по ул. 60 лет Октября д. 4", "ул. 60 лет Октября д. 4"),
    ("в районе пер. Школьный д. 2 и ул. Северной д. 8", "пер. Школьный д. 2; ул. Северной д. 8"),
])
def test_location_address_format(block, expected):
    assert loc(block).value("location") == expected


def test_location_after_expressed_variants_without_colon():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\nвыразившееся в ямах по ул. Мира д. 95\n\nконец\n")
    assert r.value("location") == "ул. Мира д. 95"


def test_location_intersections_are_reported_not_entered():
    r = loc("по ул. Мира д. 95, пересечении улиц Мира — Салманова, Северная – Индустриальная;")
    assert r.value("location") == "ул. Мира д. 95"
    assert any("Мира — Салманова" in w for w in r.warnings)
    only = loc("на пересечении улиц Мира — Салманова.")
    assert only.value("location") is None and "нет адресов" in only.missing["location"]
    assert any("пересечения" in w or "пересечении" in w for w in only.warnings)


def test_location_does_not_invent_house_number():
    assert loc("по ул. Мира;").value("location") is None


# ----- нарушенные пункты ГОСТ ----------------------------------------------------------------------------------------

def gost(text):
    return parse_protocol("ПРОТОКОЛ 86 НН 000001\n" + text + "\n")


def test_gost_from_in_violation_phrase_user_example():
    r = gost("выразившиеся:\nв наличии ям по ул. Мира д. 95 в нарушение п. 5.2.4 ГОСТ Р 50597-2017;\n\nконец")
    assert r.value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017"


def test_gost_every_point_is_listed_separately():
    r = gost("в нарушение п. 5.2.4 ГОСТ Р 50597-2017; в нарушение п. 8.8 ГОСТ Р 50597-2017")
    assert r.value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"
    r2 = gost("в нарушение п. 5.2.4, 5.2.5 и 8.8 ГОСТ Р 50597-2017")
    assert r2.value("gost_norms") == ("п. 5.2.4 ГОСТ Р 50597-2017; п. 5.2.5 ГОСТ Р 50597-2017; "
                                      "п. 8.8 ГОСТ Р 50597-2017")


def test_gost_different_standards_and_no_duplicates():
    r = gost("в нарушение п. 5.2.4 ГОСТ Р 50597-2017, п. 3.1 ГОСТ 33100-2014\n"
             "а также в нарушение п. 5.2.4 ГОСТ Р 50597-2017")
    assert r.value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017; п. 3.1 ГОСТ 33100-2014"


def test_gost_from_violated_requirements_sentence():
    r = gost('а именно нарушены требования ст. 12 Федерального закона от 10.12.1995 №196-ФЗ, '
             'п. 13 ОПД ПДД РФ, п. 8.1, 8.8 ГОСТ Р 50597-2017, выразившиеся:\nв ямах по ул. Мира д. 1;\n\nконец')
    assert r.value("gost_norms") == "п. 8.1 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"


def test_gost_explanatory_citations_are_not_violations():
    r = gost("В соответствии с п. 3.18 ГОСТ Р 59434-2021 снежный вал – это слой снега.\n"
             "Согласно п. 8.8 ГОСТ Р 50597-2017, формирование валов не допускается.")
    assert r.value("gost_norms") is None and "gost_norms" in r.missing


def test_gost_reference_without_a_point_is_ignored():
    assert gost("в нарушение ГОСТ Р 50597-2017").value("gost_norms") is None


def test_gost_evidence_names_the_trigger_phrase():
    ev = gost("в нарушение п. 5.2.4 ГОСТ Р 50597-2017").fields["gost_norms"].evidence
    assert "в нарушение" in ev and "п. 5.2.4 ГОСТ Р 50597-2017" in ev
