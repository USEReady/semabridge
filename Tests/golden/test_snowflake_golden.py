"""Golden tests: today's Snowflake output for real .pbix models.

Purpose: the Databricks work (Docs: "Power BI to Databricks, Revised
Approach", Phase 1) refactors code the Snowflake path shares -- the DAX
translator, osi_to_sml and the decline rules. These tests record what the
Snowflake path produces today and fail on any difference, so every refactor
step can show that Snowflake's output did not change.

For each model this captures, from the .pbix with no connection to anything:
  * metrics.json -- per measure: DAX, the translated SQL straight after
    conversion (OSI -> SML), after step 6b (time-intelligence flag columns,
    which re-renders some measures) and after the emitter ran (which can
    rewrite more), kept/declined status and the reason;
  * ddl.sql      -- SnowflakeEmitter.generate_ddls (semantic view DDL);
  * cortex.yaml  -- SnowflakeEmitter.generate_cortex_yaml.

Hermetic by design:
  * the AI translation tier (Tier5Service) is stubbed to "no answer", so
    results never depend on API keys or the shared on-disk AI cache;
  * outbound network connections raise, so nothing can reach a warehouse;
  * it runs from a temp folder, so debug files never land in the checkout;
  * ConnectorBehavior() defaults are used, not Config/behavior.yaml.

Models:
  * competitive_marketing -- Examples/ (committed), always runs;
  * store_sales, revenue_opportunities -- not in the repo (public). Set
    SEMABRIDGE_GOLDEN_PBIX_DIR to a folder holding Store_Sales.pbix /
    Revenue_Opportunities.pbix; skipped when absent.

Updating after an INTENDED output change: run with SEMABRIDGE_UPDATE_GOLDEN=1,
review the fixture diff, and commit it with the change that caused it.
"""

from __future__ import annotations

import difflib
import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

UPDATE = os.environ.get("SEMABRIDGE_UPDATE_GOLDEN", "").strip().lower() in ("1", "true", "yes", "on")

# Fixed names so nothing about the machine, project or run leaks into output.
GOLDEN_NAME = "golden"
GOLDEN_DATABASE = "GOLDEN_DB"
GOLDEN_SCHEMA = "GOLDEN_SCHEMA"

# Environment variables that enable an AI provider; removed for the test.
_AI_ENV_MARKERS = ("OPENAI", "GEMINI", "FEATHERLESS", "ANTHROPIC", "LLM", "API_KEY")


def _external_pbix(file_name: str) -> Optional[Path]:
    folder = os.environ.get("SEMABRIDGE_GOLDEN_PBIX_DIR", "").strip()
    return Path(folder) / file_name if folder else None


MODELS: Dict[str, Optional[Path]] = {
    "competitive_marketing": REPO_ROOT / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix",
    "store_sales": _external_pbix("Store_Sales.pbix"),
    "revenue_opportunities": _external_pbix("Revenue_Opportunities.pbix"),
}


