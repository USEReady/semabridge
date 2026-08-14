import asyncio

from semabridge.api.services.core_config_impl import validate_live


def test_validate_live_returns_structured_result_when_db_unavailable(monkeypatch):
    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db unavailable")

        def close(self):
            return None

    class BrokenDbManager:
        def _get_connection(self):
            return BrokenConnection()

    monkeypatch.setattr("semabridge.api.services.core_config_impl.db_manager", BrokenDbManager())

    result = asyncio.run(validate_live({"content": ""}))

    assert result["valid"] is False
    assert result["errors"]
    assert result["errors"][0]["severity"] == "error"
    assert "db unavailable" in result["errors"][0]["message"].lower()
