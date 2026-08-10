"""Regression test for wiring dax_calculate_filters_unreachable_dimension
into osi_to_sml.py as an ADVISORY-ONLY mapping-time check (the KPI01/KPI02
follow-up).

Proves the two halves of the requirement:
  1. The detector fires and records an advisory note on the metric at
     conversion time -- but does NOT touch sync_enabled/sync_failure_reason/
     sql_expression, and the metric still translates normally.
  2. That same metric, deployed for real, still goes through the EXACT
     existing DDL-deployment-time remediation path (Snowflake rejects it,
     auto-remediation nulls it out, drop_ledger records why) exactly as it
     would with no advisory note at all -- the advisory changes only what
     a user sees at mapping time, never what happens at deploy time.

All placeholder names (Selector/Fact/Dim/TotalVolume/MyKpi) -- generalized
from, not tied to, the KPI/Date/Category shape that motivated this.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.drop_ledger import DropStage
from semabridge.core.settings import SnowflakeConfig
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)


def _build_osi_model() -> OSIModel:
    return OSIModel(
        unique_name="synthetic-model",
        label="Synthetic Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Selector",
                columns=[OSIColumn(unique_name="Choice", data_type=OSIDataType.INTEGER)],
            ),
            OSIDataset(
                unique_name="Fact",
                columns=[
                    OSIColumn(unique_name="Amount", data_type=OSIDataType.FLOAT),
                    OSIColumn(unique_name="DimId", data_type=OSIDataType.INTEGER),
                ],
            ),
            OSIDataset(
                unique_name="Dim",
                columns=[
                    OSIColumn(unique_name="DimId", data_type=OSIDataType.INTEGER),
                    OSIColumn(unique_name="Year", data_type=OSIDataType.INTEGER),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="TotalVolume",
                label="Total Volume",
                dataset="Fact",
                expression="SUM([Amount])",
                aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="MyKpi",
                label="My Kpi",
                dataset="Selector",
                # The KPI01/KPI02 idiom, generalized: a selector-table metric
                # wraps CALCULATE(measure, filter-on-a-dimension-the-
                # selector-table-cannot-reach). TotalVolume (on Fact) DOES
                # reach Dim via the relationship below; Selector does not.
                expression="CALCULATE([TotalVolume],'Dim'[Year]=1)",
                aggregation=OSIAggregationType.NONE,
            ),
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_FACT_DIMID__DIM_DIMID",
                from_dataset="Fact",
                from_columns=["DimId"],
                to_dataset="Dim",
                to_columns=["DimId"],
            )
        ],
    )


def test_advisory_note_recorded_without_disabling_the_metric():
    """Half 1: the detector fires at conversion time, but the metric
    proceeds through translation completely normally."""
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())

    my_kpi = next(m for m in sml_model.metrics if m.unique_name == "MyKpi")
    total_volume = next(m for m in sml_model.metrics if m.unique_name == "TotalVolume")

    # The advisory fired, with a specific, well-explained reason.
    assert my_kpi.advisory_notes, "expected an advisory note on MyKpi"
    reason = my_kpi.advisory_notes[0]
    assert "Selector" in reason
    assert "Dim" in reason
    assert "TotalVolume" in reason

    # But nothing about the metric's actual sync/translation state changed.
    assert my_kpi.sync_enabled is True
    assert my_kpi.sync_failure_reason is None
    assert my_kpi.sql_expression, "expected translation to still succeed normally"

    # The reachable metric (TotalVolume, on Fact, which DOES reach Dim) must
    # never be flagged -- this is not a blanket "any CALCULATE" warning.
    assert total_volume.advisory_notes == []


def _build_emitter() -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="test.local", user="test_user", password="test_password",
        warehouse="test_wh", database="test_db", schema_name="test_schema",
        role="test_role",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.create_missing_tables = False
    behavior.snowflake.apply_inferred_types = False
    behavior.snowflake.auto_execute_precompute = False
    behavior.features.enable_cortex_analyst = False
    return SnowflakeEmitter(config, behavior)


def test_flagged_metric_still_gets_nulled_at_deploy_time_exactly_as_before(monkeypatch):
    """Half 2: deploying the SAME model (advisory note and all) still goes
    through the existing, unmodified DDL-deployment-time remediation path
    -- Snowflake rejects the metric, auto-remediation nulls it out,
    drop_ledger records why. The advisory note survives on the metric
    object but has no bearing on what deploy() does."""
    sml_model = OSIToSMLConverter().from_osi(_build_osi_model())
    my_kpi = next(m for m in sml_model.metrics if m.unique_name == "MyKpi")
    assert my_kpi.advisory_notes  # sanity: the precondition from half 1 holds here too

    emitter = _build_emitter()
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor
    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (fake_conn, True))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {"DUMMY": set()})

    # A single DDL statement, standing in for the real generate_ddls()
    # output -- this test isn't re-proving DDL emission (covered elsewhere),
    # only that the deploy-time remediation path is unaffected by this
    # metric carrying an advisory note.
    ddl = 'CREATE TABLE "DB"."SCH"."T1" (X INT)'
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", lambda model: [ddl])

    def fake_execute_sql(cursor, sql, context=""):
        if context == "DDL[0]":
            raise RuntimeError("000904: invalid identifier 'MYKPI'")
        return None

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)

    # remediate_invalid_identifier: simulate the sanitizer identifying
    # MyKpi as the metric to null out (the real sanitizer's own logic is
    # covered by test_metric_drop_ledger_coverage.py; this test only needs
    # a deterministic stand-in so the remediation retry succeeds).
    from semabridge.connectors import semantic_ddl_sanitizer as sds_module

    monkeypatch.setattr(
        sds_module.SemanticDDLSanitizer, "remediate_invalid_identifier",
        lambda self, sql, invalid_id: (sql, True, ["MyKpi"]),
    )
    # After remediation the (fixed) DDL statement is re-submitted -- let it
    # succeed on the retry.
    original_execute = fake_execute_sql

    def fake_execute_sql_after_remediation(cursor, sql, context=""):
        if context.startswith("DDL[0] pass"):
            return None
        return original_execute(cursor, sql, context)

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql_after_remediation)

    result = emitter.deploy(sml_model, sync_mode="copy")

    assert result is True, "deploy must still succeed via the existing remediation path"

    records = emitter.drop_ledger.to_json()
    deployment_drops = [
        r for r in records
        if r["stage"] == DropStage.DDL_DEPLOYMENT.value and r["entity_name"] == "MyKpi"
    ]
    assert deployment_drops, "expected MyKpi to still be recorded as nulled at deploy time"

    # The advisory note on the SML metric object is untouched by any of this
    # -- deploy() operates on the DDL text, never on advisory_notes.
    assert my_kpi.advisory_notes
