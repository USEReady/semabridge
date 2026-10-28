import pytest
"""Deterministic table binding, saved bindings, and no-duplicate table creation."""

from semabridge.connectors.schema_manager import SnowflakeSchemaManager, clear_schema_cache
from semabridge.connectors.table_binding_resolver import (
    LiveSchemaMetadata,
    compute_column_compatibility,
    resolve_dataset_to_physical_table,
)
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import SMLColumn, SMLDataset, SMLModel
from semabridge.utils.identifiers import IdentifierSanitizer

SALES_COLS = ["ProductID", "Date", "Zip", "Units", "Revenue"]
SALES_LIVE = {"PRODUCTID", "COL_DATE", "ZIP", "UNITS", "REVENUE"}


def _meta(order, row_counts=None):
    meta = LiveSchemaMetadata()
    for table in order:
        meta[table] = set(SALES_LIVE)
    meta.row_counts.update(row_counts or {})
    return meta


def test_names_normalize_case_space_underscore_and_punctuation():
    assert compute_column_compatibility(
        ["Business Area", "Business Area ID"], {"BUSINESS_AREA", "BUSINESS_AREA_ID"}
    ) == 1.0
    assert compute_column_compatibility(
        ["Sales Region", "Country/Region", "Country/Region ID"],
        {"SALES_REGION", "COUNTRY_REGION", "COUNTRY_REGION_ID"},
    ) == 1.0
    meta = LiveSchemaMetadata({"BUSINESS_AREA": {"BUSINESS_AREA", "BUSINESS_AREA_ID"}})
    assert resolve_dataset_to_physical_table("Business Area", ["Business Area", "Business Area ID"], meta) == "BUSINESS_AREA"


def test_exact_name_wins_regardless_of_row_counts_and_metadata_order():
    # Row counts never decide: the table named exactly like the dataset wins.
    for counts in ({"SALESFACT": 1, "SALES_FACT": 1_260_752}, {"SALESFACT": 1_260_752, "SALES_FACT": 1}, {}):
        for order in (["SALESFACT", "SALES_FACT"], ["SALES_FACT", "SALESFACT"]):
            meta = _meta(order, counts)
            assert resolve_dataset_to_physical_table("SalesFact", SALES_COLS, meta) == "SALESFACT"
            assert meta.binding_log["SALESFACT"]["matched_by"] == "exact name"


def test_exact_name_with_incompatible_columns_falls_through_to_column_match():
    meta = LiveSchemaMetadata({
        "SALESFACT": {"ID", "PAYLOAD"},
        "SALES_FACT": {"PRODUCT_ID", "DATE", "ZIP", "UNITS", "REVENUE"},
    })
    assert resolve_dataset_to_physical_table("SalesFact", SALES_COLS, meta) == "SALES_FACT"
    assert meta.binding_log["SALESFACT"]["matched_by"] == "columns"


def test_equal_matches_without_exact_name_stop_with_the_candidates():
    from semabridge.connectors.table_binding_resolver import TableBindingAmbiguityError

    for order in (["SALES_FACT", "SALES__FACT"], ["SALES__FACT", "SALES_FACT"]):
        meta = _meta(order, {"SALES_FACT": 1_260_752, "SALES__FACT": 1})
        with pytest.raises(TableBindingAmbiguityError) as err:
            resolve_dataset_to_physical_table("SalesFact", SALES_COLS, meta)
        assert set(err.value.candidates) == {"SALES_FACT", "SALES__FACT"}
    # A saved binding settles it.
    meta = _meta(["SALES_FACT", "SALES__FACT"])
    meta.pinned_bindings["SALESFACT"] = "SALES_FACT"
    assert resolve_dataset_to_physical_table("SalesFact", SALES_COLS, meta) == "SALES_FACT"


def test_tie_without_row_counts_is_alphabetical():
    for order in (["SALES_FACT", "SALESFACT"], ["SALESFACT", "SALES_FACT"]):
        assert resolve_dataset_to_physical_table("SalesFact", SALES_COLS, _meta(order)) == "SALESFACT"


def test_saved_binding_wins_while_its_table_exists():
    meta = _meta(["SALES_FACT", "SALESFACT"], {"SALESFACT": 1, "SALES_FACT": 5})
    meta.pinned_bindings["SALESFACT"] = "SALESFACT"
    assert resolve_dataset_to_physical_table("SalesFact", SALES_COLS, meta) == "SALESFACT"
    assert meta.binding_log["SALESFACT"]["matched_by"] == "saved"

    gone = _meta(["SALES_FACT"])
    gone.pinned_bindings["SALESFACT"] = "SALESFACT_OLD"
    assert resolve_dataset_to_physical_table("SalesFact", SALES_COLS, gone) == "SALES_FACT"


