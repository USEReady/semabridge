"""Golden snapshot of the Phase 3.7 writer's output for a real model.

The committed Competitive Marketing PBIX is converted with the Databricks
rules, matched against an empty output schema (every table proposed, not
created), planned, and every view definition is rendered. The text must match
the fixture byte for byte; any intended output change bumps WRITER_REVISION
and the fixture (run with SEMABRIDGE_UPDATE_GOLDEN=1, review the diff).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling helper module

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "competitive_marketing" / "databricks_views.sql"
UPDATE = os.environ.get("SEMABRIDGE_UPDATE_GOLDEN", "").strip().lower() in ("1", "true", "yes", "on")


def _render() -> str:
    from test_snowflake_golden import _pbix_to_osi
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
    from semabridge.target_backends.databricks.tables import plan_tables, rewrite_measure_columns
    from semabridge.target_backends.databricks.writer import WRITER_REVISION, render_plan
    from semabridge.target_rules import DATABRICKS_RULES

    pbix = next((REPO_ROOT / "Examples").glob("Competitive*.pbix"), None)
    if pbix is None:
        pytest.skip("Competitive Marketing PBIX not present")
    osi, rows = _pbix_to_osi(pbix)
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi, row_counts=rows)
    out = ("workspace", "semabridge_dev")
    tp = plan_tables(sml, {out: {}}, source_schemas=[out], on_missing_source="stub")
    # As Step 6b: measure SQL is rewritten to the (stub) tables' column names before planning.
    rewrite = tp.rewrite_map()
    for metric in sml.metrics:
        metric.sql_expression = rewrite_measure_columns(metric.sql_expression, rewrite)
    plan = plan_metric_views(sml, tp, PlannerSettings(security_roles=0, output_catalog=out[0], output_schema=out[1],
                                                     existing_objects=frozenset())).as_dict()
    plan["meta"] = {"revision": 1, "status": "valid"}
    views = render_plan(plan, catalog=out[0], schema=out[1], model_label="golden")
    parts = [f"-- writer revision {WRITER_REVISION}"]
    for v in views:
        parts.append(f"-- {v.view}  sha256={v.body_sha256}" + "".join(f"\n-- note: {w}" for w in v.warnings))
        parts.append(v.create_sql + ";")
    return "\n\n".join(parts) + "\n"


def test_writer_output_matches_golden(hermetic_golden):
    text = _render()
    if UPDATE or not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes(text.encode("utf-8"))
        if not UPDATE:
            pytest.skip("golden fixture created; review and commit it")
    assert text == FIXTURE.read_bytes().decode("utf-8")


def test_writer_output_is_stable_across_runs(hermetic_golden):
    assert _render() == _render()


@pytest.fixture
def hermetic_golden(monkeypatch, tmp_path):
    """No AI providers, no network, run from a temp folder (as the other goldens)."""
    import socket

    for key in list(os.environ):
        if any(m in key.upper() for m in ("OPENAI", "GEMINI", "FEATHERLESS", "ANTHROPIC", "LLM", "API_KEY")):
            monkeypatch.delenv(key, raising=False)

    def _no_network(*_a, **_k):
        raise RuntimeError("network disabled in golden tests")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEMABRIDGE_ALLOW_LOCAL_DB", "true")
