from __future__ import annotations

import glob as _glob
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import copy
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.engine.exceptions import is_no_source_tables_summary
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.domain.exceptions import NotFoundError, ValidationError
from semabridge.utils.identifiers import clean_pbix_model_name
from semabridge.utils.project_models import (
    assign_model_view_names,
    assign_physical_namespaces,
    is_multi_mode,
    saved_view_name_for,
)
from semabridge.utils.name_translator import (
    BatchViewNameRegistry,
    build_physical_namespace,
    get_target_deployment_name,
    make_unique_labels,
)

logger = logging.getLogger("semabridge.api")

_TYPE_ALIASES = {
    "snowflake_semantic_view": "snowflake",
    "microsoft_fabric": "fabric",
    "ms_fabric": "fabric",
    "databricks_sql": "databricks",
    "dbx": "databricks",
}

MAX_BATCH_MODELS = 10
# Default kept at 4 to stay within standard Snowflake warehouse concurrency
# (MAX_CONCURRENCY_LEVEL defaults to 8, leaving headroom for probes).
# Configurable per request (max_parallel_models / max_workers) or via
# SEMABRIDGE_MAX_PARALLEL_MODELS, clamped to MAX_PARALLEL_MODELS_CAP.
DEFAULT_MAX_PARALLEL_MODELS = 4
MAX_PARALLEL_MODELS_CAP = MAX_BATCH_MODELS
DEFAULT_MAX_PARALLEL_FABRIC_JOBS = 3
DEFAULT_PROCESS_MAX_WORKERS = 8
DEFAULT_EXECUTOR = "thread"

def _normalize_connector_type(raw_type: Any, default: str) -> str:
    key = str(raw_type or "").strip().lower()
    return _TYPE_ALIASES.get(key, key or default)

