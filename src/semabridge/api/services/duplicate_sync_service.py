"""Pre-sync duplicate check: is a model in this project already synced by
another project to the same target database/schema -- or, on the same
platform, to another database/schema (``elsewhere``; confirmed once per
project, model, other project and location)?

A model counts as already synced when another project's latest deployment
in the same database.schema
  * comes from the exact same file (same content hash, even if uploaded
    again or renamed), or
  * uses the same semantic view name (this sync would overwrite it), or
  * has the same model name (upload prefix ignored: clean_pbix_model_name), or
  * shares most of its metrics and columns (same content under another name).

A model also counts when another project dry-ran the exact same file for the
same target but has not deployed it yet (``same_file_dry_run``), or is
syncing it right now (``same_file_in_progress``).

Re-syncing the same project is an update, never a duplicate. Every sync
start runs these checks (run_service._sync_safety_checks): interactive starts
get a 409 until the user confirms (``acknowledge_duplicates``), scheduled
runs are recorded as failed. A check that cannot run fails closed there.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml

logger = logging.getLogger(__name__)

# Share of the smaller model's fields that must also exist in the other
# model for it to count as the same content.
_CONTENT_OVERLAP_THRESHOLD = 0.8
_MIN_FIELDS_FOR_CONTENT_MATCH = 3


def _norm(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper())).strip("_")


def _blob_fields(blob: Any) -> Tuple[Set[str], Set[str]]:
    """(metric names, table.column names) of an SML blob, normalised. Uses
    the original source names where a mapping override renamed them, so two
    deployments of one model compare equal regardless of chosen labels."""
    metrics: Set[str] = set()
    columns: Set[str] = set()
    if not isinstance(blob, dict):
        return metrics, columns
    for metric in blob.get("metrics") or []:
        if isinstance(metric, dict):
            name = metric.get("original_name") or metric.get("unique_name") or metric.get("label")
            if name:
                metrics.add(_norm(name))
    for dataset in blob.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        table = _norm(dataset.get("unique_name") or dataset.get("label"))
        for column in dataset.get("columns") or []:
            if isinstance(column, dict) and column.get("unique_name"):
                columns.add(f"{table}.{_norm(column.get('unique_name'))}")
    return metrics, columns


def _overlap(mine: Set[str], theirs: Set[str]) -> Dict[str, int]:
    return {"shared": len(mine & theirs), "total": len(mine), "other_total": len(theirs)}


def _is_same_content(metric_overlap: Dict[str, int], column_overlap: Dict[str, int]) -> bool:
    shared = metric_overlap["shared"] + column_overlap["shared"]
    smaller = min(
        metric_overlap["total"] + column_overlap["total"],
        metric_overlap["other_total"] + column_overlap["other_total"],
    )
    return smaller >= _MIN_FIELDS_FOR_CONTENT_MATCH and shared / smaller >= _CONTENT_OVERLAP_THRESHOLD


_DATABRICKS_TYPES = frozenset({"databricks", "databricks_sql", "dbx", "databricks_metric_view"})
_SNOWFLAKE_TYPES = frozenset({"snowflake", "snowflake_semantic_view"})


def _is_databricks_target(value: Any) -> bool:
    return str(value or "").strip().lower() in _DATABRICKS_TYPES


def _canonical_target_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in _DATABRICKS_TYPES or "databricks" in raw:
        return "databricks"
    if raw in _SNOWFLAKE_TYPES or "snowflake" in raw:
        return "snowflake"
    return raw


def _target_label(target_type: str) -> str:
    canon = _canonical_target_type(target_type)
    if canon == "databricks":
        return "Databricks"
    if canon == "snowflake":
        return "Snowflake"
    return canon.title() if canon else ""


def _config_target_type(config: Dict[str, Any]) -> str:
    if not isinstance(config, dict):
        return ""
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    if not target:
        targets = config.get("targets")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            target = targets[0]
    explicit = _canonical_target_type(target.get("type") or config.get("target_type"))
    if explicit:
        return explicit
    if target.get("catalog") and not target.get("database"):
        return "databricks"
    return ""


def _targets_same_platform(my_target_type: str, other_target_type: str) -> bool:
    """True unless both target platforms are known and differ (e.g. Databricks vs Snowflake)."""
    mine = _canonical_target_type(my_target_type)
    theirs = _canonical_target_type(other_target_type)
    if mine and theirs and mine != theirs:
        return False
    return True


def _target_location(config: Dict[str, Any]) -> Tuple[str, str]:
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    if not target:
        targets = config.get("targets")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            target = targets[0]
    if _is_databricks_target(target.get("type")) or (target.get("catalog") and not target.get("database")):
        dbx = config.get("databricks") if isinstance(config.get("databricks"), dict) else {}
        catalog = target.get("catalog") or dbx.get("catalog") or target.get("database")
        schema = (
            target.get("schema")
            or target.get("schema_name")
            or dbx.get("schema")
            or dbx.get("schema_name")
        )
        return _norm(catalog), _norm(schema)
    return _norm(target.get("database")), _norm(target.get("schema") or target.get("schema_name"))


def _planned_models(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The models this sync will deploy, with their target view names."""
    from semabridge.api.services.sync_execution_service import _build_sync_jobs
    from semabridge.utils.identifiers import clean_pbix_model_name, databricks_view_name
    from semabridge.utils.name_translator import get_target_deployment_name

    from semabridge.api.services.sync_execution_service import file_content_sha256

    jobs, _source_type, target_type, _source_cfg, _target_cfg = _build_sync_jobs(config)
    is_dbx = _is_databricks_target(target_type)
    planned = []
    for job in jobs:
        label = str(job.get("model_label") or "")
        display = str(job.get("resolved_display_name") or label)
        view_name = (
            _norm(databricks_view_name(display))
            if is_dbx
            else _norm(get_target_deployment_name(display, target_type))
        )
        planned.append({
            "model_label": label,
            "model_key": _norm(clean_pbix_model_name(str(job.get("source_ref") or label)) or label),
            "view_name": view_name,
            # sha256 of the file this sync reads (None for non-file sources).
            "content_hash": file_content_sha256(job.get("pbix_path") or job.get("source_ref")),
        })
    return planned


