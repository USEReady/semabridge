"""Feature 1: Project Resync and Safeguards.

Implements:
  1. Source identity and content hash extraction (PBIX bytes or Fabric TMSL).
  2. Pre-sync match detection (A: identical content hash -> skip/resync anyway;
     B: same source identity -> resync dialog; C: name clash -> rename).
  3. Copy mode unique suffix resolver checked against project names and target schema.
  4. Pre-deploy backup of live DDL + grants per run, with restore path.
  5. External DDL drift detection (live DDL hash vs recorded deploy hash).
  6. Removal confirmation enforcement (confirm_removals: [names]).
  7. Audit logging of user choices.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. Source Identity and Content Hashing
# ---------------------------------------------------------------------------

def extract_remote_artifact_id(pbix_path: str | Path) -> Optional[str]:
    """Inspect PBIX archive Connections -> RemoteArtifacts for embedded dataset ID."""
    try:
        p = Path(pbix_path)
        if not p.exists() or not zipfile.is_zipfile(p):
            return None
        with zipfile.ZipFile(p, "r") as z:
            if "Connections" not in z.namelist():
                return None
            raw = z.read("Connections").decode("utf-8-sig", "ignore")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            # Check RemoteArtifacts list
            for item in data.get("RemoteArtifacts") or []:
                if isinstance(item, dict) and item.get("DatasetId"):
                    return str(item["DatasetId"]).strip()
            # Check Connections list
            for conn in data.get("Connections") or []:
                if isinstance(conn, dict):
                    for k in ("PbiServiceModelId", "PbiModelDatabaseName"):
                        if conn.get(k):
                            return str(conn[k]).strip()
    except Exception as exc:
        logger.debug("extract_remote_artifact_id failed for %s: %exc", pbix_path, exc)
    return None


def extract_source_identity(
    source_cfg: Dict[str, Any],
    pbix_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Identity = any of: source path, file name, Fabric dataset ID,
    or dataset ID embedded in PBIX (Connections -> RemoteArtifacts).
    """
    cfg = dict(source_cfg or {})
    resolved_path = (
        pbix_path
        or cfg.get("pbix_path")
        or cfg.get("source_path")
        or cfg.get("file_path")
        or ""
    )
    resolved_path = str(resolved_path).strip()
    file_name = Path(resolved_path).name if resolved_path else ""
    dataset_id = str(cfg.get("dataset_id") or cfg.get("model_id") or "").strip() or None
    remote_artifact_id = extract_remote_artifact_id(resolved_path) if resolved_path else None

    return {
        "source_path": resolved_path or None,
        "file_name": file_name or None,
        "dataset_id": dataset_id,
        "remote_artifact_id": remote_artifact_id,
    }


