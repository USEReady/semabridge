"""Tests for the run-report Markdown readability redesign
(generate_run_report_markdown in run_report_service.py).

Explicitly NOT about the metric double-counting fix (see
test_run_report_metric_classification_reconciliation.py) -- this file
covers the separate, later readability pass: status immediately visible,
a compact "At a Glance" summary ahead of the detail, grouped issues
instead of long repeated-sentence lists, and zero information loss (every
name and every distinct reason from the input must still be findable in
the output; the redesign reorganizes, it never drops content).
"""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_run_report_markdown_redesign.sqlite")
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


def _patch_snapshot(monkeypatch, metrics, snapshot_id="snap-1", dataset_count=5, column_count=20, relationship_count=2):
    monkeypatch.setattr(
        svc, "_load_snapshot_data",
        lambda sid: {
            "dataset_count": dataset_count, "column_count": column_count,
            "relationship_count": relationship_count, "metrics": metrics,
        } if sid == snapshot_id else None,
    )


def _run(dropped_entities, snapshot_id="snap-1", status="success"):
    return {
        "project_id": "proj1", "project_name": "Proj One", "run_id": "run-1", "status": status,
        "results": [{
            "model": "Model A",
            "summary": {"sml_snapshot_id": snapshot_id, "target_type": "snowflake"},
            "dropped_entities": dropped_entities,
        }],
    }


# ---------------------------------------------------------------------------
# 1. Status immediately clear, at-a-glance summary ahead of the detail
# ---------------------------------------------------------------------------

def test_status_line_is_within_the_first_few_lines(monkeypatch):
    _patch_snapshot(monkeypatch, [_metric("A")])
    md = svc.generate_run_report_markdown(_run([]), "")
    head = "\n".join(md.splitlines()[:5])
    assert "✅" in head or "❌" in head or "⚠️" in head


def test_at_a_glance_appears_before_converted_and_dropped_sections(monkeypatch):
    _patch_snapshot(monkeypatch, [_metric("A"), _metric("B", sync_enabled=False)])
    md = svc.generate_run_report_markdown(_run([_dropped("B", "dax_translation")]), "")

    glance_pos = md.index("## At a Glance")
    converted_pos = md.index("## Converted Calculations")
    dropped_pos = md.index("## Not Included")
    assert glance_pos < converted_pos < dropped_pos, "summary must come first, full detail must come after"


def test_at_a_glance_shows_the_conversion_breakdown_counts(monkeypatch):
    metrics = [
        _metric("Standard A"), _metric("Standard B"),
        _metric("AI A", tier=5),
        _metric("Dropped A", sync_enabled=False),
    ]
    _patch_snapshot(monkeypatch, metrics)
    md = svc.generate_run_report_markdown(_run([_dropped("Dropped A", "dax_translation")]), "")

    glance = md.split("## At a Glance")[1].split("## ")[0]
    assert "✅ Standard conversion | 2" in glance
    assert "🤖 AI-assisted | 1" in glance
    assert "❌ Not included | 1" in glance
    assert "3 of 4 calculations converted" in glance


# ---------------------------------------------------------------------------
# 2. Grouped issues -- a repeated reason is stated once, not once per item
# ---------------------------------------------------------------------------

def test_identical_drop_reason_is_stated_once_even_for_many_items(monkeypatch):
    names = [f"Metric {i}" for i in range(10)]
    metrics = [_metric(n, sync_enabled=False) for n in names]
    dropped = [_dropped(n, "dax_translation", reason="formula too complex") for n in names]
    _patch_snapshot(monkeypatch, metrics)
    md = svc.generate_run_report_markdown(_run(dropped), "")

    dropped_section = md.split("## Not Included")[1]
    reason_sentence = svc.humanize_drop_reason(dropped[0])
    # The plain-language reason sentence must appear exactly once -- not
    # once per item, which is the exact repetition this redesign removes
    # (a real report repeated an identical sentence 33 times in a row).
    assert dropped_section.count(reason_sentence) == 1
    # But every single metric name must still be present somewhere.
    for n in names:
        assert n in dropped_section


def test_by_design_and_non_by_design_entries_are_never_merged_into_one_group(monkeypatch):
    """Even when the humanized reason text happens to coincide, a
    by-design (expected, not an error) entry must stay visually distinct
    from a genuine failure -- grouping must key on (reason, by_design),
    not reason alone."""
    metrics = [_metric("Real Failure", sync_enabled=False), _metric("Expected Exclusion", sync_enabled=False)]
    dropped = [
        _dropped("Real Failure", "ddl_emission", by_design=False, reason="xyz-unmatched-reason"),
        _dropped("Expected Exclusion", "ddl_emission", by_design=True, reason="xyz-unmatched-reason"),
    ]
    _patch_snapshot(monkeypatch, metrics)
    md = svc.generate_run_report_markdown(_run(dropped), "")

    dropped_section = md.split("## Not Included")[1]
    # Two separate table rows -- one plain, one tagged (expected) -- not
    # one row silently containing both names under a single label.
    assert "_(expected)_" in dropped_section
    lines_with_names = [
        line for line in dropped_section.splitlines()
        if "Real Failure" in line or "Expected Exclusion" in line
    ]
    assert not any("Real Failure" in line and "Expected Exclusion" in line for line in lines_with_names), (
        "a genuine failure and an expected/by-design exclusion must never share one row"
    )


# ---------------------------------------------------------------------------
# 3. Zero information loss: every name and reason from the input survives
# ---------------------------------------------------------------------------

def test_every_metric_name_appears_somewhere_regardless_of_outcome(monkeypatch):
    metrics = [
        _metric("Converted Clean"),
        _metric("Converted Flagged", advisory_categories=["some_category"], advisory_notes=True),
        _metric("AI Metric", tier=5),
        _metric("Dropped Translation", sync_enabled=False),
        _metric("Dropped Emission"),  # stale sync_enabled=True, dropped later
    ]
    dropped = [
        _dropped("Dropped Translation", "dax_translation", reason="formula too complex"),
        _dropped("Dropped Emission", "ddl_emission", reason="could not be resolved after normalization"),
    ]
    _patch_snapshot(monkeypatch, metrics)
    md = svc.generate_run_report_markdown(_run(dropped), "")

    for name in ("Converted Clean", "Converted Flagged", "AI Metric", "Dropped Translation", "Dropped Emission"):
        assert name in md, f"{name} must still appear somewhere in the redesigned report"


def test_ai_assisted_confidence_and_risk_notes_are_preserved(monkeypatch):
    metrics = [_metric("Risky One", tier=5, llm_self_reported_confidence=0.55, validation_notes="matches risky pattern")]
    _patch_snapshot(monkeypatch, metrics)
    md = svc.generate_run_report_markdown(_run([]), "")

    ai_section = md.split("### AI-assisted conversion")[1].split("## ")[0]
    assert "55%" in ai_section
    assert "review closely" in ai_section.lower()