def _file_hashes_by_project() -> Dict[str, Set[str]]:
    """Content hashes of the files each (non-preview) project has dry-run,
    so the exact same PBIX under another project is recognised even when it
    was uploaded again (new upload prefix) or renamed."""
    from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
    from semabridge.repository.orm.session_factory import db_manager
    from sqlalchemy import select

    hashes: Dict[str, Set[str]] = {}
    with db_manager.get_session() as session:
        rows = session.execute(
            select(DryRunJob.project_id, DryRunJobFile.content_hash)
            .join(DryRunJobFile, DryRunJobFile.job_id == DryRunJob.job_id)
            .where(DryRunJobFile.content_hash.is_not(None))
        ).all()
    for project_id, content_hash in rows:
        if project_id and not str(project_id).startswith("preview"):
            hashes.setdefault(str(project_id), set()).add(str(content_hash))
    return hashes


def _latest_snapshots_by_location(rows: Iterable[Any]) -> Dict[Tuple[str, str, str, str], Any]:
    """Latest row per (project_id, database, schema, view_name)."""
    latest: Dict[Tuple[str, str, str, str], Any] = {}
    for row in rows:
        key = (str(row.project_id), _norm(row.target_database), _norm(row.target_schema), _norm(row.view_name))
        current = latest.get(key)
        if current is None or (row.timestamp and current.timestamp and row.timestamp > current.timestamp):
            latest[key] = row
    return latest


# Confirmed "also deploy to another schema of the same platform" decisions.
# Same-location duplicates (collisions / overwrites) are asked every time.
_ACK_COLLECTION = "duplicate_acknowledgements"


