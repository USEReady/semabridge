from __future__ import annotations

import json


def test_validate_live_accepts_sml_identity_fields(monkeypatch):
    """SML snapshots with unique_name/label should not trigger false model-name errors."""
    from semabridge.api import main as api_main

    snapshot = {
        "unique_name": "industry",
        "label": "industry",
        "datasets": [
            {
                "unique_name": "SALES",
                "source_table": "SALES",
                "columns": [{"unique_name": "ID"}],
            }
        ],
        "relationships": [],
    }

    class _FakeConn:
        def execute(self, sql: str):
            class _R:
                def fetchall(self_inner):
                    return [("industry", json.dumps(snapshot))]

            return _R()

        def close(self):
            return None

    class _FakeDBManager:
        def _get_connection(self):
            return _FakeConn()

    monkeypatch.setattr(api_main, "db_manager", _FakeDBManager())

    result = api_main.validate_live.__wrapped__() if hasattr(api_main.validate_live, "__wrapped__") else None
    if result is None:
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(api_main.validate_live())

    assert result["valid"] is True
    messages = [e.get("message", "") for e in result["errors"]]
    assert not any("missing identity field" in m.lower() for m in messages)
