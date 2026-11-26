"""The BI models of a project and their latest snapshots (one ontology domain per model).

A multi-model project (``source.mode: multiple``) deploys one semantic view per
model, so its ontology has one domain per model. This module answers:

* which models does the project have (``project_models``) -- from the project
  YAML (``source.models`` + ``source.model_views``) and from its snapshots;
* what is each model's latest successful snapshot and deployed view.

Model ids are :func:`semabridge.ontology.models.model_key` values.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.ontology.models import model_key, per_model_domain_id, project_key, slugify_domain

logger = logging.getLogger("semabridge.ontology.project_models")

_SEMANTIC_SUFFIX = re.compile(r"_SEMANTIC$", re.I)
# Deployed view name -> model name: Snowflake "<model>_SEMANTIC", Databricks
# "<catalog>.<schema>.<model>_metric_view".
_VIEW_SUFFIX = re.compile(r"_(SEMANTIC|METRIC_VIEW)$", re.I)


def _view_model_base(view: str) -> str:
    last = str(view or "").strip().split(".")[-1].strip('"`')
    return _VIEW_SUFFIX.sub("", last)


@dataclass
class ProjectModel:
    model_id: str
    model_name: str
    view_base: Optional[str] = None
    semantic_view: Optional[str] = None  # fully-qualified DB.SCHEMA.VIEW of the latest deploy
    snapshot_id: Optional[str] = None
    blob: Optional[Dict[str, Any]] = None
    aliases: List[str] = field(default_factory=list)


def _project_yaml(project_id: str, config_dir: Optional[Path] = None) -> Dict[str, Any]:
    import yaml

    dirs = [config_dir] if config_dir else [Path("Config/projects"), Path("config/projects")]
    for d in dirs:
        for ext in ("yaml", "yml"):
            p = Path(d) / f"{project_id}.{ext}"
            if p.is_file():
                try:
                    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                    return data if isinstance(data, dict) else {}
                except Exception as exc:
                    logger.warning("Could not read project config %s: %s", p, exc)
                    return {}
    return {}


def configured_models(project_id: str, config_dir: Optional[Path] = None) -> List[ProjectModel]:
    """Models listed in the project YAML (``source.models`` / ``source.model_views``)."""
    from semabridge.utils.identifiers import clean_pbix_model_name

    cfg = _project_yaml(project_id, config_dir)
    if not cfg and not project_id.startswith("proj-"):
        cfg = _project_yaml(f"proj-{project_id}", config_dir)
    src = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    paths = [str(p) for p in (src.get("models") or []) if p]
    views = src.get("model_views") if isinstance(src.get("model_views"), dict) else {}
    for p in views:
        if str(p) not in paths:
            paths.append(str(p))
    out: List[ProjectModel] = []
    for p in paths:
        name = clean_pbix_model_name(p) or Path(p).stem
        mid = model_key(name)
        if not mid or any(m.model_id == mid for m in out):
            continue
        base = str(views.get(p) or "").strip() or None
        aliases = [mid]
        if base:
            aliases.append(model_key(_view_model_base(base)))
        out.append(ProjectModel(model_id=mid, model_name=name, view_base=base, aliases=aliases))
    return out


def is_multi_model(project_id: str, config_dir: Optional[Path] = None) -> bool:
    cfg = _project_yaml(project_id, config_dir)
    src = cfg.get("source") if isinstance(cfg.get("source"), dict) else {}
    if str(src.get("mode") or "").strip().lower() == "multiple":
        return True
    return len(configured_models(project_id, config_dir)) > 1


def _q(part: str) -> str:
    p = str(part or "").strip().strip('"')
    return f'"{p}"' if re.search(r"[^A-Za-z0-9_]", p) else p


def _blob(raw: Any) -> Optional[Dict[str, Any]]:
    """Decode a snapshot blob (JSON or canonical YAML, as the repository stores it)."""
    if isinstance(raw, dict):
        return raw
    try:
        from semabridge.repository.model_repository import _deserialize_sml_blob

        out = _deserialize_sml_blob(raw)
    except Exception:
        try:
            out = json.loads(raw)
        except Exception:
            return None
    return out if isinstance(out, dict) and out else None


def latest_model_snapshots(project_id: str, repo: Any = None, scan_limit: int = 300) -> Dict[str, ProjectModel]:
    """Latest successful snapshot (and latest deployed view) per model key of a project."""
    from sqlalchemy import and_, select

    from semabridge.repository.orm.snapshot_models import SnapshotRow

    if repo is None:
        from semabridge.repository.model_repository import ModelRepository

        repo = ModelRepository()
    try:
        from semabridge.repository.orm.project_models import Project  # type: ignore
    except Exception:  # pragma: no cover - layout differences
        Project = None  # noqa: N806
    out: Dict[str, ProjectModel] = {}
    with repo._session() as session:
        q = select(SnapshotRow).where(
            and_(
                SnapshotRow.project_id == project_id,
                SnapshotRow.status == "success",
                SnapshotRow.deleted_at.is_(None),
            )
        )
        rows = session.execute(q.order_by(SnapshotRow.timestamp.desc()).limit(scan_limit)).scalars().all()
        proj_db = proj_schema = None
        if Project is not None:
            try:
                prow = session.get(Project, project_id)
                proj_db, proj_schema = getattr(prow, "database", None), getattr(prow, "schema", None)
            except Exception:
                pass
        for row in rows:
            blob = _blob(row.sml_blob)
            if blob is None:
                continue
            base = str(blob.get("unique_name") or blob.get("label") or "").strip()
            view = str(row.view_name or "").strip().strip('"')
            key = model_key(_view_model_base(view)) if view else model_key(base)
            if not key:
                continue
            entry = out.get(key)
            if entry is None:
                entry = ProjectModel(
                    model_id=key,
                    model_name=str(blob.get("label") or base or key),
                    view_base=_view_model_base(view) if view else base or None,
                    snapshot_id=row.snapshot_id,
                    blob=blob,
                    aliases=[key, model_key(base)],
                )
                out[key] = entry
            if entry.semantic_view is None and row.deployed_at is not None and view:
                view_parts = [p.strip().strip('"`') for p in view.split(".") if p.strip()]
                if len(view_parts) >= 3:  # already catalog.schema.view (Databricks)
                    parts = view_parts[-3:]
                else:
                    parts = [p for p in (row.target_database or proj_db, row.target_schema or proj_schema, *view_parts) if p]
                entry.semantic_view = ".".join(_q(p) for p in parts)
    return out


def project_models(project_id: str, repo: Any = None, config_dir: Optional[Path] = None) -> List[ProjectModel]:
    """The project's models: configured ones (in config order) joined with their latest snapshots.

    Snapshots of models that are no longer configured are still returned (so
    their domain can be refreshed), after the configured ones.
    """
    configured = configured_models(project_id, config_dir)
    try:
        snaps = latest_model_snapshots(project_id, repo=repo)
    except Exception as exc:
        logger.warning("Snapshots of project %s unavailable: %s", project_id, exc)
        snaps = {}
    used: set = set()
    out: List[ProjectModel] = []
    for m in configured:
        hit = next((snaps[a] for a in m.aliases if a in snaps), None)
        if hit is not None:
            used.add(hit.model_id)
            m.snapshot_id, m.blob, m.semantic_view = hit.snapshot_id, hit.blob, hit.semantic_view
            m.view_base = m.view_base or hit.view_base
            m.aliases = list(dict.fromkeys([*m.aliases, *hit.aliases]))
        out.append(m)
    for key, snap in snaps.items():
        if key not in used and all(key not in m.aliases for m in out):
            out.append(snap)
    return out


def osi_from_blob(blob: Dict[str, Any]) -> Any:
    """OSI model of a snapshot blob (an OSI model already, or an SML model)."""
    from semabridge.intermediate.models import OSIModel

    if "datasets" in blob and "relationships" in blob and "unique_name" in blob:
        try:
            return OSIModel.model_validate_json(json.dumps(blob, default=str))
        except Exception:
            pass
    from semabridge.converter.sml_to_osi import SMLToOSIConverter
    from semabridge.sml.models import SMLModel

    return SMLToOSIConverter().to_osi(SMLModel(**blob))


def project_label(project_id: str) -> str:
    """Display label of a project id (``proj-`` prefix removed)."""
    pid = str(project_id or "").strip()
    return pid[5:] if pid.lower().startswith("proj-") else pid


def model_domain_name(project_id: str, model_name: str) -> str:
    """Display name of a project's per-model domain."""
    name = f"{project_label(project_id)} · {model_name}".strip()
    return re.sub(r"[/\\\x00-\x1f]|\.\.", " ", name)[:120]


__all__ = [
    "ProjectModel",
    "configured_models",
    "is_multi_model",
    "latest_model_snapshots",
    "model_domain_name",
    "osi_from_blob",
    "per_model_domain_id",
    "project_key",
    "project_label",
    "project_models",
    "slugify_domain",
]
