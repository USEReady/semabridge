from semabridge.api.services.project_mapping_engine import build_entity_mappings
from semabridge.api.services.mapping_service import _compat_serialize_auto_map_entity_mappings


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


def test_option_b_collision_resolution() -> None:
    # Scenario: Two datasets Product and Customer both have Manufacturer column.
    # The engine should detect collisions, but keep the target names as raw sanitized MANUFACTURER.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Product", "columns": [{"unique_name": "Manufacturer", "data_type": "string"}]},
            {"unique_name": "Customer", "columns": [{"unique_name": "Manufacturer", "data_type": "string"}]},
        ],
        "metrics": [],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    mappings = payload["mappings"]

    # Filter to only the columns
    columns = [row for row in mappings if row.get("entity_kind") == "column"]
    assert len(columns) == 2

    # Check target names are NOT mutated
    for col in columns:
        assert col["target_name"] == "MANUFACTURER"
        assert col["collision_detected"] is True
        assert col["collision_group"] == "column::Core_Finance_v1:MANUFACTURER"
        assert col["validation_status"] == "collision"
        assert col["validation_code"] == "NAME_COLLISION"
        # Verify suggestions ordering
        assert col["resolution_suggestions"][0] in ("PRODUCT_MANUFACTURER", "CUSTOMER_MANUFACTURER")

    # Scenario B: Overriding one of them manually makes the other unique under Option B
    existing_mappings = {
        "datasets.Product.columns.Manufacturer": {"id": "m1", "target_name": "OVERRIDDEN_MANUFACTURER", "is_user_edited": True},
    }

    payload_override = build_entity_mappings(
        project_id="p1",
        model=model,
        existing_mappings=existing_mappings,
        target_connector="snowflake"
    )
    cols = {row["source_path"]: row for row in payload_override["mappings"] if row.get("entity_kind") == "column"}

    col_prod = cols["datasets.Product.columns.Manufacturer"]
    col_cust = cols["datasets.Customer.columns.Manufacturer"]

    # Overridden column should be valid and have user edit status
    assert col_prod["target_name"] == "OVERRIDDEN_MANUFACTURER"
    assert col_prod["collision_detected"] is False
    assert col_prod["validation_status"] == "valid"

    # Symmetrically, Customer.Manufacturer is now unique, so its collision flag is cleared
    assert col_cust["target_name"] == "MANUFACTURER"
    assert col_cust["collision_detected"] is False
    assert col_cust["validation_status"] == "valid"


def test_transitive_collision() -> None:
    # Scenario: Product.Manufacturer and Customer.Manufacturer collide.
    # Product has another column Product_Manufacturer.
    # Target names should stay as raw sanitized, and conflicts are only flagged on duplicates.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {
                "unique_name": "Product",
                "columns": [
                    {"name": "Manufacturer", "data_type": "string"},
                    {"name": "Product_Manufacturer", "data_type": "string"},
                ],
            },
            {
                "unique_name": "Customer",
                "columns": [
                    {"name": "Manufacturer", "data_type": "string"},
                ],
            },
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    mappings = payload["mappings"]

    columns = {row["source_path"]: row for row in mappings if row.get("entity_kind") == "column"}
    assert len(columns) == 3

    col_prod_man = columns["datasets.Product.columns.Manufacturer"]
    col_prod_prod_man = columns["datasets.Product.columns.Product_Manufacturer"]
    col_cust_man = columns["datasets.Customer.columns.Manufacturer"]

    # Target names check: raw sanitized, not mutated
    assert col_prod_man["target_name"] == "MANUFACTURER"
    assert col_cust_man["target_name"] == "MANUFACTURER"
    assert col_prod_prod_man["target_name"] == "PRODUCT_MANUFACTURER"

    # Conflicting columns should have collision_detected = True
    assert col_prod_man["collision_detected"] is True
    assert col_prod_man["validation_status"] == "collision"
    
    assert col_cust_man["collision_detected"] is True
    assert col_cust_man["validation_status"] == "collision"

    # Unique column should be valid
    assert col_prod_prod_man["collision_detected"] is False
    assert col_prod_prod_man["validation_status"] == "valid"


