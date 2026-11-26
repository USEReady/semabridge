"""Joins made when feature/databricks-metric-views was merged in."""
from __future__ import annotations

from pathlib import Path


def test_databricks_naming_follows_the_planner_switch(monkeypatch):
    from semabridge.target_rules import naming
    from semabridge.utils.identifiers import databricks_target_name

    monkeypatch.setattr(naming, "databricks_planner_on", lambda: False)
    assert naming.naming_for("databricks").target_name("Sales %", entity_kind="metric") == \
        databricks_target_name("Sales %", entity_kind="metric")
    monkeypatch.setattr(naming, "databricks_planner_on", lambda: True)
    assert naming.naming_for("databricks").target_name("Sales %", entity_kind="metric") == "sales_pct"


def test_planner_is_on_in_the_shipped_config():
    from semabridge.core.behavior import ConnectorBehavior

    cfg = Path(__file__).resolve().parents[1] / "Config" / "behavior.yaml"
    assert ConnectorBehavior.from_yaml(cfg).databricks.use_planner_path is True


def test_planner_status_endpoint(monkeypatch):
    from semabridge.api.routers import system_router
    from semabridge.target_rules import naming

    monkeypatch.setattr(naming, "databricks_planner_on", lambda: True)
    assert system_router.databricks_planner_status() == {"use_planner_path": True}


def test_migrations_have_one_head_and_short_ids():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1] / "src" / "semabridge" / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(root))
    cfg.set_main_option("version_locations", str(root / "schema_versions"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["sb0012_merge_branches"]
    assert all(len(r.revision) <= 32 for r in script.walk_revisions())
