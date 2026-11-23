"""Phase 3.2: Step 6 translates measures with the target platform's rules.

Databricks SQL only for a Databricks target with the planner path switched
on; every other run (Snowflake, Fabric, Databricks with the switch off) gets
exactly today's Snowflake SQL. Runs the engine's real Step 6 functions on the
committed Competitive Marketing .pbix, hermetically (no AI tier, no network,
temp folder).
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.conversion.rules_choice import conversion_rules, databricks_planner_enabled
from semabridge.target_rules import DATABRICKS_RULES

REPO_ROOT = Path(__file__).resolve().parents[2]
PBIX = REPO_ROOT / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix"
_AI_ENV_MARKERS = ("OPENAI", "GEMINI", "FEATHERLESS", "ANTHROPIC", "LLM", "API_KEY")


def _behavior(on: bool) -> ConnectorBehavior:
    return ConnectorBehavior(**{"databricks": {"use_planner_path": on}})


def _ctx(target: Optional[str], on: bool) -> Any:
    return SimpleNamespace(target_type=target, behavior=_behavior(on))


# --- the choice itself ----------------------------------------------------------


@pytest.mark.parametrize(
    "target, on, expected",
    [
        ("databricks", True, DATABRICKS_RULES),
        ("Databricks", True, DATABRICKS_RULES),
        ("databricks", False, None),
        ("snowflake", True, None),
        ("snowflake", False, None),
        ("fabric", True, None),
        (None, True, None),
    ],
)
def test_rules_choice(target, on, expected) -> None:
    assert conversion_rules(_ctx(target, on)) is expected
    assert databricks_planner_enabled(_ctx(target, on)) is (expected is not None)


def test_missing_behavior_means_default() -> None:
    assert conversion_rules(SimpleNamespace(target_type="databricks")) is None
    assert conversion_rules(SimpleNamespace(target_type="databricks", behavior=None)) is None


# --- the engine's real Step 6 ----------------------------------------------------------


@pytest.fixture
def hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if any(marker in key.upper() for marker in _AI_ENV_MARKERS):
            monkeypatch.delenv(key, raising=False)
    from semabridge.dax_translation.tier5.service import Tier5Service

    monkeypatch.setattr(Tier5Service, "translate", lambda self, request: None)
    monkeypatch.setattr(Tier5Service, "translate_batch", lambda self, requests: [None] * len(requests))

    def _no_network(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("network access is blocked in this test")

    monkeypatch.setattr(socket.socket, "connect", _no_network)


@pytest.fixture(scope="module")
def tmsl() -> Any:
    if not PBIX.exists():
        pytest.skip("Competitive Marketing .pbix not available")
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector

    connector = LocalPBIXConnector({"pbix_path": str(PBIX)})
    discovered = connector.discover()
    return connector.extract() if discovered.get("raw_tmsl") is None else discovered["raw_tmsl"]


def _run_step6(tmsl: Any, source: str, target: Optional[str], on: bool) -> List[Any]:
    from semabridge.core.engine.context import RunContext
    from semabridge.core.execution_engine import ExecutionEngine
    from semabridge.core.source_format import from_pbix_tmsl

    source_format = from_pbix_tmsl(project_id="p", run_id="r", tmsl=tmsl, pbix_path=str(PBIX))
    context = RunContext(
        project_id="p",
        run_id="r",
        config=SimpleNamespace(),
        start_time=time.time(),
        source_type=source,
        target_type=target,
        behavior=_behavior(on),
        source_format=source_format,
    )
    engine = ExecutionEngine()
    if source == "fabric":
        sml = engine._convert_fabric_to_sml(context, "ws", "ds")
    else:
        sml = engine._convert_pbix_to_sml(context)
    return [(m.unique_name, m.sql_expression, m.sync_enabled, m.sync_failure_reason) for m in sml.metrics]


@pytest.mark.parametrize("source", ["pbix", "fabric"])
def test_databricks_planner_run_gets_databricks_sql(tmsl, hermetic, source) -> None:
    metrics = _run_step6(tmsl, source, "databricks", on=True)
    converted = [sql for _, sql, ok, _ in metrics if sql and ok is not False]
    assert converted
    assert all('."' not in sql and "::FLOAT" not in sql for sql in converted)
    assert any("`" in sql for sql in converted)
    for _, sql, ok, reason in metrics:
        if ok is False and sql:
            assert DATABRICKS_RULES.policies.is_placeholder(sql) and reason


@pytest.mark.parametrize("source", ["pbix", "fabric"])
def test_every_other_run_is_unchanged(tmsl, hermetic, source) -> None:
    """Snowflake target, Databricks with the switch off, and Snowflake with
    the switch on all produce the same (today's Snowflake) measures."""
    baseline = _run_step6(tmsl, source, "snowflake", on=False)
    assert any('."' in (sql or "") for _, sql, _, _ in baseline)  # Snowflake quoting
    assert _run_step6(tmsl, source, "databricks", on=False) == baseline
    assert _run_step6(tmsl, source, "snowflake", on=True) == baseline
