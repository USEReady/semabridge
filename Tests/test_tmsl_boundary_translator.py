from semabridge.adapters.tmsl_translator import translate_tmsl_to_internal_sml


def test_translate_tmsl_to_internal_sml_returns_sml_model() -> None:
    raw_tmsl = {
        "model": {
            "name": "SalesModel",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {"name": "SaleId", "dataType": "int64"},
                        {"name": "Amount", "dataType": "double"},
                    ],
                    "partitions": [{"name": "p0"}],
                }
            ],
            "relationships": [],
        }
    }

    model = translate_tmsl_to_internal_sml(
        raw_tmsl,
        workspace_id="ws-1",
        dataset_id="ds-1",
        row_counts={"Sales": 10},
    )

    assert model.unique_name == "ds-1"
    assert model.label == "SalesModel"
    assert model.dataset_count >= 1

