from __future__ import annotations

import os
import sys
import types


os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_mapping_metric_visibility_fallback.sqlite")

if "psycopg2" not in sys.modules:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_stub.__version__ = "2.9.9"
    psycopg2_stub.apilevel = "2.0"
    psycopg2_stub.threadsafety = 2
    psycopg2_stub.paramstyle = "pyformat"
    psycopg2_stub.Error = Exception
    psycopg2_stub.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services import project_runs_impl as pri


def test_build_project_entity_mappings_hydrates_manual_metrics(monkeypatch):
    monkeypatch.setattr(pri, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "Project 1", "source": "fabric"}})
    monkeypatch.setattr(pri, "_compat_project_configs", {"project-1": "project_name: Project 1\n"})
    monkeypatch.setattr(
        pri,
        "_compat_mappings",
        {
            "project-1-metric": {
                "id": "project-1-metric",
                "project_id": "project-1",
                "entity_kind": "metric",
                "source_path": "metrics.Revenue",
                "source_name": "Revenue",
                "target_name": "REV_TOTAL",
                "source_data_type": "decimal",
                "is_user_edited": True,
            }
        },
    )
    monkeypatch.setattr(
        pri,
        "_compat_latest_sml_state",
        lambda project_id: {
            "unique_name": "Project 1",
            "datasets": [{"unique_name": "Device", "columns": []}],
            "metrics": [],
        },
    )

    payload = pri._compat_build_project_entity_mappings("project-1", save_store=False, target_connector="snowflake")

    metric_rows = [row for row in payload.get("mappings", []) if str(row.get("entity_kind") or "") == "metric"]
    assert metric_rows
    revenue = next(row for row in metric_rows if str(row.get("source_path") or "") == "metrics.Revenue")
    assert str(revenue.get("target_name") or "") == "REV_TOTAL"
    assert bool(revenue.get("is_user_edited")) is True