def _resolve_models_path() -> Path:
    candidates = [
        Path.cwd() / "models",
        Path.cwd() / "pbix",
        Path.cwd(),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return Path.cwd().resolve()

def _build_console_details(summary_data: Dict[str, Any], fallback_message: Optional[str] = None) -> Dict[str, Any]:
    steps = summary_data.get("steps_completed") or []
    errors = summary_data.get("errors") or []

    lines: List[str] = []
    for step in steps:
        step_number = step.get("step_number", "?")
        step_name = step.get("step_name", "Unknown")
        status = str(step.get("status", "")).upper()
        message = step.get("message")
        line = f"[{status}] Step {step_number}: {step_name}"
        if message:
            line += f" - {message}"
        lines.append(line)

    for error in errors:
        step_number = error.get("step_number", "?")
        step_name = error.get("step_name", "Unknown")
        message = error.get("message", "Unknown error")
        lines.append(f"[ERROR] Step {step_number}: {step_name} - {message}")

    # Never hand the per-model runs page an empty log: a run that produced
    # neither step records nor structured errors still gets one line
    # explaining its outcome.
    if not lines and fallback_message:
        lines.append(fallback_message)

    return {
        "lines": lines,
        "text": "\n".join(lines),
    }

def _log_model_console_trace(result: Dict[str, Any]) -> None:
    """Emit a compact per-model console trace into the main terminal log.

    This keeps batch runs easy to follow even when jobs are executed in a
    process pool, where child-process stdout/loggers are not always visible in
    the parent terminal.
    """
    model_label = str(result.get("model") or "unknown")
    status = str(result.get("status") or "unknown").upper()
    run_id = str(result.get("run_id") or "").strip()
    console = result.get("console") if isinstance(result.get("console"), dict) else {}
    lines = console.get("lines") or []

    header = f"Console trace for model '{model_label}' [status={status}"
    if run_id:
        header += f", run_id={run_id[:8]}...]"
    else:
        header += "]"
    logger.info(header)

    if not lines:
        logger.info("  (no console lines captured)")
        return

    for line in lines:
        logger.info("  %s", line)

def _load_config(payload: Dict[str, Any], normalize_yaml_windows_path_fields) -> tuple[str, Dict[str, Any]]:
    from semabridge.core.config_loader import get_default_config_path, load_yaml_file, get_config
    import yaml
    
    project_id = payload.get("project_id")
    content = payload.get("content")

    if content:
        # Explicit YAML payload should take precedence over project-id based
        # modular loading. This avoids false modular warnings (e.g. missing
        # mapping_profile) when callers intentionally provide concrete config.
        normalized_content = normalize_yaml_windows_path_fields(content)
        try:
            config = yaml.safe_load(normalized_content) or {}
        except Exception as parse_err:
            raise ValidationError(f"Invalid YAML content: {parse_err}")
        return "(payload.content)", config

    if project_id:
        try:
            config = get_config(project_id)
            return f"config/projects/{project_id}.yaml", config
        except Exception as e:
            logger.warning(f"Project config load failed for %s: %s", project_id, e)
            raise NotFoundError(f"Project config not found for project_id '{project_id}' in Config/projects.") from e

    config_path = get_default_config_path() or ""

    if not config_path:
        raise ValidationError("Configuration is required. Provide project_id or content.")

    try:
        config = load_yaml_file(config_path)
    except Exception as parse_err:
        raw_yaml = Path(config_path).read_text(encoding="utf-8")
        normalized_yaml = normalize_yaml_windows_path_fields(raw_yaml)
        if normalized_yaml != raw_yaml:
            Path(config_path).write_text(normalized_yaml, encoding="utf-8")
            config = load_yaml_file(config_path)
        else:
            raise parse_err

    return str(config_path), config

def _resolve_target_config(config: Dict[str, Any]) -> Dict[str, Any]:
    target_cfg = config.get("target") or {}
    if not target_cfg:
        targets_list = config.get("targets") or []
        if isinstance(targets_list, list) and targets_list:
            first_target = targets_list[0]
            if isinstance(first_target, dict):
                target_cfg = first_target
            elif isinstance(first_target, str):
                target_cfg = {"type": first_target}

    if not target_cfg:
        scalar_target = config.get("target_type") or config.get("targetType")
        if scalar_target:
            target_cfg = {"type": scalar_target}

    return target_cfg

def _build_sync_jobs(config: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str, str, Dict[str, Any], Dict[str, Any]]:
    source_cfg = config.get("source", {}) or {}
    source_type = _normalize_connector_type(source_cfg.get("type", "fabric"), "fabric")
    target_cfg = _resolve_target_config(config)
    target_type = _normalize_connector_type((target_cfg or {}).get("type", "snowflake"), "snowflake")

    sync_jobs: List[Dict[str, Any]] = []

    if source_type == "fabric":
        explicit_id = source_cfg.get("dataset_id")
        if explicit_id:
            sync_jobs.append({"dataset_id": explicit_id, "pbix_path": None, "model_label": explicit_id, "source_ref": explicit_id})
        else:
            model_list = source_cfg.get("models") or []
            if not model_list:
                single = (
                    source_cfg.get("model")
                    or config.get("model_name")
                    or source_cfg.get("view")
                    or source_cfg.get("table")
                )
                if single:
                    if isinstance(single, str) and "," in single:
                        model_list = [part.strip() for part in single.split(",") if part.strip()]
                    else:
                        model_list = [single]
            if not model_list:
                raise ValidationError("No models specified in source.models")
            for model_id in model_list:
                resolved_id = str(model_id).strip()
                if resolved_id:
                    sync_jobs.append({"dataset_id": resolved_id, "pbix_path": None, "model_label": resolved_id, "source_ref": resolved_id})

    elif source_type == "pbix":
        explicit_pbix = source_cfg.get("pbix_path")
        models_list = source_cfg.get("models")
        if not explicit_pbix and not models_list:
            raise ValidationError(
                "PBIX source requires source.pbix_path or source.models."
            )

        if explicit_pbix and not models_list:
            sync_jobs.append(
                {
                    "dataset_id": None,
                    "pbix_path": explicit_pbix,
                    "model_label": clean_pbix_model_name(explicit_pbix),
                    "source_ref": explicit_pbix,
                }
            )
        else:
            model_list = models_list or [config.get("model_name", "")]
            for raw_model in model_list:
                if isinstance(raw_model, dict):
                    model_name = str(
                        raw_model.get("pbix_path")
                        or raw_model.get("pbixPath")
                        or raw_model.get("path")
                        or raw_model.get("file_path")
                        or raw_model.get("source_path")
                        or raw_model.get("name")
                        or ""
                    )
                else:
                    model_name = str(raw_model or "")

                configured_folder = source_cfg.get("pbix_folder")
                base_dir = Path(configured_folder).expanduser().resolve() if configured_folder else _resolve_models_path()
                if not base_dir.exists() or not base_dir.is_dir():
                    fallback_dir = _resolve_models_path()
                    if fallback_dir.exists() and fallback_dir.is_dir():
                        base_dir = fallback_dir

                pbix_path, resolution_error = _resolve_pbix_for_model(model_name, base_dir)
                job: Dict[str, Any] = {
                    "dataset_id": None,
                    "pbix_path": pbix_path,
                    "model_label": clean_pbix_model_name(model_name or pbix_path) or "model",
                    "source_ref": model_name or pbix_path,
                }
                if resolution_error:
                    job["resolution_error"] = resolution_error
                sync_jobs.append(job)

            _reject_duplicate_pbix_jobs(sync_jobs)

            resolution_failures = [job for job in sync_jobs if job.get("resolution_error")]
            if resolution_failures and len(resolution_failures) == len(sync_jobs):
                # Nothing in the batch can run (always the case for a
                # single-model project with a missing file): fail the
                # request loudly, exactly like before, with every reason.
                raise ValidationError(
                    "No .pbix file found for model(s): "
                    + "; ".join(str(job["resolution_error"]) for job in resolution_failures)
                )


    elif source_type in ("snowflake", "snowflake_semantic_view"):
        model_list = source_cfg.get("models") or []
        if not model_list:
            single = (
                source_cfg.get("model")
                or config.get("model_name")
                or source_cfg.get("view")
                or source_cfg.get("table")
            )
            if single:
                if isinstance(single, str) and "," in single:
                    model_list = [part.strip() for part in single.split(",") if part.strip()]
                else:
                    model_list = [single]

        if not model_list:
            raise ValidationError((
                    "No models specified for Snowflake source. "
                    "Add source.models: [...] or set model_name in semabridge.yaml."
                ))

        for raw_model in model_list:
            if isinstance(raw_model, dict):
                view_name = str(raw_model.get("name") or raw_model.get("view") or raw_model.get("table") or "")
            else:
                view_name = str(raw_model or "").strip()
            if view_name:
                sync_jobs.append({"dataset_id": view_name, "pbix_path": None, "model_label": view_name})

    if not sync_jobs:
        raise ValidationError("No sync jobs resolved from config.")

    if len(sync_jobs) > MAX_BATCH_MODELS:
        raise ValidationError(f"Batch sync currently supports up to {MAX_BATCH_MODELS} models per request.")

    # Results are sorted and looked up (run_service, per-model reports) by
    # model label, so labels must be unique within one batch.
    unique_labels = make_unique_labels([str(job["model_label"]) for job in sync_jobs])
    for job, label in zip(sync_jobs, unique_labels):
        job["model_label"] = label

    _assign_project_mode_naming(sync_jobs, source_cfg)

    return sync_jobs, source_type, target_type, source_cfg, target_cfg


def _resolve_pbix_for_model(model_name: str, base_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """Resolve one configured PBIX model entry to a file on disk.

    Returns (path, None) on success or (None, "PBIX file not found: ...")
    when the entry cannot be resolved unambiguously. Exact matches (full
    path, or <pbix_folder>/<name>[.pbix]) always win; a fuzzy "*<stem>*"
    match is accepted only when it is the ONLY match. There is deliberately
    no "first .pbix in the folder" fallback: silently syncing a different
    model's file is worse than failing.
    """
    base_dir = Path(base_dir)
    model_path = Path(model_name).expanduser() if model_name else None
    if model_path and model_path.suffix.lower() == ".pbix" and model_path.exists():
        return str(model_path.resolve()), None

    if not model_name:
        candidates = sorted(base_dir.glob("*.pbix")) if base_dir.is_dir() else []
        if len(candidates) == 1:
            return str(candidates[0]), None
        if not candidates:
            return None, f"PBIX file not found: no model name configured and no .pbix file in '{base_dir}'."
        return None, (
            f"PBIX file not found: no model name configured and {len(candidates)} .pbix files "
            f"exist in '{base_dir}' (ambiguous). Set source.pbix_path or list the model explicitly."
        )

    model_stem = Path(model_name).stem
    candidate = base_dir / model_name
    if candidate.suffix.lower() != ".pbix":
        candidate = candidate.with_suffix(".pbix")
    if candidate.exists():
        return str(candidate), None

    pattern = f"*{_glob.escape(model_stem)}*.pbix"
    matches = sorted(base_dir.glob(pattern)) if base_dir.is_dir() else []
    if not matches:
        matches = sorted(Path.cwd().rglob(pattern))
    if len(matches) == 1:
        return str(matches[0]), None
    if len(matches) > 1:
        shown = ", ".join(path.name for path in matches[:5])
        return None, (
            f"PBIX file not found: '{model_name}' is ambiguous ({len(matches)} files match: {shown}). "
            "Use the exact file name or a full path."
        )
    return None, (
        f"PBIX file not found: '{model_name}' (searched '{base_dir}'). "
        "Ensure the .pbix file exists in source.pbix_folder or set source.pbix_path."
    )


def _reject_duplicate_pbix_jobs(sync_jobs: List[Dict[str, Any]]) -> None:
    """Two models of one batch must never silently sync the same file: the
    second (and later) job resolving to an already-claimed file fails with
    its own clear error instead."""
    claimed: Dict[str, str] = {}
    for job in sync_jobs:
        path = job.get("pbix_path")
        if not path or job.get("resolution_error"):
            continue
        try:
            key = os.path.normcase(str(Path(path).resolve()))
        except OSError:
            key = os.path.normcase(str(path))
        owner = claimed.get(key)
        if owner is not None:
            job["resolution_error"] = (
                f"PBIX file '{path}' is already used by model '{owner}' in this batch; "
                "each model must reference its own .pbix file."
            )
            job["pbix_path"] = None
            continue
        claimed[key] = str(job.get("model_label") or path)


def _assign_project_mode_naming(sync_jobs: List[Dict[str, Any]], source_cfg: Dict[str, Any]) -> None:
    """Per-model naming, driven by the project's MODE (source.mode), never by
    how many models this particular run contains.

    Single-mode projects (and every project created before modes existed):
    no per-model view name, no physical prefix -- object names exactly as
    before, whatever the number of models.

    Multi-mode projects: each model deploys to the view name saved for it at
    project creation (source.model_views; computed the same way for a model
    that has no saved name yet), and every physical object it creates is
    prefixed with a readable per-model prefix derived from that name
    (assign_physical_namespaces guarantees the prefixes can never produce
    clashing object names). Removing or adding models never renames the
    others: names come from the saved config, not from the batch.
    """
    count = len(sync_jobs)
    multi = is_multi_mode(source_cfg)
    for job in sync_jobs:
        job["batch_model_count"] = count
        job["multi_mode"] = multi
        if not multi:
            job["resolved_display_name"] = None
            job["physical_namespace"] = None
    if not multi:
        return
    computed = assign_model_view_names(source_cfg)
    for job in sync_jobs:
        refs = (job.get("source_ref"), job.get("pbix_path"), job.get("dataset_id"), job.get("model_label"))
        job["resolved_display_name"] = (
            saved_view_name_for(source_cfg, *refs)
            or saved_view_name_for({"model_views": computed}, *refs)
        )
    namespaces = assign_physical_namespaces(
        [str(job.get("resolved_display_name") or job.get("model_label") or "model") for job in sync_jobs]
    )
    for job, namespace in zip(sync_jobs, namespaces):
        job["physical_namespace"] = namespace


# ---------------------------------------------------------------------------
# Per-model plan: the ONE place both the dry run and the real sync get each
# model's deploy naming from. The sync runs exactly the jobs
# _build_sync_jobs() returns; the dry run builds its config from the
# project's own saved config (mappings_controller._dry_run_config) and runs
# the same _build_sync_jobs() on it, scoped to the model being dry-run
# (payload ``scope_source_refs``), so view names and physical prefixes are
# computed by the same code over the same model list.
# ---------------------------------------------------------------------------


def model_plan_for_job(job: Dict[str, Any], target_type: str) -> Dict[str, Any]:
    """The deploy plan of one sync job (as returned by _build_sync_jobs)."""
    display = job.get("resolved_display_name")
    return {
        "model_label": job.get("model_label"),
        "source_ref": job.get("source_ref") or job.get("pbix_path") or job.get("dataset_id"),
        "multi_mode": bool(job.get("multi_mode")),
        "batch_model_count": int(job.get("batch_model_count") or 1),
        # Multi-mode: the saved per-model view base name. Single-mode: None
        # (the view name then comes from the extracted model's own name).
        "view_base_name": display,
        "view_name": get_target_deployment_name(str(display), target_type) if display else None,
        "physical_namespace": job.get("physical_namespace"),
        "resolution_error": job.get("resolution_error"),
    }


def build_model_plans(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every configured model's deploy plan, exactly as a sync of ``config``
    would compute it."""
    sync_jobs, _source_type, target_type, _source_cfg, _target_cfg = _build_sync_jobs(config)
    return [model_plan_for_job(job, target_type) for job in sync_jobs]


def job_matches_source_ref(job: Dict[str, Any], ref: Any) -> bool:
    """True when ``ref`` (a source.models entry / pbix path / dataset name)
    names this job exactly (case/slash-insensitive, never fuzzy)."""
    key = _model_identity_key(ref)
    raw = str(ref or "").strip().replace("\\", "/").lower()
    if not raw:
        return False
    for value in (job.get("source_ref"), job.get("pbix_path"), job.get("dataset_id")):
        text = str(value or "").strip().replace("\\", "/").lower()
        if text and text == raw:
            return True
    # Base-name match only for path-like refs (a PBIX entry vs its resolved file).
    if "/" in raw or raw.endswith(".pbix"):
        return any(
            _model_identity_key(v) == key and key
            for v in (job.get("source_ref"), job.get("pbix_path"))
        )
    return False


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def model_config_fingerprint(config: Dict[str, Any], job: Dict[str, Any], target_type: str) -> str:
    """sha256 of everything in ``config`` that decides WHERE and UNDER WHICH
    NAMES this job's model deploys: source type + project mode, the model's
    view name and physical prefix, and the target (type/database/schema).
    Computed identically by the dry run (on its scoped config) and by the
    sync gate (on the project's config), so a mismatch means the sync would
    deploy something the dry run never showed.

    File paths (random upload prefixes), mapping overrides (re-validated by
    the gate against the dry run's rows instead -- resolving a collision in
    the dry-run table must not invalidate the dry run) and run options are
    deliberately excluded."""
    import hashlib

    source_cfg = config.get("source") if isinstance(config.get("source"), dict) else {}
    target_cfg = _resolve_target_config(config) or {}
    payload = {
        "source_type": _normalize_connector_type(source_cfg.get("type", "fabric"), "fabric"),
        "multi_mode": bool(job.get("multi_mode")),
        "view_base_name": str(job.get("resolved_display_name") or "").upper() or None,
        "physical_namespace": str(job.get("physical_namespace") or "").upper() or None,
        "target": {
            "type": _normalize_connector_type(target_type or target_cfg.get("type"), "snowflake"),
            "database": str(target_cfg.get("database") or "").strip().upper(),
            "schema": str(target_cfg.get("schema") or target_cfg.get("schema_name") or "").strip().upper(),
        },
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def model_overrides_fingerprint(config: Dict[str, Any], job: Dict[str, Any]) -> str:
    """sha256 of the mapping overrides that apply to this job (informational:
    lets the gate tell 'overrides edited since the dry run')."""
    import hashlib

    rows = _config_with_model_scoped_overrides(config, job).get("mappings_overrides") or []
    pairs = sorted(
        (str(r.get("source_path") or "").strip(), str(r.get("target_name") or "").strip().upper())
        for r in rows if isinstance(r, dict) and str(r.get("source_path") or "").strip()
    )
    return hashlib.sha256(_canonical_json(pairs).encode("utf-8")).hexdigest()


_FILE_HASH_CACHE: Dict[tuple, str] = {}


def file_content_sha256(path: Any) -> Optional[str]:
    """sha256 of a source file's bytes (None when it is not a readable local
    file, e.g. a Fabric model name). Every upload copy of the same file
    hashes identically, so a re-upload of an unchanged file still matches its
    dry run. Cached by (path, size, mtime)."""
    import hashlib

    text = str(path or "").strip()
    if not text:
        return None
    try:
        p = Path(text).expanduser()
        if not p.is_file():
            return None
        stat = p.stat()
        key = (str(p.resolve()), stat.st_size, stat.st_mtime_ns)
        cached = _FILE_HASH_CACHE.get(key)
        if cached:
            return cached
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        if len(_FILE_HASH_CACHE) > 256:
            _FILE_HASH_CACHE.clear()
        _FILE_HASH_CACHE[key] = value
        return value
    except OSError as exc:
        logger.warning("Could not hash source file %s: %s", text, exc)
        return None


def _persist_model_version(
    repository: ModelRepository,
    config: Dict[str, Any],
    summary_data: Dict[str, Any],
    model_label: str,
    resolved_workspace_id: str,
) -> None:
    snapshot_payload: Dict[str, Any] = config
    sml_snap_id = summary_data.get("sml_snapshot_id")
    if sml_snap_id:
        snap = repository.get_snapshot(sml_snap_id)
        if snap and isinstance(snap.sml_blob, dict):
            snapshot_payload = snap.sml_blob

    repository.insert_model_version(
        model_id=model_label,
        workspace_id=resolved_workspace_id,
        snapshot=snapshot_payload,
        author="ui",
        change_summary=f"Sync run {summary_data.get('run_id', '')}".strip(),
        version_tag=str(config.get("version_tag", "") or "") or None,
    )

def _model_identity_key(value: Any) -> str:
    """Normalize a model reference (pbix path, file name, dataset id or
    display name) to a comparable key: lower-cased base name without .pbix."""
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    name = text.rsplit("/", 1)[-1]
    if name.lower().endswith(".pbix"):
        name = name[: -len(".pbix")]
    return name.strip().lower()


def _model_match_key(value: Any) -> str:
    """Like _model_identity_key, but also drops the 32-hex upload prefix
    (``<uuid>_Sales.pbix`` -> ``sales``). Every upload -- the dry run's and
    the project's own -- stores the same file under a fresh random prefix, so
    an override tagged with the dry-run copy's path must still match the
    project's copy of that model."""
    return (clean_pbix_model_name(_model_identity_key(value)) or "").strip().lower()


def _config_with_model_scoped_overrides(config: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    """Return the config this job should run with: mappings_overrides rows
    tagged with a ``model`` (written by the multi-model dry run) are kept only
    for the job they belong to; untagged rows apply to every model, exactly as
    before. Returns ``config`` itself when there is nothing to filter, so a
    single-model run sees the very same config object.

    A row matches its job by exact model key first. Failing that, it matches
    by upload-prefix-free name (see _model_match_key) -- unless that name is
    shared by several of the project's configured models, where it would be
    ambiguous, or the row's exact key names a different configured model."""
    overrides = config.get("mappings_overrides") if isinstance(config, dict) else None
    if not isinstance(overrides, list) or not any(
        isinstance(row, dict) and str(row.get("model") or "").strip() for row in overrides
    ):
        return config
    job_values = [
        job.get(key) for key in ("pbix_path", "dataset_id", "model_label", "resolved_display_name", "source_ref")
    ]
    identities = {_model_identity_key(v) for v in job_values}
    identities.discard("")
    match_identities = {_model_match_key(v) for v in job_values}
    match_identities.discard("")

    source_cfg = config.get("source") if isinstance(config.get("source"), dict) else {}
    configured_models = [m for m in (source_cfg.get("models") or []) if str(m or "").strip()]
    configured_exact = {_model_identity_key(m) for m in configured_models}
    configured_match_counts: Dict[str, int] = {}
    for m in configured_models:
        key = _model_match_key(m)
        configured_match_counts[key] = configured_match_counts.get(key, 0) + 1

    def _belongs(row: Any) -> bool:
        if not (isinstance(row, dict) and str(row.get("model") or "").strip()):
            return True
        exact = _model_identity_key(row.get("model"))
        if exact in identities:
            return True
        if exact in configured_exact:
            return False  # explicitly another configured model
        match = _model_match_key(row.get("model"))
        if not match or match not in match_identities:
            return False
        return configured_match_counts.get(match, 0) <= 1

    scoped = [row for row in overrides if _belongs(row)]
    dropped_ambiguous = sum(
        1 for row in overrides
        if isinstance(row, dict) and str(row.get("model") or "").strip()
        and _model_identity_key(row.get("model")) not in identities
        and _model_match_key(row.get("model")) in match_identities
        and configured_match_counts.get(_model_match_key(row.get("model")), 0) > 1
    )
    if dropped_ambiguous:
        logger.warning(
            "Skipped %d mapping override(s) for model '%s': several configured models share its "
            "file name, so overrides tagged with another upload path cannot be attributed safely.",
            dropped_ambiguous, job.get("model_label"),
        )
    return {**config, "mappings_overrides": scoped}


def _run_single_job(job: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    """Run one model's job inside its own log scope: every log record it
    emits (in this thread/context) is tagged with the project run id and
    this model and written to this model's own log file."""
    from semabridge.core.run_model_logs import append_model_log_line, model_log_scope

    from semabridge.core.run_coordination import model_slot

    model_label = job["model_label"]
    run_id = job.get("project_run_id")
    project_id = job.get("log_project_id")
    # One global cap on concurrent model executions (syncs AND dry runs,
    # SEMABRIDGE_MAX_CONCURRENT_MODELS). A process-executor job already
    # holds its slot in the parent (see _run_parallel_jobs).
    slot_ctx = _NoLock() if job.get("_slot_held") else model_slot()
    with slot_ctx, model_log_scope(project_id, run_id, model_label):
        logger.info(
            "Model '%s' started (view=%s, prefix=%s)",
            model_label, job.get("resolved_display_name") or "-", job.get("physical_namespace") or "-",
        )
        result = _run_single_job_impl(job, **kwargs)
        logger.info(
            "Model '%s' finished: status=%s view=%s duration_ms=%s",
            model_label, result.get("status"), result.get("view_name") or "-", result.get("duration_ms"),
        )
    if run_id:
        # The model's step-by-step console summary (incl. errors) at the end
        # of its own log, tagged like every other line.
        for line in ((result.get("console") or {}).get("lines") or []):
            append_model_log_line(project_id or "", run_id, model_label, f"[run={run_id}] [model={model_label}] {line}")
    return result


def _run_single_job_impl(
    job: Dict[str, Any],
    *,
    engine_source_type: str,
    target_type: str,
    config: Dict[str, Any],
    config_path: str,
    deploy_enabled: bool,
    resolved_workspace_id: str,
    requested_project_id: Optional[str] = None,
    account_id: Optional[str] = None,
    sync_mode: str = "copy",
    force: bool = False,
    batch_name_registry: Optional[BatchViewNameRegistry] = None,
) -> Dict[str, Any]:
    model_label = job["model_label"]
    started = time.perf_counter()

    if job.get("resolution_error"):
        # Resolved (and rejected) at batch-build time -- report it as this
        # model's own failure without running the engine at all.
        message = str(job["resolution_error"])
        logger.error("Sync skipped for model '%s': %s", model_label, message)
        return _failed_result(job, message, "FileNotFoundError", started)

    # Every job gets its own deep copy of the config: nothing a job (or the
    # engine running it) mutates can leak into a concurrently running sibling.
    local_config = copy.deepcopy(_config_with_model_scoped_overrides(config, job))
    preview = local_config.get("delta_preview_overrides")
    if isinstance(preview, dict):
        # Same per-model filtering a real run of the project applies.
        preview["mappings_overrides"] = _config_with_model_scoped_overrides(
            {"mappings_overrides": preview.get("mappings_overrides") or []}, job
        ).get("mappings_overrides") or []
    target_view_name = get_target_deployment_name(
        str(job.get("resolved_display_name") or model_label), target_type
    )
    lock_key = _target_lock_key(local_config, target_view_name)

    # A dry run never deploys, so it must not take the target view's lease: it
    # would reject (and be rejected by) a concurrent real sync of that view.
    # Real deploys lease db.schema.<view> here (DB-backed, cross-process); the
    # emitter additionally leases every physical/enriched object it writes
    # under the same holder (see core/run_coordination.acquire_target_objects).
    lock_ctx = _TargetLease(job, local_config, target_view_name, requested_project_id) if deploy_enabled else _NoLock()
    with lock_ctx as acquired:
        if not acquired:
            logger.warning("Target view lease for '%s' is held by another active run; rejecting request.", lock_key)
            result = _failed_result(
                job,
                f"Target view name '{target_view_name}' is currently being deployed by another active run.",
                "TargetViewLockConflict",
                started,
            )
            result["status"] = "conflict"
            return result

        repository = ModelRepository()
        engine = ExecutionEngine(db_manager=repository)

        logger.info("Syncing model: %s (target_view=%s)", model_label, target_view_name)
        try:
            summary = engine.execute(
                source=engine_source_type,
                target=target_type,
                project_id=requested_project_id,
                config_path=Path(config_path) if config_path else None,
                config_dict=local_config,
                dataset_id=job["dataset_id"],
                workspace_id=resolved_workspace_id,
                pbix_path=job["pbix_path"],
                project_name=model_label,
                tag=str(local_config.get("version_tag", "v1.0")),
                deploy=deploy_enabled,
                dry_run=not deploy_enabled,
                account_id=account_id,
                sync_mode=sync_mode,
                force=force,
                resolved_display_name=job.get("resolved_display_name"),
                physical_namespace=job.get("physical_namespace"),
                batch_model_count=int(job.get("batch_model_count") or 1),
                batch_name_registry=batch_name_registry,
            )
            summary_data = summary.model_dump(mode="json")
            job_ok = str(summary_data.get("status", "")).upper() == "SUCCESS"
            run_context = getattr(engine, "_context", None)

            _record_true_run_completion(repository, summary_data)

            if job_ok and deploy_enabled:
                try:
                    _persist_model_version(repository, local_config, summary_data, model_label, resolved_workspace_id)
                except Exception as version_error:
                    logger.warning("Version row write failed for %s: %s", model_label, version_error)

            if job_ok:
                status = "success"
            elif is_no_source_tables_summary(summary_data):
                # No tables (e.g. a thin report): nothing to deploy for this
                # model. Skipped, not failed, so the other models still count.
                status = "skipped"
            else:
                status = "failed"
            return {
                "model": model_label,
                "status": status,
                "summary": summary_data,
                "routing_summary": summary_data.get("routing_summary") if isinstance(summary_data, dict) else None,
                "console": _build_console_details(
                    summary_data,
                    fallback_message=f"[{status.upper()}] Model '{model_label}' finished with status {summary_data.get('status')}",
                ),
                "run_id": summary_data.get("run_id"),
                "missing_dims": summary_data.get("missing_dims") or {},
                "dropped_entities": summary_data.get("dropped_entities") or [],
                "data_backfill_results": summary_data.get("data_backfill_results") or [],
                "cortex_validation_results": summary_data.get("cortex_validation_results") or [],
                "upsert_delta": summary_data.get("upsert_delta"),
                "pbix_path": job.get("pbix_path"),
                "view_name": _resolve_result_view_name(run_context, target_type),
                "target_artifact_path": summary_data.get("target_artifact_path")
                or getattr(run_context, "target_artifact_path", None),
                "physical_namespace": job.get("physical_namespace"),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            # exc_info=True so an uncaught failure at this outermost layer of
            # per-model sync execution still leaves a full traceback in the log,
            # not just str(exc) -- see local_pbix_connector.py's
            # _extract_with_pbixray for the same fix applied one layer down, at
            # the actual extraction call site.
            logger.error("Sync failed for model '%s': %s", model_label, exc, exc_info=True)
            _close_engine_row(repository, engine, exc)
            accumulated_drops = _collect_drop_ledger(engine, model_label)
            result = _failed_result(job, str(exc), type(exc).__name__, started, dropped_entities=accumulated_drops)
            if "CONFLICT:" in str(exc):
                result["status"] = "conflict"
            return result


def _drop_record_to_dict(record: Any) -> Dict[str, Any]:
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    if isinstance(record, dict):
        return dict(record)
    return dict(record)


def _collect_drop_ledger(engine: Any, model_label: str) -> List[Dict[str, Any]]:
    """The engine's drop ledger as JSON-able dicts, for a FAILED result.

    The failed result must still say what was dropped before the failure.
    Each record is serialised on its own: one record that cannot be dumped is
    kept as a minimal placeholder (and logged) instead of losing the whole
    ledger, which used to make a failed run look like it dropped nothing.
    """
    ledger = getattr(getattr(engine, "_context", None), "drop_ledger", None)
    if ledger is None:
        return []
    try:
        records = list(getattr(ledger, "records", []) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drop ledger of failed model '%s' could not be read: %s", model_label, exc)
        return [{"entity": None, "reason": f"drop ledger unreadable: {exc}", "ledger_error": True}]
    drops: List[Dict[str, Any]] = []
    for record in records:
        try:
            drops.append(_drop_record_to_dict(record))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Drop ledger record of failed model '%s' could not be serialised (%s); keeping a placeholder",
                model_label, exc,
            )
            drops.append({"entity": str(getattr(record, "entity", None) or record), "reason": str(
                getattr(record, "reason", "") or ""), "ledger_error": True})
    return drops


class _NoLock:
    """Stand-in for a lease/slot that is not needed (always acquired)."""

    def __enter__(self) -> bool:
        return True

    def __exit__(self, *exc: Any) -> bool:
        return False


class _TargetLease:
    """DB-backed lease on the model's semantic view (``target:db.schema.view``)
    held for the whole model execution, plus the deploy-holder context the
    emitter uses to lease the physical/enriched objects it writes. Works
    across worker processes; expires if the holder stops heartbeating."""

    def __init__(self, job: Dict[str, Any], config: Dict[str, Any], view_name: str, project_id: Optional[str]):
        import uuid as _uuid

        from semabridge.core import run_coordination

        self._rc = run_coordination
        target_cfg = _resolve_target_config(config) or {}
        self._key = run_coordination.target_lease_key(
            target_cfg.get("database"), target_cfg.get("schema") or target_cfg.get("schema_name"), view_name
        )
        self._own_run = not job.get("project_run_id")
        self._run_id = str(job.get("project_run_id") or f"adhoc-{_uuid.uuid4()}")
        self._holder = f"{self._run_id}:{job.get('model_label')}"
        self._project_id = project_id
        self._holder_ctx: Any = None

    def __enter__(self) -> bool:
        if self._own_run:
            # A deploy outside a project run still heartbeats its leases.
            self._rc.register_live_run(self._run_id)
        result = self._rc.acquire_lease(
            self._key, run_id=self._run_id, holder=self._holder, lease_type="target", project_id=self._project_id
        )
        if not result:
            if self._own_run:
                self._rc.unregister_live_run(self._run_id)
            return False
        self._holder_ctx = self._rc.deploy_holder(self._run_id, self._holder, self._project_id)
        self._holder_ctx.__enter__()
        return True

    def __exit__(self, *exc: Any) -> bool:
        if self._holder_ctx is not None:
            self._holder_ctx.__exit__(*exc)
            self._holder_ctx = None
            self._rc.release_leases(holder=self._holder)
        if self._own_run:
            self._rc.unregister_live_run(self._run_id)
        return False


def _close_engine_row(repository: Any, engine: Any, exc: BaseException) -> None:
    """The engine raised before recording its outcome: finish its runs row
    (failed) so it is not left "running" and heartbeated forever."""
    run_id = getattr(getattr(engine, "_context", None), "run_id", None)
    if not run_id:
        return
    try:
        repository.record_run_complete(
            run_id=str(run_id), status="failed", final_step=0, duration_ms=0,
            error_message=str(exc)[:2000],
        )
    except Exception as record_exc:  # noqa: BLE001
        logger.debug("Could not close engine run %s: %s", run_id, record_exc)
        from semabridge.core.run_coordination import unregister_live_run

        unregister_live_run(str(run_id))


def _target_lock_key(config: Dict[str, Any], target_view_name: str) -> str:
    """Lock key for one deployed view: database.schema.view, so two projects
    deploying a view with the same name into DIFFERENT schemas never block
    each other, while two runs targeting the same view always do."""
    target_cfg = _resolve_target_config(config) or {}
    database = str(target_cfg.get("database") or "").strip()
    schema = str(target_cfg.get("schema") or target_cfg.get("schema_name") or "").strip()
    return ".".join(part for part in (database, schema, target_view_name) if part)


def _record_true_run_completion(repository: ModelRepository, summary_data: Dict[str, Any]) -> None:
    """Persist the run's TRUE terminal status (demo-mode aware) on its runs
    row. Without this the modern engine leaves runs rows at "running", so
    first-sync-only behaviour (e.g. the opt-in data load) would re-trigger
    on every sync."""
    run_id = summary_data.get("run_id")
    if not run_id:
        return
    try:
        true_status = str(summary_data.get("status") or "").lower()
        demo_mode_active = bool(summary_data.get("demo_mode"))
        errors = summary_data.get("errors") or []
        repository.record_run_complete(
            run_id=str(run_id),
            status=true_status,
            final_step=int(summary_data.get("last_successful_step") or 0),
            duration_ms=int(summary_data.get("duration_ms") or 0),
            error_message=(errors[0].get("message") if errors else None),
            demo_mode=demo_mode_active,
            demo_masked=(demo_mode_active and true_status in ("failed", "partial")),
        )
    except Exception as record_exc:
        logger.error("Failed to persist true run completion for run_id=%s: %s", run_id, record_exc)


def _resolve_result_view_name(run_context: Any, target_type: str) -> Optional[str]:
    """Final deployed view name for a per-model result (None if unknown)."""
    if run_context is None:
        return None
    recorded = getattr(run_context, "deployed_view_name", None)
    if recorded:
        return str(recorded)
    sml_model = getattr(run_context, "sml_model", None)
    if sml_model is not None and str(target_type or "").lower() == "snowflake":
        raw = getattr(sml_model, "unique_name", None) or getattr(sml_model, "label", None)
        if raw:
            return get_target_deployment_name(str(raw), "snowflake")
    return None


def _failed_result(
    job: Dict[str, Any],
    message: str,
    error_type: str,
    started: Optional[float] = None,
    dropped_entities: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Uniform per-model failure entry -- same keys as a successful one, with
    the error message always present in console.lines and recorded in dropped_entities."""
    model_label = str(job.get("model_label") or "unknown")
    drops: List[Dict[str, Any]] = list(dropped_entities or [])
    # Always include a record explaining the failure rejection so the runs page displays the exact reason
    drops.append({
        "entity_kind": "model",
        "entity_name": model_label,
        "stage": "ddl_deployment",
        "reason": f"Deployment rejected: {message}",
        "detail": f"{error_type}: {message}",
        "by_design": False,
    })
    summary_data = {
        "status": "FAILED",
        "errors": [
            {
                "step_number": 0,
                "step_name": "Execution",
                "message": message,
                "error_type": error_type,
            }
        ],
        "dropped_entities": drops,
    }
    return {
        "model": model_label,
        "status": "failed",
        "summary": summary_data,
        "routing_summary": None,
        "console": _build_console_details(summary_data, fallback_message=f"[ERROR] {message}"),
        "run_id": None,
        "missing_dims": {},
        "dropped_entities": drops,
        "data_backfill_results": [],
        "cortex_validation_results": [],
        "upsert_delta": None,
        "pbix_path": job.get("pbix_path"),
        "view_name": None,
        "target_artifact_path": None,
        "physical_namespace": job.get("physical_namespace"),
        "duration_ms": int((time.perf_counter() - started) * 1000) if started is not None else 0,
    }

def _resolve_requested_parallelism(payload: Dict[str, Any]) -> int:
    if "max_parallel_models" in payload:
        requested_parallelism = payload.get("max_parallel_models")
    elif "max_workers" in payload:
        requested_parallelism = payload.get("max_workers")
    else:
        requested_parallelism = os.getenv("SEMABRIDGE_MAX_PARALLEL_MODELS") or str(DEFAULT_MAX_PARALLEL_MODELS)
    try:
        return max(1, min(int(requested_parallelism), MAX_PARALLEL_MODELS_CAP))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PARALLEL_MODELS

def _resolve_executor_kind(payload: Dict[str, Any], is_fabric_bound: bool) -> str:
    # Process workers are disabled for Fabric-bound runs to avoid auth token/session
    # cross-process issues and preserve stability under API throttling.
    requested = str(
        payload.get("executor")
        or payload.get("concurrency_executor")
        or os.getenv("SEMABRIDGE_SYNC_EXECUTOR", DEFAULT_EXECUTOR)
    ).strip().lower()

    if requested not in {"thread", "process"}:
        requested = DEFAULT_EXECUTOR

    if is_fabric_bound and requested == "process":
        logger.info("Process executor requested but run is Fabric-bound; falling back to thread executor")
        return "thread"

    return requested

def _resolve_effective_parallelism(
    *,
    sync_jobs: List[Dict[str, Any]],
    payload: Dict[str, Any],
    max_parallel_models: int,
    is_fabric_bound: bool,
    executor_kind: str,
) -> int:
    effective_parallelism = min(len(sync_jobs), max_parallel_models)
    if is_fabric_bound:
        fabric_limit_raw = payload.get("max_parallel_fabric_jobs")
        if fabric_limit_raw in (None, ""):
            fabric_limit_raw = os.getenv("SEMABRIDGE_MAX_PARALLEL_FABRIC_JOBS", str(DEFAULT_MAX_PARALLEL_FABRIC_JOBS))
        try:
            fabric_limit = max(1, int(fabric_limit_raw))
        except ValueError:
            fabric_limit = DEFAULT_MAX_PARALLEL_FABRIC_JOBS
        effective_parallelism = min(effective_parallelism, fabric_limit)

    if executor_kind == "process":
        cpu_count = os.cpu_count() or 4
        process_ceiling_raw = payload.get("process_max_workers")
        if process_ceiling_raw in (None, ""):
            process_ceiling_raw = os.getenv("SEMABRIDGE_PROCESS_MAX_WORKERS", str(DEFAULT_PROCESS_MAX_WORKERS))
        try:
            process_ceiling = max(1, int(process_ceiling_raw))
        except ValueError:
            process_ceiling = DEFAULT_PROCESS_MAX_WORKERS
        effective_parallelism = min(effective_parallelism, max(1, min(cpu_count, process_ceiling)))

    return max(1, effective_parallelism)

def _run_parallel_jobs(
    *,
    sync_jobs: List[Dict[str, Any]],
    effective_parallelism: int,
    executor_kind: str,
    source_type: str,
    target_type: str,
    config: Dict[str, Any],
    config_path: str,
    deploy_enabled: bool,
    resolved_workspace_id: str,
    requested_project_id: Optional[str] = None,
    account_id: Optional[str] = None,
    sync_mode: str = "copy",
    force: bool = False,
) -> List[Dict[str, Any]]:
    per_model_results: List[Dict[str, Any]] = []

    # Fabric dataset display names are only known after each job's own
    # extraction, so a multi-model Fabric batch disambiguates colliding view
    # names through one shared, thread-scoped registry. (Fabric-bound runs
    # always use the thread executor -- see _resolve_executor_kind.)
    batch_name_registry: Optional[BatchViewNameRegistry] = None
    if (
        source_type == "fabric"
        and any(job.get("multi_mode") for job in sync_jobs)
        and executor_kind != "process"
    ):
        batch_name_registry = BatchViewNameRegistry()

    if effective_parallelism <= 1:
        for job in sync_jobs:
            result = _run_single_job(
                job,
                engine_source_type=source_type,
                target_type=target_type,
                config=config,
                config_path=config_path,
                deploy_enabled=deploy_enabled,
                resolved_workspace_id=resolved_workspace_id,
                requested_project_id=requested_project_id,
                account_id=account_id,
                sync_mode=sync_mode,
                force=force,
                batch_name_registry=batch_name_registry,
            )
            per_model_results.append(result)
            _log_model_console_trace(result)
        return per_model_results

    executor_cls = ProcessPoolExecutor if executor_kind == "process" else ThreadPoolExecutor
    with executor_cls(max_workers=effective_parallelism) as executor:
        # Use contextvars.copy_context() for thread-based executors so that
        # observability context (run_id, user_id) propagates to child threads.
        if executor_kind == "thread":
            import contextvars

            future_to_job = {}
            for job in sync_jobs:
                # Copy the parent context for EACH job individually.
                # Sharing the same Context object across threads causes "already entered" errors.
                ctx = contextvars.copy_context()
                future = executor.submit(
                    ctx.run,
                    _run_single_job,
                    job,
                    engine_source_type=source_type,
                    target_type=target_type,
                    config=config,
                    config_path=config_path,
                    deploy_enabled=deploy_enabled,
                    resolved_workspace_id=resolved_workspace_id,
                    requested_project_id=requested_project_id,
                    account_id=account_id,
                    sync_mode=sync_mode,
                    force=force,
                    batch_name_registry=batch_name_registry,
                )
                future_to_job[future] = job
        else:
            # Process workers get their own memory space — context vars
            # do not propagate. They rely on account_id for isolation. The
            # global model slot is taken HERE (parent) before each submit and
            # released when the child finishes, so the cap holds across
            # processes too.
            from semabridge.core.run_coordination import (
                acquire_model_slot_for_child,
                release_model_slot_for_child,
            )

            future_to_job = {}
            for job in sync_jobs:
                acquire_model_slot_for_child()
                child_job = {**job, "_slot_held": True}
                try:
                    future = executor.submit(
                        _run_single_job,
                        child_job,
                        engine_source_type=source_type,
                        target_type=target_type,
                        config=config,
                        config_path=config_path,
                        deploy_enabled=deploy_enabled,
                        resolved_workspace_id=resolved_workspace_id,
                        requested_project_id=requested_project_id,
                        account_id=account_id,
                        sync_mode=sync_mode,
                        force=force,
                    )
                except BaseException:
                    release_model_slot_for_child()
                    raise
                future.add_done_callback(release_model_slot_for_child)
                future_to_job[future] = job
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                result = future.result()
                per_model_results.append(result)
                _log_model_console_trace(result)
            except Exception as exc:  # noqa: BLE001
                model_label = str(job.get("model_label") or "unknown")
                logger.exception("Parallel worker crashed for model '%s': %s", model_label, exc)
                per_model_results.append(_failed_result(job, str(exc), type(exc).__name__))
                _log_model_console_trace(per_model_results[-1])

    return per_model_results

def execute_sync_request(payload: Dict[str, Any], normalize_yaml_windows_path_fields, account_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    _config_path, config = _load_config(payload, normalize_yaml_windows_path_fields)
    config_path = str(Path(_config_path).resolve()) if _config_path else ""
    requested_project_id = str(payload.get("project_id") or config.get("project_id") or "").strip() or None
    sync_jobs, source_type, target_type, source_cfg, target_cfg = _build_sync_jobs(config)
    # Dry run of ONE model of a project: naming was computed over the whole
    # configured model list above (exactly as the project's sync computes
    # it); only the scoped model actually runs.
    scope_refs = [r for r in (payload.get("scope_source_refs") or []) if str(r or "").strip()]
    if scope_refs and payload.get("dry_run"):
        scoped = [job for job in sync_jobs if any(job_matches_source_ref(job, ref) for ref in scope_refs)]
        if not scoped:
            raise ValidationError(f"Dry-run scope {scope_refs} matches none of the configured models.")
        sync_jobs = scoped
    # Retry of failed models (run_service.retry_failed_models_compat): only
    # the listed models run; naming was computed over all of them above.
    scope_models = [
        str(m) for m in (payload.get("scope_models") or config.get("sync_scope_models") or [])
        if str(m or "").strip()
    ]
    if scope_models:
        wanted = {m.casefold() for m in scope_models}
        scoped = [
            job for job in sync_jobs
            if str(job.get("model_label") or "").casefold() in wanted
            or any(job_matches_source_ref(job, ref) for ref in scope_models)
        ]
        if not scoped:
            raise ValidationError(f"Retry scope {scope_models} matches none of the configured models.")
        sync_jobs = scoped
    plans_by_label = {
        str(job["model_label"]): {
            **model_plan_for_job(job, target_type),
            "config_hash": model_config_fingerprint(config, job, target_type),
            "overrides_hash": model_overrides_fingerprint(config, job),
        }
        for job in sync_jobs
    }
    # Per-model log files are keyed by the PROJECT run id (set by run_service
    # for real runs; absent for dry runs and direct calls -> no log files).
    project_run_id = str(payload.get("project_run_id") or "").strip() or None
    for job in sync_jobs:
        job["project_run_id"] = project_run_id
        job["log_project_id"] = requested_project_id
    settings = get_settings()

    _fabric_env_workspace_id = ""
    try:
        _fabric_env_workspace_id = settings.fabric.workspace_id or ""
    except Exception:
        pass  # Fabric not configured in .env — credentials come from Account table via identity_id

    resolved_workspace_id = str(
        source_cfg.get("workspace_id")
        or (target_cfg.get("workspace_id") if isinstance(target_cfg, dict) else None)
        or (config.get("fabric", {}) or {}).get("workspace_id")
        or _fabric_env_workspace_id
        or "default"
    )
    deploy_enabled = bool((target_cfg or {}).get("deploy", True))
    dry_run = bool(payload.get("dry_run", False))
    if dry_run:
        deploy_enabled = False

    max_parallel_models = _resolve_requested_parallelism(payload)
    is_fabric_bound = source_type == "fabric" or target_type == "fabric"
    executor_kind = _resolve_executor_kind(payload, is_fabric_bound)
    effective_parallelism = _resolve_effective_parallelism(
        sync_jobs=sync_jobs,
        payload=payload,
        max_parallel_models=max_parallel_models,
        is_fabric_bound=is_fabric_bound,
        executor_kind=executor_kind,
    )
    
    from semabridge.core.run_helpers import sync_mode_from_project_config

    sync_mode = str(
        payload.get("sync_mode") or sync_mode_from_project_config(config) or "copy"
    ).lower()
    if sync_mode not in {"copy", "upsert"}:
        sync_mode = "copy"
    # UPSERT delta baseline: dry runs execute under a preview project id but
    # must diff against the real project's last deployed snapshot.
    if payload.get("binding_project_id") and isinstance(config, dict):
        config["binding_project_id"] = str(payload["binding_project_id"])
    if payload.get("delta_baseline_project_id") and isinstance(config, dict):
        config["delta_baseline_project_id"] = str(payload["delta_baseline_project_id"])
        if isinstance(payload.get("delta_preview_overrides"), dict):
            config["delta_preview_overrides"] = payload["delta_preview_overrides"]
    effective_force = force or bool(payload.get("force", False))

    logger.info(
        "Batch sync start: source=%s target=%s models=%d executor=%s parallelism=%d workspace=%s deploy=%s",
        source_type,
        target_type,
        len(sync_jobs),
        executor_kind,
        effective_parallelism,
        resolved_workspace_id,
        deploy_enabled,
    )

    # No process->thread whole-batch retry: re-running every job on threads
    # after a process-pool failure could deploy models that already deployed.
    per_model_results = _run_parallel_jobs(
        sync_jobs=sync_jobs,
        effective_parallelism=effective_parallelism,
        executor_kind=executor_kind,
        source_type=source_type,
        target_type=target_type,
        config=config,
        config_path=config_path,
        deploy_enabled=deploy_enabled,
        resolved_workspace_id=resolved_workspace_id,
        requested_project_id=requested_project_id,
        account_id=account_id,
        sync_mode=sync_mode,
        force=effective_force,
    )

    job_order = {str(job["model_label"]): index for index, job in enumerate(sync_jobs)}
    per_model_results.sort(key=lambda item: job_order.get(str(item.get("model") or ""), 0))
    for result in per_model_results:
        plan = plans_by_label.get(str(result.get("model") or ""))
        if plan is not None:
            result.setdefault("model_plan", plan)

    succeeded = [result for result in per_model_results if result["status"] == "success"]
    skipped = [result for result in per_model_results if result["status"] == "skipped"]
    if succeeded and len(succeeded) + len(skipped) == len(per_model_results):
        # Skipped models (no tables) do not make a run partial: retrying
        # them cannot help. A run where nothing deployed is never a success.
        overall_status = "success"
    elif succeeded:
        overall_status = "partial"
    elif any(result["status"] == "conflict" for result in per_model_results):
        overall_status = "conflict"
    else:
        overall_status = "failed"

    last_summary = next(
        (result.get("summary") for result in reversed(per_model_results) if isinstance(result.get("summary"), dict)),
        {},
    )

    logger.info(
        "Sync complete: %d/%d models succeeded (status=%s, parallelism=%d, executor=%s)",
        len(succeeded),
        len(per_model_results),
        overall_status,
        effective_parallelism,
        executor_kind,
    )

    return {
        "status": overall_status,
        "models_synced": len(succeeded),
        "models_skipped": [str(result.get("model") or "") for result in skipped],
        "total_models": len(per_model_results),
        "results": per_model_results,
        "summary": last_summary or {},
        "routing_summary": (last_summary or {}).get("routing_summary") if isinstance(last_summary, dict) else None,
        "batch": {
            "max_batch_models": MAX_BATCH_MODELS,
            "requested_parallelism": max_parallel_models,
            "effective_parallelism": effective_parallelism,
            "executor": executor_kind,
            "fabric_limited": bool(is_fabric_bound and effective_parallelism < max_parallel_models),
        },
    }