def _ack_signature(finding: Dict[str, Any]) -> str:
    return "|".join([
        _norm(finding.get("model")), str(finding.get("other_project_id") or ""),
        _norm(finding.get("target")), _norm(finding.get("other_view_name")),
    ])


def _acknowledged(project_id: str) -> Set[str]:
    try:
        from semabridge.repository import state_store

        state_store.ensure_table()
        value = state_store.load_keys(_ACK_COLLECTION, [project_id]).get(project_id)
        return set(value or []) if isinstance(value, list) else set()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Duplicate acknowledgements of %s unavailable: %s", project_id, exc)
        return set()


def record_acknowledged(project_id: str, findings: Iterable[Dict[str, Any]]) -> int:
    """Remember that the user confirmed these other-location duplicates."""
    new = {_ack_signature(f) for f in findings if f.get("elsewhere")}
    if not project_id or not new:
        return 0
    try:
        from semabridge.repository import state_store

        merged = sorted(_acknowledged(project_id) | new)
        state_store.apply_changes([(_ACK_COLLECTION, project_id, merged)])
        return len(new)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store duplicate acknowledgements of %s: %s", project_id, exc)
        return 0


def without_acknowledged(project_id: str, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Findings still needing confirmation: same-location ones always, other
    locations only until confirmed once."""
    if not any(f.get("elsewhere") for f in findings):
        return findings
    acked = _acknowledged(project_id)
    return [f for f in findings if not (f.get("elsewhere") and _ack_signature(f) in acked)]


def _latest_blob_of(project_ids: Iterable[str], rows: List[Any], *, view_name: Optional[str] = None,
                    deployed_only: bool = False) -> Optional[dict]:
    """SML blob of the newest snapshot row of any of ``project_ids``."""
    from semabridge.repository.model_repository import ModelRepository

    wanted = {str(p) for p in project_ids if p}
    if not wanted:
        return None
    repo = ModelRepository()
    for row in sorted(rows, key=lambda r: r.timestamp or 0, reverse=True):
        if str(row.project_id) not in wanted:
            continue
        if view_name is not None and _norm(row.view_name) != view_name:
            continue
        if deployed_only and row.deployed_at is None:
            continue
        snap = repo.get_snapshot(row.snapshot_id)
        if snap and isinstance(snap.sml_blob, dict):
            return snap.sml_blob
    return None


class SyncCheckError(RuntimeError):
    """A sync-start check could not run; the caller must not take it as "no
    finding" (fail closed)."""


def accepted_previews(project_id: str, config: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """model_label -> preview project id of the dry run the sync gate accepts
    for that model (same file content the sync will read; see
    dry_run_job_service.accepted_dry_run_previews), None when there is none."""
    from semabridge.api.services.dry_run_job_service import accepted_dry_run_previews

    return accepted_dry_run_previews(project_id, config)


def models_never_deployed(project_id: str, config_yaml: Any) -> List[str]:
    """Labels of the models this sync deploys that have no deployed snapshot
    of THIS project (the model's first deployment). Raises SyncCheckError
    when that cannot be determined."""
    try:
        config = yaml.safe_load(config_yaml) if isinstance(config_yaml, str) else (config_yaml or {})
        if not isinstance(config, dict):
            return []
        planned = _planned_models(config)
        if not planned:
            return []
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.snapshot_models import SnapshotRow
        from sqlalchemy import select

        with db_manager.get_session() as session:
            deployed_views = {
                _norm(v) for v in session.execute(
                    select(SnapshotRow.view_name).where(
                        SnapshotRow.project_id == project_id,
                        SnapshotRow.deployed_at.is_not(None),
                        SnapshotRow.deleted_at.is_(None),
                    )
                ).scalars().all() if v
            }
    except Exception as exc:  # noqa: BLE001
        raise SyncCheckError(f"deployment history of project {project_id} unavailable: {exc}") from exc
    return [m["model_label"] for m in planned if m["view_name"] not in deployed_views]


def _field_names(blob: Any) -> Dict[str, Tuple[str, str]]:
    """normalised key -> (kind, display name) for every metric and column of
    an SML blob (original source names where an override renamed them)."""
    out: Dict[str, Tuple[str, str]] = {}
    if not isinstance(blob, dict):
        return out
    for metric in blob.get("metrics") or []:
        if isinstance(metric, dict):
            name = metric.get("original_name") or metric.get("unique_name") or metric.get("label")
            if name:
                out[f"M:{_norm(name)}"] = ("metric", str(name))
    for dataset in blob.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        table = str(dataset.get("unique_name") or dataset.get("label") or "")
        for column in dataset.get("columns") or []:
            if isinstance(column, dict) and column.get("unique_name"):
                out[f"C:{_norm(table)}.{_norm(column['unique_name'])}"] = ("column", f"{table}.{column['unique_name']}")
    return out


def find_removed_fields(
    project_id: str, config_yaml: Any, dry_run_project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fields this project's last deployment has that the content about to
    be synced no longer has, per model. A COPY sync rebuilds the view from
    the source, so these disappear from Snowflake; an UPSERT keeps them.

    The content about to be synced is the dry run the sync gate just ran
    (``dry_run_project_id``, single-model) or, per model, the dry run the
    gate accepts (same content hash as the file the sync reads -- see
    dry_run_job_service.accepted_dry_run_previews). Models never deployed
    are skipped (nothing to lose). A deployed model without a usable dry run
    cannot be verified: it is reported with ``unverifiable: True`` (the
    caller refuses the sync), never silently passed.
    """
    try:
        config = yaml.safe_load(config_yaml) if isinstance(config_yaml, str) else (config_yaml or {})
        if not isinstance(config, dict):
            return []
        planned = _planned_models(config)
        if not planned:
            return []
    except Exception as exc:  # noqa: BLE001 -- the sync cannot build its jobs either
        logger.warning("Removed-fields check skipped for %s (models not resolvable): %s", project_id, exc)
        return []

    def _unverifiable(model: Dict[str, Any], reason: str) -> Dict[str, Any]:
        return {
            "model": model["model_label"], "view_name": model["view_name"],
            "removed": [], "count": 0, "unverifiable": True, "reason": reason,
        }

    use_gate_preview = bool(dry_run_project_id) and len(planned) == 1
    try:
        previews: Dict[str, Optional[str]] = (
            {planned[0]["model_label"]: dry_run_project_id} if use_gate_preview
            else accepted_previews(project_id, config)
        )
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.snapshot_models import SnapshotRow
        from sqlalchemy import select

        ids = sorted({project_id, *[p for p in previews.values() if p]})
        with db_manager.get_session() as session:
            rows = list(session.execute(
                select(SnapshotRow).where(SnapshotRow.project_id.in_(ids), SnapshotRow.deleted_at.is_(None))
            ).scalars().all())
            session.expunge_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Removed-fields check for %s could not run: %s", project_id, exc)
        return [_unverifiable(m, f"check failed: {exc}") for m in planned]

    findings: List[Dict[str, Any]] = []
    for model in planned:
        try:
            deployed = _latest_blob_of([project_id], rows, view_name=model["view_name"], deployed_only=True)
            if deployed is None:
                continue  # first deployment of this model: nothing to lose
            preview_id = previews.get(model["model_label"])
            incoming = _latest_blob_of([preview_id], rows) if preview_id else None
            if incoming is None:
                findings.append(_unverifiable(model, "no successful dry run of the content about to be synced"))
                continue
            old, new = _field_names(deployed), _field_names(incoming)
            removed = [
                {"kind": kind, "name": name}
                for key, (kind, name) in sorted(old.items(), key=lambda kv: kv[1])
                if key not in new
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Removed-fields check for %s/%s could not run: %s", project_id, model["model_label"], exc)
            findings.append(_unverifiable(model, f"check failed: {exc}"))
            continue
        if removed:
            findings.append({
                "model": model["model_label"],
                "view_name": model["view_name"],
                "removed": removed,
                "count": len(removed),
            })
    return findings


def _projects_with_active_runs(exclude: str) -> Set[str]:
    """Projects (other than ``exclude``) with a queued/running run or a
    project lease held right now."""
    from semabridge.core.run_coordination import ACTIVE_STATUSES
    from semabridge.repository.orm.run_models import Run, RunLease
    from semabridge.repository.orm.session_factory import db_manager
    from sqlalchemy import select

    active: Set[str] = set()
    with db_manager.get_session() as session:
        active.update(str(p) for p in session.execute(
            select(Run.project_id).where(Run.status.in_(ACTIVE_STATUSES))
        ).scalars().all() if p)
        active.update(str(p) for p in session.execute(
            select(RunLease.project_id).where(RunLease.lease_type == "project")
        ).scalars().all() if p)
    active.discard(exclude)
    return active


def _same_file_elsewhere(project_id: str, hashes: Set[str]) -> List[Tuple[Any, ...]]:
    """(other project, content hash, (database, schema), target_type) for every
    other project whose successful dry run read a file with one of ``hashes``."""
    import json

    from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
    from semabridge.repository.orm.session_factory import db_manager
    from sqlalchemy import select

    if not hashes:
        return []
    with db_manager.get_session() as session:
        rows = session.execute(
            select(DryRunJob.project_id, DryRunJobFile.content_hash, DryRunJob.target_config_json)
            .join(DryRunJobFile, DryRunJobFile.job_id == DryRunJob.job_id)
            .where(DryRunJobFile.content_hash.in_(sorted(hashes)), DryRunJobFile.status == "success")
        ).all()
    out = []
    for other, content_hash, target_json in rows:
        other = str(other or "")
        if not other or other == project_id or other.startswith("preview"):
            continue
        try:
            target = json.loads(target_json or "{}") or {}
        except Exception:  # noqa: BLE001
            target = {}
        if not isinstance(target, dict):
            target = {}
        wrapped = {"target": target}
        out.append((other, str(content_hash), _target_location(wrapped), _config_target_type(wrapped)))
    return out


def _same_model_elsewhere(project_id: str, model_keys: Set[str]) -> List[Tuple[Any, ...]]:
    """(other project, normalised model key, (database, schema), target_type) for non-file
    models dry-run by another project that has not deployed them yet."""
    import json
    from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.utils.identifiers import clean_pbix_model_name
    from sqlalchemy import select

    if not model_keys:
        return []
    with db_manager.get_session() as session:
        exec_res = session.execute(
            select(DryRunJob.project_id, DryRunJobFile.source_ref, DryRunJobFile.display_name, DryRunJob.target_config_json)
            .join(DryRunJobFile, DryRunJobFile.job_id == DryRunJob.job_id)
            .where(DryRunJobFile.content_hash.is_(None), DryRunJobFile.status == "success")
        )
        rows = exec_res.all() if hasattr(exec_res, "all") else []
    out = []
    for other, source_ref, display_name, target_json in rows:
        other = str(other or "")
        if not other or other == project_id or other.startswith("preview"):
            continue
        raw_name = str(source_ref or display_name or "").strip()
        mkey = _norm(clean_pbix_model_name(raw_name) or raw_name)
        if not mkey or mkey not in model_keys:
            continue
        try:
            target = json.loads(target_json or "{}") or {}
        except Exception as exc:
            logger.debug("Ignoring malformed target_config_json in duplicate check: %s", exc)
            target = {}
        if not isinstance(target, dict):
            target = {}
        wrapped = {"target": target}
        out.append((other, mkey, _target_location(wrapped), _config_target_type(wrapped)))
    return out


def _resolve_other_project_target_type(
    pid: str,
    run_id: Optional[str],
    run_target_types: Dict[str, str],
) -> str:
    """Resolve the target platform ('databricks' | 'snowflake' | '') of another project."""
    from semabridge.api.services.project_shared import _compat_project_configs, _compat_projects

    if run_id and run_target_types.get(str(run_id)):
        return run_target_types[str(run_id)]

    proj = _compat_projects.get(pid)
    if isinstance(proj, dict):
        from_proj = _canonical_target_type(proj.get("target_type")) or _config_target_type(proj)
        if from_proj:
            return from_proj

    raw_cfg = _compat_project_configs.get(pid)
    if isinstance(raw_cfg, str) and raw_cfg.strip():
        try:
            parsed_cfg = yaml.safe_load(raw_cfg)
            if isinstance(parsed_cfg, dict):
                from_cfg = _config_target_type(parsed_cfg)
                if from_cfg:
                    return from_cfg
        except Exception as cfg_exc:
            logger.debug("Could not parse project config for %s during target-type lookup: %s", pid, cfg_exc)
    return ""


def _resolve_other_project_location(pid: str) -> Tuple[str, str]:
    """Fallback (database/catalog, schema) from saved project metadata when DryRunJob target_config is sparse."""
    from semabridge.api.services.project_shared import _compat_project_configs, _compat_projects

    proj = _compat_projects.get(pid)
    if isinstance(proj, dict):
        loc = _target_location(proj)
        if loc[0] or loc[1]:
            return loc
        db_fallback = _norm(proj.get("target_catalog") or proj.get("target_database") or proj.get("database"))
        sch_fallback = _norm(proj.get("target_schema") or proj.get("schema"))
        if db_fallback or sch_fallback:
            return db_fallback, sch_fallback

    raw_cfg = _compat_project_configs.get(pid)
    if isinstance(raw_cfg, str) and raw_cfg.strip():
        try:
            parsed_cfg = yaml.safe_load(raw_cfg)
            if isinstance(parsed_cfg, dict):
                return _target_location(parsed_cfg)
        except Exception as cfg_exc:
            logger.debug("Could not parse project config for %s during target-location lookup: %s", pid, cfg_exc)
    return "", ""


def find_duplicate_deployments(project_id: str, config_yaml: str, strict: bool = False) -> List[Dict[str, Any]]:
    """Models of ``project_id`` already synced -- or about to be synced -- by
    another project to the same database.schema.

    Besides deployed snapshots, the same file (content hash) dry-run by
    another project that has not deployed it yet counts:
    ``same_file_in_progress`` when that project has a sync queued/running
    now, ``same_file_dry_run`` otherwise (two new projects of one model
    would otherwise both pass, as neither is deployed yet).

    When the check cannot run: ``strict`` raises SyncCheckError (callers
    starting a sync fail closed); otherwise it logs and returns []."""
    try:
        config = yaml.safe_load(config_yaml) if isinstance(config_yaml, str) else (config_yaml or {})
        if not isinstance(config, dict):
            return []
        database, schema = _target_location(config)
        target_type = _config_target_type(config)
        planned = _planned_models(config)
        if not planned:
            return []

        from semabridge.repository.model_repository import ModelRepository
        from semabridge.repository.orm.run_models import Run
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.snapshot_models import SnapshotRow
        from sqlalchemy import select

        run_target_types: Dict[str, str] = {}
        with db_manager.get_session() as session:
            rows = list(session.execute(select(SnapshotRow)).scalars().all())
            run_ids = {str(r.run_id) for r in rows if getattr(r, "run_id", None)}
            if run_ids:
                run_exec = session.execute(select(Run.run_id, Run.target_type).where(Run.run_id.in_(sorted(run_ids))))
                for rid, rtype in (run_exec.all() if hasattr(run_exec, "all") else []):
                    canon_rt = _canonical_target_type(rtype)
                    if rid and canon_rt:
                        run_target_types[str(rid)] = canon_rt
            session.expunge_all()
        file_hashes = _file_hashes_by_project()
        pending = _same_file_elsewhere(project_id, {m["content_hash"] for m in planned if m["content_hash"]})
        pending_models = _same_model_elsewhere(
            project_id, {m["model_key"] for m in planned if not m["content_hash"] and m["model_key"]}
        )
        active = _projects_with_active_runs(project_id) if (pending or pending_models) else set()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Duplicate-sync check for %s could not run: %s", project_id, exc)
        if strict:
            raise SyncCheckError(str(exc)) from exc
        return []
    try:
        previews = accepted_previews(project_id, config)
    except Exception as exc:  # noqa: BLE001 -- own content falls back to the last deployment
        logger.warning("Dry-run lookup for the duplicate check of %s failed: %s", project_id, exc)
        previews = {}

    from semabridge.api.services.project_shared import _compat_projects

    # Every other project's deployment: same database.schema (would collide or
    # overwrite) and, on the same platform, other locations (a second copy of
    # the same semantic layer -- reported as ``elsewhere``).
    deployed = [
        row for row in rows
        if row.deployed_at is not None
        and str(row.project_id) != project_id
        and not str(row.project_id).startswith("preview")
    ]
    others = _latest_snapshots_by_location(deployed)
    repo = ModelRepository()
    blob_cache: Dict[str, Optional[dict]] = {}

    def _blob(row: Any) -> Optional[dict]:
        if row.snapshot_id not in blob_cache:
            snap = repo.get_snapshot(row.snapshot_id)
            blob_cache[row.snapshot_id] = snap.sml_blob if snap and isinstance(snap.sml_blob, dict) else None
        return blob_cache[row.snapshot_id]

    def _project_name(pid: str) -> str:
        # The name the user gave the project ("name" holds the source model's name).
        meta = _compat_projects.get(pid) or {}
        return str(meta.get("display_name") or meta.get("semantic_name") or meta.get("name") or pid)

    findings: List[Dict[str, Any]] = []
    for model in planned:
        preview_id = previews.get(model["model_label"])
        own_blob = (
            (_latest_blob_of([preview_id], rows) if preview_id else None)
            or _latest_blob_of([project_id], rows, view_name=model["view_name"])
        )
        own_metrics, own_columns = _blob_fields(own_blob)
        flagged: Set[str] = set()
        for (other_project, other_db, other_schema, other_view), row in others.items():
            other_target_type = _resolve_other_project_target_type(
                other_project, getattr(row, "run_id", None), run_target_types
            )
            if not _targets_same_platform(target_type, other_target_type):
                continue
            same_location = other_db == database and other_schema == schema
            other_blob = _blob(row) or {}
            other_metrics, other_columns = _blob_fields(other_blob)
            metric_overlap = _overlap(own_metrics, other_metrics)
            column_overlap = _overlap(own_columns, other_columns)
            reasons = []
            if model["content_hash"] and model["content_hash"] in file_hashes.get(other_project, set()):
                reasons.append("same_file")
            if same_location and other_view == model["view_name"]:
                reasons.append("same_view")
            if model["model_key"] and model["model_key"] in {
                _norm(other_blob.get("unique_name")), _norm(other_blob.get("label"))
            }:
                reasons.append("same_model")
            if own_blob and _is_same_content(metric_overlap, column_overlap):
                reasons.append("same_content")
            if not reasons:
                continue
            if not same_location:
                # Another schema of the same platform: nothing is overwritten,
                # but it is a second copy of the same semantic layer. Only when
                # both platforms are KNOWN to be the same (an unknown platform
                # is no evidence of a second copy).
                mine_p, theirs_p = _canonical_target_type(target_type), _canonical_target_type(other_target_type)
                if not (mine_p and theirs_p and mine_p == theirs_p):
                    continue
                if not ({"same_file", "same_model", "same_content"} & set(reasons)):
                    continue
                reasons.append("same_platform_elsewhere")
            elif "same_file" in reasons or "same_model" in reasons or "same_view" in reasons:
                flagged.add(other_project)
            resolved_type = target_type or other_target_type
            findings.append({
                "model": model["model_label"],
                "view_name": model["view_name"],
                "other_project_id": other_project,
                "other_project_name": _project_name(other_project),
                "other_view_name": other_view,
                "target": f"{other_db}.{other_schema}",
                "elsewhere": not same_location,
                "target_type": resolved_type,
                "target_label": _target_label(resolved_type),
                "reasons": reasons,
                "overwrites": "same_view" in reasons,
                "metrics": metric_overlap,
                "columns": column_overlap,
                "deployed_at": row.deployed_at.isoformat() if row.deployed_at else None,
            })
        # The same file dry-run by another project that has not deployed it.
        for item in pending:
            other_project, content_hash, (other_db, other_schema) = item[0], item[1], item[2]
            other_target_type = item[3] if len(item) > 3 else ""
            if not other_target_type:
                other_target_type = _resolve_other_project_target_type(other_project, None, run_target_types)
            if not other_db or not other_schema:
                fb_db, fb_sch = _resolve_other_project_location(other_project)
                other_db = other_db or fb_db
                other_schema = other_schema or fb_sch
            if content_hash != model["content_hash"] or other_project in flagged:
                continue
            if other_project not in _compat_projects:
                continue  # deleted project: its dry runs are history
            if not _targets_same_platform(target_type, other_target_type):
                continue
            if (other_db and other_db != database) or (other_schema and other_schema != schema):
                continue  # dry-run for another target
            flagged.add(other_project)
            resolved_type = target_type or other_target_type
            findings.append({
                "model": model["model_label"],
                "view_name": model["view_name"],
                "other_project_id": other_project,
                "other_project_name": _project_name(other_project),
                "other_view_name": "",
                "target": f"{database}.{schema}",
                "elsewhere": False,
                "target_type": resolved_type,
                "target_label": _target_label(resolved_type),
                "reasons": ["same_file_in_progress" if other_project in active else "same_file_dry_run"],
                "overwrites": False,
                "metrics": _overlap(own_metrics, set()),
                "columns": _overlap(own_columns, set()),
                "deployed_at": None,
            })
        for item in pending_models:
            other_project, mkey, (other_db, other_schema) = item[0], item[1], item[2]
            other_target_type = item[3] if len(item) > 3 else ""
            if not other_target_type:
                other_target_type = _resolve_other_project_target_type(other_project, None, run_target_types)
            if not other_db or not other_schema:
                fb_db, fb_sch = _resolve_other_project_location(other_project)
                other_db = other_db or fb_db
                other_schema = other_schema or fb_sch
            if mkey != model["model_key"] or other_project in flagged:
                continue
            if other_project not in _compat_projects:
                continue
            if not _targets_same_platform(target_type, other_target_type):
                continue
            if (other_db and other_db != database) or (other_schema and other_schema != schema):
                continue
            flagged.add(other_project)
            resolved_type = target_type or other_target_type
            findings.append({
                "model": model["model_label"],
                "view_name": model["view_name"],
                "other_project_id": other_project,
                "other_project_name": _project_name(other_project),
                "other_view_name": "",
                "target": f"{database}.{schema}",
                "elsewhere": False,
                "target_type": resolved_type,
                "target_label": _target_label(resolved_type),
                "reasons": ["same_model_in_progress" if other_project in active else "same_model_dry_run"],
                "overwrites": False,
                "metrics": _overlap(own_metrics, set()),
                "columns": _overlap(own_columns, set()),
                "deployed_at": None,
            })
    return findings
