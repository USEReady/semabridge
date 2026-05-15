from semabridge.bidirectional.reverse_translator import ReverseTranslator


def test_reverse_translator_maps_safe_division_to_fabric_divide():
    translator = ReverseTranslator()

    dax = translator.translate_to_dax(
        'COALESCE((SUM("FACT"."PROFIT"::FLOAT)) / NULLIF((SUM("FACT"."REVENUE"::FLOAT)), 0), 0)',
        target_table="Fact",
    )

    assert dax == "DIVIDE(SUM('Fact'[PROFIT]), SUM('Fact'[REVENUE]), 0)"


def test_reverse_translator_maps_databricks_safe_division_to_fabric_divide():
    translator = ReverseTranslator()

    dax = translator.translate_to_dax(
        "COALESCE((SUM(`profit`)) / NULLIF((SUM(`revenue`)), 0), 0)",
        target_table="Fact",
    )

    assert dax == "DIVIDE(SUM('Fact'[profit]), SUM('Fact'[revenue]), 0)"
