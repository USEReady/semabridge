"""Phase 3.10a: a Databricks dry run carries its catalog/schema, and the
existing deploy path never runs on planner-path (Databricks-dialect) SQL.

Everything added is keyed on fields only a Databricks target sends, so
Snowflake configs and dry-run fingerprints stay exactly as before.
"""

from __future__ import annotations

import yaml
import pytest
from types import SimpleNamespace

from semabridge.api.controllers.mappings_controller import (
    _build_config_yaml_from_request,
    _saved_project_target,
)
from semabridge.api.services.sync_execution_service import model_config_fingerprint
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.exceptions import ConversionError, DeploymentError


def _targets(yaml_text: str) -> dict:
    return yaml.safe_load(yaml_text)["targets"][0]


def test_dry_run_config_keeps_databricks_catalog_and_schema() -> None:
    text = _build_config_yaml_from_request(
        {"type": "pbix", "pbix_path": "x.pbix"},
        {"type": "databricks", "identity_id": "acc-1", "catalog": "workspace", "schema": "semabridge_dev"},
        [],
    )
    assert _targets(text) == {
        "type": "databricks", "schema": "semabridge_dev", "identity_id": "acc-1", "catalog": "workspace",
    }


def test_snowflake_dry_run_config_is_unchanged() -> None:
    text = _build_config_yaml_from_request(
        {"type": "pbix"},
        {"type": "snowflake", "database": "DB", "schema": "SC", "identity_id": "a"},
        [],
    )
    assert list(_targets(text)) == ["type", "database", "schema", "identity_id"]


def test_saved_project_target_keeps_catalog() -> None:
    project = {"targets": [{"type": "databricks", "catalog": "workspace", "schema": "s", "identity_id": "a"}]}
    assert _saved_project_target(project, {"type": "databricks"}) == {
        "type": "databricks", "identity_id": "a", "schema": "s", "catalog": "workspace",
    }


def test_fingerprint_includes_catalog_only_when_set() -> None:
    job = {"multi_mode": False, "resolved_display_name": "Sales"}
    snowflake = {"source": {"type": "pbix"}, "targets": [{"type": "snowflake", "database": "DB", "schema": "S"}]}
    # A catalog-less config hashes exactly as before (the key is only added when set).
    same = {"source": {"type": "pbix"}, "targets": [{"type": "snowflake", "database": "DB", "schema": "S", "catalog": ""}]}
    assert model_config_fingerprint(snowflake, job, "snowflake") == model_config_fingerprint(same, job, "snowflake")

    def dbx(catalog: str) -> dict:
        return {"source": {"type": "pbix"}, "targets": [{"type": "databricks", "catalog": catalog, "schema": "S"}]}

    assert model_config_fingerprint(dbx("workspace"), job, "databricks") != model_config_fingerprint(
        dbx("other"), job, "databricks"
    )


def _context(on: bool) -> SimpleNamespace:
    return SimpleNamespace(
        target_type="databricks",
        behavior=ConnectorBehavior(**{"databricks": {"use_planner_path": on}}),
        config=SimpleNamespace(databricks=None, targets=[]),
    )


def test_old_publisher_never_runs_on_planner_path_sql() -> None:
    from semabridge.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    with pytest.raises(ConversionError, match="not built yet"):
        engine._convert_to_databricks_target(_context(on=True))
    with pytest.raises(DeploymentError, match="not built yet"):
        engine._deploy_to_databricks(_context(on=True))
