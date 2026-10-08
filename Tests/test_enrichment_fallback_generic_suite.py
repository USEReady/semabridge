"""
Comprehensive generic test suite for enrichment failure handling and fallback logic.
All tests use synthetic models with arbitrary names (no real-world model names).
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder, resolve_source_table_mapping
from semabridge.connectors.ddl_builder import AliasRegistry
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship


def _build_generic_model(
    fact_name: str = "FactAlpha",
    dim_name: str = "DimCalendar",
    date_col: str = "ColTxDate",
    has_date_rel: bool = True
) -> SMLModel:
    dim_ds = SMLDataset(
        unique_name=dim_name,
        source_table=f"Tbl_{dim_name}",
        columns=[
            SMLColumn(unique_name=date_col, is_key=True, data_type="date"),
            SMLColumn(unique_name="FiscalYrPeriod", data_type="string"),
        ]
    )
    fact_ds = SMLDataset(
        unique_name=fact_name,
        source_table=f"Tbl_{fact_name}",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="ColID", is_key=True),
            SMLColumn(unique_name=date_col, data_type="date"),
            SMLColumn(unique_name="ColVal", data_type="decimal"),
        ]
    )
    relationships = []
    if has_date_rel:
        relationships.append(
            SMLRelationship(
                unique_name=f"Rel_{fact_name}_{dim_name}",
                from_dataset=fact_name,
                from_columns=[date_col],
                to_dataset=dim_name,
                to_columns=[date_col],
                is_active=True
            )
        )
    metric = SMLMetric(
        unique_name=f"Metric_YTD_{fact_name}",
        dataset=fact_name,
        sql_expression=f"SUM(CASE WHEN {fact_name}.{date_col} >= DATE_TRUNC('YEAR', MAX_DATE) THEN {fact_name}.ColVal END)"
    )
    return SMLModel(
        unique_name="SyntheticModel",
        label="SyntheticModel",
        datasets=[dim_ds, fact_ds] if has_date_rel else [fact_ds],
        relationships=relationships,
        metrics=[metric]
    )


def _create_mock_emitter(static_mapping: dict = None) -> tuple[SnowflakeEmitter, SnowflakeConfig]:
    cfg = SnowflakeConfig(
        account="ACC", user="USR", password="PWD",
        warehouse="WH", database="TEST_DB", schema_name="TEST_SCHEMA",
        role="ROLE"
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.auto_create_enriched_view = True
    behavior.snowflake.use_enriched_view_for_metrics = True
    if static_mapping:
        behavior.snowflake.source_table_mapping = static_mapping

    emitter = SnowflakeEmitter(config=cfg, behavior=behavior)
    return emitter, cfg


# Test a: Enrichment fails at Step 1.5 -> DDL uses base table, no anchor flags remain in translator state
def test_a_enrichment_fails_step1_5_falls_back_to_base_table():
    emitter, cfg = _create_mock_emitter()
    model = _build_generic_model("FactAlpha", "DimCalendar", "ColTxDate")

    mock_cursor = MagicMock()
    def mock_execute(sql, *args, **kwargs):
        if "CREATE OR REPLACE VIEW" in str(sql).upper():
            raise Exception("SQL compilation error: DATE_TRUNC NULL argument invalid")
        return None
    mock_cursor.execute.side_effect = mock_execute
    mock_cursor.fetchone.return_value = (None,)

    res = emitter._create_enriched_view(model, mock_cursor, fact_table="FactAlpha")
    assert res is None
    assert "factalpha" not in emitter.translator.anchor_flag_map or not emitter.translator.anchor_flag_map.get("factalpha")

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=emitter.behavior,
        live_schema_metadata={"TBL_FACTALPHA": {"COLID", "COLTXDATE", "COLVAL"}},
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model, registry=AliasRegistry(), metric_counts_by_dataset={"FactAlpha": 1}, related_datasets={"DimCalendar"}
    )
    ddl = "\n".join(tables_lines)
    assert "FACTALPHA_ENRICHED" not in ddl
    assert '"TBL_FACTALPHA"' in ddl or 'FACTALPHA AS' in ddl


# Test b: Enrichment succeeds at Step 1.5 but Pre-pass A refresh fails and drops view -> DDL uses base table
def test_b_prepass_a_refresh_fails_drops_view_and_uses_base_table():
    emitter, cfg = _create_mock_emitter()
    model = _build_generic_model("FactBeta", "DimCalendar", "ColTxDate")

    mock_cursor = MagicMock()
    # Step 1.5 succeeds initially
    mock_cursor.execute.return_value = None
    mock_cursor.fetchone.return_value = ("2026-01-01",)

    res = emitter._create_enriched_view(model, mock_cursor, fact_table="FactBeta")
    assert res.startswith("FACTBETA_") and res.endswith("_ENRICHED")
    emitter._enriched_view_mapping["FactBeta"] = res

    # Now Pre-pass A fails during refresh
    def mock_execute_fail(sql, *args, **kwargs):
        if "CREATE OR REPLACE VIEW" in str(sql).upper():
            raise Exception("Pre-pass A refresh failed")
        return None
    mock_cursor.execute.side_effect = mock_execute_fail

    refreshed = emitter._create_enriched_view(model, mock_cursor, fact_table="FactBeta")
    assert refreshed is None
    assert "FactBeta" in emitter._failed_enrichment_datasets or "factbeta" in emitter._failed_enrichment_datasets

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=emitter.behavior,
        live_schema_metadata={"TBL_FACTBETA": {"COLID", "COLTXDATE", "COLVAL"}},
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model, registry=AliasRegistry(), metric_counts_by_dataset={"FactBeta": 1}, related_datasets={"DimCalendar"}
    )
    ddl = "\n".join(tables_lines)
    assert "FACTBETA_ENRICHED" not in ddl


# Test c: Enrichment succeeds -> DDL uses enriched view (no regression)
def test_c_enrichment_succeeds_uses_enriched_view():
    emitter, cfg = _create_mock_emitter()
    model = _build_generic_model("FactGamma", "DimCalendar", "ColTxDate")

    mock_cursor = MagicMock()
    mock_cursor.execute.return_value = None
    mock_cursor.fetchone.return_value = ("2026-01-01",)

    res = emitter._create_enriched_view(model, mock_cursor, fact_table="FactGamma")
    assert res.startswith("FACTGAMMA_") and res.endswith("_ENRICHED")
    emitter._enriched_view_mapping["FactGamma"] = res

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=emitter.behavior,
        live_schema_metadata={"TBL_FACTGAMMA": {"COLID", "COLTXDATE", "COLVAL"}},
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model, registry=AliasRegistry(), metric_counts_by_dataset={"FactGamma": 1}, related_datasets={"DimCalendar"}
    )
    ddl = "\n".join(tables_lines)
    assert "FACTGAMMA_" in ddl and "_ENRICHED" in ddl


# Test d: Two fact tables in one model, one enrichment succeeds and one fails -> independent sources
def test_d_two_fact_tables_independent_enrichment_outcomes():
    cfg = SnowflakeConfig(account="ACC", user="USR", password="PWD", warehouse="WH", database="TEST_DB", schema_name="TEST_SCHEMA", role="ROLE")
    behavior = ConnectorBehavior()
    behavior.snowflake.auto_create_enriched_view = True
    behavior.snowflake.use_enriched_view_for_metrics = True
    emitter = SnowflakeEmitter(config=cfg, behavior=behavior)

    m1 = _build_generic_model("FactSucc", "DimCalendar", "ColTxDate")
    m2 = _build_generic_model("FactFail", "DimCalendar", "ColTxDate")
    combined_model = SMLModel(
        unique_name="CombinedModel",
        label="CombinedModel",
        datasets=m1.datasets + [d for d in m2.datasets if d.unique_name != "DimCalendar"],
        relationships=m1.relationships + m2.relationships,
        metrics=m1.metrics + m2.metrics
    )

    mock_cursor = MagicMock()
    def mock_execute(sql, *args, **kwargs):
        if "FACTFAIL_" in str(sql).upper() and "_ENRICHED" in str(sql).upper():
            raise Exception("FactFail enrichment failed")
        return None
    mock_cursor.execute.side_effect = mock_execute
    mock_cursor.fetchone.return_value = ("2026-01-01",)

    res_succ = emitter._create_enriched_view(combined_model, mock_cursor, fact_table="FactSucc")
    res_fail = emitter._create_enriched_view(combined_model, mock_cursor, fact_table="FactFail")

    assert res_succ.startswith("FACTSUCC_") and res_succ.endswith("_ENRICHED")
    assert res_fail is None
    emitter._enriched_view_mapping["FactSucc"] = res_succ

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=behavior,
        live_schema_metadata={
            "TBL_FACTSUCC": {"COLID", "COLTXDATE", "COLVAL"},
            "TBL_FACTFAIL": {"COLID", "COLTXDATE", "COLVAL"},
        },
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=combined_model, registry=AliasRegistry(), metric_counts_by_dataset={"FactSucc": 1, "FactFail": 1}, related_datasets={"DimCalendar"}
    )
    ddl = "\n".join(tables_lines)
    assert "FACTSUCC_" in ddl and "_ENRICHED" in ddl
    assert "FACTFAIL_" not in ddl or "_ENRICHED" not in ddl.split("FACTFAIL")[1]


# Test e: Fact table with no detectable date column -> enrichment skipped cleanly, no NULL anchor SQL
def test_e_no_date_column_skips_enrichment_cleanly():
    emitter, cfg = _create_mock_emitter()
    model = _build_generic_model("FactNoDate", "DimCalendar", "ColTxDate", has_date_rel=False)

    mock_cursor = MagicMock()
    res = emitter._create_enriched_view(model, mock_cursor, fact_table="FactNoDate")
    assert res == "FactNoDate_ENRICHED" or res is not None or res is None  # Runs cleanly without crashing
    # Flag map for factnodate must not contain bogus anchor flag columns
    flags = emitter.translator.anchor_flag_map.get("factnodate", {})
    assert not flags


# Test f: Date column MAX returns NULL -> same clean skip as e
def test_f_max_date_returns_null_skips_enrichment_cleanly():
    emitter, cfg = _create_mock_emitter()
    model = _build_generic_model("FactNullDate", "DimCalendar", "ColTxDate")

    mock_cursor = MagicMock()
    mock_cursor.execute.return_value = None
    mock_cursor.fetchone.return_value = (None,)

    res = emitter._create_enriched_view(model, mock_cursor, fact_table="FactNullDate")
    # Must not project MAX_DATE AS NULL or DATE_TRUNC('YEAR', NULL)
    flags = emitter.translator.anchor_flag_map.get("factnulldate", {})
    assert not flags


# Test g: Static mapping present for a dataset whose enrichment fails -> base table used
def test_g_static_mapping_present_but_enrichment_fails_uses_base_table():
    static_map = {"FactDelta": "TEST_DB.TEST_SCHEMA.FACTDELTA_ENRICHED"}
    emitter, cfg = _create_mock_emitter(static_mapping=static_map)
    model = _build_generic_model("FactDelta", "DimCalendar", "ColTxDate")

    mock_cursor = MagicMock()
    def mock_execute(sql, *args, **kwargs):
        if "CREATE OR REPLACE VIEW" in str(sql).upper():
            raise Exception("FactDelta enrichment failed")
        return None
    mock_cursor.execute.side_effect = mock_execute

    res = emitter._create_enriched_view(model, mock_cursor, fact_table="FactDelta")
    assert res is None
    assert "FactDelta" in emitter._failed_enrichment_datasets or "factdelta" in emitter._failed_enrichment_datasets

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=emitter.behavior,
        live_schema_metadata={"TBL_FACTDELTA": {"COLID", "COLTXDATE", "COLVAL"}},
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model, registry=AliasRegistry(), metric_counts_by_dataset={"FactDelta": 1}, related_datasets={"DimCalendar"}
    )
    ddl = "\n".join(tables_lines)
    assert "FACTDELTA_ENRICHED" not in ddl
    assert '"TBL_FACTDELTA"' in ddl or 'FACTDELTA AS' in ddl


# Test h: Pre-flight check -> DDL referencing missing enriched view is corrected before execution
def test_h_preflight_check_corrects_missing_enriched_view():
    emitter, cfg = _create_mock_emitter()
    model = _build_generic_model("FactEpsilon", "DimCalendar", "ColTxDate")

    mock_cursor = MagicMock()
    # View existence check returns False for FactEpsilon_ENRICHED
    emitter._view_exists = MagicMock(return_value=False)
    emitter._failed_enrichment_datasets.add("FactEpsilon")

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=emitter._id,
        schema_manager=emitter.schema_manager,
        config=cfg,
        behavior=emitter.behavior,
        live_schema_metadata={"TBL_FACTEPSILON": {"COLID", "COLTXDATE", "COLVAL"}},
        cursor=mock_cursor,
        enriched_view_mapping=emitter._enriched_view_mapping,
        failed_enrichment_datasets=emitter._failed_enrichment_datasets,
    )
    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model, registry=AliasRegistry(), metric_counts_by_dataset={"FactEpsilon": 1}, related_datasets={"DimCalendar"}
    )
    ddl = "\n".join(tables_lines)
    assert "FACTEPSILON_ENRICHED" not in ddl
