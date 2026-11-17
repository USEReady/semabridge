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
    # Product.Manufacturer and Customer.Manufacturer live in DIFFERENT logical
    # tables: a Snowflake semantic view names them PRODUCT.MANUFACTURER and
    # CUSTOMER.MANUFACTURER, so they do not collide (the emitter dedupes per
    # logical table too). Earlier versions used one flat namespace.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "Product", "columns": [{"unique_name": "Manufacturer", "data_type": "string"}]},
            {"unique_name": "Customer", "columns": [{"unique_name": "Manufacturer", "data_type": "string"}]},
        ],
        "metrics": [],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    columns = [row for row in payload["mappings"] if row.get("entity_kind") == "column"]
    assert len(columns) == 2
    for col in columns:
        assert col["target_name"] == "MANUFACTURER"
        assert col["collision_detected"] is False
        assert col["validation_status"] == "valid"

    # Two fields of the SAME table that sanitize to one name do collide; the
    # names are not mutated and every row gets unique, to_alias-form options.
    same_table = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {
                "unique_name": "Product",
                "columns": [
                    {"unique_name": "Margin %", "data_type": "string"},
                    {"unique_name": "Margin PCT", "data_type": "string", "type": "calculated"},
                ],
            },
        ],
        "metrics": [],
    }
    payload = build_entity_mappings(project_id="p1", model=same_table, target_connector="snowflake")
    columns = [row for row in payload["mappings"] if row.get("entity_kind") == "column"]
    firsts = []
    for col in columns:
        assert col["target_name"] == "MARGIN_PCT"  # emitter's to_alias: % -> PCT
        assert col["collision_detected"] is True
        assert col["validation_code"] == "NAME_COLLISION"
        first = col["resolution_suggestions"][0]
        assert first != "MARGIN_PCT"
        assert first in ("PRODUCT_MARGIN_PCT", "COL_MARGIN_PCT")
        firsts.append(first)
    assert len(set(firsts)) == 2  # Resolve All yields unique names

    # Overriding one of them manually makes the other unique.
    existing_mappings = {
        "datasets.Product.columns.Margin %": {"id": "m1", "target_name": "OVERRIDDEN_MARGIN", "is_user_edited": True},
    }
    payload_override = build_entity_mappings(
        project_id="p1", model=same_table, existing_mappings=existing_mappings, target_connector="snowflake"
    )
    cols = {row["source_path"]: row for row in payload_override["mappings"] if row.get("entity_kind") == "column"}
    assert cols["datasets.Product.columns.Margin %"]["collision_detected"] is False
    assert cols["datasets.Product.columns.Margin PCT"]["collision_detected"] is False
    assert cols["datasets.Product.columns.Margin PCT"]["validation_status"] == "valid"


def test_transitive_collision() -> None:
    # Product.Manufacturer, Product.Product_Manufacturer, Customer.Manufacturer:
    # every name is unique within its own logical table -> no collision.
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
            {"unique_name": "Customer", "columns": [{"name": "Manufacturer", "data_type": "string"}]},
        ],
    }
    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    columns = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}
    assert len(columns) == 3
    assert columns["datasets.Product.columns.Manufacturer"]["target_name"] == "MANUFACTURER"
    assert columns["datasets.Customer.columns.Manufacturer"]["target_name"] == "MANUFACTURER"
    assert columns["datasets.Product.columns.Product_Manufacturer"]["target_name"] == "PRODUCT_MANUFACTURER"
    assert not any(c["collision_detected"] for c in columns.values())


def test_collisions_remain_unresolved_until_user_action() -> None:
    # Same-table duplicates stay unresolved (names unchanged) until the user
    # acts; suggestions are in the emitter's to_alias form and unique.
    model = {
        "unique_name": "Core_Finance_v1",
        "datasets": [
            {"unique_name": "BU", "columns": [{"unique_name": "Executive_id", "data_type": "integer"}]},
            {"unique_name": "Executive", "columns": [{"unique_name": "ID", "data_type": "integer"}]},
            {
                "unique_name": "Industry",
                "columns": [
                    {"unique_name": "% ID", "data_type": "integer"},
                    {"unique_name": "PCT ID", "data_type": "integer", "type": "calculated"},
                ],
            },
        ],
    }

    payload = build_entity_mappings(project_id="p1", model=model, target_connector="snowflake")
    cols = {row["source_path"]: row for row in payload["mappings"] if row.get("entity_kind") == "column"}

    assert cols["datasets.BU.columns.Executive_id"]["collision_detected"] is False
    assert cols["datasets.Executive.columns.ID"]["collision_detected"] is False

    pair = [cols["datasets.Industry.columns.% ID"], cols["datasets.Industry.columns.PCT ID"]]
    suggestions = set()
    for c in pair:
        assert c["target_name"] == "PCT_ID"  # unchanged until the user acts
        assert c["collision_detected"] is True
        assert c["validation_status"] == "collision"
        assert c["resolution_suggestions"][0] in ("INDUSTRY_PCT_ID", "COL_PCT_ID")
        suggestions.add(c["resolution_suggestions"][0])
    assert len(suggestions) == 2


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