def compute_source_content_hash(
    source_cfg: Dict[str, Any],
    pbix_path: Optional[str] = None,
    tmsl: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """For PBIX: SHA256 of file bytes. For Fabric: SHA256 of extracted TMSL."""
    resolved_path = pbix_path or (source_cfg or {}).get("pbix_path") or (source_cfg or {}).get("source_path")
    if resolved_path and Path(resolved_path).exists():
        try:
            h = hashlib.sha256()
            with open(resolved_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest().lower()
        except Exception as exc:
            logger.debug("compute_source_content_hash file read error: %s", exc)

    if isinstance(tmsl, dict):
        try:
            raw = json.dumps(tmsl, sort_keys=True, ensure_ascii=False)
            return hashlib.sha256(raw.encode("utf-8")).hexdigest().lower()
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# 2. Match Detection (Match A, Match B, Match C)
# ---------------------------------------------------------------------------

def _extract_project_workspace(p: Dict[str, Any]) -> str:
    ws = p.get("workspace_id")
    if not ws:
        src = p.get("source")
        if isinstance(src, dict):
            ws = src.get("workspace_id")
    return str(ws or "").strip().casefold()


def _is_preview_project(p_id: str, p: Dict[str, Any]) -> bool:
    pid_str = str(p_id or "").lower()
    if pid_str.startswith("preview-") or pid_str.startswith("dry-run-"):
        return True
    if p.get("is_preview") or p.get("mode") == "preview":
        return True
    return False


def detect_project_match(
    new_name: str,
    source_cfg: Dict[str, Any],
    workspace_id: str,
    content_hash: Optional[str],
    existing_projects: Dict[str, Dict[str, Any]],
    pbix_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Detect an existing project in the same workspace before a sync/create:
      a) Match A: same file content hash -> "identical, nothing to update" (Skip / Resync anyway)
      b) Match B: same source identity with different content (or missing hash) -> resync dialog
      c) Match C: same project name, different source -> name clash (Rename / Cancel)

    Returns match details dict or None if no match.
    """
    ident = extract_source_identity(source_cfg, pbix_path)
    clean_new_name = str(new_name or "").strip().casefold()
    target_ws = str(workspace_id or "").strip().casefold()

    # Pass 1: Match A - Identical content hash checked against ALL projects in the workspace
    if content_hash:
        for p_id, p in existing_projects.items():
            if not isinstance(p, dict) or p.get("is_deleted") or _is_preview_project(p_id, p):
                continue
            p_ws = _extract_project_workspace(p)
            if target_ws and p_ws and target_ws != p_ws:
                continue

            stored_hash = str(p.get("last_source_content_hash") or "").strip().lower()
            if stored_hash and stored_hash == content_hash.strip().lower():
                return _build_match_info(
                    match_type="A",
                    reason="identical, nothing to update",
                    project=p,
                    project_id=p_id,
                    options=["skip", "resync_anyway"],
                )

    # Pass 2: Match B - Same source identity with different content (or missing hash on existing)
    for p_id, p in existing_projects.items():
        if not isinstance(p, dict) or p.get("is_deleted") or _is_preview_project(p_id, p):
            continue
        p_ws = _extract_project_workspace(p)
        if target_ws and p_ws and target_ws != p_ws:
            continue

        p_src = p.get("source") if isinstance(p.get("source"), dict) else {}
        p_ident = extract_source_identity(p_src)

        is_same_identity = False
        # Check source_path
        if ident["source_path"] and p_ident["source_path"] and (
            Path(ident["source_path"]).resolve() == Path(p_ident["source_path"]).resolve()
        ):
            is_same_identity = True
        # Check file_name
        elif ident["file_name"] and p_ident["file_name"] and (
            ident["file_name"].casefold() == p_ident["file_name"].casefold()
        ):
            is_same_identity = True
        # Check Fabric dataset_id
        elif ident["dataset_id"] and p_ident["dataset_id"] and (
            str(ident["dataset_id"]).casefold() == str(p_ident["dataset_id"]).casefold()
        ):
            is_same_identity = True
        # Check embedded remote_artifact_id
        elif ident["remote_artifact_id"] and p_ident["remote_artifact_id"] and (
            str(ident["remote_artifact_id"]).casefold() == str(p_ident["remote_artifact_id"]).casefold()
        ):
            is_same_identity = True

        if is_same_identity:
            stored_hash = p.get("last_source_content_hash")
            # If missing hash, user instruction 5: "Missing hash -> treat as Match B (ask)"
            # If different content hash -> Match B
            if not stored_hash or not content_hash or stored_hash != content_hash:
                return _build_match_info(
                    match_type="B",
                    reason="same source identity with different content",
                    project=p,
                    project_id=p_id,
                    options=["upsert", "copy", "cancel"],
                )

    # Pass 3: Match C - Same project name, different source in the same workspace -> name clash
    if clean_new_name:
        for p_id, p in existing_projects.items():
            if not isinstance(p, dict) or p.get("is_deleted") or _is_preview_project(p_id, p):
                continue
            p_ws = _extract_project_workspace(p)
            if target_ws and p_ws and target_ws != p_ws:
                continue
            p_name = str(p.get("name") or "").strip().casefold()
            if p_name and p_name == clean_new_name:
                return _build_match_info(
                    match_type="C",
                    reason="name clash",
                    project=p,
                    project_id=p_id,
                    options=["rename", "cancel"],
                )

    return None


def _build_match_info(
    match_type: str,
    reason: str,
    project: Dict[str, Any],
    project_id: str,
    options: List[str],
) -> Dict[str, Any]:
    target = project.get("target") if isinstance(project.get("target"), dict) else {}
    target_db = target.get("database") or project.get("database") or ""
    target_schema = target.get("schema") or target.get("schema_name") or project.get("schema") or ""
    db_schema = f"{target_db}.{target_schema}" if target_db and target_schema else (target_db or target_schema or "")
    views = project.get("deployed_views") or []

    return {
        "match_type": match_type,
        "reason": reason,
        "matched_project_id": project_id,
        "project_name": project.get("name") or project_id,
        "last_sync_time": project.get("last_sync_time") or project.get("updated_at"),
        "target_db_schema": db_schema,
        "number_of_views": len(views) if isinstance(views, list) else 1,
        "options": options,
    }


def check_target_semantic_view_exists(
    project_id: Optional[str],
    config_yaml_or_dict: Any,
    cursor: Optional[Any] = None,
    target_database: Optional[str] = None,
    target_schema: Optional[str] = None,
    view_name: Optional[str] = None,
    existing_projects: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Checks whether the target semantic view already exists before deploying.
    Executes SHOW SEMANTIC VIEWS IN SCHEMA <db>.<schema> if a cursor is available,
    and checks SemaBridge's deployed snapshots to determine ownership:
      - this project
      - another project in the workspace
      - not created by SemaBridge (upsert_available = False)
    """
    import yaml
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.snapshot_models import SnapshotRow
    from semabridge.api.services.project_shared import _compat_projects
    from sqlalchemy import select

    cfg: Dict[str, Any] = {}
    if isinstance(config_yaml_or_dict, str):
        try:
            cfg = yaml.safe_load(config_yaml_or_dict) or {}
        except Exception:
            cfg = {}
    elif isinstance(config_yaml_or_dict, dict):
        cfg = config_yaml_or_dict

    db, sch = target_database, target_schema
    if not db or not sch:
        target = cfg.get("target") or {}
        if isinstance(target, dict):
            db = target.get("database") or cfg.get("database")
            sch = target.get("schema") or target.get("schema_name") or cfg.get("schema")
        elif isinstance(target, list) and len(target) > 0 and isinstance(target[0], dict):
            db = target[0].get("database") or cfg.get("database")
            sch = target[0].get("schema") or target[0].get("schema_name") or cfg.get("schema")
        if not db:
            db = cfg.get("snowflake_database") or cfg.get("database")
        if not sch:
            sch = cfg.get("snowflake_schema") or cfg.get("schema")

    db = str(db or "").strip().upper()
    sch = str(sch or "").strip().upper()

    planned_views: List[str] = []
    if view_name:
        planned_views.append(str(view_name).strip().upper())
    else:
        src = cfg.get("source") or {}
        models = src.get("models") if isinstance(src, dict) else None
        if isinstance(models, list):
            for m in models:
                if isinstance(m, str) and m.strip():
                    vn = Path(m).stem if ("/" in m or "\\" in m or m.endswith(".pbix")) else m
                    clean_vn = re.sub(r"[^A-Za-z0-9_]+", "_", vn).strip("_").upper()
                    if clean_vn and clean_vn not in planned_views:
                        planned_views.append(clean_vn)
        target = cfg.get("target") or {}
        if isinstance(target, dict) and target.get("view"):
            vn = str(target["view"]).strip().upper()
            if vn and vn not in planned_views:
                planned_views.append(vn)
        if not planned_views:
            model_name = str(cfg.get("name") or cfg.get("semantic_name") or project_id or "SEMANTIC_VIEW").strip().upper()
            clean_vn = re.sub(r"[^A-Za-z0-9_]+", "_", model_name).strip("_")
            if clean_vn:
                planned_views.append(clean_vn)

    # 1. Query Snowflake SHOW SEMANTIC VIEWS if cursor available
    snowflake_views: Set[str] = set()
    if cursor is not None and db and sch:
        try:
            cursor.execute(f"SHOW SEMANTIC VIEWS IN SCHEMA {db}.{sch}")
            rows = cursor.fetchall() or []
            for r in rows:
                vn = None
                if isinstance(r, dict):
                    vn = r.get("name") or r.get("NAME")
                elif isinstance(r, (list, tuple)) and len(r) > 1:
                    vn = r[1]
                if vn:
                    snowflake_views.add(str(vn).strip().upper())
        except Exception as exc:
            logger.debug("SHOW SEMANTIC VIEWS query failed or not supported on this cursor: %s", exc)

    # 2. Check deployed snapshots in DB
    deployed_snapshots: List[SnapshotRow] = []
    try:
        with db_manager.get_session() as session:
            stmt = (
                select(SnapshotRow)
                .where(SnapshotRow.deployed_at.is_not(None))
                .order_by(SnapshotRow.deployed_at.desc())
            )
            all_snaps = list(session.execute(stmt).scalars().all())
            session.expunge_all()
            for s in all_snaps:
                s_db = str(s.target_database or "").strip().upper()
                s_sch = str(s.target_schema or "").strip().upper()
                if (not db or s_db == db) and (not sch or s_sch == sch):
                    deployed_snapshots.append(s)
    except Exception as exc:
        logger.debug("SnapshotRow query failed: %s", exc)

    projects_store = existing_projects if existing_projects is not None else _compat_projects

    # Check each planned view
    for target_vn in planned_views:
        view_in_snowflake = target_vn in snowflake_views
        matching_snap = None
        for s in deployed_snapshots:
            snap_vn = str(s.view_name or "").strip().upper()
            if snap_vn == target_vn:
                matching_snap = s
                break

        if not view_in_snowflake and not matching_snap:
            continue

        if matching_snap:
            owner_id = str(matching_snap.project_id)
            owner_proj = projects_store.get(owner_id) if isinstance(projects_store, dict) else {}
            owner_name = str((owner_proj or {}).get("name") or owner_id)
            last_deployed_at = matching_snap.deployed_at.isoformat() if matching_snap.deployed_at else None

            sml_dict: Dict[str, Any] = {}
            if matching_snap.sml_blob:
                try:
                    sml_dict = json.loads(matching_snap.sml_blob) if isinstance(matching_snap.sml_blob, str) else matching_snap.sml_blob
                except Exception:
                    sml_dict = {}

            metric_count = len(sml_dict.get("metrics") or [])
            dimension_count = sum(len(d.get("columns") or []) for d in (sml_dict.get("datasets") or []))
            upsert_available = True
        else:
            owner_id = None
            owner_name = None
            last_deployed_at = None
            metric_count = 0
            dimension_count = 0
            upsert_available = False

        full_view_name = f"{db}.{sch}.{target_vn}" if db and sch else target_vn
        return {
            "status": "target_view_exists",
            "view_name": full_view_name,
            "raw_view_name": target_vn,
            "owner_project_id": owner_id,
            "owner_project_name": owner_name,
            "last_deployed_at": last_deployed_at,
            "metric_count": metric_count,
            "dimension_count": dimension_count,
            "upsert_available": upsert_available,
            "options": ["upsert", "copy"] if upsert_available else ["copy"],
            "message": f"Semantic view '{full_view_name}' already exists in target schema {db}.{sch}.",
        }

    return None


