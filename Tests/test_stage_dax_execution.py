"""Parametrized stage tests over [pbix, fabric] for Stage (e) DAX Execution."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.reconciliation.dax_executor import DaxExecutionResult
from semabridge.sources.base import (
    SourceError,
    get_source_adapter,
)


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_execute_dax_returns_normalized_result(source_type: str, monkeypatch):
    adapter = get_source_adapter(source_type)
    query = "EVALUATE ROW(\"Value\", 42)"
    if source_type == "fabric":
        fake_target = {
            "project_id": "proj-1",
            "workspace_id": "ws-1",
            "dataset_id": "ds-1",
        }
        monkeypatch.setattr(
            "semabridge.sources.fabric.dax_target.run_fabric_dax",
            lambda target, q, creds=None: {
                "backend": "fabric_project",
                "columns": ["Value"],
                "rows": [{"Value": 42}],
                "metadata": {"workspace_id": "ws-1", "dataset_id": "ds-1"},
            },
        )
        res = adapter.execute_dax(fake_target, query)
        assert res["backend"] == "fabric_project"
        assert res["columns"] == ["Value"]
        assert res["rows"] == [{"Value": 42}]
    elif source_type == "pbix":
        fake_res = DaxExecutionResult(
            success=True,
            backend="pbi_desktop",
            columns=["Value"],
            rows=[{"Value": 42}],
            metadata={"port": 12345},
        )
        monkeypatch.setattr(
            "semabridge.reconciliation.dax_executor.execute_dax",
            lambda *args, **kwargs: fake_res,
        )
        res = adapter.execute_dax({"backend": "pbi_desktop"}, query)
        assert res["backend"] == "pbi_desktop"
        assert res["columns"] == ["Value"]
        assert res["rows"] == [{"Value": 42}]


@pytest.mark.parametrize("source_type", ["pbix", "fabric"])
def test_execute_dax_raises_on_failure(source_type: str, monkeypatch):
    adapter = get_source_adapter(source_type)
    query = "INVALID QUERY"
    if source_type == "fabric":
        def _fail(target, q, creds=None):
            raise SourceError("DAX query semantic error")

        monkeypatch.setattr("semabridge.sources.fabric.dax_target.run_fabric_dax", _fail)
        with pytest.raises(SourceError, match="DAX query semantic error"):
            adapter.execute_dax({"workspace_id": "w", "dataset_id": "d"}, query)
    elif source_type == "pbix":
        fake_res = DaxExecutionResult(
            success=False,
            backend="pbi_desktop",
            error="Connection refused to local AS port",
        )
        monkeypatch.setattr(
            "semabridge.reconciliation.dax_executor.execute_dax",
            lambda *args, **kwargs: fake_res,
        )
        with pytest.raises(SourceError, match="Connection refused"):
            adapter.execute_dax({"backend": "pbi_desktop"}, query)
