"""
Tests for calculated-table relationship preservation, auto date/time exclusion,
inferred column name resolution, and drop-ledger diagnostic accounting.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.core.drop_ledger import DropLedger, DropStage


class MockSQLiteHandler:
    """In-memory SQLite mock handler responding to execute_query."""

    def __init__(self, dfs_by_pattern):
        self.dfs_by_pattern = dfs_by_pattern
        self.closed = False

    def execute_query(self, sql: str) -> pd.DataFrame:
        sql_clean = " ".join(sql.split())
        for pattern, df in self.dfs_by_pattern.items():
            if pattern in sql_clean:
                return df.copy()
        return pd.DataFrame()

    def close_connection(self):
        self.closed = True


@pytest.fixture
def sample_metadata_catalog():
    """Build mock dataframes covering:
    - Legitimate imported table (SystemFlags=0)
    - Legitimate calculated table (SystemFlags=2, IsPrivate=0, ShowAsVariationsOnly=0)
    - Private auto date template (SystemFlags=2, IsPrivate=1, ShowAsVariationsOnly=0)
    - Variations-only local date table (SystemFlags=2, IsPrivate=0, ShowAsVariationsOnly=1)
    """
    auto_tbls_df = pd.DataFrame([
        {
            "ID": 30,
            "Name": "DateTableTemplate_12345",
            "SystemFlags": 2,
            "IsPrivate": 1,
            "ShowAsVariationsOnly": 0,
        },
        {
            "ID": 40,
            "Name": "LocalDateTable_67890",
            "SystemFlags": 2,
            "IsPrivate": 0,
            "ShowAsVariationsOnly": 1,
        },
    ])

    # Tables and Columns
    tables_columns_df = pd.DataFrame([
        # Indicators (Imported)
        {
            "TableID": 10,
            "TableName": "Indicators",
            "TableIsHidden": 0,
            "TableDescription": "",
            "TableSystemFlags": 0,
            "TableIsPrivate": 0,
            "TableShowAsVariationsOnly": 0,
            "ColumnID": 101,
            "ColumnExplicitName": "Year",
            "ColumnInferredName": None,
            "ColumnName": "Year",
            "ColumnType": 1,
            "ColumnDataType": 6,  # int64
            "ColumnIsHidden": 0,
            "ColumnDescription": "",
        },
        {
            "TableID": 10,
            "TableName": "Indicators",
            "TableIsHidden": 0,
            "TableDescription": "",
            "TableSystemFlags": 0,
            "TableIsPrivate": 0,
            "TableShowAsVariationsOnly": 0,
            "ColumnID": 102,
            "ColumnExplicitName": "Value",
            "ColumnInferredName": None,
            "ColumnName": "Value",
            "ColumnType": 1,
            "ColumnDataType": 8,  # double
            "ColumnIsHidden": 0,
            "ColumnDescription": "",
        },
        # Years (Calculated table with GENERATESERIES, SystemFlags=2, IsPrivate=0, ShowAsVariationsOnly=0)
        # ExplicitName is None, InferredName is "Years"
        {
            "TableID": 20,
            "TableName": "Years",
            "TableIsHidden": 0,
            "TableDescription": "Generated series of years",
            "TableSystemFlags": 2,
            "TableIsPrivate": 0,
            "TableShowAsVariationsOnly": 0,
            "ColumnID": 201,
            "ColumnExplicitName": None,
            "ColumnInferredName": "Years",
            "ColumnName": "Years",
            "ColumnType": 4,  # calculated column
            "ColumnDataType": 6,  # int64
            "ColumnIsHidden": 0,
            "ColumnDescription": "",
        },
    ])

    # Partitions
    partitions_df = pd.DataFrame([
        {
            "ID": 501,
            "TableID": 10,
            "PartitionName": "Indicators_Partition",
            "QueryDefinition": "let Source = ...",
            "PartitionType": 4,  # M expression
            "TableName": "Indicators",
        },
        {
            "ID": 502,
            "TableID": 20,
            "PartitionName": "Years_Partition",
            "QueryDefinition": "GENERATESERIES(1800, 2017, 1)",
            "PartitionType": 2,  # DAX calculated table
            "TableName": "Years",
        },
    ])

    # Sample relationships query to check column names
    rel_sample_df = pd.DataFrame([{"FromColumnID": 1, "ToColumnID": 2, "FromCardinality": 1, "ToCardinality": 1}])

    # Relationships query
    rels_df = pd.DataFrame([
        # Rel 1: Indicators[Year] -> Years[Years] (Calculated table relationship - MUST BE PRESERVED)
        {
            "RelID": 1,
            "FromTableID": 10,
            "FromTableName": "Indicators",
            "FromColumnID": 101,
            "FromColumnName": "Year",
            "ToTableID": 20,
            "ToTableName": "Years",
            "ToColumnID": 201,
            "ToColumnName": "Years",
            "IsActive": 1,
            "FromCard": 2,
            "ToCard": 1,
            "CrossFilteringBehavior": 1,
            "FromIsPrivate": 0,
            "FromShowAsVariationsOnly": 0,
            "ToIsPrivate": 0,
            "ToShowAsVariationsOnly": 0,
        },
        # Rel 2: Indicators[Year] -> DateTableTemplate_12345[Date] (Auto date template - MUST BE EXCLUDED by design)
        {
            "RelID": 2,
            "FromTableID": 10,
            "FromTableName": "Indicators",
            "FromColumnID": 101,
            "FromColumnName": "Year",
            "ToTableID": 30,
            "ToTableName": "DateTableTemplate_12345",
            "ToColumnID": 301,
            "ToColumnName": "Date",
            "IsActive": 0,
            "FromCard": 2,
            "ToCard": 1,
            "CrossFilteringBehavior": 1,
            "FromIsPrivate": 0,
            "FromShowAsVariationsOnly": 0,
            "ToIsPrivate": 1,
            "ToShowAsVariationsOnly": 0,
        },
        # Rel 3: Indicators[Year] -> LocalDateTable_67890[Date] (Variations-only local date table - MUST BE EXCLUDED by design)
        {
            "RelID": 3,
            "FromTableID": 10,
            "FromTableName": "Indicators",
            "FromColumnID": 101,
            "FromColumnName": "Year",
            "ToTableID": 40,
            "ToTableName": "LocalDateTable_67890",
            "ToColumnID": 401,
            "ToColumnName": "Date",
            "IsActive": 0,
            "FromCard": 2,
            "ToCard": 1,
            "CrossFilteringBehavior": 1,
            "FromIsPrivate": 0,
            "FromShowAsVariationsOnly": 0,
            "ToIsPrivate": 0,
            "ToShowAsVariationsOnly": 1,
        },
        # Rel 4: Broken relationship with unresolvable target table/column (MUST BE RECORDED IN DROP LEDGER with by_design=False)
        {
            "RelID": 4,
            "FromTableID": 10,
            "FromTableName": "Indicators",
            "FromColumnID": 101,
            "FromColumnName": "Year",
            "ToTableID": 999,
            "ToTableName": None,
            "ToColumnID": 9991,
            "ToColumnName": None,
            "IsActive": 1,
            "FromCard": 2,
            "ToCard": 1,
            "CrossFilteringBehavior": 1,
            "FromIsPrivate": 0,
            "FromShowAsVariationsOnly": 0,
            "ToIsPrivate": 0,
            "ToShowAsVariationsOnly": 0,
        },
    ])

    dfs = {
        "FROM [Table] WHERE (COALESCE(IsPrivate, 0) != 0": auto_tbls_df,
        "FROM [Table] t LEFT JOIN [Column] c": tables_columns_df,
        "FROM [Partition] p JOIN [Table] t": partitions_df,
        "SELECT * FROM Relationship LIMIT 1": rel_sample_df,
        "FROM Relationship rel": rels_df,
    }
    return dfs


def _build_mocked_connector(dfs):
    connector = LocalPBIXConnector.__new__(LocalPBIXConnector)
    connector._pbix_path = Path("mock_model.pbix")
    connector.drop_ledger = DropLedger()

    mock_sh = MockSQLiteHandler(dfs)

    def mock_extract_raw():
        with patch("pbixray.pbix_unpacker.PbixUnpacker") as mock_unpacker_cls, \
             patch("pbixray.utils.get_data_slice", return_value=b"fake-db"), \
             patch("pbixray.meta.sqlite_handler.SQLiteHandler", return_value=mock_sh):
            unpacker_inst = MagicMock()
            unpacker_inst.data_model.file_type = "pbix"
            mock_unpacker_cls.return_value = unpacker_inst
            return connector._extract_raw_metadata_catalog()

    return connector, mock_extract_raw


def test_a_keep_legitimate_calculated_table_relationship(sample_metadata_catalog):
    """Test A: Keep a legitimate calculated-table relationship.

    Given a relationship whose target table has SystemFlags = 2, IsPrivate = 0,
    ShowAsVariationsOnly = 0, the relationship must be preserved.
    Representative case: Indicators[Year] -> Years[Years].
    """
    connector, extract_fn = _build_mocked_connector(sample_metadata_catalog)
    tables, rels = extract_fn()

    assert tables is not None
    assert "Years" in tables
    assert any(c["name"] == "Years" for c in tables["Years"]["columns"])

    # Confirm relationship is preserved
    assert rels is not None
    calc_rel = next((r for r in rels if r["from_table"] == "Indicators" and r["to_table"] == "Years"), None)
    assert calc_rel is not None
    assert calc_rel["from_column"] == "Year"
    assert calc_rel["to_column"] == "Years"
    assert calc_rel["cardinality"] == "ManyToOne"
    assert calc_rel["cross_filtering_behavior"] == "Single"


def test_b_exclude_actual_auto_date_time_relationships(sample_metadata_catalog):
    """Test B: Exclude an actual auto date/time relationship.

    Given a relationship involving a table identified as an internal auto date/time table
    (IsPrivate = 1 template, or ShowAsVariationsOnly = 1 local date table),
    the relationship must be excluded and recorded in the drop ledger with by_design=True.
    """
    connector, extract_fn = _build_mocked_connector(sample_metadata_catalog)
    tables, rels = extract_fn()

    assert rels is not None
    # Neither auto-date table should be in the returned valid relationships list
    assert not any(r["to_table"] == "DateTableTemplate_12345" for r in rels)
    assert not any(r["to_table"] == "LocalDateTable_67890" for r in rels)

    # But they MUST be recorded in the drop ledger with by_design=True
    ledger_records = connector.drop_ledger.records
    auto_drops = [r for r in ledger_records if r.by_design and r.entity_kind == "relationship"]
    assert len(auto_drops) == 2
    assert any("DateTableTemplate_12345" in r.entity_name for r in auto_drops)
    assert any("LocalDateTable_67890" in r.entity_name for r in auto_drops)


def test_c_inferred_column_name_fallback(sample_metadata_catalog):
    """Test C: Inferred column name fallback.

    If ExplicitName is empty and InferredName exists (common for calculated tables),
    verify that the connector resolves the column name correctly and preserves the relationship.
    """
    connector, extract_fn = _build_mocked_connector(sample_metadata_catalog)
    tables, rels = extract_fn()

    years_cols = [c["name"] for c in tables["Years"]["columns"]]
    assert "Years" in years_cols

    calc_rel = next(r for r in rels if r["to_table"] == "Years")
    assert calc_rel["to_column"] == "Years"


def test_d_missing_endpoint_diagnostic(sample_metadata_catalog):
    """Test D: Missing endpoint diagnostic.

    If a source or target endpoint cannot be resolved, verify that the relationship
    is recorded in the drop ledger with its IDs and reason (by_design=False)
    instead of disappearing silently.
    """
    connector, extract_fn = _build_mocked_connector(sample_metadata_catalog)
    tables, rels = extract_fn()

    # Rel 4 had unresolvable target endpoint
    assert not any(r["name"].startswith("Indicators_Year_None") for r in rels)

    # Check drop ledger
    missing_drops = [r for r in connector.drop_ledger.records if not r.by_design]
    assert len(missing_drops) == 1
    drop = missing_drops[0]
    assert drop.entity_name == "Relationship_4"
    assert drop.stage == DropStage.EXTRACTION
    assert "Unresolvable relationship endpoint" in drop.reason
    assert "from_table_id=10" in drop.detail
    assert "to_table_id=999" in drop.detail


def test_e_preserve_existing_behavior():
    """Test E: Preserve existing behavior.

    Ensure cardinality, cross-filtering, active status normalizations,
    and fallback TMSL assembly work as expected without regressions.
    """
    assert LocalPBIXConnector._normalize_pbixray_cardinality("1:1") == "OneToOne"
    assert LocalPBIXConnector._normalize_pbixray_cardinality("M:1") == "ManyToOne"
    assert LocalPBIXConnector._normalize_pbixray_cardinality("1:M") == "OneToMany"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("Both") == "Both"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("Single") == "Single"

    # Test incomplete endpoint recording during TMSL fallback assembly
    connector = LocalPBIXConnector.__new__(LocalPBIXConnector)
    connector._pbix_path = Path("test_model.pbix")
    connector.drop_ledger = DropLedger()

    fallback_data = {
        "models": [{"name": "TestModel"}],
        "tables": [{"name": "A", "columns": [{"name": "ID", "data_type": "int64"}]}],
        "relationships": [
            {"from_table": "A", "from_column": "ID", "to_table": "", "to_column": ""},  # missing to_table/col
        ],
    }
    tmsl = connector._build_tmsl_from_fallback(fallback_data)
    assert len(tmsl["model"]["relationships"]) == 0

    # Incomplete endpoint was recorded in drop_ledger, not silently swallowed
    records = [r for r in connector.drop_ledger.records if not r.by_design]
    assert len(records) == 1
    assert "Incomplete relationship endpoint in TMSL assembly" in records[0].reason


def test_f_live_models_calculated_tables_if_available():
    """Verify live reproduction models if present in the local filesystem."""
    downloads_dir = Path(r"C:\Users\manoj.s\Downloads\PBIX Files")
    life_exp_pbix = downloads_dir / "Life expectancy v202009.pbix"
    ai_sample_pbix = downloads_dir / "Artificial Intelligence Sample.pbix"

    if life_exp_pbix.exists():
        connector = LocalPBIXConnector({"pbix_path": str(life_exp_pbix)})
        discovered = connector.discover()
        rels = discovered.get("relationships", [])
        # Confirmed 9 relationships including Years
        assert len(rels) == 9
        endpoints = {(r["from_table"], r["from_column"], r["to_table"], r["to_column"]) for r in rels}
        assert ("Indicators", "Year", "Years", "Years") in endpoints
        assert ("Population", "Year", "Years", "Years") in endpoints
        # Auto-date relationships are in drop_ledger
        auto_rels = [r for r in connector.drop_ledger.records if r.by_design and r.entity_kind == "relationship"]
        assert len(auto_rels) >= 1

    if ai_sample_pbix.exists():
        connector = LocalPBIXConnector({"pbix_path": str(ai_sample_pbix)})
        discovered = connector.discover()
        rels = discovered.get("relationships", [])
        # Confirmed 12 relationships including Case Calendar and Opportunity Calendar
        assert len(rels) == 12
        endpoints = {(r["from_table"], r["from_column"], r["to_table"], r["to_column"]) for r in rels}
        assert ("Opportunities", "CloseDate", "Opportunity Calendar", "DAY") in endpoints
        assert ("Cases", "Case Created On", "Case Calendar", "Date") in endpoints
        # Auto-date relationships are in drop_ledger
        auto_rels = [r for r in connector.drop_ledger.records if r.by_design and r.entity_kind == "relationship"]
        assert len(auto_rels) >= 5


def test_g_sync_execution_pipeline_preserves_calculated_tables():
    """Verify that the actual ExecutionEngine sync pipeline preserves calculated tables
    and emits their relationships in generated Snowflake semantic-view DDL."""
    downloads_dir = Path(r"C:\Users\manoj.s\Downloads\PBIX Files")
    life_exp_pbix = downloads_dir / "Life expectancy v202009.pbix"

    if not life_exp_pbix.exists():
        pytest.skip("Life expectancy pbix not available in Downloads")

    from semabridge.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    with patch("semabridge.connectors.connection_manager.SnowflakeConnectionManager.get_connection") as mock_conn:
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.return_value = (MagicMock(cursor=lambda: mock_cur), False)
        with patch("semabridge.connectors.schema_manager.SnowflakeSchemaManager._fetch_schema_metadata") as mock_meta:
            mock_meta.return_value = {
                "INDICATORS": {"YEAR", "COUNTRY_CODE", "SEX", "LIFE_EXP_BUCKETS"},
                "YEAR": {"YEAR"},
                "COUNTRY": {"COUNTRY_CODE"},
                "SEX": {"SEX"},
                "VARIANCE_ANALYSIS": {"COUNTRY"},
                "FLAGS": {"COUNTRY_CODE"},
                "YEARS": {"YEARS"},
                "POPULATION": {"CODE", "YEAR"},
                "BUCKETS": {"CATEGORY"},
            }
            summary = engine.execute(
                source="pbix",
                target="snowflake",
                project_id="test_life_exp_sync",
                pbix_path=str(life_exp_pbix),
                deploy=False,
                dry_run=True,
            )

    context = getattr(engine, "_context", None)
    assert context is not None
    assert context.sml_model is not None

    # Verify all 9 relationships survive in SML
    assert len(context.sml_model.relationships) == 9
    rel_endpoints = {
        (r.from_dataset, tuple(r.from_columns), r.to_dataset, tuple(r.to_columns))
        for r in context.sml_model.relationships
    }
    assert ("Indicators", ("Year",), "Years", ("Years",)) in rel_endpoints
    assert ("Population", ("Year",), "Years", ("Years",)) in rel_endpoints

    # Verify DDL contains both calculated-table relationships and YEARS PRIMARY KEY
    debug_ddl_path = Path("output/debug/debug_generated_sql_Life_expectancy_v202009.sql")
    assert debug_ddl_path.exists()
    ddl_content = debug_ddl_path.read_text(encoding="utf-8")

    assert 'YEARS AS "SEMABRIDGE"."TESTING_SEMABRIDGE"."YEARS" PRIMARY KEY ("YEARS")' in ddl_content
    assert 'INDICATORS_YEAR_YEARS_YEARS AS INDICATORS ("YEAR") REFERENCES YEARS ("YEARS")' in ddl_content
    assert 'POPULATION_YEAR_YEARS_YEARS AS POPULATION ("YEAR") REFERENCES YEARS ("YEARS")' in ddl_content