# ---------------------------------------------------------------------------
# 3. Copy Mode Unique Suffix Resolver
# ---------------------------------------------------------------------------

def resolve_unique_copy_name(
    base_name: str,
    existing_project_names: Set[str],
    target_schema_objects: Optional[Set[str]] = None,
) -> Tuple[str, str]:
    """Generates unique project name and view name suffix `(2)`, `(3)`, etc.
    checked against project names AND existing objects in target schema.
    """
    clean_base = re.sub(r"\s*\(\d+\)$", "", str(base_name or "Project").strip())
    existing_lower = {n.strip().casefold() for n in existing_project_names}
    schema_objs_lower = {o.strip().casefold() for o in (target_schema_objects or set())}

    counter = 2
    candidate_name = f"{clean_base} ({counter})"
    candidate_suffix = f"_{counter}"

    while (
        candidate_name.casefold() in existing_lower
        or candidate_name.casefold() in schema_objs_lower
        or f"{clean_base.casefold()}{candidate_suffix}" in schema_objs_lower
    ):
        counter += 1
        candidate_name = f"{clean_base} ({counter})"
        candidate_suffix = f"_{counter}"

    return candidate_name, candidate_suffix


# ---------------------------------------------------------------------------
# 4. Pre-deploy Backup of Live DDL + Grants & Restore Path
# ---------------------------------------------------------------------------