def test_collisions_remain_unresolved_until_user_action() -> None:
    # Regression test: verify that when build_entity_mappings is called with multiple colliding columns,
    # target_names remain unresolved initially and resolution_suggestions are populated deterministically.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "BU", "columns": [{"unique_name": "Executive_id", "data_type": "integer"}]},
            {"unique_name": "Executive", "columns": [{"unique_name": "ID", "data_type": "integer"}]},
            {"unique_name": "Industry", "columns": [{"unique_name": "ID", "data_type": "integer"}]},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    col_bu_exec = cols["datasets.BU.columns.Executive_id"]
    col_exec_id = cols["datasets.Executive.columns.ID"]
    col_ind_id = cols["datasets.Industry.columns.ID"]

    assert col_bu_exec["target_name"] == "EXECUTIVE_ID"
    assert col_bu_exec["collision_detected"] is False
    assert col_bu_exec["validation_status"] == "valid"

    assert col_exec_id["target_name"] == "ID"
    assert col_exec_id["collision_detected"] is True
    assert col_exec_id["validation_status"] == "collision"
    
    # Verify suggestions order:
    assert col_exec_id["resolution_suggestions"][0] == "EXECUTIVE_ID"
    assert "EXECUTIVE_ID" in col_exec_id["resolution_suggestions"][1]

    assert col_ind_id["target_name"] == "ID"
    assert col_ind_id["collision_detected"] is True
    assert col_ind_id["validation_status"] == "collision"
    assert col_ind_id["resolution_suggestions"][0] == "INDUSTRY_ID"


def test_metric_and_column_rows_include_synonyms() -> None:
    # 1. Model with synonyms
    model_with_synonyms = {
        "unique_name": "SalesModel",
        "datasets": [
            {
                "unique_name": "SALES",
                "columns": [
                    {
                        "unique_name": "REVENUE",
                        "data_type": "number",
                        "synonyms": ["Total Revenue", "Monthly Revenue"],
                    }
                ],
            },
        ],
        "metrics": [
            {
                "unique_name": "REV_METRIC",
                "expression": "SUM(SALES[REVENUE])",
                "data_type": "number",
                "synonyms": ["Sales Rev", "Rev KPI"],
            },
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model_with_synonyms)
    mappings = payload["mappings"]

    col_row = next(r for r in mappings if r.get("entity_kind") == "column")
    metric_row = next(r for r in mappings if r.get("entity_kind") == "metric")

    assert col_row["synonyms"] == ["Total Revenue", "Monthly Revenue"]
    assert metric_row["synonyms"] == ["Sales Rev", "Rev KPI"]

    # 2. Model without synonyms (backward compatibility / Fabric default)
    model_without_synonyms = {
        "unique_name": "FinanceModel",
        "datasets": [
            {
                "unique_name": "COSTS",
                "columns": [
                    {
                        "unique_name": "EXPENSE",
                        "data_type": "number",
                    }
                ],
            },
        ],
        "metrics": [
            {
                "unique_name": "EXP_METRIC",
                "expression": "SUM(COSTS[EXPENSE])",
                "data_type": "number",
            },
        ],
    }

    payload_empty = build_entity_mappings(project_id="p1", model=model_without_synonyms)
    mappings_empty = payload_empty["mappings"]

    col_empty = next(r for r in mappings_empty if r.get("entity_kind") == "column")
    metric_empty = next(r for r in mappings_empty if r.get("entity_kind") == "metric")

    assert col_empty["synonyms"] == []
    assert metric_empty["synonyms"] == []


def test_complexity_tier_survives_end_to_end_with_distinct_values() -> None:
    # Synthetic model: one rule-based (tier 2) metric and one LLM-fallback (tier 5) metric.
    model = {
        "unique_name": "SyntheticModel",
        "datasets": [
            {"unique_name": "FACT", "columns": [{"unique_name": "AMOUNT", "data_type": "number"}]},
        ],
        "metrics": [
            {"unique_name": "RULE_BASED_METRIC", "expression": "SUM(FACT[AMOUNT])", "data_type": "number", "complexity_tier": 2},
            {"unique_name": "AI_ASSISTED_METRIC", "expression": "SOME_COMPLEX_DAX(FACT[AMOUNT])", "data_type": "number", "complexity_tier": 5},
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model)
    mappings = payload["mappings"]

    rule_based = next(r for r in mappings if r.get("source_name") == "RULE_BASED_METRIC")
    ai_assisted = next(r for r in mappings if r.get("source_name") == "AI_ASSISTED_METRIC")

    assert rule_based["complexity_tier"] == 2
    assert ai_assisted["complexity_tier"] == 5

    # A column entity has no complexity_tier concept and should not fake one.
    column_row = next(r for r in mappings if r.get("entity_kind") == "column")
    assert column_row["complexity_tier"] is None

    # And the field survives the API-facing compat serializer unchanged.
    serialized = _compat_serialize_auto_map_entity_mappings(mappings, target_connector="snowflake")
    serialized_rule_based = next(r for r in serialized if r.get("source_name") == "RULE_BASED_METRIC")
    serialized_ai_assisted = next(r for r in serialized if r.get("source_name") == "AI_ASSISTED_METRIC")

    assert serialized_rule_based["complexity_tier"] == 2
    assert serialized_ai_assisted["complexity_tier"] == 5



