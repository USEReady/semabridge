"""Tests for Databricks dry-run preview (databricks_preview, engine, mappings_controller)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.deployment.databricks_preview import (
    preview_databricks_plan,
    target_databricks_config,
)
from semabridge.core.run_summary import RunStatus
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel, SMLRelationship


def _sample_sml_model() -> SMLModel:
    sales = SMLDataset(
        unique_name="Fact Sales",
        label="Fact Sales",
        source_table="fact_sales",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="Order ID", label="Order ID"),
            SMLColumn(unique_name="Date Key", label="Date Key"),
            SMLColumn(unique_name="Amount", label="Amount"),
            SMLColumn(unique_name="Hidden Col", label="Hidden Col", is_hidden=True),
        ],
    )
    calendar = SMLDataset(
        unique_name="Dim Date",
        label="Dim Date",
        source_table="dim_date",
        is_fact=False,
        columns=[SMLColumn(unique_name="Date Key", label="Date Key")],
    )
    rel = SMLRelationship(
        unique_name="rel_sales_date",
        from_dataset="Fact Sales",
        from_columns=["Date Key"],
        to_dataset="Dim Date",
        to_columns=["Date Key"],
        is_active=True,
    )
    metrics = [
        SMLMetric(
            unique_name="Total Sales",
            dataset="Fact Sales",
            expression="SUM('Fact Sales'[Amount])",
            sql_expression="SUM(`Amount`)",
        ),
        SMLMetric(
            unique_name="Margin %",
            dataset="Fact Sales",
            expression="DIVIDE([Profit], [Total Sales])",
            sql_expression=None,
        ),
    ]
    return SMLModel(
        unique_name="Sales Model",
        label="Sales Model",
        datasets=[sales, calendar],
        relationships=[rel],
        metrics=metrics,
    )


def _make_context(
    sml_model: SMLModel | None = None,
    *,
    catalog: str = "analytics",
    schema: str = "semantic",
) -> RunContext:
    cfg = SimpleNamespace(
        targets=[{"type": "databricks", "catalog": catalog, "schema": schema}],
        target=None,
        databricks=SimpleNamespace(host="", warehouse_id="", catalog=catalog, schema_name=schema),
    )
    ctx = RunContext(
        project_id="proj-dbx",
        run_id="run-dbx-1",
        config=cfg,  # type: ignore[arg-type]
        start_time=0.0,
        source_type="pbix",
        target_type="databricks",
        behavior=ConnectorBehavior(),
    )
    ctx.config_payload = {"target": {"type": "databricks", "catalog": catalog, "schema": schema}}
    ctx.sml_model = sml_model if sml_model is not None else _sample_sml_model()
    return ctx


class _FakePublisherWithPlan:
    def __init__(self, plan_result):
        self.plan_result = plan_result
        self.calls = []

    def plan(self, sml_model, lookup=False):
        self.calls.append((sml_model, lookup))
        if isinstance(self.plan_result, Exception):
            raise self.plan_result
        return self.plan_result


class _FakePublisherWithoutPlan:
    pass


def test_preview_uses_publisher_plan_contract_when_available():
    ctx = _make_context()
    raw_plan = {
        "target": "analytics.semantic",
        "views": [
            {
                "name": "`analytics`.`semantic`.`mv_Sales_Model_Fact_Sales`",
                "kind": "metric_view",
                "fact": "Fact Sales",
                "source_table": "`analytics`.`semantic`.`fact_sales`",
                "joins": [
                    {
                        "dataset": "Dim Date",
                        "table": "`analytics`.`semantic`.`dim_date`",
                        "on": "`Date Key` = `Date Key`",
                    }
                ],
                "dimensions": [
                    {"name": "Order_ID", "expr": "`Order_ID`", "display_name": "Order ID", "synonyms": ["ord"]}
                ],
                "measures": [
                    {"name": "Total_Sales", "expr": "SUM(`Amount`)", "status": "ok", "reason": ""},
                    {"name": "Margin_Pct", "expr": "0", "status": "placeholder", "reason": "Complex DAX"},
                ],
                "skipped": False,
                "reason": "",
            },
            {
                "name": "`analytics`.`semantic`.`mv_Sales_Model_Empty`",
                "kind": "metric_view",
                "fact": "Empty",
                "source_table": "`analytics`.`semantic`.`empty`",
                "joins": [],
                "dimensions": [],
                "measures": [],
                "skipped": True,
                "reason": "Missing source table",
            },
        ],
        "missing_tables": ["`analytics`.`semantic`.`empty`"],
        "blocked": [{"table": "fact_sales", "message": "Column mismatch"}],
        "warnings": ["Low confidence DAX measure"],
    }
    fake_pub = _FakePublisherWithPlan(raw_plan)

    plan = preview_databricks_plan(ctx, publisher=fake_pub, lookup=True)
    assert len(fake_pub.calls) == 1
    assert fake_pub.calls[0][1] is True
    assert plan["source"] == "publisher"
    assert plan["target"] == "analytics.semantic"
    assert plan["error"] is None
    assert len(plan["views"]) == 2
    assert plan["views"][0]["measures"][1]["status"] == "placeholder"
    assert plan["views"][1]["skipped"] is True
    assert plan["missing_tables"] == ["`analytics`.`semantic`.`empty`"]
    assert plan["blocked"] == [{"table": "fact_sales", "message": "Column mismatch"}]
    assert plan["warnings"] == ["Low confidence DAX measure"]


def test_preview_falls_back_to_derived_plan_when_publisher_lacks_plan():
    ctx = _make_context(catalog="prod_cat", schema="gold")
    plan = preview_databricks_plan(ctx, publisher=_FakePublisherWithoutPlan())

    assert plan["source"] == "derived"
    assert plan["target"] == "prod_cat.gold"
    assert plan["error"] is None
    assert plan["missing_tables"] == []
    assert plan["blocked"] == []
    assert len(plan["views"]) == 1

    v = plan["views"][0]
    assert v["kind"] == "metric_view"
    assert v["fact"] == "Fact Sales"
    assert v["name"] == "`prod_cat`.`gold`.`mv_Sales_Model_Fact_Sales`"
    assert v["source_table"] == "`prod_cat`.`gold`.`fact_sales`"
    assert len(v["joins"]) == 1
    assert v["joins"][0]["dataset"] == "Dim Date"
    # Hidden column excluded -> 3 dimensions
    assert [d["name"] for d in v["dimensions"]] == ["Order_ID", "Date_Key", "Amount"]
    assert len(v["measures"]) == 2
    by_name = {m["name"]: m for m in v["measures"]}
    assert by_name["Total_Sales"]["status"] == "ok"
    assert by_name["Margin_Pct"]["status"] == "placeholder"
    assert v["skipped"] is False


def test_preview_returns_structured_error_when_publisher_plan_raises():
    ctx = _make_context()
    fake_pub = _FakePublisherWithPlan(RuntimeError("warehouse unavailable"))
    plan = preview_databricks_plan(ctx, publisher=fake_pub)

    assert plan["source"] == "publisher"
    assert plan["error"] == "warehouse unavailable"
    assert plan["views"] == []


def test_target_databricks_config_prefers_payload_catalog_and_schema():
    ctx = _make_context(catalog="custom_cat", schema="custom_schema")
    cfg = target_databricks_config(ctx)
    assert cfg.catalog == "custom_cat"
    assert cfg.schema_name == "custom_schema"


def test_engine_dry_run_for_databricks_populates_databricks_plan(monkeypatch):
    from semabridge.core.engine.engine import ExecutionEngine

    sml = _sample_sml_model()
    expected_plan = {
        "target": "analytics.semantic",
        "source": "publisher",
        "views": [
            {
                "name": "`analytics`.`semantic`.`mv_Sales_Model_Fact_Sales`",
                "kind": "metric_view",
                "fact": "Fact Sales",
                "source_table": "`analytics`.`semantic`.`fact_sales`",
                "joins": [],
                "dimensions": [],
                "measures": [],
                "skipped": False,
                "reason": "",
            }
        ],
        "missing_tables": [],
        "blocked": [],
        "warnings": [],
        "error": None,
    }

    monkeypatch.setattr(
        "semabridge.core.engine.deployment.databricks_preview.preview_databricks_plan",
        lambda context: expected_plan,
    )

    mock_repo = MagicMock()
    engine = ExecutionEngine(db_manager=mock_repo)
    ctx = _make_context(sml)

    monkeypatch.setattr(engine, "_step1_load_config", lambda *a, **kw: ctx.config)
    monkeypatch.setattr(engine, "_step2_init_identifiers", lambda *a, **kw: ctx)
    monkeypatch.setattr(engine, "_step3_resolve_auth", lambda c: None)
    monkeypatch.setattr(engine, "_step4_extract", lambda c, *a, **kw: SimpleNamespace())
    monkeypatch.setattr(engine, "_step5_validate_source", lambda c: None)
    monkeypatch.setattr(engine, "_step6_convert_to_sml", lambda c, *a, **kw: sml)
    monkeypatch.setattr(engine, "_apply_project_ontology", lambda c: None)
    monkeypatch.setattr(engine, "_step7_persist_artifacts", lambda c, tag: None)

    summary = engine.execute(source="pbix", target="databricks", dry_run=True, deploy=False)
    assert summary.status == RunStatus.SUCCESS
    assert ctx.databricks_plan == expected_plan
    assert summary.databricks_plan == expected_plan


def test_mappings_controller_includes_databricks_plan_in_dry_run_result(monkeypatch):
    from semabridge.api.controllers import mappings_controller

    dbx_plan = {
        "target": "main.semabridge",
        "source": "derived",
        "views": [
            {
                "name": "`main`.`semabridge`.`mv_Sales_Fact_Sales`",
                "kind": "metric_view",
                "fact": "Fact Sales",
                "source_table": "`main`.`semabridge`.`fact_sales`",
                "joins": [],
                "dimensions": [{"name": "Amount", "expr": "`Amount`", "display_name": "Amount", "synonyms": []}],
                "measures": [{"name": "Total_Sales", "expr": "SUM(`Amount`)", "status": "ok", "reason": ""}],
                "skipped": False,
                "reason": "",
            }
        ],
        "missing_tables": [],
        "blocked": [],
        "warnings": [],
        "error": None,
    }

    sml_blob = _sample_sml_model().model_dump(mode="json")

    async def _fake_sync_models(payload):
        return {
            "status": "success",
            "results": [
                {
                    "model": "Sales Model",
                    "status": "success",
                    "summary": {
                        "status": "success",
                        "sml_snapshot_id": "snap-dbx-1",
                        "databricks_plan": dbx_plan,
                        "dropped_entities": [],
                        "errors": [],
                    },
                    "model_plan": {"config_hash": "a" * 64, "overrides_hash": "b" * 64},
                }
            ],
        }

    monkeypatch.setattr("semabridge.api.services.core_domain_service.sync_models", _fake_sync_models)
    monkeypatch.setattr(
        "semabridge.api.services.project_shared.db_manager.get_snapshot",
        lambda sid: SimpleNamespace(sml_blob=sml_blob) if sid == "snap-dbx-1" else None,
    )
    monkeypatch.setattr(
        "semabridge.api.services.deployed_name_changes.annotate_dry_run_rows",
        lambda *a, **kw: False,
    )
    monkeypatch.setattr(
        "semabridge.utils.synonyms.load_synonym_overrides",
        lambda *a, **kw: {},
    )

    result = asyncio.run(
        mappings_controller._run_dry_run_pipeline(
            project_id="preview-dbx-1",
            request_user_id=None,
            source_config={"type": "pbix", "pbix_path": "Sales.pbix"},
            target_config={"type": "databricks", "catalog": "main", "schema": "semabridge"},
            selected_sources=["Sales.pbix"],
        )
    )
    assert result["success"] is True
    assert result["databricks_plan"] == dbx_plan
