"""Tests for Databricks sync gate, config fingerprint, duplicate & removed-fields checks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from semabridge.api.services.dry_run_job_service import (
    _plan_blockers,
    validate_multi_model_dry_run_readiness,
)
from semabridge.api.services.duplicate_sync_service import (
    _planned_models,
    _target_location,
    find_duplicate_deployments,
    find_removed_fields,
)
from semabridge.api.services.sync_execution_service import model_config_fingerprint
from semabridge.domain.exceptions import ValidationError


# ── Requirement 2: Gate & config fingerprint ────────────────────────────────


def test_plan_blockers_flags_databricks_plan_issues():
    clean = {
        "entity_mappings": [],
        "databricks_plan": {
            "target": "analytics.gold",
            "views": [{"name": "`analytics`.`gold`.`mv_sales`", "skipped": False, "reason": ""}],
            "missing_tables": [],
            "blocked": [],
            "warnings": ["placeholder measure"],
            "error": None,
        },
    }
    assert _plan_blockers(clean, [], "databricks") == []

    blocked = {
        "entity_mappings": [],
        "databricks_plan": {
            "target": "analytics.gold",
            "views": [
                {"name": "`analytics`.`gold`.`mv_a`", "skipped": True, "reason": "No source table"},
            ],
            "missing_tables": ["`analytics`.`gold`.`fact_sales`"],
            "blocked": [{"table": "fact_sales", "message": "Schema conflict"}],
            "warnings": [],
            "error": None,
        },
    }
    issues = _plan_blockers(blocked, [], "databricks")
    assert any("blocked" in i.lower() for i in issues), issues
    assert any("missing" in i.lower() for i in issues), issues
    assert any("skipped" in i.lower() for i in issues), issues

    errored = {
        "entity_mappings": [],
        "databricks_plan": {"error": "warehouse stopped", "views": [], "missing_tables": [], "blocked": []},
    }
    err_issues = _plan_blockers(errored, [], "databricks")
    assert any("warehouse stopped" in i for i in err_issues), err_issues


def test_plan_blockers_treats_snowflake_table_binding_blocked_equivalently():
    sf_blocked = {
        "entity_mappings": [],
        "table_binding_plan": {
            "tables": [{"dataset": "Sales", "action": "blocked", "table": "SALES"}],
            "blocked": [{"dataset": "Sales", "action": "blocked", "table": "SALES"}],
            "error": None,
        },
    }
    issues = _plan_blockers(sf_blocked, [], "snowflake")
    assert any("blocked target table binding" in i for i in issues), issues


def test_plan_blockers_uses_databricks_naming_for_overrides():
    result = {
        "entity_mappings": [
            {
                "id": "m1",
                "source_path": "metrics.Sales %",
                "target_name": "Sales_Pct",
                "entity_kind": "metric",
                "status": "auto",
            },
            {
                "id": "m2",
                "source_path": "metrics.Other",
                "target_name": "Other",
                "entity_kind": "metric",
                "status": "auto",
            },
        ]
    }
    overrides = [{"source_path": "metrics.Other", "target_name": "Sales %"}]
    issues = _plan_blockers(result, overrides, "databricks")
    assert any("duplicate target name" in i and "SALES_PCT" in i for i in issues), issues


def test_model_config_fingerprint_tracks_databricks_catalog_schema_and_views():
    job = {
        "model_label": "Sales Model",
        "resolved_display_name": "Sales Model",
        "multi_mode": True,
        "physical_namespace": "SALES",
    }
    cfg_a = {
        "source": {"type": "pbix"},
        "target": {"type": "databricks", "catalog": "cat_a", "schema": "gold"},
    }
    cfg_b_cat = {
        "source": {"type": "pbix"},
        "target": {"type": "databricks", "catalog": "cat_b", "schema": "gold"},
    }
    cfg_b_sch = {
        "source": {"type": "pbix"},
        "target": {"type": "databricks", "catalog": "cat_a", "schema": "silver"},
    }
    cfg_b_prefix = {
        "source": {"type": "pbix"},
        "target": {"type": "databricks", "catalog": "cat_a", "schema": "gold", "view_prefix": "metric"},
    }
    job_views = {**job, "databricks_view_names": ["mv_sales_a", "mv_sales_b"]}

    fp_a = model_config_fingerprint(cfg_a, job, "databricks")
    assert fp_a != model_config_fingerprint(cfg_b_cat, job, "databricks")
    assert fp_a != model_config_fingerprint(cfg_b_sch, job, "databricks")
    assert fp_a != model_config_fingerprint(cfg_b_prefix, job, "databricks")
    assert fp_a != model_config_fingerprint(cfg_a, job_views, "databricks")


def test_multi_model_gate_blocks_on_databricks_plan_and_target_change(monkeypatch, tmp_path):
    pbix = tmp_path / "Sales.pbix"
    pbix.write_bytes(b"fake-pbix-bytes")

    source_cfg = {"type": "pbix", "mode": "multiple", "models": [str(pbix)]}
    config = {
        "source": source_cfg,
        "target": {"type": "databricks", "catalog": "analytics", "schema": "gold"},
    }
    from semabridge.api.services.sync_execution_service import file_content_sha256

    content_hash = file_content_sha256(str(pbix))
    fake_job = {
        "model_label": "Sales",
        "resolved_display_name": "Sales",
        "source_ref": str(pbix),
        "pbix_path": str(pbix),
        "multi_mode": True,
        "physical_namespace": "SALES",
    }
    good_config_hash = model_config_fingerprint(config, fake_job, "databricks")

    monkeypatch.setattr(
        "semabridge.api.services.dry_run_job_service._gate_jobs",
        lambda cfg: ([fake_job], "databricks"),
    )

    # 1. Blocked by Databricks missing_tables
    blocked_result = {
        "entity_mappings": [],
        "databricks_plan": {
            "target": "analytics.gold",
            "views": [],
            "missing_tables": ["`analytics`.`gold`.`fact_sales`"],
            "blocked": [],
            "warnings": [],
            "error": None,
        },
    }
    monkeypatch.setattr(
        "semabridge.api.services.dry_run_job_service._load_gate_files",
        lambda pid: [{
            "status": "success",
            "error": None,
            "label": "Sales",
            "result_json": json.dumps(blocked_result),
            "content_hash": content_hash,
            "config_hash": good_config_hash,
            "preview_project_id": "preview-1",
            "keys": {"sales", str(pbix).lower()},
            "order": datetime.now(timezone.utc),
            "job_created": datetime.now(timezone.utc),
        }],
    )
    with pytest.raises(ValidationError, match="Databricks missing source table"):
        validate_multi_model_dry_run_readiness("proj-dbx", source_cfg, config=config)

    # 2. Target catalog changed since dry run -> config_hash mismatch
    clean_result = {
        "entity_mappings": [],
        "databricks_plan": {
            "target": "analytics.gold",
            "views": [{"name": "`analytics`.`gold`.`mv_Sales_Fact`", "skipped": False}],
            "missing_tables": [],
            "blocked": [],
            "warnings": [],
            "error": None,
        },
    }
    monkeypatch.setattr(
        "semabridge.api.services.dry_run_job_service._load_gate_files",
        lambda pid: [{
            "status": "success",
            "error": None,
            "label": "Sales",
            "result_json": json.dumps(clean_result),
            "content_hash": content_hash,
            "config_hash": good_config_hash,
            "preview_project_id": "preview-1",
            "keys": {"sales", str(pbix).lower()},
            "order": datetime.now(timezone.utc),
            "job_created": datetime.now(timezone.utc),
        }],
    )
    # Passes for the original catalog
    validate_multi_model_dry_run_readiness("proj-dbx", source_cfg, config=config)

    # Fails when catalog changes
    changed_config = {
        "source": source_cfg,
        "target": {"type": "databricks", "catalog": "other_catalog", "schema": "gold"},
    }
    with pytest.raises(ValidationError, match="changed since its dry run"):
        validate_multi_model_dry_run_readiness("proj-dbx", source_cfg, config=changed_config)


# ── Requirement 3: Duplicate & removed-fields checks ────────────────────────


def test_target_location_uses_catalog_and_schema_for_databricks():
    dbx_single = {"target": {"type": "databricks", "catalog": "Main_Cat", "schema": "Gold_Schema"}}
    assert _target_location(dbx_single) == ("MAIN_CAT", "GOLD_SCHEMA")

    dbx_targets = {"targets": [{"type": "databricks", "catalog": "prod", "schema_name": "semabridge"}]}
    assert _target_location(dbx_targets) == ("PROD", "SEMABRIDGE")

    dbx_top_block = {"target": {"type": "databricks"}, "databricks": {"catalog": "top_cat", "schema": "top_sch"}}
    assert _target_location(dbx_top_block) == ("TOP_CAT", "TOP_SCH")

    sf = {"target": {"type": "snowflake", "database": "SF_DB", "schema": "PUBLIC"}}
    assert _target_location(sf) == ("SF_DB", "PUBLIC")


def test_planned_models_uses_databricks_view_naming(monkeypatch):
    monkeypatch.setattr(
        "semabridge.api.services.sync_execution_service._build_sync_jobs",
        lambda cfg: (
            [{"model_label": "Sales Model", "resolved_display_name": "Sales Model", "source_ref": "Sales Model"}],
            "fabric",
            "databricks",
            {},
            {"type": "databricks", "catalog": "main", "schema": "gold"},
        ),
    )
    planned = _planned_models({"target": {"type": "databricks", "catalog": "main", "schema": "gold"}})
    assert len(planned) == 1
    # Must NOT have Snowflake/name_translator's _DBX suffix
    assert planned[0]["view_name"] == "SALES_MODEL"
    assert not planned[0]["view_name"].endswith("_DBX")


def test_find_duplicate_deployments_matches_databricks_catalog_schema_and_view(monkeypatch):
    now = datetime.now(timezone.utc)
    other_row = SimpleNamespace(
        project_id="proj-other",
        snapshot_id="snap-other",
        view_name="Sales_Model",
        target_database="main_cat",
        target_schema="gold_schema",
        deployed_at=now,
        timestamp=now,
        deleted_at=None,
    )
    different_catalog_row = SimpleNamespace(
        project_id="proj-diff-cat",
        snapshot_id="snap-diff",
        view_name="Sales_Model",
        target_database="other_cat",
        target_schema="gold_schema",
        deployed_at=now,
        timestamp=now,
        deleted_at=None,
    )

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [other_row, different_catalog_row]))

        def expunge_all(self):
            pass

    monkeypatch.setattr(
        "semabridge.repository.orm.session_factory.db_manager.get_session",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "semabridge.api.services.duplicate_sync_service._planned_models",
        lambda cfg: [{"model_label": "Sales Model", "model_key": "SALES_MODEL", "view_name": "SALES_MODEL", "content_hash": None}],
    )
    monkeypatch.setattr(
        "semabridge.api.services.duplicate_sync_service._file_hashes_by_project",
        lambda: {},
    )
    monkeypatch.setattr(
        "semabridge.api.services.duplicate_sync_service._same_file_elsewhere",
        lambda pid, hashes: [],
    )
    monkeypatch.setattr(
        "semabridge.api.services.duplicate_sync_service.accepted_previews",
        lambda pid, cfg: {},
    )
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.get_snapshot",
        lambda self, sid: SimpleNamespace(sml_blob={"unique_name": "Sales Model", "datasets": [], "metrics": []}),
    )

    cfg = {"target": {"type": "databricks", "catalog": "main_cat", "schema": "gold_schema"}}
    findings = find_duplicate_deployments("proj-mine", cfg, strict=True)
    assert len(findings) == 1
    assert findings[0]["other_project_id"] == "proj-other"
    assert findings[0]["target"] == "MAIN_CAT.GOLD_SCHEMA"
    assert "same_view" in findings[0]["reasons"]


def test_find_removed_fields_matches_databricks_deployed_view_name(monkeypatch):
    now = datetime.now(timezone.utc)
    deployed_row = SimpleNamespace(
        project_id="proj-dbx",
        snapshot_id="snap-deployed",
        view_name="Sales_Model",
        deployed_at=now,
        timestamp=now,
        deleted_at=None,
    )
    preview_row = SimpleNamespace(
        project_id="preview-dbx",
        snapshot_id="snap-preview",
        view_name="Sales_Model",
        deployed_at=None,
        timestamp=now,
        deleted_at=None,
    )

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [deployed_row, preview_row]))

        def expunge_all(self):
            pass

    blobs = {
        "snap-deployed": {
            "unique_name": "Sales Model",
            "datasets": [{"unique_name": "Sales", "columns": [{"unique_name": "Amount"}, {"unique_name": "LegacyCol"}]}],
            "metrics": [{"unique_name": "Total Sales"}, {"unique_name": "Old Metric"}],
        },
        "snap-preview": {
            "unique_name": "Sales Model",
            "datasets": [{"unique_name": "Sales", "columns": [{"unique_name": "Amount"}]}],
            "metrics": [{"unique_name": "Total Sales"}],
        },
    }

    monkeypatch.setattr(
        "semabridge.repository.orm.session_factory.db_manager.get_session",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "semabridge.api.services.duplicate_sync_service._planned_models",
        lambda cfg: [{"model_label": "Sales Model", "model_key": "SALES_MODEL", "view_name": "SALES_MODEL", "content_hash": None}],
    )
    monkeypatch.setattr(
        "semabridge.repository.model_repository.ModelRepository.get_snapshot",
        lambda self, sid: SimpleNamespace(sml_blob=blobs[sid]) if sid in blobs else None,
    )

    cfg = {"target": {"type": "databricks", "catalog": "main", "schema": "gold"}}
    findings = find_removed_fields("proj-dbx", cfg, dry_run_project_id="preview-dbx")
    assert len(findings) == 1
    assert findings[0]["count"] == 2
    removed_names = {r["name"] for r in findings[0]["removed"]}
    assert removed_names == {"Sales.LegacyCol", "Old Metric"}
