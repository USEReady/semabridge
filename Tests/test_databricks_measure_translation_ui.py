import pytest
from semabridge.connectors.databricks_measure_translation import DatabricksMeasureTranslator
from semabridge.core.behavior import DatabricksBehavior

def test_parse_switch_ui_logic():
    behavior = DatabricksBehavior()
    translator = DatabricksMeasureTranslator(
        behavior=behavior,
        sanitize_identifier=lambda x: x,
        build_aggregation_sql=lambda x: "",
        distinct_count_expression=lambda x: x
    )

    dax = '''
    SWITCH(SELECTEDVALUE('Plant_BU_Mapping'[business_unit]),
      "USP", "• Source of dashboard is SAP subledger via BW...",
      "MSH", "• Source of dashboard is SAP subledger via BW...",
      "CMM", "• Source of dashboard is SAP subledger via BW...",
      "DefaultText"
    )
    '''

    result = translator.parse_switch_ui_logic(dax)
    assert result is not None
    assert result["dataset"] == "Plant_BU_Mapping"
    assert result["column"] == "business_unit"
    assert result["default"] == "DefaultText"
    assert len(result["cases"]) == 3
    assert result["cases"][0] == ("USP", "• Source of dashboard is SAP subledger via BW...")

def test_parse_switch_ui_logic_no_quotes_around_table():
    behavior = DatabricksBehavior()
    translator = DatabricksMeasureTranslator(
        behavior=behavior,
        sanitize_identifier=lambda x: x,
        build_aggregation_sql=lambda x: "",
        distinct_count_expression=lambda x: x
    )

    dax = '''SWITCH(SELECTEDVALUE(Plant_BU_Mapping[business_unit]), "USP", "Result")'''
    result = translator.parse_switch_ui_logic(dax)
    assert result is not None
    assert result["dataset"] == "Plant_BU_Mapping"
    assert len(result["cases"]) == 1
    assert result["cases"][0] == ("USP", "Result")
    assert result["default"] is None

def test_generate_lookup_ddl():
    translator = DatabricksMeasureTranslator(
        behavior=DatabricksBehavior(),
        sanitize_identifier=lambda x: x.lower(),
        build_aggregation_sql=lambda x: "",
        distinct_count_expression=lambda x: x
    )

    lookup_dict = {
        "dataset": "Plant_BU_Mapping",
        "column": "business_unit",
        "cases": [("USP", "Text1"), ("MSH", "Text's")],
        "default": "Default"
    }

    ddl = translator.generate_lookup_ddl("Subledger_Callout", lookup_dict, schema_prefix="test_schema.")
    assert "CREATE TABLE IF NOT EXISTS test_schema.plant_bu_mapping_subledger_callout_callouts (" in ddl
    assert "`business_unit` STRING" in ddl
    assert "`subledger_callout_text` STRING" in ddl
    assert "INSERT INTO test_schema.plant_bu_mapping_subledger_callout_callouts VALUES" in ddl
    assert "('USP', 'Text1')" in ddl
    assert "('MSH', 'Text''s')" in ddl
    assert "('__DEFAULT__', 'Default')" in ddl

