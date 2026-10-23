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
    long = "\n".join(f"в пункте {i}" for i in range(60))
    r = parse_protocol(f"ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\n{long}\n")
    assert r.value("location") is None and "граница" in r.missing["location"]
    ok = parse_protocol("ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\nв наличии ямы по ул. Мира д. 95;\n\nДалее\n")
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


def test_numeric_protocol_date_is_a_fallback_directly_under_the_header():
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\n26.03.2026\n")
    assert r.value("protocol_date") == "2026-03-26"
    assert r.fields["protocol_date"].rule == "date.header_numeric"
    # дата словами приоритетнее цифровой
    both = parse_protocol("ПРОТОКОЛ 86 НН 000001\n26.03.2026\n27 марта 2026 г.\n")
    assert both.value("protocol_date") == "2026-03-27"
    # цифровая дата далеко от заголовка (например, в извещении) не берётся
    far = parse_protocol("ПРОТОКОЛ 86 НН 000001\n" + "текст " * 80 + "\nизвещение от 05.02.2026\n")
    assert far.value("protocol_date") is None
    # несуществующая цифровая дата не исправляется
    bad = parse_protocol("ПРОТОКОЛ 86 НН 000001\n31.02.2026\n")
    assert bad.value("protocol_date") is None and "несуществующая" in bad.missing["protocol_date"]


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
    return parse_protocol(f"ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\n{block}\n\nКонец\n")


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
    r = parse_protocol("ПРОТОКОЛ 86 НН 000001\nвыразившееся в ямах по ул. Мира д. 95\n\nКонец\n")
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


# ----- переносы строк внутри фраз (PDF и сканы) -------------------------------------------------------------------

def test_violation_date_survives_line_breaks_anywhere():
    phrase = "проведённых с 11 ч. 34 мин. по 16 ч. 10 мин. 05.02.2026"
    words = phrase.split(" ")
    for i in range(1, len(words)):                       # перенос в каждом возможном месте
        wrapped = " ".join(words[:i]) + "\n" + " ".join(words[i:])
        r = parse_protocol(f"ПРОТОКОЛ 86 НН 000001\n{wrapped}, в рамках\n")
        assert r.value("violation_date") == "2026-02-05", wrapped


def test_article_date_type_survive_line_breaks():
    text = ("ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\nв отношении\nюридического лица\n"
            "06 февраля\n2026 г.\nсоставил протокол за нарушение,\nпредусмотренное ч.\n1 ст.\n12.34 КоАП\nРФ в\n"
            "отношении:\nООО \"Тест\"\n")
    r = parse_protocol(text)
    assert r.value("protocol_date") == "2026-02-06"
    assert r.value("offender_type") == "legal_entity"
    assert r.value("article") == "ч. 1 ст. 12.34 КоАП РФ"
    assert r.value("offender_name") == 'ООО "Тест"'


def test_gost_survives_line_breaks_in_references():
    r = gost("в нарушение п.\n5.2.4 ГОСТ Р\n50597-2017, п. 8.8\nГОСТ\nР 50597-2017")
    assert r.value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"
    r2 = gost("нарушены требования\nп. 8.1,\n8.8 ГОСТ Р\n50597-2017, выразившиеся:")
    assert r2.value("gost_norms") == "п. 8.1 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"


def test_gost_after_in_violation_does_not_run_into_explanations():
    r = gost("в нарушение п. 5.2.4 ГОСТ Р 50597-2017.\nВ соответствии с п. 3.18 ГОСТ Р 59434-2021 снежный вал")
    assert r.value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017"


def test_location_block_with_many_wrapped_lines():
    items = "\n".join(["в ямах по ул. Мира", "д. 95 и по ул.", "Северной д. 8;"] * 3)
    r = parse_protocol(f"ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\n{items}\nПо итогам действий\n")
    assert r.value("location") == "ул. Мира д. 95; ул. Северной д. 8"


def test_location_blank_lines_between_items_do_not_end_the_block():
    """Tesseract ставит пустые строки между абзацами списка."""
    text = ("ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\n\nв формировании валов по ул. Мира д. 95;\n\n"
            "в наличии ям по ул. Северной д. 8.\n\nПо итогам действий составлен протокол\n")
    assert parse_protocol(text).value("location") == "ул. Мира д. 95; ул. Северной д. 8"


