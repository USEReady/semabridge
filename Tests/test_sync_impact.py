"""Impact before a sync: each field a Copy would remove shows what depends on it."""
from types import SimpleNamespace

from semabridge.governance import impact


class _Sim:
    def simulate_impact(self, target_name, target_type, change_type, mock_mode):
        assert mock_mode is False  # live only
        if target_name == "Revenue":
            return SimpleNamespace(
                severity="BREAKING",
                affected_metrics=[SimpleNamespace(name="Revenue"), SimpleNamespace(name="Margin %")],
                affected_ai_tools=[SimpleNamespace(name="What is revenue by region?")],
            )
        raise ValueError("not found")


def test_removed_fields_are_annotated(monkeypatch):
    monkeypatch.setattr(impact, "_simulator", lambda pid: (_Sim(), {"revenue"}))
    findings = [{"model": "Sales", "view_name": "SALES_SV", "count": 2,
                 "removed": [{"name": "Revenue", "kind": "metric"}, {"name": "Notes", "kind": "dimension"}]}]
    out = impact.annotate_removed_fields("p1", findings)
    rev, notes = out[0]["removed"]
    assert rev["impact"] == {"certified": True, "severity": "BREAKING", "dependent_metrics": ["Margin %"],
                             "ai_queries": ["What is revenue by region?"]}
    assert notes["impact"]["dependent_metrics"] == [] and notes["impact"]["certified"] is False
    assert out[0]["impact_summary"] == {"fields_with_dependents": 1}


def test_impact_failure_never_blocks_the_check(monkeypatch):
    def boom(pid):
        raise RuntimeError("ontology store down")

    monkeypatch.setattr(impact, "_simulator", boom)
    findings = [{"model": "Sales", "removed": [{"name": "x", "kind": "metric"}]}]
    assert impact.annotate_removed_fields("p1", findings) == findings
