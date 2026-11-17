"""Pre-sync duplicate check: is a model in this project already synced by
another project to the same target database/schema?

A model counts as already synced when another project's latest deployment
in the same database.schema
  * uses the same semantic view name (this sync would overwrite it), or
  * has the same model name, or
  * shares most of its metrics and columns (same content under another name).

Re-syncing the same project is an update, never a duplicate. The check is
advisory: run-now refuses with 409 until the user confirms
(``acknowledge_duplicates``).
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


def _target_location(config: Dict[str, Any]) -> Tuple[str, str]:
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    if not target:
        targets = config.get("targets")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            target = targets[0]
    return _norm(target.get("database")), _norm(target.get("schema"))


def _planned_models(config: Dict[str, Any]) -> List[Dict[str, str]]:
    """The models this sync will deploy, with their target view names."""
    from semabridge.api.services.sync_execution_service import _build_sync_jobs
    from semabridge.utils.identifiers import clean_pbix_model_name
    from semabridge.utils.name_translator import get_target_deployment_name

    jobs, _source_type, target_type, _source_cfg, _target_cfg = _build_sync_jobs(config)
    planned = []
    for job in jobs:
        label = str(job.get("model_label") or "")
        display = str(job.get("resolved_display_name") or label)
        planned.append({
            "model_label": label,
            "model_key": _norm(clean_pbix_model_name(str(job.get("source_ref") or label)) or label),
            "view_name": _norm(get_target_deployment_name(display, target_type)),
        })
    return planned


def _latest_snapshots(rows: Iterable[Any]) -> Dict[Tuple[str, str], Any]:
    """Latest row per (project_id, view_name)."""
    latest: Dict[Tuple[str, str], Any] = {}
    for row in rows:
        key = (str(row.project_id), _norm(row.view_name))
        current = latest.get(key)
        if current is None or (row.timestamp and current.timestamp and row.timestamp > current.timestamp):
            latest[key] = row
    return latest


def _own_model_blob(project_id: str, model_key: str, view_name: str, rows: List[Any]) -> Optional[dict]:
    """This project's own fields for a model: its latest dry-run snapshot
    (the content about to be synced), else its own last deployment."""
    from semabridge.repository.model_repository import ModelRepository

    repo = ModelRepository()
    try:
        from semabridge.repository.orm.dry_run_job_models import DryRunJob
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.utils.identifiers import clean_pbix_model_name
        from sqlalchemy import select

        with db_manager.get_session() as session:
            job = session.execute(
                select(DryRunJob).where(DryRunJob.project_id == project_id).order_by(DryRunJob.created_at.desc())
            ).scalars().first()
            preview_ids = [
                f.preview_project_id for f in (job.files if job else [])
                if f.preview_project_id and f.status == "success"
                and _norm(clean_pbix_model_name(f.source_ref or f.pbix_path or f.label or "")) == model_key
            ]
        for row in sorted(rows, key=lambda r: r.timestamp or 0, reverse=True):
            if str(row.project_id) in preview_ids:
                snap = repo.get_snapshot(row.snapshot_id)
                if snap and isinstance(snap.sml_blob, dict):
                    return snap.sml_blob
    except Exception as exc:
        logger.debug("Dry-run snapshot lookup for %s/%s skipped: %s", project_id, model_key, exc)
    for row in sorted(rows, key=lambda r: r.timestamp or 0, reverse=True):
        if str(row.project_id) == project_id and _norm(row.view_name) == view_name:
            snap = repo.get_snapshot(row.snapshot_id)
            if snap and isinstance(snap.sml_blob, dict):
                return snap.sml_blob
    return None


def find_duplicate_deployments(project_id: str, config_yaml: str) -> List[Dict[str, Any]]:
    """Models of ``project_id`` already deployed by another project to the
    same database.schema. Empty when there is nothing to warn about, or when
    the check cannot run (it never blocks a sync on its own failure)."""
    try:
        config = yaml.safe_load(config_yaml) if isinstance(config_yaml, str) else (config_yaml or {})
        if not isinstance(config, dict):
            return []
        database, schema = _target_location(config)
        planned = _planned_models(config)
        if not planned:
            return []

        from semabridge.repository.model_repository import ModelRepository
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.snapshot_models import SnapshotRow
        from sqlalchemy import select

        with db_manager.get_session() as session:
            rows = list(session.execute(select(SnapshotRow)).scalars().all())
            session.expunge_all()
    except Exception as exc:
        logger.warning("Duplicate-sync check skipped for %s: %s", project_id, exc)
        return []

    from semabridge.api.services.project_shared import _compat_projects

    deployed = [
        row for row in rows
        if row.deployed_at is not None
        and str(row.project_id) != project_id
        and not str(row.project_id).startswith("preview")
        and _norm(row.target_database) == database
        and _norm(row.target_schema) == schema
    ]
    others = _latest_snapshots(deployed)
    repo = ModelRepository()
    blob_cache: Dict[str, Optional[dict]] = {}

    def _blob(row: Any) -> Optional[dict]:
        if row.snapshot_id not in blob_cache:
            snap = repo.get_snapshot(row.snapshot_id)
            blob_cache[row.snapshot_id] = snap.sml_blob if snap and isinstance(snap.sml_blob, dict) else None
        return blob_cache[row.snapshot_id]

    findings: List[Dict[str, Any]] = []
    for model in planned:
        own_blob = _own_model_blob(project_id, model["model_key"], model["view_name"], rows)
        own_metrics, own_columns = _blob_fields(own_blob)
        for (other_project, other_view), row in others.items():
            other_blob = _blob(row) or {}
            other_metrics, other_columns = _blob_fields(other_blob)
            metric_overlap = _overlap(own_metrics, other_metrics)
            column_overlap = _overlap(own_columns, other_columns)
            reasons = []
            if other_view == model["view_name"]:
                reasons.append("same_view")
            if model["model_key"] and model["model_key"] in {
                _norm(other_blob.get("unique_name")), _norm(other_blob.get("label"))
            }:
                reasons.append("same_model")
            if own_blob and _is_same_content(metric_overlap, column_overlap):
                reasons.append("same_content")
            if not reasons:
                continue
            findings.append({
                "model": model["model_label"],
                "view_name": model["view_name"],
                "other_project_id": other_project,
                "other_project_name": str((_compat_projects.get(other_project) or {}).get("name") or other_project),
                "other_view_name": other_view,
                "target": f"{database}.{schema}",
                "reasons": reasons,
                "overwrites": "same_view" in reasons,
                "metrics": metric_overlap,
                "columns": column_overlap,
                "deployed_at": row.deployed_at.isoformat() if row.deployed_at else None,
            })
    return findings
