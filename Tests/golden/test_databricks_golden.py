"""Golden tests: the Databricks SQL Step 6 writes for real .pbix models (Phase 3.2).

When a run targets Databricks with the planner path on, Step 6 translates
every measure with the Databricks rules and Step 7 saves that SQL in the
snapshot (what the dry-run / Ontology / Comparator screens show). This
records, per measure, the SQL, kept/declined status and reason, and fails
on any difference, so a later change to the shared translator or the
Databricks rules cannot alter Databricks output unnoticed.

Same models, hermetic setup and update switch as test_snowflake_golden.py
(no AI tier, no network, temp folder; SEMABRIDGE_UPDATE_GOLDEN=1 rewrites the
fixture after an intended change). Only the conversion is captured: the
Databricks deploy side (planner, YAML) is covered by its own steps.

A summary line per model compares how many measures each platform converts,
so coverage gaps against Snowflake stay visible in the fixture diff.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from test_snowflake_golden import (  # same folder (pytest puts it on sys.path)
    FIXTURES,
    MODELS,
    UPDATE,
    _metric_record,
    _normalise,
    _pbix_to_osi,
    hermetic,  # noqa: F401  (fixture used by the tests below)
)

FIXTURE_NAME = "databricks_metrics.json"


def _converted(metrics: List[Any], rules: Any) -> int:
    return sum(
        1
        for m in metrics
        if m.sql_expression and m.sync_enabled is not False and not rules.policies.is_placeholder(m.sql_expression)
    )


def _capture(pbix_path: Path) -> str:
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_rules import DATABRICKS_RULES, SNOWFLAKE_RULES

    osi_model, row_counts = _pbix_to_osi(pbix_path)
    databricks = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi_model, row_counts=row_counts)
    snowflake = OSIToSMLConverter().from_osi(osi_model, row_counts=row_counts)

    snowflake_ok = {
        m.unique_name
        for m in snowflake.metrics
        if m.sql_expression and m.sync_enabled is not False and not SNOWFLAKE_RULES.policies.is_placeholder(m.sql_expression)
    }
    databricks_ok = {
        m.unique_name
        for m in databricks.metrics
        if m.sql_expression and m.sync_enabled is not False and not DATABRICKS_RULES.policies.is_placeholder(m.sql_expression)
    }
    doc: Dict[str, Any] = {
        "metric_count": len(databricks.metrics),
        "coverage": {
            "snowflake_converted": len(snowflake_ok),
            "databricks_converted": len(databricks_ok),
            "converted_on_snowflake_only": sorted(snowflake_ok - databricks_ok),
            "converted_on_databricks_only": sorted(databricks_ok - snowflake_ok),
        },
        "metrics": [_metric_record(m) for m in databricks.metrics],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


@pytest.mark.parametrize("model", sorted(MODELS))
def test_databricks_conversion_matches_golden(model: str, hermetic: None) -> None:  # noqa: F811
    pbix_path = MODELS[model]
    if pbix_path is None:
        pytest.skip(f"{model}: set SEMABRIDGE_GOLDEN_PBIX_DIR to run this model")
    if not pbix_path.exists():
        pytest.skip(f"{model}: {pbix_path.name} not found")

    actual = _capture(pbix_path)
    path = FIXTURES / model / FIXTURE_NAME

    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not path.exists():
        pytest.fail(f"{model}: no {path}. Create it with SEMABRIDGE_UPDATE_GOLDEN=1 and commit it.")

    expected = _normalise(path.read_text(encoding="utf-8"))
    if expected != actual:
        diff = list(
            difflib.unified_diff(
                expected.splitlines(), actual.splitlines(), fromfile=f"golden/{model}/{FIXTURE_NAME}",
                tofile="actual", lineterm="", n=2,
            )
        )
        shown = "\n".join(diff[:80])
        more = f"\n... {len(diff) - 80} more diff lines" if len(diff) > 80 else ""
        pytest.fail(
            f"Databricks conversion output changed for {model}.\n"
            "If this change is intended, rerun with SEMABRIDGE_UPDATE_GOLDEN=1 and commit the fixture diff.\n\n"
            + shown + more
        )


@pytest.mark.parametrize("model", sorted(MODELS))
def test_no_snowflake_spelling_in_databricks_measures(model: str, hermetic: None) -> None:  # noqa: F811
    """Every measure the Databricks rules convert is written in Databricks
    spelling (independent of the fixture, so a fixture update can't hide a leak)."""
    import re

    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_rules import DATABRICKS_RULES

    pbix_path = MODELS[model]
    if pbix_path is None or not pbix_path.exists():
        pytest.skip(f"{model}: .pbix not available")
    osi_model, row_counts = _pbix_to_osi(pbix_path)
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi_model, row_counts=row_counts)

    snowflake_only = [
        r'\."', r"::FLOAT\b", r"(?<![A-Za-z_])IFF\(", r"(?<![A-Za-z_])TRY_TO_NUMBER\(",
        r"(?<![A-Za-z_])TO_VARCHAR\(", r"(?<![A-Za-z_])DATEADD\(", r"PARTITION BY EXCLUDING",
    ]
    converted = [m for m in sml.metrics if m.sql_expression and m.sync_enabled is not False]
    assert converted, "some measures convert"
    for m in converted:
        for pattern in snowflake_only:
            assert not re.search(pattern, m.sql_expression), (m.unique_name, pattern, m.sql_expression)
    # Declined measures carry the Databricks placeholder and a reason.
    for m in sml.metrics:
        if m.sync_enabled is False and m.sql_expression:
            assert DATABRICKS_RULES.policies.is_placeholder(m.sql_expression), (m.unique_name, m.sql_expression)
            assert m.sync_failure_reason, m.unique_name
