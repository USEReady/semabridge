from semabridge.connectors.synonym_clause import synonyms_clause
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.utils.synonyms import merge_synonyms, synonym_override_key


def test_merge_synonyms_prioritizes_ui_then_tmsl_then_auto():
    assert merge_synonyms(
        ui_overrides=["SKU", "Product Code"],
        user_defined=["Item ID", "sku"],
        auto_generated=["Product Id", "Item ID", "Product Number", "Ignored"],
        max_auto=2,
    ) == ["SKU", "Product Code", "Item ID", "Product Id", "Product Number"]


def test_synonyms_clause_escapes_quotes_and_omits_empty():
    assert synonyms_clause([]) == ""
    assert synonyms_clause(["Sales", "O'Brien"]) == " WITH SYNONYMS = ('Sales', 'O''Brien')"


def test_tmsl_to_osi_extracts_column_and_measure_synonyms_with_overrides():
    tmsl = {
        "model": {
            "name": "Retail Model",
            "tables": [
                {
                    "name": "Product",
                    "columns": [
                        {
                            "name": "ProductID",
                            "dataType": "string",
                            "synonyms": ["Product Number"],
                        }
                    ],
                    "measures": [
                        {
                            "name": "Total Revenue",
                            "expression": "SUM([Revenue])",
                            "synonyms": ["Sales Total"],
                        }
                    ],
                }
            ],
        }
    }
    overrides = {
        synonym_override_key("Retail Model", "Product", "ProductID"): ["SKU"],
        synonym_override_key("Retail Model", "Product", "Total Revenue"): ["Income"],
    }

    osi = TMSLToOSIConverter().to_osi({
        "tmsl": tmsl,
        "workspace_id": "ws",
        "dataset_id": "ds",
        "display_name": "Retail Model",
        "synonym_overrides": overrides,
    })

    column = osi.datasets[0].columns[0]
    metric = osi.metrics[0]

    # Override then TMSL-authored, in that order -- no auto-generated
    # synonym is produced from the name alone (removed as a source).
    assert column.synonyms == ["SKU", "Product Number"]
    assert metric.synonyms == ["Income", "Sales Total"]