def test_name_match_with_incompatible_columns_blocks_instead_of_reusing():
    from semabridge.connectors.table_binding_resolver import check_table_name_collision_blocker
    meta = LiveSchemaMetadata({"SALES": {"RANDOM_A", "RANDOM_B"}})
    # Incompatible table is never reused
    assert resolve_dataset_to_physical_table("Sales", ["Opportunity ID", "Sales Date"], meta) == "Sales"
    blocker = check_table_name_collision_blocker("Sales", ["Opportunity ID", "Sales Date"], meta)
    assert blocker is not None
    assert "already exists with different columns" in blocker["message"]


class _Cursor:
    def __init__(self):
        self.rows = []

    def fetchall(self):
        return self.rows


class _Conn:
    """Answers the read-only queries plan_source_tables issues; records the rest."""

    def __init__(self, tables, columns, row_counts):
        self.tables, self.columns, self.row_counts = tables, columns, row_counts
        self.executed = []

    def _execute_sql(self, cursor, sql, params=None, context=""):
        self.executed.append(sql)
        if context == "SHOW TABLES":
            cursor.rows = [(None, t) for t in self.tables]
        elif context == "SHOW VIEWS":
            cursor.rows = []
        elif context == "FETCH SCHEMA METADATA":
            cursor.rows = [(t, c) for t, cols in self.columns.items() for c in cols]
        elif context == "FETCH TABLE ROW COUNTS":
            cursor.rows = list(self.row_counts.items())
        else:
            cursor.rows = []


def _schema_manager(conn):
    behavior = ConnectorBehavior()
    manager = SnowflakeSchemaManager(
        config=SnowflakeConfig(
            account="test.local", user="u", password="p", warehouse="w",
            database="test_db", schema_name="test_schema", role="r",
        ),
        behavior=behavior,
        identifier_sanitizer=IdentifierSanitizer(
            force_uppercase=behavior.compatibility.force_uppercase,
            always_quote=behavior.snowflake.quote_identifiers,
            suppress_reserved=behavior.compatibility.suppress_reserved_words,
        ),
        connection_manager=conn,
    )
    manager._live_schema_metadata = LiveSchemaMetadata()
    return manager


def test_salesfact_binds_existing_sales_fact_and_never_creates_salesfact():
    clear_schema_cache()
    conn = _Conn(
        tables=["SALES_FACT", "PRODUCT"],
        columns={"SALES_FACT": ["PRODUCT_ID", "DATE", "ZIP", "UNITS", "REVENUE"], "PRODUCT": ["PRODUCT_ID"]},
        row_counts={"SALES_FACT": 1_260_752, "PRODUCT": 10},
    )
    manager = _schema_manager(conn)
    model = SMLModel(
        unique_name="Competitive",
        datasets=[
            SMLDataset(unique_name="SalesFact", columns=[SMLColumn(unique_name=c) for c in SALES_COLS]),
            SMLDataset(unique_name="Returns", columns=[SMLColumn(unique_name="Return ID")]),
        ],
    )

    plan = {e["dataset"]: e for e in manager.plan_source_tables(_Cursor(), model)}
    assert plan["SalesFact"]["action"] == "bind"
    assert plan["SalesFact"]["table"] == "SALES_FACT"
    assert plan["Returns"]["action"] == "create"

    manager._ensure_source_tables_exist(_Cursor(), model)
    creates = [s for s in conn.executed if s.upper().startswith("CREATE TABLE")]
    assert len(creates) == 1 and "RETURNS" in creates[0].upper()
    assert not any("SALESFACT" in s.upper().replace("SALES_FACT", "") for s in creates)
    clear_schema_cache()


def test_exact_name_beats_a_larger_column_match():
    # Binding never looks at row counts: the exactly-named table wins.
    cols = ["Date", "Value", "Business Area ID"]
    live = {"DATE", "VALUE", "BUSINESS_AREA_ID"}
    meta = LiveSchemaMetadata({"FACT": set(live), "SPEND_FACT": set(live), "BUSINESS_AREA": {"BUSINESS_AREA", "BUSINESS_AREA_ID"}})
    meta.row_counts.update({"FACT": 1, "SPEND_FACT": 166_216, "BUSINESS_AREA": 7})
    assert resolve_dataset_to_physical_table("Fact", cols, meta) == "FACT"
    assert meta.binding_log["FACT"]["matched_by"] == "exact name"
    # A dimension still binds by name over an unrelated, larger fact table.
    assert resolve_dataset_to_physical_table(
        "Business Area", ["Business Area", "Business Area ID"],
        LiveSchemaMetadata({**meta, "SPEND_FACT": {"BUSINESS_AREA", "BUSINESS_AREA_ID", "VALUE"}}),
    ) == "BUSINESS_AREA"