def backup_live_ddl_and_grants(
    project_id: str,
    run_id: str,
    view_names: List[str],
    backup_dir: Optional[Path] = None,
    live_ddl_fetcher: Optional[Any] = None,
    live_grants_fetcher: Optional[Any] = None,
) -> Dict[str, Any]:
    """Back up current DDL + grants first before deploy in Upsert mode."""
    target_dir = backup_dir or (Path("output/backups") / project_id / run_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    backup_payload: Dict[str, Any] = {
        "project_id": project_id,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "views": {},
    }

    for vn in view_names:
        ddl_text = ""
        grants = []
        if callable(live_ddl_fetcher):
            try:
                ddl_text = live_ddl_fetcher(vn) or ""
            except Exception as exc:
                logger.warning("Could not fetch live DDL for %s during backup: %s", vn, exc)
        if callable(live_grants_fetcher):
            try:
                grants = live_grants_fetcher(vn) or []
            except Exception as exc:
                logger.warning("Could not fetch live grants for %s during backup: %s", vn, exc)

        ddl_hash = hashlib.sha256(ddl_text.strip().encode("utf-8")).hexdigest() if ddl_text else None
        backup_payload["views"][vn] = {
            "view_name": vn,
            "ddl": ddl_text,
            "ddl_hash": ddl_hash,
            "grants": grants,
        }

    backup_file = target_dir / "live_ddl_backup.json"
    backup_file.write_text(json.dumps(backup_payload, indent=2), encoding="utf-8")
    logger.info("Backed up live DDL + grants for %s to %s", view_names, backup_file)
    return backup_payload


def restore_live_ddl_backup(
    backup_file_path: Path | str,
    executor: Optional[Any] = None,
) -> bool:
    """Restore view DDL and grants from a stored backup artifact."""
    p = Path(backup_file_path)
    if not p.exists():
        raise FileNotFoundError(f"Backup file not found: {p}")

    payload = json.loads(p.read_text(encoding="utf-8"))
    views = payload.get("views") or {}

    for vn, info in views.items():
        ddl = info.get("ddl")
        if ddl and callable(executor):
            executor(ddl)
        for grant in info.get("grants") or []:
            if isinstance(grant, str) and callable(executor):
                executor(grant)

    logger.info("Restored live DDL + grants from %s", p)
    return True


# ---------------------------------------------------------------------------
# 5. External DDL Drift Detection
# ---------------------------------------------------------------------------

def check_ddl_drift(
    live_ddl: str,
    recorded_deploy_hash: Optional[str],
) -> Tuple[bool, str, Optional[str]]:
    """Compare live DDL hash against recorded deploy hash.
    Returns (has_drift, live_hash, warning_message).
    """
    if not live_ddl or not recorded_deploy_hash:
        live_hash = hashlib.sha256(live_ddl.strip().encode("utf-8")).hexdigest() if live_ddl else ""
        return False, live_hash, None

    live_hash = hashlib.sha256(live_ddl.strip().encode("utf-8")).hexdigest()
    if live_hash.lower() != recorded_deploy_hash.strip().lower():
        msg = (
            f"External drift detected: live semantic view DDL hash ({live_hash[:12]}...) "
            f"differs from recorded deploy hash ({recorded_deploy_hash[:12]}...). "
            "The view may have been modified outside SemaBridge."
        )
        return True, live_hash, msg

    return False, live_hash, None


# ---------------------------------------------------------------------------
# 6. Removal Confirmation Enforcement
# ---------------------------------------------------------------------------

def validate_removals_confirmation(
    removed_objects: List[str],
    confirm_removals: Optional[List[str]],
) -> Tuple[List[str], List[str], bool]:
    """Removed objects are never dropped automatically; they need explicit confirmation.
    Returns: (confirmed_to_drop, kept_unconfirmed, has_unconfirmed_warning)
    """
    confirmed_set = {str(n).strip().casefold() for n in (confirm_removals or [])}
    confirmed_drops = []
    kept_unconfirmed = []

    for obj in removed_objects:
        obj_name = str(obj).strip()
        if obj_name.casefold() in confirmed_set:
            confirmed_drops.append(obj_name)
        else:
            kept_unconfirmed.append(obj_name)

    has_warning = len(kept_unconfirmed) > 0
    return confirmed_drops, kept_unconfirmed, has_warning


# ---------------------------------------------------------------------------
# 7. Audit Logging
# ---------------------------------------------------------------------------

def record_resync_audit(
    user_id: Optional[str],
    project_id: str,
    mode: str,
    match_type: Optional[str] = None,
    audit_log_path: Optional[Path] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Audit who chose which mode and when."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": str(user_id or "anonymous"),
        "project_id": str(project_id),
        "mode": str(mode).lower(),
        "match_type": match_type,
        "details": details or {},
    }

    target_path = audit_log_path or Path("output/audit/project_resync_audit.jsonl")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    logger.info("Recorded resync audit: user=%s project=%s mode=%s match=%s", user_id, project_id, mode, match_type)
    return record
