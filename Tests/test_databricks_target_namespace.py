"""Target catalog/schema is verified before any Databricks deploy.

Regression: a project with no catalog silently fell back to the hard-coded
``main``; on workspaces without that catalog (Free Edition / new serverless
workspaces use ``workspace``) every metric view failed with
NO_SUCH_CATALOG_EXCEPTION after a multi-minute warehouse start.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from semabridge.core.engine.deployment.databricks import ensure_databricks_target_namespace
from semabridge.core.engine.exceptions import DeploymentError
from semabridge.core.settings import DatabricksConfig


def _rows(*values):
    return [{"result": {"data_array": [[v] for v in values]}}]


class FakePublisher:
    def __init__(self, catalogs, schemas, current="workspace"):
        self.catalogs = catalogs
        self.schemas = schemas  # {catalog: [schemas]}
        self.current = current
        self.statements: list[str] = []

    def execute_statements(self, statements):
        (stmt,) = statements
        self.statements.append(stmt)
        if stmt == "SHOW CATALOGS":
            return _rows(*self.catalogs)
        if stmt == "SELECT current_catalog()":
            return _rows(self.current)
        if stmt.startswith("SHOW SCHEMAS IN"):
            cat = stmt.split("`")[1]
            return _rows(*self.schemas.get(cat, []))
        raise AssertionError(f"unexpected statement {stmt}")


def _ctx(**target):
    return SimpleNamespace(config=SimpleNamespace(targets=[{"type": "databricks", **target}], target=None))


def _cfg(catalog="main", schema="semabridge"):
    return DatabricksConfig(host="dbc-x.cloud.databricks.com", warehouse_id="wh", catalog=catalog, schema_name=schema)


def test_unset_catalog_falls_back_to_workspace_default():
    pub = FakePublisher(["workspace", "samples", "system"], {"workspace": ["default", "information_schema"]})
    out = ensure_databricks_target_namespace(_ctx(schema="default"), _cfg("main", "default"), publisher=pub)
    assert (out.catalog, out.schema_name) == ("workspace", "default")


def test_unset_catalog_and_schema_fall_back_to_default_schema():
    pub = FakePublisher(["workspace"], {"workspace": ["default"]})
    out = ensure_databricks_target_namespace(_ctx(), _cfg("main", "semabridge"), publisher=pub)
    assert (out.catalog, out.schema_name) == ("workspace", "default")


def test_chosen_catalog_missing_fails_fast_with_choices():
    pub = FakePublisher(["workspace", "samples"], {})
    with pytest.raises(DeploymentError) as exc:
        ensure_databricks_target_namespace(_ctx(catalog="main", schema="default"), _cfg("main", "default"), publisher=pub)
    msg = str(exc.value)
    assert "'main' was not found" in msg and "samples, workspace" in msg
    assert "SELECT current_catalog()" not in pub.statements  # never silently replaced


def test_chosen_schema_missing_fails_and_never_creates():
    pub = FakePublisher(["workspace"], {"workspace": ["default", "sales"]})
    with pytest.raises(DeploymentError) as exc:
        ensure_databricks_target_namespace(_ctx(catalog="workspace", schema="finance"), _cfg("workspace", "finance"), publisher=pub)
    assert "never creates" in str(exc.value) and "default, sales" in str(exc.value)
    assert not any(s.upper().startswith("CREATE") for s in pub.statements)


def test_existing_namespace_is_unchanged_and_case_normalised():
    pub = FakePublisher(["Workspace"], {"Workspace": ["Default"]})
    cfg = _cfg("workspace", "default")
    out = ensure_databricks_target_namespace(_ctx(catalog="workspace", schema="default"), cfg, publisher=pub)
    assert (out.catalog, out.schema_name) == ("Workspace", "Default")
    cfg2 = _cfg("Workspace", "Default")
    assert ensure_databricks_target_namespace(_ctx(catalog="Workspace"), cfg2, publisher=pub) is cfg2


def test_untrusted_empty_lookup_leaves_config_alone():
    pub = FakePublisher([], {})
    cfg = _cfg()
    assert ensure_databricks_target_namespace(_ctx(), cfg, publisher=pub) is cfg


def test_preview_surfaces_missing_catalog_as_blocked(monkeypatch):
    from semabridge.core.engine.deployment import databricks_preview as prev

    pub = FakePublisher(["workspace"], {})
    monkeypatch.setattr(prev, "target_databricks_config", lambda ctx: _cfg("main", "default"))
    monkeypatch.setattr(
        "semabridge.connectors.databricks_publisher.DatabricksPublisher",
        lambda cfg, behavior=None: pub,
    )
    ctx = _ctx(catalog="main", schema="default")
    ctx.behavior = None
    ctx.sml_model = SimpleNamespace(datasets=[])
    plan = prev.preview_databricks_plan(ctx, lookup=True)
    assert "'main' was not found" in (plan["error"] or "")
    assert plan["blocked"]
