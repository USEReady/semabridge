"""Databricks: structure-only (stubbed) metric views are never reported as a
plain success, and a missing source table is never silently bound to a
same-named table in another schema."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.engine.deployment.base import _step9_deploy
from semabridge.core.engine.deployment.databricks import (
    DeploymentError,
    _raise_if_databricks_fallback_failed,
)
from semabridge.core.run_summary import StepStatus
from semabridge.core.settings import DatabricksConfig


def _publisher(**behavior_kwargs) -> DatabricksPublisher:
    cfg = DatabricksConfig(
        host="test.cloud.databricks.com",
        token="dapi-test-token",
        warehouse_id="wh-1",
        catalog="workspace",
        schema_name="default",
    )
    behavior = ConnectorBehavior(databricks=DatabricksBehavior(**behavior_kwargs))
    return DatabricksPublisher(cfg, behavior=behavior)


def _schemas_with_table(pub: DatabricksPublisher, schemas: list[str]) -> list[str]:
    """Answer the information_schema lookup with these schemas; return executed SQL."""
    executed: list[str] = []

    def fake_execute(statements):
        executed.extend(statements)
        return [{"result": {"data_array": [[s] for s in schemas]}}]

    pub.execute_statements = fake_execute  # type: ignore[method-assign]
    return executed


# ── Exact binding: no silent cross-schema table search ───────────────────────


def test_same_named_table_in_other_schema_is_reported_not_used_by_default():
    pub = _publisher()
    _schemas_with_table(pub, ["bkp"])

    assert pub._discover_source_table_schema("`workspace`.`default`.`Sales`") is None

    issues = pub._source_binding_issues
    assert len(issues) == 1
    assert issues[0]["source_table"] == "workspace.default.Sales"
    assert "workspace.bkp.Sales" in issues[0]["message"]
    assert "source_table_mapping" in issues[0]["message"]


def test_binding_issue_is_recorded_once_per_table():
    pub = _publisher()
    _schemas_with_table(pub, ["bkp"])

    pub._discover_source_table_schema("`workspace`.`default`.`Sales`")
    pub._discover_source_table_schema("`workspace`.`default`.`Sales`")

    assert len(pub._source_binding_issues) == 1


def test_opt_in_discovery_binds_only_a_unique_match():
    pub = _publisher(allow_alternate_schema_discovery=True)
    _schemas_with_table(pub, ["analytics"])

    assert pub._discover_source_table_schema("`workspace`.`default`.`Sales`") == "`workspace`.`analytics`.`Sales`"
    assert pub._source_binding_issues == []


def test_opt_in_discovery_refuses_to_guess_between_several_schemas():
    pub = _publisher(allow_alternate_schema_discovery=True)
    _schemas_with_table(pub, ["dev", "bkp"])

    assert pub._discover_source_table_schema("`workspace`.`default`.`Sales`") is None
    message = pub._source_binding_issues[0]["message"]
    assert "workspace.bkp.Sales" in message and "workspace.dev.Sales" in message
    assert "none was chosen" in message


def test_the_configured_schema_itself_is_not_an_alternate():
    pub = _publisher()
    _schemas_with_table(pub, ["default"])

    assert pub._discover_source_table_schema("`workspace`.`default`.`Sales`") is None
    assert pub._source_binding_issues == []


def test_resolve_existing_source_does_not_fall_back_to_other_schema(monkeypatch):
    pub = _publisher()
    _schemas_with_table(pub, ["bkp"])
    monkeypatch.setattr(pub, "_check_source_table_exists", lambda ref: False)
    dataset = SimpleNamespace(unique_name="Sales", source_table="Sales")

    assert pub._resolve_existing_source_table(dataset, "`workspace`.`default`.`Sales`") is None


# ── Structure-only views are a warning, never a plain success ─────────────────


def _summary(**deploy_summary):
    base = {
        "planned_views": 1,
        "deployable_views": 1,
        "views_deployed": 0,
        "views_stubbed": 0,
        "stubbed_views": [],
        "source_binding_issues": [],
        "views_failed": 0,
        "views_skipped": 0,
        "skipped_reasons": [],
        "failed_reasons": [],
        "missing_source_tables": [],
    }
    base.update(deploy_summary)
    return {"sql_fallback_state": "NOT_TRIGGERED", "deploy_summary": base}


def _ctx():
    return SimpleNamespace(project_id="proj-1", target_warnings=[], deploy_step_message=None)


def test_all_stubbed_views_are_a_warning_not_a_plain_success():
    ctx = _ctx()
    _raise_if_databricks_fallback_failed(
        None,
        ctx,
        _summary(
            views_stubbed=1,
            stubbed_views=[{"view": "workspace.default.sales_mv", "missing_source_tables": ["`workspace`.`default`.`Sales`"]}],
            missing_source_tables=["`workspace`.`default`.`Sales`"],
        ),
    )

    assert len(ctx.target_warnings) == 1
    warning = ctx.target_warnings[0]
    assert warning.startswith("Structure only (no data): 1 metric view(s)")
    assert "workspace.default.sales_mv" in warning
    assert "`workspace`.`default`.`Sales`" in warning
    assert ctx.deploy_step_message == (
        "Deployment complete with warnings: 0 view(s) with data, 1 structure only (no data, source tables missing)"
    )


def test_mixed_real_and_stubbed_views_report_both_counts():
    ctx = _ctx()
    _raise_if_databricks_fallback_failed(
        None,
        ctx,
        _summary(
            planned_views=3,
            views_deployed=2,
            views_stubbed=1,
            stubbed_views=[{"view": "workspace.default.returns_mv", "missing_source_tables": []}],
        ),
    )

    assert "2 view(s) with data, 1 structure only" in ctx.deploy_step_message
    assert "(missing:" not in ctx.target_warnings[0]


def test_binding_issues_become_run_warnings():
    ctx = _ctx()
    message = "Source table workspace.default.Sales does not exist. A table with the same name exists in workspace.bkp.Sales"
    _raise_if_databricks_fallback_failed(
        None,
        ctx,
        _summary(views_deployed=1, source_binding_issues=[{"source_table": "workspace.default.Sales", "message": message}]),
    )

    assert ctx.target_warnings == [message]
    assert ctx.deploy_step_message is None  # real data deployed, nothing stubbed


def test_no_views_at_all_still_fails():
    with pytest.raises(DeploymentError, match="No Databricks views were deployed"):
        _raise_if_databricks_fallback_failed(None, _ctx(), _summary(views_skipped=1, deployable_views=0))


def test_clean_deploy_adds_no_warning():
    ctx = _ctx()
    _raise_if_databricks_fallback_failed(None, ctx, _summary(views_deployed=1))

    assert ctx.target_warnings == []
    assert ctx.deploy_step_message is None


# ── Step 9 message carries the warning ─────────────────────────────────────────


def _engine_recording_steps(deploy):
    recorded: list[tuple] = []
    engine = SimpleNamespace(
        _deploy_to_databricks=deploy,
        _record_step=lambda *args: recorded.append(args),
    )
    return engine, recorded


def test_step9_message_reports_structure_only_deploy():
    def deploy(context):
        context.deploy_step_message = "Deployment complete with warnings: 0 view(s) with data, 1 structure only"

    engine, recorded = _engine_recording_steps(deploy)
    ctx = SimpleNamespace(target_type="databricks", deploy_step_message=None)

    _step9_deploy(engine, ctx)

    assert recorded == [(9, StepStatus.SUCCESS, "Deployment complete with warnings: 0 view(s) with data, 1 structure only")]


def test_step9_message_is_unchanged_for_a_clean_deploy():
    engine, recorded = _engine_recording_steps(lambda context: None)
    ctx = SimpleNamespace(target_type="databricks", deploy_step_message=None)

    _step9_deploy(engine, ctx)

    assert recorded == [(9, StepStatus.SUCCESS, "Deployment complete")]


def test_partial_deploy_is_reported_with_counts():
    ctx = _ctx()
    _raise_if_databricks_fallback_failed(
        None,
        ctx,
        _summary(
            planned_views=3,
            views_deployed=2,
            views_failed=1,
            failed_reasons=["workspace.default.returns_mv: PARSE_SYNTAX_ERROR"],
        ),
    )
    assert ctx.target_warnings[0].startswith("Partially deployed: 1 metric view(s) failed while 2 deployed")
    assert "PARSE_SYNTAX_ERROR" in ctx.target_warnings[0]
    assert ctx.deploy_step_message == "Deployment complete with failures: 2 view(s) with data, 0 structure only, 1 failed"


# ── Metric view comments carry business meaning, not diagnostics ─────────────


def test_view_comment_is_marker_plus_business_description():
    pub = _publisher()
    assert pub._metric_view_comment("Sales", "Orders", None) == "Semabridge: Sales - Orders"
    text = pub._metric_view_comment("Sales", "Orders", "  Every order line,\n net of returns. ")
    assert text == "Semabridge: Sales - Orders — Every order line, net of returns."
    assert pub._comment_view_hint(text) == "Orders"
    assert pub._comment_view_hint("Semabridge: Sales - Orders") == "Orders"
    assert pub._comment_view_hint("hand written") == ""


def test_marker_still_marks_the_view_as_semabridge_owned():
    pub = _publisher()
    comment = pub._metric_view_comment("Sales", "Orders", "Every order line")
    assert DatabricksPublisher._is_semabridge_owned_describe_rows([["Comment", comment]])


def test_rejected_measure_goes_to_the_run_report_not_the_comment():
    import yaml as _yaml

    pub = _publisher()
    body = _yaml.safe_dump({
        "version": "1.1",
        "comment": "Semabridge: Sales - Orders — Every order line",
        "source": "workspace.default.orders",
        "measures": [{"name": "good", "expr": "SUM(a)"}, {"name": "bad_measure", "expr": "SUM(nope)"}],
    }, sort_keys=False)
    sql = f"CREATE OR REPLACE VIEW `workspace`.`default`.`orders_mv` WITH METRICS LANGUAGE YAML AS $$\n{body}$$"
    repaired, omitted = pub._strip_rejected_metric_measure(sql, "INVALID_METRIC_VIEW_MEASURE: measure `bad_measure` is invalid")
    assert omitted == "bad_measure"
    doc = _yaml.safe_load(repaired.split("$$")[1])
    assert doc["comment"] == "Semabridge: Sales - Orders — Every order line"
    assert pub._omitted_measures[0]["name"] == "bad_measure"


def test_omitted_measures_become_a_run_warning():
    ctx = _ctx()
    _raise_if_databricks_fallback_failed(
        None, ctx, _summary(views_deployed=1, omitted_measures=[{"name": "bad_measure", "reason": "INVALID_METRIC_VIEW_MEASURE"}]),
    )
    assert ctx.target_warnings == [
        "Databricks rejected 1 measure(s); they were left out of the metric view: bad_measure: INVALID_METRIC_VIEW_MEASURE"
    ]
