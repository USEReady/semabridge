from semabridge.api.services.project_mapping_engine import build_entity_mappings


def test_metric_rows_include_single_measure_source_table() -> None:
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "ORDERS", "columns": [{"unique_name": "QUANTITY", "data_type": "number"}]},
        ],
        "metrics": [
            {"unique_name": "TOTAL_QUANTITY", "expression": "SUM(ORDERS[QUANTITY])", "data_type": "number"},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model)
    metric = next(row for row in payload["mappings"] if row.get("entity_kind") == "metric")

    assert metric["measure_source_tables"] == ["ORDERS"]
    assert metric["parent_source_path"] == "datasets.ORDERS"
    assert metric["source_expression"] == "SUM(ORDERS[QUANTITY])"


def test_metric_rows_include_multi_table_measure_sources() -> None:
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "ORDERS", "columns": [{"unique_name": "QUANTITY", "data_type": "number"}]},
            {"unique_name": "FABRICMODEL_DATA", "columns": [{"unique_name": "QUANTITY", "data_type": "number"}]},
        ],
        "metrics": [
            {
                "unique_name": "BLENDED_QUANTITY",
                "expression": "SUM(ORDERS[QUANTITY]) + SUM('FABRICMODEL_DATA'[QUANTITY])",
                "data_type": "number",
            },
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model)
    metric = next(row for row in payload["mappings"] if row.get("entity_kind") == "metric")

    assert metric["measure_source_tables"] == ["ORDERS", "FABRICMODEL_DATA"]
    assert metric["parent_source_path"] is None


def test_metric_target_name_uses_snowflake_reserved_prefix() -> None:
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [{"unique_name": "ORDERS", "columns": []}],
        "metrics": [{"unique_name": "DATE", "expression": "COUNTROWS(ORDERS)", "data_type": "number"}],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    metric = next(row for row in payload["mappings"] if row.get("entity_kind") == "metric")

    assert metric["target_name"] == "COL_DATE"


def test_metric_target_name_uses_snowflake_leading_digit_rule() -> None:
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [{"unique_name": "ORDERS", "columns": []}],
        "metrics": [{"unique_name": "2024 Sales", "expression": "COUNTROWS(ORDERS)", "data_type": "number"}],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    metric = next(row for row in payload["mappings"] if row.get("entity_kind") == "metric")

    assert metric["target_name"] == "_2024_SALES"
