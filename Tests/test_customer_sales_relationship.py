import re
import pytest
from unittest.mock import MagicMock
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLRelationship, Cardinality, CrossFilterDirection
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder


def _extract_clause_body(sql: str, clause_name: str) -> str:
    m = re.search(rf"\b{clause_name}\s*\(", sql, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    depth = 1
    for i in range(start, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[start:i]
    return ""


def test_customer_sales_one_to_one_relationship_pk_consistency_static_ddl():
    """
    Regression test (Static DDL):
    CUSTOMER.ID and SALES.ID are unique, and Power BI defines
    an active 1:1 relationship with bidirectional filtering.
    The generated Semantic View must not reference SALES(ID)
    unless SALES(ID) is declared as its primary key.
    """
    ddl = """
    tables (
        CUSTOMER AS SEMABRIDGE.SALES_AND_RETURNS.CUSTOMER_ENRICHED PRIMARY KEY (ID),
        SEMABRIDGE.SALES_AND_RETURNS.SALES PRIMARY KEY (ID)
    )
    relationships (
        CUSTOMER_ID_SALES_ID AS CUSTOMER(ID) REFERENCES SALES(ID)
    )
    """
    tables_block = _extract_clause_body(ddl, "tables")
    relationships_block = _extract_clause_body(ddl, "relationships")

    assert tables_block, "DDL missing TABLES clause"
    assert relationships_block, "DDL missing RELATIONSHIPS clause"

    # Parse declared primary keys from tables block: TABLE_ALIAS -> list of PK columns
    declared_pks = {}
    for line in tables_block.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        pk_match = re.search(r"PRIMARY\s+KEY\s*\((.*?)\)", line, re.IGNORECASE)
        table_def = re.sub(r"PRIMARY\s+KEY\s*\(.*?\)", "", line, flags=re.IGNORECASE).strip()
        if " AS " in table_def.upper():
            alias = re.split(r"\s+AS\s+", table_def, flags=re.IGNORECASE)[0].strip()
        else:
            alias = table_def.split(".")[-1].strip()
        alias = alias.strip('"').upper()

        if pk_match:
            declared_pks[alias] = [c.strip().strip('"').upper() for c in pk_match.group(1).split(",")]
        else:
            declared_pks[alias] = []

    # Check each relationship target table has declared PK
    ref_matches = list(re.finditer(
        r"([A-Za-z0-9_]+)\s*\((.*?)\)\s+REFERENCES\s+([A-Za-z0-9_]+)\s*\((.*?)\)",
        relationships_block,
        re.IGNORECASE,
    ))
    assert len(ref_matches) > 0, f"No relationship references parsed in relationships clause: {relationships_block}"

    for ref in ref_matches:
        from_table = ref.group(1).upper()
        from_col = ref.group(2).strip().strip('"').upper()
        target_table = ref.group(3).upper()
        target_col = ref.group(4).strip().strip('"').upper()

        target_pk_cols = declared_pks.get(target_table, [])
        assert target_col in target_pk_cols, (
            f"Relationship references {target_table}({target_col}), but {target_table} "
            f"does not declare PRIMARY KEY ({target_col}) in the TABLES clause! "
            f"Declared PKs: {declared_pks}"
        )


def test_customer_sales_builder_one_to_one_pk_consistency():
    """
    Builder-level regression test:
    Verify that when TablesClauseBuilder and RelationshipsClauseBuilder process
    a 1:1 relationship between CUSTOMER and SALES (where SALES is a fact table),
    CUSTOMER REFERENCES SALES(ID) is NEVER emitted if SALES has its primary key suppressed.
    """
    customer_ds = SMLDataset(
        unique_name="Customer",
        columns=[
            SMLColumn(unique_name="ID", is_key=True),
            SMLColumn(unique_name="CustomerName"),
        ],
    )
    sales_ds = SMLDataset(
        unique_name="Sales",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="ID", is_key=True),
            SMLColumn(unique_name="Amount"),
        ],
    )

    one_to_one_rel = SMLRelationship(
        unique_name="CUSTOMER_ID_SALES_ID",
        from_dataset="Customer",
        from_columns=["ID"],
        to_dataset="Sales",
        to_columns=["ID"],
        cardinality=Cardinality.ONE_TO_ONE,
        cross_filter=CrossFilterDirection.BOTH,
        is_active=True,
    )

    builder = TablesClauseBuilder(
        config=MagicMock(database="DB", schema_name="SALES_AND_RETURNS"),
        schema_manager=MagicMock(
            _resolve_physical_column_name=lambda d, c, **k: c,
            _collect_physical_source_columns=lambda d: {c.unique_name: "VARCHAR" for c in d.columns},
        ),
        identifier_sanitizer=MagicMock(sanitize_table_name=lambda t: t, sanitize_column=lambda c: c),
        live_schema_metadata={"CUSTOMER": {"ID", "CUSTOMERNAME"}, "SALES": {"ID", "AMOUNT"}},
        behavior=MagicMock(snowflake=MagicMock(auto_add_anchors=False, source_table_mapping=None)),
    )

    model = MagicMock(datasets=[customer_ds, sales_ds], relationships=[one_to_one_rel])
    registry = MagicMock(get_or_create=lambda name, fallback: name.upper())

    table_lines, declared_pks, targets, lookup, by_name, live = builder.build_for_sml(
        model, registry, {}, set()
    )

    rel_builder = RelationshipsClauseBuilder(
        identifier_sanitizer=MagicMock(sanitize_column=lambda c: c),
        schema_manager=MagicMock(_resolve_physical_column_name=lambda d, c, **k: c),
        sanitizer=MagicMock(to_snowflake_relationship_name=lambda n: n),
    )

    dataset_aliases = {"Customer": "CUSTOMER", "Sales": "SALES"}
    rel_lines = rel_builder.build_for_sml(
        model, dataset_aliases, by_name, lookup, declared_pks, targets
    )

    # If any relationship REFERENCES SALES(ID), SALES must declare PRIMARY KEY ("ID")
    sales_pks = declared_pks.get("SALES", [])
    for rel_line in rel_lines:
        if "REFERENCES SALES" in rel_line.upper():
            assert "ID" in [p.upper() for p in sales_pks], (
                f"Relationship clause generated: '{rel_line}', but SALES has no declared primary key! "
                f"Declared PKs: {declared_pks}"
            )