def test_location_block_ends_at_capitalized_paragraph_after_blank_line():
    text = ("ПРОТОКОЛ 86 НН 000001\nвыразившиеся:\nв ямах по ул. Мира д. 95;\n\n"
            "Далее описано нарушение на ул. Чужой д. 1\n")
    assert parse_protocol(text).value("location") == "ул. Мира д. 95"


def test_gost_without_space_before_r():
    r = gost("в нарушение п. 5.2.4 ГОСТР 50597-2017; п. 8.8 ГОСТ  Р  50597-2017")
    assert r.value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017; п. 8.8 ГОСТ Р 50597-2017"
    assert gost("в нарушение п. 3.1 ГОСТ 33100-2014").value("gost_norms") == "п. 3.1 ГОСТ 33100-2014"


# ===== формулировки из реальных документов пользователя =========================================================

H = "ПРОТОКОЛ 86 НН 000001\nоб административном правонарушении\nв отношении юридического лица\n"


def field(body, name):
    return parse_protocol(H + body).value(name)


@pytest.mark.parametrize("body,expected", [
    # дата после интервала, время «ч./мин.»
    ("осмотра, проведённых с 11 ч. 34 мин. по 16 ч. 10 мин. 05.02.2026, в рамках рейда", "2026-02-05"),
    # «В период времени …», дата после интервала
    ("В период времени с 10 ч. 44 мин. по 10 ч. 45 мин. 04.05.2026 установлено", "2026-05-04"),
    # дата ПЕРЕД интервалом, время ЧЧ:ММ
    ("осмотра, проведённого 06.07.2026 с 14:10 по 14:14, установлено", "2026-07-06"),
    ("проведённого 29.07.2026 с 15:55 по 15:57", "2026-07-29"),
    # «до» вместо «по», перенос строки внутри интервала
    ("проведённого осмотра в период с 9 ч. 05 мин.\nдо 9 ч. 40 мин. 01.03.2026", "2026-03-01"),
])
def test_violation_date_real_formats(body, expected):
    assert field(body, "violation_date") == expected


def test_violation_date_ignores_dates_outside_the_time_interval():
    body = ("в соответствии с решением от 02.02.2026 №4, осмотра, проведённого 06.07.2026 с 14:10 по 14:14; "
            "извещение от 05.07.2026")
    assert field(body, "violation_date") == "2026-07-06"      # дата решения и извещения не подходят
    assert field("решение от 02.02.2026 №4, извещение от 05.07.2026", "violation_date") is None
    # интервал без слов о контрольных действиях рядом — не берём
    assert field("Часы приёма с 9:00 по 18:00 01.03.2026", "violation_date") is None


def test_violation_date_evidence_is_the_interval_phrase():
    r = parse_protocol(H + "осмотра, проведённого 06.07.2026 с 14:10 по 14:14, установлено")
    assert r.fields["violation_date"].evidence == "06.07.2026 с 14:10 по 14:14"


@pytest.mark.parametrize("header", ["ПРОТОКОЛ 86 Н Н 105317", "ПРОТОКОЛ 86 НН 105317", "ПРОТОКОЛ 86 H H 105317",
                                    "Протокол 86нн105317"])
def test_number_with_spaces_between_series_letters(header):
    assert parse_protocol(header + "\n").value("protocol_number") == "86 НН 105317"


@pytest.mark.parametrize("body,expected", [
    # улица + дом, полное написание, «по адресу г. …»
    ("выразившееся в проведении дорожных работ по демонтажу сборно-разборной искусственной неровности "
     "по адресу г. Нижневартовск ул. Мира д. 95 в нарушение\nПо итогам", "ул. Мира д. 95"),
    # «в районе дома 45 по улице Лопарева» → дом перед улицей
    ("выразившееся в наличии дефекта обочины на 14,5 см, в районе дома 45 по улице Лопарева.\nПо итогам",
     "ул. Лопарева д. 45"),
    ("выразившееся в яме в районе д. 3/1 по проспекту Победы.\nПо итогам", "пр-кт Победы д. 3/1"),
    ("выразившееся в яме у дома 8А по ул. 60 лет Октября, а также\nПо итогам", "ул. 60 лет Октября д. 8А"),
    # оба порядка в одном блоке
    ("выразившееся в ямах по ул. Мира д. 95 и в районе дома 45 по улице Лопарева.\nПо итогам",
     "ул. Мира д. 95; ул. Лопарева д. 45"),
])
def test_location_real_formats(body, expected):
    assert field(body, "location") == expected


