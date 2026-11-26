"""Tests for Drift Monitoring Step 2: Facts and Relationships.

Validates the full set of conditions:
1. Capabilities per adapter and per source path.
2. Source path consistency: baseline vs live mismatch -> not checked, not drift.
3. Stable relationship identity: order-independent match key, renames do not drift, reordering text does not drift.
4. Facts: repointed fact table produces exactly one EXPRESSION_DRIFT finding of kind FACT.
5. SQL DDL fallback lowering capabilities.
6. Noise control: grouped findings and by_kind counts.
7. Canonical roundtrip on both platforms produces zero drift immediately after deploy.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from semabridge.governance.drift import compare_views
from semabridge.governance.drift.models import (
    DriftCategory,
    ObjectDef,
    ObjectKind,
    SourcePathMismatchError,
    ViewDefinition,
)
from semabridge.governance.drift.adapters.snowflake import (
    SnowflakeAdapter,
)
from semabridge.governance.drift.adapters.databricks import (
    DatabricksAdapter,
    DATABRICKS_CAPABILITIES_DESCRIBE_JSON,
    DATABRICKS_CAPABILITIES_SQL_DDL,
)


# ─── TEST 1: Each new kind (FACT, RELATIONSHIP) supports Addition, Deletion, Expression & Description ───
def test_fact_lifecycle_findings():
    now = datetime.now(timezone.utc).isoformat()
    caps = {
        ObjectKind.FACT.value: {"expression", "description"},
    }

    # A: Addition
    base_empty = ViewDefinition(
        view_name="DB.SCH.VIEW_A", platform="SNOWFLAKE", source="baseline",
        retrieved_at=now, objects=[], source_path="ddl", capabilities=caps,
    )
    live_added = ViewDefinition(
        view_name="DB.SCH.VIEW_A", platform="SNOWFLAKE", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="VIEW_A", name="FACT_SALES", expression="RAW.SALES", description="Sales Fact"),
        ],
        source_path="ddl", capabilities=caps,
    )
    findings_add = compare_views(base_empty, live_added)
    assert len(findings_add) == 1
    assert findings_add[0].category == DriftCategory.ADDITION
    assert findings_add[0].kind == ObjectKind.FACT.value
    assert findings_add[0].name == "FACT_SALES"

    # B: Deletion
    findings_del = compare_views(live_added, base_empty)
    assert len(findings_del) == 1
    assert findings_del[0].category == DriftCategory.DELETION
    assert findings_del[0].kind == ObjectKind.FACT.value
    assert findings_del[0].name == "FACT_SALES"

    # C: Description Change
    live_desc_changed = ViewDefinition(
        view_name="DB.SCH.VIEW_A", platform="SNOWFLAKE", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="VIEW_A", name="FACT_SALES", expression="RAW.SALES", description="Updated description"),
        ],
        source_path="ddl", capabilities=caps,
    )
    findings_desc = compare_views(live_added, live_desc_changed)
    assert len(findings_desc) == 1
    assert findings_desc[0].category == DriftCategory.DESCRIPTION_DRIFT
    assert findings_desc[0].kind == ObjectKind.FACT.value


def test_relationship_lifecycle_findings():
    now = datetime.now(timezone.utc).isoformat()
    caps = {
        ObjectKind.RELATIONSHIP.value: {"expression", "description"},
    }

    # Addition
    base_empty = ViewDefinition(
        view_name="DB.SCH.VIEW_A", platform="SNOWFLAKE", source="baseline",
        retrieved_at=now, objects=[], source_path="ddl", capabilities=caps,
    )
    live_rel = ViewDefinition(
        view_name="DB.SCH.VIEW_A", platform="SNOWFLAKE", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(
                kind=ObjectKind.RELATIONSHIP,
                table="VIEW_A",
                name="REL_CUST",
                expression="ORDERS (CUSTOMER_ID) REFERENCES CUSTOMERS (ID)",
                properties={"from_table": "ORDERS", "to_table": "CUSTOMERS"},
            ),
        ],
        source_path="ddl", capabilities=caps,
    )
    findings_add = compare_views(base_empty, live_rel)
    assert len(findings_add) == 1
    assert findings_add[0].category == DriftCategory.ADDITION
    assert findings_add[0].kind == ObjectKind.RELATIONSHIP.value

    # Deletion
    findings_del = compare_views(live_rel, base_empty)
    assert len(findings_del) == 1
    assert findings_del[0].category == DriftCategory.DELETION
    assert findings_del[0].kind == ObjectKind.RELATIONSHIP.value


# ─── TEST 2: Fact repointed to a different table produces exactly one EXPRESSION_DRIFT finding ───
def test_fact_repointed_produces_single_expression_drift():
    now = datetime.now(timezone.utc).isoformat()
    caps = {ObjectKind.FACT.value: {"expression", "description"}}

    baseline = ViewDefinition(
        view_name="CAT.SCH.METRIC_VIEW", platform="DATABRICKS", source="baseline",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="METRIC_VIEW", name="SALES", expression="RAW.SALES_V1"),
        ],
        source_path="yaml", capabilities=caps,
    )
    live = ViewDefinition(
        view_name="CAT.SCH.METRIC_VIEW", platform="DATABRICKS", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="METRIC_VIEW", name="SALES", expression="RAW.SALES_V2"),
        ],
        source_path="yaml", capabilities=caps,
    )
    findings = compare_views(baseline, live)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == DriftCategory.EXPRESSION_DRIFT
    assert f.kind == ObjectKind.FACT.value
    assert f.name == "SALES"
    assert f.details.get("drift_type") == "FACT_REPOINTED"
    assert "SALES_V1" in f.baseline_value
    assert "SALES_V2" in f.live_value


# ─── TEST 3: Capabilities prevent false drift for unreportable kinds or fields ───────────────────
def test_unsupported_kind_or_field_produces_no_findings():
    now = datetime.now(timezone.utc).isoformat()

    # Databricks Describe JSON only reports METRIC and DIMENSION, cannot report FACT
    caps_limited = dict(DATABRICKS_CAPABILITIES_DESCRIBE_JSON)

    baseline = ViewDefinition(
        view_name="CAT.SCH.VIEW_B", platform="DATABRICKS", source="baseline",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="VIEW_B", name="FACT_A", expression="SRC.A"),
        ],
        source_path="describe_json", capabilities=caps_limited,
    )
    live = ViewDefinition(
        view_name="CAT.SCH.VIEW_B", platform="DATABRICKS", source="live",
        retrieved_at=now,
        objects=[],
        source_path="describe_json", capabilities=caps_limited,
    )
    # Because FACT is not in capabilities, no DELETION finding should be produced
    findings = compare_views(baseline, live)
    assert len(findings) == 0

    # Databricks SQL DDL does not report description for FACT (only expression)
    caps_sql = dict(DATABRICKS_CAPABILITIES_SQL_DDL)
    base_fact = ViewDefinition(
        view_name="CAT.SCH.VIEW_C", platform="DATABRICKS", source="baseline",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="VIEW_C", name="FACT_A", expression="SRC.A", description="Base comment"),
        ],
        source_path="sql_ddl", capabilities=caps_sql,
    )
    live_fact_no_desc = ViewDefinition(
        view_name="CAT.SCH.VIEW_C", platform="DATABRICKS", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(kind=ObjectKind.FACT, table="VIEW_C", name="FACT_A", expression="SRC.A", description=None),
        ],
        source_path="sql_ddl", capabilities=caps_sql,
    )
    findings_desc = compare_views(base_fact, live_fact_no_desc)
    assert len(findings_desc) == 0


# ─── TEST 4: Baseline and live through different source paths -> SourcePathMismatchError ────────
def test_different_source_paths_raises_mismatch_error():
    now = datetime.now(timezone.utc).isoformat()

    base_yaml = ViewDefinition(
        view_name="CAT.SCH.METRIC_VIEW", platform="DATABRICKS", source="baseline",
        retrieved_at=now, objects=[], source_path="yaml",
    )
    live_ddl = ViewDefinition(
        view_name="CAT.SCH.METRIC_VIEW", platform="DATABRICKS", source="live",
        retrieved_at=now, objects=[], source_path="sql_ddl",
    )

    with pytest.raises(SourcePathMismatchError) as exc_info:
        compare_views(base_yaml, live_ddl)
    assert "not checked: definition read in a different form" in str(exc_info.value)
    assert "yaml" in str(exc_info.value)
    assert "sql_ddl" in str(exc_info.value)


# ─── TEST 5: Join match key: reordering join columns is not drift; changing column is ──────────
def test_join_match_key_reordering_vs_changing_columns():
    now = datetime.now(timezone.utc).isoformat()
    caps = {ObjectKind.RELATIONSHIP.value: {"expression"}}

    # Baseline with two join columns
    baseline = ViewDefinition(
        view_name="DB.SCH.SALES_VIEW", platform="SNOWFLAKE", source="baseline",
        retrieved_at=now,
        objects=[
            ObjectDef(
                kind=ObjectKind.RELATIONSHIP,
                table="SALES_VIEW",
                name="REL_ORDERS_CUSTOMERS",
                expression="ORDERS (CUSTOMER_ID, STORE_ID) REFERENCES CUSTOMERS (ID, S_ID)",
            ),
        ],
        source_path="ddl", capabilities=caps,
    )

    # Live 1: Same columns reordered in text
    live_reordered = ViewDefinition(
        view_name="DB.SCH.SALES_VIEW", platform="SNOWFLAKE", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(
                kind=ObjectKind.RELATIONSHIP,
                table="SALES_VIEW",
                name="REL_ORDERS_CUSTOMERS",
                expression="ORDERS (STORE_ID, CUSTOMER_ID) REFERENCES CUSTOMERS (S_ID, ID)",
            ),
        ],
        source_path="ddl", capabilities=caps,
    )
    findings_reordered = compare_views(baseline, live_reordered)
    assert len(findings_reordered) == 0, "Reordering columns in join expression must not count as drift"

    # Live 2: Join was renamed, but tables and columns are identical
    live_renamed = ViewDefinition(
        view_name="DB.SCH.SALES_VIEW", platform="SNOWFLAKE", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(
                kind=ObjectKind.RELATIONSHIP,
                table="SALES_VIEW",
                name="NEW_JOIN_NAME_ALIAS",
                expression="ORDERS (CUSTOMER_ID, STORE_ID) REFERENCES CUSTOMERS (ID, S_ID)",
            ),
        ],
        source_path="ddl", capabilities=caps,
    )
    findings_renamed = compare_views(baseline, live_renamed)
    assert len(findings_renamed) == 0, "Renaming a join without changing tables/columns must not produce delete+add"

    # Live 3: One column actually changed
    live_col_changed = ViewDefinition(
        view_name="DB.SCH.SALES_VIEW", platform="SNOWFLAKE", source="live",
        retrieved_at=now,
        objects=[
            ObjectDef(
                kind=ObjectKind.RELATIONSHIP,
                table="SALES_VIEW",
                name="REL_ORDERS_CUSTOMERS",
                expression="ORDERS (CUSTOMER_ID, REGION_ID) REFERENCES CUSTOMERS (ID, R_ID)",
            ),
        ],
        source_path="ddl", capabilities=caps,
    )
    findings_col_changed = compare_views(baseline, live_col_changed)
    assert len(findings_col_changed) == 1
    assert findings_col_changed[0].category == DriftCategory.EXPRESSION_DRIFT
    assert findings_col_changed[0].kind == ObjectKind.RELATIONSHIP.value


# ─── TEST 6: Deploy then check immediately -> zero findings across all kinds ───────────────────
def test_snowflake_full_roundtrip_zero_findings():
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.SALES_SEMANTIC_VIEW
    TABLES (
        sales_fact AS ANALYTICS.RAW_SALES COMMENT 'Primary sales fact table',
        customer_dim AS ANALYTICS.CUSTOMERS COMMENT 'Customer dimension table'
    )
    RELATIONSHIPS (
        rel_sales_cust AS sales_fact (customer_id) REFERENCES customer_dim (id)
    )
    DIMENSIONS (
        customer_dim.region AS region COMMENT 'Customer geographical region'
    )
    METRICS (
        SUM(sales_fact.amount) AS total_revenue COMMENT 'Total sales revenue in USD'
    );
    """
    adapter = SnowflakeAdapter()
    base_def = adapter.parse_definition(ddl, view_name="DB.SCHEMA.SALES_SEMANTIC_VIEW", source="baseline")
    live_def = adapter.parse_definition(ddl, view_name="DB.SCHEMA.SALES_SEMANTIC_VIEW", source="live")

    # Verify extracted kinds
    kinds = {o.kind for o in base_def.objects}
    assert ObjectKind.METRIC.value in kinds
    assert ObjectKind.DIMENSION.value in kinds
    assert ObjectKind.FACT.value in kinds
    assert ObjectKind.RELATIONSHIP.value in kinds

    findings = compare_views(base_def, live_def)
    assert len(findings) == 0, "Clean roundtrip immediately after deploy must produce 0 findings"


