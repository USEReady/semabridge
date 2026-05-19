from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from semabridge.api.services import project_runs_impl as pri


def test_get_snapshot_content_parses_serialized_sml_blob_from_orm(monkeypatch):
    snapshot_payload = {
        "models": [
            {
                "name": "sales",
                "measures": [
                    {"name": "revenue", "expression": "SUM(amount)"},
                ],
            }
        ]
    }

    orm_row = SimpleNamespace(
        project_id="proj-1",
        timestamp="2026-05-19T00:00:00Z",
        sml_blob=json.dumps(snapshot_payload),
    )

    monkeypatch.setattr(pri, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(pri, "_compat_backfill_snapshots_to_orm", lambda _project_id: None)
    monkeypatch.setattr(pri.db_manager, "get_snapshot", lambda _snapshot_id: orm_row)

    result = asyncio.run(pri.get_snapshot_content_compat("proj-1", "snap-1"))

    assert result["snapshot_id"] == "snap-1"
    assert result["project_id"] == "proj-1"
    assert isinstance(result["content"], dict)
    assert result["content"]["models"][0]["measures"][0]["name"] == "revenue"
