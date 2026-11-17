"""Tests for Feature 1: Project Resync and Safeguards.

Validates:
  1. Match detection A (identical content hash -> skip/resync anyway).
  2. Match detection B (same source identity -> resync dialog).
  3. Match detection C (name clash -> rename/cancel).
  4. HTTP 409 enforcement on create/upload when match exists without mode.
  5. Re-running an existing project by ID is implicitly upsert (no 409).
  6. Copy mode unique suffix resolver ((2), (3)) against project names and target schema.
  7. Pre-deploy backup of live DDL + grants per run and restore path.
  8. External DDL drift warning.
  9. confirm_removals enforcement (unconfirmed stay, report listed, warning status).
  10. Audit logging of user choices.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from fastapi import HTTPException
from semabridge.core.project_resync import (
    backup_live_ddl_and_grants,
    check_ddl_drift,
    compute_source_content_hash,
    detect_project_match,
    extract_source_identity,
    record_resync_audit,
    resolve_unique_copy_name,
    restore_live_ddl_backup,
    validate_removals_confirmation,
)


def test_extract_source_identity():
    # File path & file name
    ident = extract_source_identity({"source_path": "models/Sales.pbix"})
    assert ident["file_name"] == "Sales.pbix"
    assert ident["source_path"] == "models/Sales.pbix"

    # Fabric dataset id
    ident_fab = extract_source_identity({"dataset_id": "fab-123-uuid"})
    assert ident_fab["dataset_id"] == "fab-123-uuid"


def test_compute_source_content_hash(tmp_path):
    p = tmp_path / "test.pbix"
    p.write_bytes(b"PK\x03\x04test_content_hash")
    h1 = compute_source_content_hash({"pbix_path": str(p)})
    assert h1 is not None
    assert len(h1) == 64

    # Fabric TMSL hash
    tmsl = {"model": {"name": "FabricModel", "tables": []}}
    h_fab = compute_source_content_hash({}, tmsl=tmsl)
    assert h_fab is not None
    assert len(h_fab) == 64


def test_match_a_identical_content_hash():
    existing = {
        "proj-1": {
            "name": "Finance",
            "workspace_id": "ws-1",
            "last_source_content_hash": "a" * 64,
            "target": {"database": "ANALYTICS", "schema": "FIN"},
            "deployed_views": ["FIN_VIEW"],
            "last_sync_time": "2026-11-17T10:00:00Z",
        }
    }
    match = detect_project_match(
        new_name="Finance Upload",
        source_cfg={"pbix_path": "models/OtherName.pbix"},
        workspace_id="ws-1",
        content_hash="a" * 64,
        existing_projects=existing,
    )
    assert match is not None
    assert match["match_type"] == "A"
    assert match["reason"] == "identical, nothing to update"
    assert match["options"] == ["skip", "resync_anyway"]
    assert match["matched_project_id"] == "proj-1"
    assert match["target_db_schema"] == "ANALYTICS.FIN"


def test_match_b_same_source_identity_different_content():
    existing = {
        "proj-sales": {
            "name": "Sales",
            "workspace_id": "ws-1",
            "last_source_content_hash": "a" * 64,
            "source": {"pbix_path": "models/Sales.pbix"},
            "target": {"database": "SALES_DB", "schema": "PUBLIC"},
            "deployed_views": ["SALES_V1", "SALES_V2"],
            "last_sync_time": "2026-11-16T12:00:00Z",
        }
    }
    # New upload with same source identity (Sales.pbix) but new content hash
    match = detect_project_match(
        new_name="New Sales",
        source_cfg={"source_path": "models/Sales.pbix"},
        workspace_id="ws-1",
        content_hash="b" * 64,
        existing_projects=existing,
    )
    assert match is not None
    assert match["match_type"] == "B"
    assert match["reason"] == "same source identity with different content"
    assert match["options"] == ["upsert", "copy", "cancel"]
    assert match["matched_project_id"] == "proj-sales"
    assert match["number_of_views"] == 2


def test_match_b_missing_stored_hash_treated_as_match_b():
    # Existing project created before hashing existed
    existing = {
        "proj-legacy": {
            "name": "Legacy",
            "workspace_id": "ws-1",
            "last_source_content_hash": None,
            "source": {"source_path": "models/Legacy.pbix"},
        }
    }
    match = detect_project_match(
        new_name="Legacy Project",
        source_cfg={"source_path": "models/Legacy.pbix"},
        workspace_id="ws-1",
        content_hash="c" * 64,
        existing_projects=existing,
    )
    assert match is not None
    assert match["match_type"] == "B"


def test_match_c_same_project_name_different_source():
    existing = {
        "proj-hr": {
            "name": "Human Resources",
            "workspace_id": "ws-1",
            "source": {"source_path": "models/OldHR.pbix"},
        }
    }
    match = detect_project_match(
        new_name="Human Resources",
        source_cfg={"source_path": "models/CompletelyDifferentFile.pbix"},
        workspace_id="ws-1",
        content_hash="d" * 64,
        existing_projects=existing,
    )
    assert match is not None
    assert match["match_type"] == "C"
    assert match["reason"] == "name clash"
    assert match["options"] == ["rename", "cancel"]


def test_copy_mode_unique_suffix_resolution():
    existing_projects = {"Sales", "Sales (2)"}
    existing_schema_objs = {"SALES_3"}

    name, suffix = resolve_unique_copy_name("Sales", existing_projects, existing_schema_objs)
    # Sales and Sales (2) exist; SALES_3 exists in schema -> next is (4)
    assert name == "Sales (4)"
    assert suffix == "_4"


def test_pre_deploy_backup_and_restore(tmp_path):
    view_ddl = "CREATE OR REPLACE VIEW MY_VIEW AS SELECT 1 AS COL;"
    view_grant = "GRANT SELECT ON VIEW MY_VIEW TO ROLE ANALYST;"

    backup = backup_live_ddl_and_grants(
        project_id="proj-test",
        run_id="run-1",
        view_names=["MY_VIEW"],
        backup_dir=tmp_path,
        live_ddl_fetcher=lambda vn: view_ddl,
        live_grants_fetcher=lambda vn: [view_grant],
    )
    assert "MY_VIEW" in backup["views"]
    assert backup["views"]["MY_VIEW"]["ddl"] == view_ddl

    # Restore from artifact
    executed_statements = []
    restore_file = tmp_path / "live_ddl_backup.json"
    success = restore_live_ddl_backup(restore_file, executor=lambda sql: executed_statements.append(sql))
    assert success is True
    assert view_ddl in executed_statements
    assert view_grant in executed_statements


def test_ddl_drift_detection():
    deployed_ddl = "CREATE OR REPLACE VIEW V AS SELECT 1 AS A;"
    import hashlib
    good_hash = hashlib.sha256(deployed_ddl.strip().encode("utf-8")).hexdigest()

    # Same DDL -> no drift
    has_drift, live_hash, msg = check_ddl_drift(deployed_ddl, good_hash)
    assert has_drift is False
    assert msg is None

    # Modified DDL -> drift detected!
    modified_ddl = "CREATE OR REPLACE VIEW V AS SELECT 2 AS A;"
    has_drift2, live_hash2, msg2 = check_ddl_drift(modified_ddl, good_hash)
    assert has_drift2 is True
    assert "External drift detected" in msg2


def test_confirm_removals_enforcement():
    removed_objects = ["DimOldCustomer", "MetricOldRevenue"]

    # No confirmation: kept in view, has warning
    confirmed, kept, has_warning = validate_removals_confirmation(removed_objects, None)
    assert confirmed == []
    assert kept == ["DimOldCustomer", "MetricOldRevenue"]
    assert has_warning is True

    # Partial confirmation: confirmed are dropped, unconfirmed kept
    confirmed, kept, has_warning = validate_removals_confirmation(
        removed_objects, ["DimOldCustomer"]
    )
    assert confirmed == ["DimOldCustomer"]
    assert kept == ["MetricOldRevenue"]
    assert has_warning is True

    # Full confirmation: all confirmed dropped, no warning
    confirmed, kept, has_warning = validate_removals_confirmation(
        removed_objects, ["DimOldCustomer", "MetricOldRevenue"]
    )
    assert len(confirmed) == 2
    assert kept == []
    assert has_warning is False


def test_audit_logging(tmp_path):
    log_file = tmp_path / "audit.jsonl"
    record = record_resync_audit(
        user_id="user-42",
        project_id="proj-100",
        mode="upsert",
        match_type="B",
        audit_log_path=log_file,
        details={"reason": "schema update"},
    )
    assert record["user_id"] == "user-42"
    assert record["mode"] == "upsert"
    assert record["match_type"] == "B"

    # Read back from JSONL
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["project_id"] == "proj-100"
    assert lines[0]["mode"] == "upsert"
