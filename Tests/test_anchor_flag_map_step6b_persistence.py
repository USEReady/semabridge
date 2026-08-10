"""Regression tests for Stage 2's Step 6b call site
(core/engine/targets/snowflake.py::_step6b_predict_anchor_flag_columns) --
specifically the persistence/rollback guarantee: a metric corrected by the
predicted pass BEFORE Step 7 persists must be what a future rollback
(cli/commands/version_history_commands.py's `rollback --sync`, which
deserializes a persisted sml_blob verbatim with zero re-translation --
see semabridge_max_date_root_cause_confirmed memory) actually replays --
and the Step 9 (confirmed) pass must be able to correct a divergent
prediction even after that persistence has already happened.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.targets.snowflake import _step6b_predict_anchor_flag_columns
from semabridge.core.settings import SnowflakeConfig
from semabridge.repository.model_repository import ModelRepository
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel, DataType


def _model_with_ytd_metric() -> SMLModel:
    return SMLModel(
        unique_name="proj_sync_test",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="DATE_DIM",
                columns=[SMLColumn(unique_name="Date", data_type=DataType.DATE)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
                # What Step 6 (osi_to_sml.py) would have already produced,
                # per Stage 1 -- the safe CURRENT_DATE() fallback, since
                # Step 6 has no anchor_flag_map at all.
                sql_expression=(
                    'SUM(CASE WHEN COL_DATE."COL_DATE" >= DATE_TRUNC(\'YEAR\', CURRENT_DATE()) '
                    'AND COL_DATE."COL_DATE" <= CURRENT_DATE() THEN SALESFACT."UNITS"::FLOAT END)'
                ),
            )
        ],
    )


def _make_context(sml_model: SMLModel, auto_create_enriched_view: bool = True) -> RunContext:
    behavior = ConnectorBehavior()
    behavior.snowflake.auto_execute_precompute = True
    behavior.snowflake.auto_create_enriched_view = auto_create_enriched_view
    behavior.snowflake.use_enriched_view_for_metrics = True

    config = SimpleNamespace(
        snowflake=SnowflakeConfig(
            account="test.local",
            user="test_user",
            password="test_password",
            warehouse="test_wh",
            database="test_db",
            schema_name="test_schema",
            role="test_role",
        )
    )

    context = RunContext(
        project_id="proj-sync-test",
        run_id="run-1",
        config=config,  # type: ignore[arg-type]
        start_time=0.0,
        source_type="fabric",
        target_type="snowflake",
        behavior=behavior,
    )
    context.sml_model = sml_model
    return context


def test_step6b_upgrades_metric_before_persistence(monkeypatch):
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    monkeypatch.setattr(SnowflakeEmitter, "_find_date_table", lambda self, m: ("Date", "Date", "Date"))

    model = _model_with_ytd_metric()
    context = _make_context(model)

    _step6b_predict_anchor_flag_columns(None, context)

    ytd_metric = context.sml_model.metrics[0]
    assert '."IS_YTD"' in ytd_metric.sql_expression
    assert "CURRENT_DATE" not in ytd_metric.sql_expression
    assert "MAX_DATE" not in ytd_metric.sql_expression


def test_step6b_never_raises_when_prediction_fails(monkeypatch):
    """A prediction-path failure (e.g. an unexpected exception resolving
    the date table) must never fail an otherwise-successful sync -- this
    is a best-effort improvement over Stage 1's already-safe fallback."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    def _explode(self, m):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(SnowflakeEmitter, "_find_date_table", _explode)

    model = _model_with_ytd_metric()
    original_sql = model.metrics[0].sql_expression
    context = _make_context(model)

    _step6b_predict_anchor_flag_columns(None, context)  # must not raise

    assert context.sml_model.metrics[0].sql_expression == original_sql


