from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.domain.exceptions import NotFoundError, ValidationError

logger = logging.getLogger("semabridge.api")

_TYPE_ALIASES = {
    "snowflake_semantic_view": "snowflake",
    "microsoft_fabric": "fabric",
    "ms_fabric": "fabric",
    "databricks_sql": "databricks",
    "dbx": "databricks",
}

MAX_BATCH_MODELS = 10
DEFAULT_MAX_PARALLEL_MODELS = 10
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

def _build_console_details(summary_data: Dict[str, Any]) -> Dict[str, Any]:
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
            sync_jobs.append({"dataset_id": explicit_id, "pbix_path": None, "model_label": explicit_id})
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
                    sync_jobs.append({"dataset_id": resolved_id, "pbix_path": None, "model_label": resolved_id})

    elif source_type == "pbix":
        explicit_pbix = source_cfg.get("pbix_path")
        if not explicit_pbix and not source_cfg.get("models"):
            raise ValidationError(
                "PBIX source requires source.pbix_path or source.models."
            )

        if explicit_pbix:
            sync_jobs.append(
                {
                    "dataset_id": None,
                    "pbix_path": explicit_pbix,
                    "model_label": Path(explicit_pbix).stem,
                }
            )
        else:
            model_list = source_cfg.get("models") or [config.get("model_name", "")]
            for raw_model in model_list:
                if isinstance(raw_model, dict):
                    model_name = str(raw_model.get("pbixPath") or raw_model.get("name") or "")
                else:
                    model_name = str(raw_model or "")

                configured_folder = source_cfg.get("pbix_folder")
                base_dir = Path(configured_folder).expanduser().resolve() if configured_folder else _resolve_models_path()
                if not base_dir.exists() or not base_dir.is_dir():
                    fallback_dir = _resolve_models_path()
                    if fallback_dir.exists() and fallback_dir.is_dir():
                        base_dir = fallback_dir

                pbix_path: Optional[str] = None
                model_path = Path(model_name).expanduser() if model_name else None
                if model_path and model_path.suffix.lower() == ".pbix" and model_path.exists():
                    pbix_path = str(model_path.resolve())

                if not pbix_path and model_name:
                    model_stem = Path(model_name).stem
                    candidate = Path(base_dir) / model_name
                    if candidate.suffix.lower() != ".pbix":
                        candidate = candidate.with_suffix(".pbix")
                    if candidate.exists():
                        pbix_path = str(candidate)
                    else:
                        for path in Path(base_dir).glob(f"*{model_stem}*.pbix"):
                            pbix_path = str(path)
                            break
                        if not pbix_path:
                            for path in Path.cwd().rglob(f"*{model_stem}*.pbix"):
                                pbix_path = str(path)
                                break

                if not pbix_path and base_dir.exists() and base_dir.is_dir():
                    first_pbix = next(base_dir.glob("*.pbix"), None)
                    if first_pbix:
                        pbix_path = str(first_pbix)

                if not pbix_path:
                    all_pbix = list(Path.cwd().rglob("*.pbix"))
                    if len(all_pbix) == 1:
                        pbix_path = str(all_pbix[0])

                if not pbix_path:
                    raise ValidationError((
                            f"No .pbix file found for model '{model_name}'. "
                            "Ensure a .pbix file exists in source.pbix_folder or set source.pbix_path."
                        ))

                sync_jobs.append(
                    {
                        "dataset_id": None,
                        "pbix_path": pbix_path,
                        "model_label": model_name or Path(pbix_path).stem,
                    }
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

    return sync_jobs, source_type, target_type, source_cfg, target_cfg

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

def _run_single_job(
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
) -> Dict[str, Any]:
    model_label = job["model_label"]
    repository = ModelRepository()
    engine = ExecutionEngine(db_manager=repository)

    logger.info("Syncing model: %s", model_label)
    try:
        summary = engine.execute(
            source=engine_source_type,
            target=target_type,
            project_id=requested_project_id,
            config_path=Path(config_path) if config_path else None,
            config_dict=config,
            dataset_id=job["dataset_id"],
            workspace_id=resolved_workspace_id,
            pbix_path=job["pbix_path"],
            project_name=model_label,
            tag=str(config.get("version_tag", "v1.0")),
            deploy=deploy_enabled,
            dry_run=not deploy_enabled,
            account_id=account_id,
            sync_mode=sync_mode,
            force=force,
        )
        summary_data = summary.model_dump(mode="json")
        job_ok = str(summary_data.get("status", "")).upper() == "SUCCESS"

        if job_ok and deploy_enabled:
            try:
                _persist_model_version(repository, config, summary_data, model_label, resolved_workspace_id)
            except Exception as version_error:
                logger.warning("Version row write failed for %s: %s", model_label, version_error)

        return {
            "model": model_label,
            "status": "success" if job_ok else "failed",
            "summary": summary_data,
            "routing_summary": summary_data.get("routing_summary") if isinstance(summary_data, dict) else None,
            "console": _build_console_details(summary_data),
            "run_id": summary_data.get("run_id"),
            "missing_dims": summary_data.get("missing_dims") or {},
            "dropped_entities": summary_data.get("dropped_entities") or [],
        }
    except Exception as exc:
        logger.error("Sync failed for model '%s': %s", model_label, exc)
        summary_data = {
            "errors": [
                {
                    "step_number": 0,
                    "step_name": "Execution",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            ]
        }
        return {
            "model": model_label,
            "status": "conflict" if "CONFLICT:" in str(exc) else "failed",
            "summary": summary_data,
            "routing_summary": None,
            "console": _build_console_details(summary_data),
            "run_id": summary_data.get("run_id"),
        }

def _resolve_requested_parallelism(payload: Dict[str, Any]) -> int:
    if "max_parallel_models" in payload:
        requested_parallelism = payload.get("max_parallel_models")
    elif "max_workers" in payload:
        requested_parallelism = payload.get("max_workers")
    else:
        requested_parallelism = os.getenv("SEMABRIDGE_MAX_PARALLEL_MODELS", str(DEFAULT_MAX_PARALLEL_MODELS))
    try:
        return max(1, min(int(requested_parallelism), DEFAULT_MAX_PARALLEL_MODELS))
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
                )
                future_to_job[future] = job
        else:
            # Process workers get their own memory space — context vars
            # do not propagate. They rely on account_id for isolation.
            future_to_job = {
                executor.submit(
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
                ): job
                for job in sync_jobs
            }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                result = future.result()
                per_model_results.append(result)
                _log_model_console_trace(result)
            except Exception as exc:  # noqa: BLE001
                model_label = str(job.get("model_label") or "unknown")
                logger.exception("Parallel worker crashed for model '%s': %s", model_label, exc)
                summary_data = {
                    "errors": [
                        {
                            "step_number": 0,
                            "step_name": "Execution",
                            "message": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    ]
                }
                per_model_results.append(
                    {
                        "model": model_label,
                        "status": "failed",
                        "summary": summary_data,
                        "routing_summary": None,
                        "console": _build_console_details(summary_data),
                        "run_id": None,
                    }
                )
                _log_model_console_trace(per_model_results[-1])

    return per_model_results

def execute_sync_request(payload: Dict[str, Any], normalize_yaml_windows_path_fields, account_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    _config_path, config = _load_config(payload, normalize_yaml_windows_path_fields)
    config_path = str(Path(_config_path).resolve()) if _config_path else ""
    requested_project_id = str(payload.get("project_id") or config.get("project_id") or "").strip() or None
    sync_jobs, source_type, target_type, source_cfg, target_cfg = _build_sync_jobs(config)
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
    
    sync_mode = str(payload.get("sync_mode") or config.get("sync_mode") or "copy").lower()
    if sync_mode not in {"copy", "upsert"}:
        sync_mode = "copy"

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

    try:
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
            force=force or bool(payload.get("force", False)),
        )
    except Exception as exc:  # noqa: BLE001
        if executor_kind == "process":
            logger.exception(
                "Process executor failed for batch sync; retrying with thread executor. Error: %s",
                exc,
            )
            executor_kind = "thread"
            effective_parallelism = _resolve_effective_parallelism(
                sync_jobs=sync_jobs,
                payload=payload,
                max_parallel_models=max_parallel_models,
                is_fabric_bound=is_fabric_bound,
                executor_kind=executor_kind,
            )
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
            )
        else:
            raise

    job_order = {str(job["model_label"]): index for index, job in enumerate(sync_jobs)}
    per_model_results.sort(key=lambda item: job_order.get(str(item.get("model") or ""), 0))

    succeeded = [result for result in per_model_results if result["status"] == "success"]
    if len(succeeded) == len(per_model_results):
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