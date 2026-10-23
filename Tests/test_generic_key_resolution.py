"""Regression tests for SemaBridge Phase 2: Generic Key Resolution."""

import pytest
from unittest.mock import MagicMock
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLRelationship,
    Cardinality,
    CrossFilterDirection,
)
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer


def _create_builder(live_schema=None):
    sanitizer = IdentifierSanitizer(force_uppercase=True, suppress_reserved=True)
    schema_manager = MagicMock(
        _resolve_physical_column_name=lambda d, c, **k: sanitizer.sanitize_column(c),
        _collect_physical_source_columns=lambda d: {sanitizer.sanitize_column(c.unique_name): "VARCHAR" for c in (d.columns or [])},
        warn_if_key_not_unique=lambda *a, **k: None,
    )
    builder = TablesClauseBuilder(
        sanitizer,
        schema_manager,
        MagicMock(database="TEST_DB", schema_name="TEST_SCHEMA"),
        MagicMock(snowflake=MagicMock(auto_add_anchors=False, source_table_mapping=None)),
        live_schema_metadata=live_schema or {},
    )
    return builder, sanitizer, schema_manager


def test_fact_table_1_to_1_reference_preserves_key_without_weakening_safeguard():
    """Requirement 5 & 6: Fact table targeted by 1:1 relationship receives PRIMARY KEY;

    unreferenced fact table has primary key suppressed.
    """
    builder, sanitizer, schema_mgr = _create_builder()

    customer_ds = SMLDataset(
        unique_name="Customer",
        columns=[SMLColumn(unique_name="ID", is_key=True), SMLColumn(unique_name="Name")],
    )
    # Referenced fact table
    sales_ds = SMLDataset(
        unique_name="Sales",
        is_fact=True,
        columns=[SMLColumn(unique_name="ID", is_key=True), SMLColumn(unique_name="Amount")],
    )
    # Unreferenced fact table
    returns_ds = SMLDataset(
        unique_name="Returns",
        is_fact=True,
        columns=[SMLColumn(unique_name="ReturnID"), SMLColumn(unique_name="Units")],
    )

    rel_cust_sales = SMLRelationship(
        unique_name="REL_CUSTOMER_SALES",
        from_dataset="Customer",
        from_columns=["ID"],
        to_dataset="Sales",
        to_columns=["ID"],
        cardinality=Cardinality.ONE_TO_ONE,
        cross_filter=CrossFilterDirection.BOTH,
        is_active=True,
    )
    rel_returns_sales = SMLRelationship(
        unique_name="REL_RETURNS_SALES",
        from_dataset="Returns",
        from_columns=["Units"],
        to_dataset="Sales",
        to_columns=["Amount"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True,
    )

    model = SMLModel(
        unique_name="TestModel",
        datasets=[customer_ds, sales_ds, returns_ds],
        relationships=[rel_cust_sales, rel_returns_sales],
    )

    registry = AliasRegistry()
    lines, declared_pk, targets, lookup, by_name, live = builder.build_for_sml(
        model, registry, {}, {"Customer", "Sales", "Returns"}
    )

    table_lines_by_alias = {l.strip().split()[0]: l for l in lines}

    # Sales is referenced by 1:1 on ID -> Must declare PRIMARY KEY ("ID")
    assert "PRIMARY KEY (\"ID\")" in table_lines_by_alias["SALES"]
    assert declared_pk.get("SALES") == ["ID"]

    # Returns is an unreferenced fact table -> Must NOT declare any PRIMARY KEY
    assert "PRIMARY KEY" not in table_lines_by_alias["RETURNS"]
    assert "RETURNS" not in declared_pk or not declared_pk["RETURNS"]


def test_dimension_multiple_referenced_columns_receive_unique_constraints():
    """Requirement 1, 3, 7: Table referenced on multiple distinct columns

    declares PRIMARY KEY on the primary target and UNIQUE on secondary targets.
    """
    builder, sanitizer, schema_mgr = _create_builder()

    associated_prod_ds = SMLDataset(
        unique_name="Associated Product",
        columns=[
            SMLColumn(unique_name="ProductID", is_key=True),
            SMLColumn(unique_name="Product"),
            SMLColumn(unique_name="Category"),
        ],
    )
    customer_ds = SMLDataset(
        unique_name="Customer",
        columns=[SMLColumn(unique_name="ID", is_key=True), SMLColumn(unique_name="Product")],
    )
    assoc_ds = SMLDataset(
        unique_name="Association",
        columns=[SMLColumn(unique_name="RightItemSetId")],
    )

    # 1. Many-to-One to Associated Product[ProductID]
    rel1 = SMLRelationship(
        unique_name="REL_ASSOC_AP_ID",
        from_dataset="Association",
        from_columns=["RightItemSetId"],
        to_dataset="Associated Product",
        to_columns=["ProductID"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True,
    )
    # 2. Many-to-One to Associated Product[Product]
    rel2 = SMLRelationship(
        unique_name="REL_CUST_AP_PROD",
        from_dataset="Customer",
        from_columns=["Product"],
        to_dataset="Associated Product",
        to_columns=["Product"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True,
    )

    model = SMLModel(
        unique_name="TestModel",
        datasets=[associated_prod_ds, customer_ds, assoc_ds],
        relationships=[rel1, rel2],
    )

    registry = AliasRegistry()
    lines, declared_pk, targets, lookup, by_name, live = builder.build_for_sml(
        model, registry, {}, {"Associated Product", "Customer", "Association"}
    )

    table_lines_by_alias = {l.strip().split()[0]: l for l in lines}
    ap_line = table_lines_by_alias["ASSOCIATED_PRODUCT"]

    # Both PRIMARY KEY and UNIQUE must be present on the same table
    assert 'PRIMARY KEY ("PRODUCTID")' in ap_line
    assert 'UNIQUE ("PRODUCT")' in ap_line
    # Both keys must be registered in targets for relationship reference mapping
    assert targets.get(("Associated Product", "PRODUCTID")) == "ASSOCIATED_PRODUCT"
    assert targets.get(("Associated Product", "PRODUCT")) == "ASSOCIATED_PRODUCT"


def test_composite_key_relationship_preserved():
    """Requirement 7: Composite key target is preserved in PRIMARY KEY clause."""
    builder, sanitizer, schema_mgr = _create_builder()

    calendar_ds = SMLDataset(
        unique_name="CalendarPeriod",
        columns=[
            SMLColumn(unique_name="Year", is_key=True),
            SMLColumn(unique_name="Period", is_key=True),
        ],
    )
    sales_ds = SMLDataset(
        unique_name="Sales",
        columns=[SMLColumn(unique_name="OrderYear"), SMLColumn(unique_name="OrderPeriod")],
    )
    composite_rel = SMLRelationship(
        unique_name="REL_SALES_PERIOD",
        from_dataset="Sales",
        from_columns=["OrderYear", "OrderPeriod"],
        to_dataset="CalendarPeriod",
        to_columns=["Year", "Period"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True,
    )

    model = SMLModel(
        unique_name="TestModel",
        datasets=[calendar_ds, sales_ds],
        relationships=[composite_rel],
    )

    registry = AliasRegistry()
    lines, declared_pk, targets, lookup, by_name, live = builder.build_for_sml(
        model, registry, {}, {"CalendarPeriod", "Sales"}
    )

    table_lines_by_alias = {l.strip().split()[0]: l for l in lines}
    cal_line = table_lines_by_alias["CALENDARPERIOD"]

    assert 'PRIMARY KEY ("YEAR", "PERIOD")' in cal_line
    assert declared_pk.get("CALENDARPERIOD") == ["YEAR", "PERIOD"]


def test_unverified_many_to_many_does_not_generate_unique_constraint():
    """Requirement 4: When uniqueness cannot be established (e.g. Many-to-Many

    with is_key=False), no invalid UNIQUE constraint is generated.
    """
    builder, sanitizer, schema_mgr = _create_builder()

    tag_ds = SMLDataset(
        unique_name="Tag",
        columns=[
            SMLColumn(unique_name="TagID", is_key=True),
            SMLColumn(unique_name="TagName", is_key=False),  # Non-key
        ],
    )
    article_ds = SMLDataset(
        unique_name="Article",
        columns=[SMLColumn(unique_name="TagRef")],
    )
    m2m_rel = SMLRelationship(
        unique_name="REL_ARTICLE_TAG",
        from_dataset="Article",
        from_columns=["TagRef"],
        to_dataset="Tag",
        to_columns=["TagName"],
        cardinality=Cardinality.MANY_TO_MANY,  # M:M does not guarantee uniqueness
        is_active=True,
    )

    model = SMLModel(
        unique_name="TestModel",
        datasets=[tag_ds, article_ds],
        relationships=[m2m_rel],
    )

    registry = AliasRegistry()
    lines, declared_pk, targets, lookup, by_name, live = builder.build_for_sml(
        model, registry, {}, {"Tag", "Article"}
    )

    table_lines_by_alias = {l.strip().split()[0]: l for l in lines}
    tag_line = table_lines_by_alias["TAG"]

    # TagName is M:M and not a key column -> MUST NOT have a UNIQUE constraint
    assert 'UNIQUE ("TAGNAME")' not in tag_line
    assert 'PRIMARY KEY ("TAGNAME")' not in tag_line


def test_snowflake_compatibility_metadata_and_ddl_representation():
    """Verify metadata preservation and Snowflake DDL compatibility:

    - One-to-One, Both, Active
    - One-to-One, Both, Inactive
    - Many-to-One, Single, Active
    - Validates that internal SML metadata preserves cardinality, cross-filter, and active status
    - Validates that generated DDL conforms to Snowflake's syntax (TABLES PK/UNIQUE + RELATIONSHIPS REFERENCES)
    """
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from validate_semantic_view import parse_ddl, static_checks, Report

    builder, sanitizer, schema_mgr = _create_builder()
    ddl_sanitizer = SemanticDDLSanitizer(sanitizer)

    customer_ds = SMLDataset(
        unique_name="Customer",
        columns=[SMLColumn(unique_name="ID", is_key=True), SMLColumn(unique_name="Product")],
    )
    sales_ds = SMLDataset(
        unique_name="Sales",
        is_fact=True,
        columns=[SMLColumn(unique_name="ID", is_key=True), SMLColumn(unique_name="Amount")],
    )
    product_ds = SMLDataset(
        unique_name="Product",
        columns=[SMLColumn(unique_name="ProductID", is_key=True), SMLColumn(unique_name="Product")],
    )
    assoc_prod_ds = SMLDataset(
        unique_name="Associated Product",
        columns=[SMLColumn(unique_name="ProductID", is_key=True), SMLColumn(unique_name="Product")],
    )

    # 1. One-to-One, Both, Active
    rel_active_1to1 = SMLRelationship(
        unique_name="REL_CUSTOMER_SALES",
        from_dataset="Customer",
        from_columns=["ID"],
        to_dataset="Sales",
        to_columns=["ID"],
        cardinality=Cardinality.ONE_TO_ONE,
        cross_filter=CrossFilterDirection.BOTH,
        is_active=True,
    )

    # 2. One-to-One, Both, Inactive
    rel_inactive_1to1 = SMLRelationship(
        unique_name="REL_PRODUCT_AP",
        from_dataset="Product",
        from_columns=["Product"],
        to_dataset="Associated Product",
        to_columns=["Product"],
        cardinality=Cardinality.ONE_TO_ONE,
        cross_filter=CrossFilterDirection.BOTH,
        is_active=False,
    )

    # 3. Many-to-One, Single, Active
    rel_active_m21 = SMLRelationship(
        unique_name="REL_CUSTOMER_AP",
        from_dataset="Customer",
        from_columns=["Product"],
        to_dataset="Associated Product",
        to_columns=["Product"],
        cardinality=Cardinality.MANY_TO_ONE,
        cross_filter=CrossFilterDirection.SINGLE,
        is_active=True,
    )

    model = SMLModel(
        unique_name="SnowflakeCompatModel",
        datasets=[customer_ds, sales_ds, product_ds, assoc_prod_ds],
        relationships=[rel_active_1to1, rel_inactive_1to1, rel_active_m21],
    )

    # Step A: Verify internal SML metadata is preserved and unchanged
    assert rel_active_1to1.cardinality == Cardinality.ONE_TO_ONE
    assert rel_active_1to1.cross_filter == CrossFilterDirection.BOTH
    assert rel_active_1to1.is_active is True

    assert rel_inactive_1to1.cardinality == Cardinality.ONE_TO_ONE
    assert rel_inactive_1to1.cross_filter == CrossFilterDirection.BOTH
    assert rel_inactive_1to1.is_active is False

    assert rel_active_m21.cardinality == Cardinality.MANY_TO_ONE
    assert rel_active_m21.cross_filter == CrossFilterDirection.SINGLE
    assert rel_active_m21.is_active is True

    # Step B: Generate Snowflake DDL
    registry = AliasRegistry()
    tbl_lines, declared_pks, targets, lookup, by_name, live = builder.build_for_sml(
        model, registry, {}, {"Customer", "Sales", "Product", "Associated Product"}
    )
    rbuilder = RelationshipsClauseBuilder(sanitizer, schema_mgr, ddl_sanitizer)
    rel_lines = rbuilder.build_for_sml(
        model, registry.dataset_aliases, by_name, lookup, declared_pks, targets
    )

    # Both active and inactive relationships are emitted in DDL
    assert len(rel_lines) == 3

    # Build semantic view DDL
    tbl_joined = ",\n".join(tbl_lines)
    rel_joined = ",\n".join(rel_lines)
    ddl = f"TABLES (\n{tbl_joined}\n)\nRELATIONSHIPS (\n{rel_joined}\n)"

    # Step C: Validate against Snowflake Semantic View syntax rules
    tables_parsed, rels_parsed = parse_ddl(ddl)
    report = Report()
    static_checks(tables_parsed, rels_parsed, report)

    n_fail = sum(1 for r in report.rows if r[0] == "FAIL")
    n_pass = sum(1 for r in report.rows if r[0] == "PASS")

    assert n_fail == 0, f"Expected 0 failures in Snowflake DDL validation, got {n_fail}: {report.rows}"
    assert n_pass == 3