def test_location_single_paragraph_followed_by_a_capitalized_paragraph():
    """Абзац без пустых строк вокруг: конец блока — заглавная буква после законченного предложения."""
    body = ("выразившееся в ямах по ул. Мира д. 95 в нарушение п. 5.1.6 ГОСТ Р 52289-2019.\n"
            "Создаётся угроза причинения вреда на ул. Чужой д. 1.\n")
    assert field(body, "location") == "ул. Мира д. 95"


def test_location_wrapped_street_abbreviation_does_not_end_the_block():
    body = "выразившиеся:\nв ямах по ул.\nМира д. 95;\nв ямах по ул.\nСеверной д. 8.\nПо итогам\n"
    assert field(body, "location") == "ул. Мира д. 95; ул. Северной д. 8"


def test_gost_points_word_in_plural():
    assert field("в нарушение пунктов 6.3.2, 6.3.4, 8.1.2, 8.1.6 ГОСТ Р 52289-2019", "gost_norms") == (
        "п. 6.3.2 ГОСТ Р 52289-2019; п. 6.3.4 ГОСТ Р 52289-2019; "
        "п. 8.1.2 ГОСТ Р 52289-2019; п. 8.1.6 ГОСТ Р 52289-2019")


def test_gost_two_standards_in_one_phrase_keep_their_own_points():
    body = ("в нарушение пунктов 6.1.1.1, 6.4.3.1, 6.5.1.2, 6.5.3.1, 6.6.1 ГОСТ Р 58350-2019, "
            "5.9.29, 5.5.5, 5.6.23, 5.1.6, 5.2.27, 5.6.25, 5.2.22, 5.4.22 ГОСТ Р 52289-2019")
    value = field(body, "gost_norms")
    items = value.split("; ")
    assert len(items) == 13
    assert items[0] == "п. 6.1.1.1 ГОСТ Р 58350-2019" and items[4] == "п. 6.6.1 ГОСТ Р 58350-2019"
    assert items[5] == "п. 5.9.29 ГОСТ Р 52289-2019" and items[-1] == "п. 5.4.22 ГОСТ Р 52289-2019"
    assert all(i.endswith("ГОСТ Р 58350-2019") for i in items[:5])
    assert all(i.endswith("ГОСТ Р 52289-2019") for i in items[5:])


def test_gost_two_standards_in_requirements_sentence():
    body = ("нарушены требования ст. 12 Федерального закона, п. 6.6.1 ГОСТ Р 58350-2019, "
            "5.9.29, 5.5.5 ГОСТ Р 52289-2019, выразившиеся в наличии ям")
    assert field(body, "gost_norms") == ("п. 6.6.1 ГОСТ Р 58350-2019; п. 5.9.29 ГОСТ Р 52289-2019; "
                                         "п. 5.5.5 ГОСТ Р 52289-2019")


def test_bare_points_without_a_preceding_reference_are_not_gost():
    assert field("в нарушение требований, 5.9.29, 5.5.5 ГОСТ Р 52289-2019", "gost_norms") is None
    assert field("номера 5.9.29, 5.5.5 ГОСТ Р 52289-2019 приведены в таблице", "gost_norms") is None


# ===== форма реального скана: место описано словами, а не улицей и домом ===================================

SAMPLE2 = (Path(__file__).parent / "data" / "protocol_legal_entity_descriptive_place.txt").read_text(encoding="utf-8")


