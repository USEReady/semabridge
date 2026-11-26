"""Shared fixtures for the Databricks planner-path tests."""
import pytest


@pytest.fixture(autouse=True)
def _databricks_planner_on(monkeypatch):
    """These tests describe the Databricks planner path (naming v2). The
    planner switch (behavior.databricks.use_planner_path) is off by default,
    so turn it on for them."""
    from semabridge.target_rules import naming

    monkeypatch.setattr(naming, "databricks_planner_on", lambda: True)
    try:
        import semabridge.api.services.project_mapping_engine as pme

        monkeypatch.setattr(pme, "databricks_planner_on", lambda: True)
    except Exception:  # noqa: BLE001
        pass