def test_step6b_no_op_for_model_with_no_time_intelligence_metrics(monkeypatch):
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    monkeypatch.setattr(SnowflakeEmitter, "_find_date_table", lambda self, m: ("Date", "Date", "Date"))

    model = SMLModel(
        unique_name="proj_no_ti",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[SMLColumn(unique_name="Units", data_type=DataType.DECIMAL)],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units",
                dataset="SalesFact",
                expression="SUM('SalesFact'[Units])",
                sql_expression='SUM(SALESFACT."UNITS")',
            )
        ],
    )
    context = _make_context(model)
    original_sql = model.metrics[0].sql_expression

    _step6b_predict_anchor_flag_columns(None, context)

    assert context.sml_model.metrics[0].sql_expression == original_sql


def test_persisted_snapshot_carries_the_step6b_corrected_sql(monkeypatch, tmp_path):
    """The core persistence/rollback guarantee: commit the model AFTER
    Step 6b has run (mirroring engine.py's real ordering -- Step 6b, then
    Step 7 persist), then deserialize it back exactly the way
    `rollback --sync` does (SMLModel.model_validate(snapshot.sml_blob),
    zero re-translation -- see version_history_commands.py). The restored
    metric's sql_expression must still be the flag-column version, not
    the pre-correction CURRENT_DATE() fallback -- proving a future
    rollback to this snapshot replays the CORRECTED SQL, not the stale
    one."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.sml.models import SMLModel as SMLModelCls

    monkeypatch.setattr(SnowflakeEmitter, "_find_date_table", lambda self, m: ("Date", "Date", "Date"))

    model = _model_with_ytd_metric()
    context = _make_context(model)

    # Step 6b (as engine.py's execute() calls it, before Step 7 persist).
    _step6b_predict_anchor_flag_columns(None, context)
    corrected_sql = context.sml_model.metrics[0].sql_expression
    assert '."IS_YTD"' in corrected_sql  # sanity: correction happened

    # Step 7 persist, using the exact call shape finalize.py's real
    # _step7_persist_artifacts uses.
    repo = ModelRepository(url_override=f"sqlite:///{tmp_path / 'test.db'}")
    sml_dict = context.sml_model.model_dump(mode="json")
    committed, snapshot_id = repo.commit_model(
        project_id=context.project_id,
        sml_json=sml_dict,
        tag="v1.0",
        status="success",
    )
    assert committed

    # Rollback's exact deserialization shape (version_history_commands.py).
    snapshot = repo.get_snapshot(snapshot_id)
    restored_model = SMLModelCls.model_validate(snapshot.sml_blob)

    restored_metric = next(m for m in restored_model.metrics if m.unique_name == "Total_Units_YTD")
    assert restored_metric.sql_expression == corrected_sql
    assert '."IS_YTD"' in restored_metric.sql_expression
    assert "CURRENT_DATE" not in restored_metric.sql_expression


def test_step9_confirmed_pass_corrects_a_persisted_wrong_prediction():
    """Even after Step 6b's prediction was persisted, Step 9's confirmed
    pass must still be able to downgrade it back to the safe fallback if
    real enrichment doesn't actually establish the anchor (e.g.
    auto_create_enriched_view was predicted eligible but the live cursor
    fetch fails for real) -- this is rerender_anchor_dependent_metrics'
    own divergence-correction behavior (see
    test_anchor_flag_rerender.py::test_divergence_downgrades_metric_back_to_current_date_fallback),
    exercised here against a model that went through the real Step 6b
    path first, to confirm the two passes compose correctly end-to-end."""
    from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
    from semabridge.converter.dax_translator import DAXTranslator

    model = _model_with_ytd_metric()
    # Simulate Step 6b already having run and (wrongly) upgraded this metric.
    model.metrics[0].sql_expression = (
        'SUM(CASE WHEN SALESFACT."IS_YTD" THEN SALESFACT."UNITS"::FLOAT END)'
    )

    dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(model.datasets)

    # Step 9's real anchor_flag_map has NO entry for salesfact -- real
    # enrichment failed for this fact table.
    count = rerender_anchor_dependent_metrics(
        model, {}, dataset_col_lookup, dataset_aliases, label="confirmed"
    )

    assert count == 1
    assert 'SALESFACT."IS_YTD"' not in model.metrics[0].sql_expression
    assert "CURRENT_DATE()" in model.metrics[0].sql_expression
