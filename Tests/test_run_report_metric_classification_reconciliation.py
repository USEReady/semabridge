"""Regression tests for the metric double-counting bug in
run_report_service.py: a metric could appear in BOTH "Standard
conversion"/"AI-assisted" AND a dropped-entities section, with mutually
exclusive outcomes for the same metric.

Root cause: _classify_metrics() classified a metric as converted based
solely on the persisted SML snapshot's `sync_enabled` flag, which is set
exactly once at Stage 6 (DAX translation, converter/tmsl_to_sml.py /
converter/osi_to_sml.py) and never revised when a LATER stage -- DDL
emission/deployment, connectors/metrics_clause_builder.py -- drops that
same metric for an unrelated, later-discovered reason. The two report
sections came from two independent, never-cross-checked data sources.

Confirmed against a real run's report (run-1788863859037, project
proj-pbix_multi_test13): "Net Sales" (and ~15 others) appeared in both
"### Standard conversion" (converted) and "### While preparing
calculations for deployment" ("refers to a data field that couldn't be
matched to anything in the source model") in the SAME single-PBIX-file
report -- impossible for two distinct metrics, since Power BI enforces
unique measure names within one model.

Fix: _classify_metrics() now takes `dropped_metric_names` and excludes
any metric already recorded in this run's dropped_entities, regardless
of what its stale sync_enabled flag says -- the later pipeline stage
(actual deployment outcome) wins.
"""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_run_report_metric_classification_reconciliation.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

import semabridge.api.services.run_report_service as svc


def _metric(name, sync_enabled=True, tier=1, **overrides):
    m = {"unique_name": name, "label": name, "sync_enabled": sync_enabled, "complexity_tier": tier}
    m.update(overrides)
    return m


def _dropped(name, stage, entity_kind="metric", by_design=False, reason="dropped"):
    return {"entity_kind": entity_kind, "entity_name": name, "stage": stage, "by_design": by_design, "reason": reason}


# ---------------------------------------------------------------------------
# 1. _classify_metrics: direct unit coverage of the exclusion rule
# ---------------------------------------------------------------------------

def test_classify_metrics_excludes_a_metric_present_in_dropped_names_even_when_sync_enabled():
    metrics = [
        _metric("Net Sales", sync_enabled=True),   # stale flag -- really dropped later
        _metric("Clean Metric", sync_enabled=True),  # genuinely converted, never dropped
    ]
    classified = svc._classify_metrics(metrics, dropped_metric_names={"Net Sales"})

    names = {e["name"] for e in classified["standard"] + classified["ai_assisted"]}
    assert "Net Sales" not in names, "a metric present in dropped_entities must never also show as converted"
    assert "Clean Metric" in names


def test_classify_metrics_without_dropped_names_arg_behaves_as_before():
    """Backward compatible: omitting dropped_metric_names classifies
    purely off sync_enabled, same as before this fix -- callers that
    don't have a dropped-names set yet must not break."""
    metrics = [_metric("Solo Metric", sync_enabled=True)]
    classified = svc._classify_metrics(metrics)
    assert classified["standard"][0]["name"] == "Solo Metric"


def test_classify_metrics_exclusion_applies_to_ai_assisted_tier_too():
    metrics = [_metric("Risky AI Metric", sync_enabled=True, tier=5)]
    classified = svc._classify_metrics(metrics, dropped_metric_names={"Risky AI Metric"})
    assert classified["standard"] == []
    assert classified["ai_assisted"] == []


# ---------------------------------------------------------------------------
# 2. Full JSON pipeline (build_run_report_data / RunReportSummary.jsx's
#    data source) -- reproduces the real "Net Sales" incident shape.
# ---------------------------------------------------------------------------

_REAL_INCIDENT_METRICS = [
    _metric("Net Sales", sync_enabled=True),
    _metric("Net Sales PM", sync_enabled=True),
    _metric("Units Sold", sync_enabled=True),
    _metric("Clean Standard Metric", sync_enabled=True),
    _metric("Clean AI Metric", sync_enabled=True, tier=5),
    _metric("Genuinely Untranslatable", sync_enabled=False),  # never converted, drop-ledger-only
]

_REAL_INCIDENT_DROPPED = [
    # The exact real shape: a metric with sync_enabled=True in the
    # snapshot, ALSO recorded dropped at a later (deployment) stage.
    _dropped("Net Sales", "ddl_emission", reason="refers to a data field that couldn't be matched to anything in the source model"),
    _dropped("Net Sales PM", "ddl_emission", reason="refers to a data field that couldn't be matched to anything in the source model"),
    _dropped("Units Sold", "ddl_emission", reason="refers to a data field that couldn't be matched to anything in the source model"),
    _dropped("Genuinely Untranslatable", "dax_translation", reason="formula too complex"),
]


def _real_incident_run():
    return {
        "project_id": "proj1", "project_name": "Proj One", "run_id": "run-1",
        "status": "success",
        "results": [{
            "model": "Sales Model",
            "summary": {"sml_snapshot_id": "snap-1", "target_type": "snowflake"},
            "dropped_entities": _REAL_INCIDENT_DROPPED,
        }],
    }