def test_descriptive_place_sample_extracts_everything():
    r = parse_protocol(SAMPLE2)
    assert r.missing == {}
    assert r.value("protocol_number") == "86 НН 000003"
    assert r.value("protocol_date") == "2026-10-05"
    assert r.value("violation_date") == "2026-10-02"          # «проведённого 02.10.2026 с 10:15 по 10:19»
    assert r.value("article") == "ч. 1 ст. 12.34 КоАП РФ"
    assert r.value("offender_type") == "legal_entity"
    assert r.value("offender_name") == 'МБУ "Тестовое управление по дорожному хозяйству"'
    assert r.value("authority").endswith("капитан полиции Тестов Т. Т.")
    assert r.value("location") == "на подъездной дороге к СНТ «Тест-1» г. Тестограда"
    assert r.fields["location"].rule == "location.descriptive_place"
    assert any("без улицы и номера дома" in w for w in r.warnings)
    # ГОСТ без «Р» записывается как в документе; ссылки «в соответствии с…/согласно…» не считаются
    assert r.value("gost_norms") == ("п. 6.2.1 ГОСТ 52289-2019; п. 6.2.4 ГОСТ 52289-2019; "
                                     "п. 6.2.7 ГОСТ 52289-2019")


def test_descriptive_place_keeps_decimals_and_abbreviations_inside():
    r = loc("выразившееся в отсутствии разметки 1.2, 1.5 на подъездной дороге к СНТ «Мега-84» г. Тестограда.")
    assert r.value("location") == "на подъездной дороге к СНТ «Мега-84» г. Тестограда"


@pytest.mark.parametrize("block,expected", [
    ("выразившееся в отсутствии знака на участке дороги Тестовая – Образцовая;", "на участке дороги Тестовая – Образцовая"),
    ("выразившиеся в ямах на территории СНТ «Тест-1».", "на территории СНТ «Тест-1»"),
    ("выразившееся в отсутствии освещения вдоль обочины автодороги Тест – Образец.", "вдоль обочины автодороги Тест – Образец"),
])
def test_descriptive_place_variants(block, expected):
    assert loc(block).value("location") == expected


def test_street_address_wins_over_descriptive_place():
    r = loc("выразившееся в яме на подъездной дороге по ул. Мира д. 95.")
    assert r.value("location") == "ул. Мира д. 95"
    assert not any("без улицы" in w for w in r.warnings)


def test_descriptive_place_is_not_invented_from_unrelated_words():
    assert loc("выразившееся в наличии дефекта размером 14,5 см.").value("location") is None
    assert loc("выразившееся в нарушении по демонтажу неровности.").value("location") is None
    assert loc("выразившееся в яме на пересечении улиц Мира — Салманова.").value("location") is None


def test_gost_list_follows_document_order():
    body = ("в наличии ям в нарушение п. 5.2.4 ГОСТ Р 50597-2017.\n"
            "а именно нарушены требования п. 8.1 ГОСТ Р 50597-2017, выразившееся в ямах")
    assert gost(body).value("gost_norms") == "п. 5.2.4 ГОСТ Р 50597-2017; п. 8.1 ГОСТ Р 50597-2017"


# ===== реальные формы: список пунктов ГОСТ после «выразившиеся», дом перед улицей =========================

LIST_FORM = (
    "а именно нарушены требования ст. 12 Федерального закона от 10.12.1995 №196-ФЗ, п. 13 ОПД ПДД РФ, "
    "выразившиеся в несоблюдении обязательных требований по ул. 2П-2 д. 60 ст1:\n"
    "1) п. 6.3.1 ГОСТ 50597-2017 — отсутствие дорожной разметки 1.2;\n"
    "2) п. 8.1.17 ГОСТ 52289-2019 — отсутствие начальных и концевых участков барьерного ограждения "
    "по ул. 2П-2 д. 60 ст1;\n"
    "3) п. 4.5.1.2 ГОСТ Р 52766-2007 — отсутствие тротуаров;\n"
    "Создается непосредственная угроза причинения вреда жизни, с учетом раздела 1 ГОСТ 50597-2017.\n"
    "По итогам действий составлен протокол.\n"
    "В соответствии с п. 9.9 ГОСТ Р 50597-2017 и пунктом 8.1.17 ГОСТ Р 52289-2019 определено иное.\n")