@pytest.fixture
def hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No AI provider, no AI cache, no network -- for this test only.

    Also runs from a temp folder: conversion writes debug artifacts to a
    relative output/debug/ path (tmsl_to_osi.py), which must not land in
    the checkout.
    """
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if any(marker in key.upper() for marker in _AI_ENV_MARKERS):
            monkeypatch.delenv(key, raising=False)

    from semabridge.dax_translation.tier5.service import Tier5Service

    monkeypatch.setattr(Tier5Service, "translate", lambda self, request: None)
    monkeypatch.setattr(Tier5Service, "translate_batch", lambda self, requests: [None] * len(requests))

    def _no_network(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("network access is blocked in golden tests")

    monkeypatch.setattr(socket.socket, "connect", _no_network)


def _pbix_to_sml(pbix_path: Path):
    """Same calls the engine makes in steps 4 and 6 for a .pbix source
    (core/engine/extraction/pbix.py, core/engine/conversion/pbix.py)."""
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.core.source_format import from_pbix_tmsl

    connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
    discovered = connector.discover()
    tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered["raw_tmsl"]
    source_format = from_pbix_tmsl(
        project_id=GOLDEN_NAME,
        run_id=GOLDEN_NAME,
        tmsl=tmsl,
        pbix_path=str(pbix_path),
        field_aliases=discovered.get("field_aliases", []),
        unresolved_report_field_references=discovered.get("unresolved_report_field_references", []),
        ambiguous_report_aliases=discovered.get("ambiguous_report_aliases", []),
    )
    osi_model = TMSLToOSIConverter().to_osi(
        {
            "tmsl": source_format.tmsl_definition,
            "workspace_id": "local",
            "dataset_id": GOLDEN_NAME,
            "display_name": GOLDEN_NAME,
            "project_id": GOLDEN_NAME,
            "field_aliases": getattr(source_format, "field_aliases", []),
            "source_file": None,
            "infer_relationships": None,
        }
    )
    return OSIToSMLConverter().from_osi(osi_model, row_counts=source_format.row_counts)


def _build_emitter():
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.settings import SnowflakeConfig

    config = SnowflakeConfig(
        account="golden.local",
        user="golden_user",
        password="golden_password",
        warehouse="GOLDEN_WH",
        database=GOLDEN_DATABASE,
        schema_name=GOLDEN_SCHEMA,
        role="GOLDEN_ROLE",
    )
    return SnowflakeEmitter(config, ConnectorBehavior())


def _metric_record(metric: Any) -> Dict[str, Any]:
    non_additive = metric.non_additive_dimension
    return {
        "name": metric.unique_name,
        "dataset": metric.dataset,
        "dax": metric.expression,
        "sql_after_convert": metric.sql_expression,
        "sync_enabled": metric.sync_enabled,
        "sync_failure_reason": metric.sync_failure_reason,
        "complexity_tier": metric.complexity_tier,
        "depends_on_measures": sorted(metric.depends_on_measures or []),
        "non_additive_dimension": list(non_additive) if non_additive else None,
        "is_placeholder": metric.is_placeholder,
        "advisory_categories": list(metric.advisory_categories or []),
    }


def _predict_anchor_flags(sml: Any, emitter: Any) -> None:
    """Engine step 6b for a Snowflake target (core/engine/targets/snowflake.py,
    _step6b_predict_anchor_flag_columns): predict the time-intelligence flag
    columns offline and re-render the measures that depend on them. Unlike
    the engine this does not swallow errors, so a break here fails the test.
    """
    from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
    from semabridge.converter.dax_translator import DAXTranslator

    predicted_map = emitter.predict_anchor_flag_map(sml)
    if not predicted_map:
        return
    dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(getattr(sml, "datasets", []) or [])
    rerender_anchor_dependent_metrics(sml, predicted_map, dataset_col_lookup, dataset_aliases, label="predicted")


def _capture(pbix_path: Path) -> Dict[str, str]:
    sml = _pbix_to_sml(pbix_path)
    records: List[Dict[str, Any]] = [_metric_record(m) for m in sml.metrics]

    # Step 6b (time-intelligence flag columns), as the engine runs it before
    # persisting the model for a Snowflake target.
    _predict_anchor_flags(sml, _build_emitter())
    after_anchor = {m.unique_name: m.sql_expression for m in sml.metrics}
    for record in records:
        record["sql_after_anchor"] = after_anchor.get(record["name"])

    emitter = _build_emitter()
    ddl = "\n\n".join(emitter.generate_ddls(sml))
    cortex_yaml = emitter.generate_cortex_yaml(sml)

    # The emitter rewrites some measures' SQL in place; record the final SQL
    # and status next to the post-conversion values.
    by_name = {m.unique_name: m for m in sml.metrics}
    for record in records:
        metric = by_name.get(record["name"])
        record["sql_after_emit"] = metric.sql_expression if metric else None
        record["sync_enabled_after_emit"] = metric.sync_enabled if metric else None
    emitted_only = sorted(set(by_name) - {r["name"] for r in records})

    metrics_doc = {
        "metric_count": len(records),
        "metrics": records,
        "metrics_added_by_emitter": emitted_only,
    }
    return {
        "metrics.json": json.dumps(metrics_doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        "ddl.sql": ddl.rstrip("\n") + "\n",
        "cortex.yaml": cortex_yaml.rstrip("\n") + "\n",
    }


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n")


@pytest.mark.parametrize("model", sorted(MODELS))
def test_snowflake_output_matches_golden(model: str, hermetic: None) -> None:
    pbix_path = MODELS[model]
    if pbix_path is None:
        pytest.skip(f"{model}: set SEMABRIDGE_GOLDEN_PBIX_DIR to run this model")
    if not pbix_path.exists():
        pytest.skip(f"{model}: {pbix_path.name} not found")

    actual = _capture(pbix_path)
    model_dir = FIXTURES / model

    if UPDATE:
        model_dir.mkdir(parents=True, exist_ok=True)
        for file_name, content in actual.items():
            (model_dir / file_name).write_text(content, encoding="utf-8", newline="\n")
        return

    missing = [name for name in actual if not (model_dir / name).exists()]
    if missing:
        pytest.fail(
            f"{model}: no golden files {missing} in {model_dir}. "
            "Create them with SEMABRIDGE_UPDATE_GOLDEN=1 and commit them."
        )

    problems: List[str] = []
    for file_name, content in actual.items():
        expected = _normalise((model_dir / file_name).read_text(encoding="utf-8"))
        if expected != content:
            diff = list(
                difflib.unified_diff(
                    expected.splitlines(),
                    content.splitlines(),
                    fromfile=f"golden/{model}/{file_name}",
                    tofile="actual",
                    lineterm="",
                    n=2,
                )
            )
            shown = "\n".join(diff[:80])
            more = f"\n... {len(diff) - 80} more diff lines" if len(diff) > 80 else ""
            problems.append(f"{file_name} differs:\n{shown}{more}")

    if problems:
        pytest.fail(
            f"Snowflake output changed for {model}.\n"
            "If this change is intended, rerun with SEMABRIDGE_UPDATE_GOLDEN=1 and commit the fixture diff.\n\n"
            + "\n\n".join(problems)
        )