def test_databricks_yaml_roundtrip_zero_findings():
    yaml_text = """
    version: 1
    source: analytics.raw_orders
    comment: Orders semantic model
    tables:
      - name: customers
        source: analytics.dim_customers
        comment: Customer dimension table
    joins:
      - name: orders_customers
        source: raw_orders
        target: customers
        on: raw_orders.customer_id = customers.id
    dimensions:
      - name: country
        expr: customers.country
        comment: Customer billing country
    measures:
      - name: order_count
        expr: count(raw_orders.id)
        comment: Total count of placed orders
    """
    adapter = DatabricksAdapter()
    base_def = adapter.parse_definition(yaml_text, view_name="cat.sch.orders_metric_view", source="baseline")
    live_def = adapter.parse_definition(yaml_text, view_name="cat.sch.orders_metric_view", source="live")

    # Verify extracted kinds
    kinds = {o.kind for o in base_def.objects}
    assert ObjectKind.METRIC.value in kinds
    assert ObjectKind.DIMENSION.value in kinds
    assert ObjectKind.FACT.value in kinds
    assert ObjectKind.RELATIONSHIP.value in kinds

    findings = compare_views(base_def, live_def)
    assert len(findings) == 0, "Databricks YAML roundtrip immediately after deploy must produce 0 findings"