def test_gost_numbered_list_after_expressed():
    r = parse_protocol(H + LIST_FORM)
    assert r.value("gost_norms") == ("п. 6.3.1 ГОСТ 50597-2017; п. 8.1.17 ГОСТ 52289-2019; "
                                     "п. 4.5.1.2 ГОСТ Р 52766-2007")
    assert "9.9" not in r.value("gost_norms")                       # пояснение после блока
    assert r.value("location") == "ул. 2П-2 д. 60"
    assert "выразившиеся" in r.fields["gost_norms"].evidence


def test_gost_list_does_not_run_past_the_end_of_the_block():
    only_after = H + ("выразившиеся в яме по ул. Мира д. 5.\nСоздается угроза.\n"
                      "В соответствии с п. 6.3.1 ГОСТ 50597-2017 разметка обязательна.\n")
    assert parse_protocol(only_after).value("gost_norms") is None


def test_gost_list_without_closed_block_is_not_used():
    unclosed = H + "выразившиеся:\n" + "\n".join(f"1) п. 6.{i}.1 ГОСТ 50597-2017;" for i in range(60))
    assert parse_protocol(unclosed).value("gost_norms") is None


def test_gost_numbered_list_items_in_one_line_per_paragraph_and_wrapped():
    text = H + ("выразившиеся в нарушениях:\n1) п. 6.3.1 ГОСТ\n50597-2017 — отсутствие разметки;\n"
                "2) п. 8.1.17 ГОСТ Р\n52289-2019 — нет ограждения;\nСоздается угроза.\n")
    assert parse_protocol(text).value("gost_norms") == "п. 6.3.1 ГОСТ 50597-2017; п. 8.1.17 ГОСТ Р 52289-2019"


DISTRICT_FORM = (
    "а именно нарушены требования ст. 12 Федерального закона, п. 13 ОПД ПДД РФ, п. 8.8 ГОСТ 50597-2017, "
    "выразившиеся в формировании снежных валов непосредственно на границе нерегулируемого пешеходного "
    "перехода по ул. Дзержинского, в районе д. 60/5 ул. Мира г. Тестограда. По итогам контрольных "
    "(надзорных) действий составлен 1 протокол осмотра, велась фотофиксация.\n"
    "Создается непосредственная угроза причинения вреда.\n")


def test_house_before_street_without_preposition():
    r = parse_protocol(H + DISTRICT_FORM)
    assert r.value("location") == "ул. Мира д. 60/5"
    assert r.value("gost_norms") == "п. 8.8 ГОСТ 50597-2017"
    assert any("ул. Дзержинского" in w and "без номера дома" in w for w in r.warnings)


def test_trailing_sentence_in_the_same_paragraph_is_not_part_of_the_block():
    body = ("выразившееся в яме по ул. Мира д. 5. По итогам действий составлен протокол, "
            "в районе д. 7 ул. Чужая г. Тестограда.\nСоздается угроза.\n")
    assert field(body, "location") == "ул. Мира д. 5"


@pytest.mark.parametrize("block,expected", [
    ("в районе д. 60/5 ул. Мира г. Тестограда.", "ул. Мира д. 60/5"),
    ("у д. 8А ул. Северной.", "ул. Северной д. 8А"),
    ("возле дома 12 пр. Победы, а также", "пр-кт Победы д. 12"),
    ("по ул. Спортивная в районе д.17 А, д.17", "ул. Спортивная д. 17А"),
    ("по ул. Мира д. 95 в нарушение пункта", "ул. Мира д. 95"),                    # «в» — не буква дома
    ("по ул. Мира д. 95 ул. Северной д. 8.", "ул. Мира д. 95; ул. Северной д. 8"),   # два адреса подряд
])
def test_address_orders_and_house_letters(block, expected):
    assert loc(block).value("location") == expected


def test_streets_without_house_are_reported_only_when_an_address_exists():
    r = loc("по ул. Дзержинского, в районе д. 60/5 ул. Мира г. Тестограда.")
    assert r.value("location") == "ул. Мира д. 60/5"
    assert any("ул. Дзержинского" in w for w in r.warnings)
    assert not any("ул. Мира" in w for w in r.warnings)                     # улица с домом — не «без номера»