def _patch_snapshot(monkeypatch, metrics):
    monkeypatch.setattr(
        svc, "_load_snapshot_data",
        lambda snapshot_id: {
            "dataset_count": 1, "column_count": 1, "relationship_count": 0,
            "metrics": metrics,
        } if snapshot_id == "snap-1" else None,
    )


def test_json_report_never_double_counts_the_real_incident_shape(monkeypatch):
    _patch_snapshot(monkeypatch, _REAL_INCIDENT_METRICS)
    data = svc.build_run_report_data(_real_incident_run(), "")

    standard_names = {e["name"] for e in data["sections"]["standard"]}
    ai_names = {e["name"] for e in data["sections"]["ai_assisted"]}
    dropped_names = {
        item["name"]
        for group in data["sections"]["dropped"]
        for item in group["items"]
    }

    for name in ("Net Sales", "Net Sales PM", "Units Sold"):
        assert name not in standard_names, f"{name} must not appear as converted -- it was dropped at a later stage"
        assert name in dropped_names, f"{name} must still appear exactly once, in the dropped section"

    assert "Clean Standard Metric" in standard_names
    assert "Clean AI Metric" in ai_names

    # No name appears in more than one bucket.
    all_buckets = [standard_names, ai_names, dropped_names]
    for i, bucket_a in enumerate(all_buckets):
        for bucket_b in all_buckets[i + 1:]:
            assert not (bucket_a & bucket_b), f"a metric name appears in more than one bucket: {bucket_a & bucket_b}"


def test_markdown_report_never_double_counts_the_real_incident_shape(monkeypatch):
    _patch_snapshot(monkeypatch, _REAL_INCIDENT_METRICS)
    md = svc.generate_run_report_markdown(_real_incident_run(), "")

    standard_block = md.split("### Standard conversion")[1].split("###")[0] if "### Standard conversion" in md else ""
    dropped_block = "\n".join(md.split("## Not Included")[1:]) if "## Not Included" in md else ""

    for name in ("Net Sales", "Net Sales PM", "Units Sold"):
        assert name not in standard_block, f"{name} must not be listed under Standard conversion -- it was dropped later"
        assert name in dropped_block, f"{name} must still be listed exactly once, under why it was dropped"


# ---------------------------------------------------------------------------
# 3. General reconciliation invariant: sum(all buckets) == total metric
#    count, and no metric name appears in more than one bucket -- the
#    guard the user asked for so this can't silently regress.
# ---------------------------------------------------------------------------

def test_bucket_totals_reconcile_against_total_metric_count(monkeypatch):
    metrics = [
        _metric("Standard A", sync_enabled=True),
        _metric("Standard B", sync_enabled=True),
        _metric("AI Assisted A", sync_enabled=True, tier=5),
        _metric("Dropped At Translation", sync_enabled=False),
        _metric("Dropped At Emission", sync_enabled=True),   # stale flag -- the bug shape
        _metric("Excluded By Design", sync_enabled=True),    # stale flag -- the bug shape
    ]
    dropped_entities = [
        _dropped("Dropped At Translation", "dax_translation"),
        _dropped("Dropped At Emission", "ddl_emission"),
        _dropped("Excluded By Design", "ddl_emission", by_design=True, reason="not part of your real business data"),
    ]
    run = {
        "project_id": "proj1", "project_name": "Proj One", "run_id": "run-1",
        "status": "success",
        "results": [{
            "model": "Model A",
            "summary": {"sml_snapshot_id": "snap-1", "target_type": "snowflake"},
            "dropped_entities": dropped_entities,
        }],
    }
    _patch_snapshot(monkeypatch, metrics)
    data = svc.build_run_report_data(run, "")

    counts = data["counts"]
    total_bucketed = (
        counts["standard"] + counts["ai_assisted"] + counts["needs_review"]
        + counts["dropped"] + counts["excluded_by_design"]
    )
    assert total_bucketed == len(metrics) == counts["calculations"], (
        f"bucket totals ({total_bucketed}) must reconcile exactly against the total "
        f"calculation count ({len(metrics)}) -- a mismatch means a metric was counted "
        "more than once (or not at all)"
    )

    # Cross-bucket uniqueness: collect every metric name from every section
    # and confirm none repeats.
    all_names = []
    all_names.extend(e["name"] for e in data["sections"]["standard"])
    all_names.extend(e["name"] for e in data["sections"]["ai_assisted"])
    for group in data["sections"]["dropped"]:
        all_names.extend(item["name"] for item in group["items"])
    for group in data["sections"]["excluded_by_design"]:
        all_names.extend(item["name"] for item in group["items"])

    assert len(all_names) == len(set(all_names)), (
        f"a metric name appears in more than one bucket: "
        f"{[n for n in set(all_names) if all_names.count(n) > 1]}"
    )
