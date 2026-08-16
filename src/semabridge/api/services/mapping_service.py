"""Mapping service — field mapping management, auto-mapping, and identifier diagnostics.

Provides list, auto-map, update, and delete mappings plus all supporting
private helpers for building, serializing, and validating entity mappings.
"""
import re
import uuid
from typing import Any, Dict, List, Optional

import yaml

from fastapi import Response

from semabridge.api.services.project_mapping_engine import (
    _extract_metric_source_tables,
    build_entity_mappings,
    sanitize_identifier,
)
from semabridge.api.services.project_shared import (
    _compat_ensure_loaded,
    _compat_mappings,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_runs,
    _compat_projects,
    _compat_save_store,
)
from semabridge.domain.exceptions import ExternalServiceError, NotFoundError, ValidationError
from semabridge.utils.identifiers import IdentifierSanitizer

_SNOWFLAKE_SANITIZER = IdentifierSanitizer(force_uppercase=True, suppress_reserved=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compat_parse_project_cfg_dict(project_cfg: str) -> Dict[str, Any]:
    try:
        parsed = yaml.safe_load(project_cfg) or {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _compat_source_model_names_from_project_cfg(project_cfg: str) -> List[str]:
    parsed = _compat_parse_project_cfg_dict(project_cfg)
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    model_names: List[str] = []

    source_models = source_cfg.get("models") if isinstance(source_cfg.get("models"), list) else []
    for raw_name in source_models:
        name = str(raw_name or "").strip()
        if name:
            model_names.append(name)

    if not model_names:
        source_model = source_cfg.get("model")
        if isinstance(source_model, str):
            source_model = source_model.strip()
            if source_model and source_model != "*":
                model_names.append(source_model)

    if not model_names:
        top_level_models = parsed.get("models") if isinstance(parsed.get("models"), list) else []
        for raw_name in top_level_models:
            name = str(raw_name or "").strip()
            if name:
                model_names.append(name)

    return model_names


def _compat_mapping_project_ids(project_id: str = "") -> List[str]:
    pid = str(project_id or "").strip()
    if pid:
        return [pid]
    return [
        str(row.get("project_id") or row.get("id") or "").strip()
        for row in _compat_projects.values()
        if str(row.get("project_id") or row.get("id") or "").strip()
    ]


def _compat_project_target_type(project_id: str) -> str:
    cfg = str(_compat_project_configs.get(project_id) or "").strip()
    if not cfg:
        return ""
    parsed = _compat_parse_project_cfg_dict(cfg)
    if not isinstance(parsed, dict):
        return ""
    target = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}
    if not target:
        targets = parsed.get("targets")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            target = targets[0]
    return str((target or {}).get("type") or "").strip().lower()


def _compat_sanitize_target_name_for_project(project_id: str, target_name: str) -> str:
    raw = str(target_name or "").strip()
    if not raw:
        return raw
    if _compat_project_target_type(project_id) != "snowflake":
        return raw
    return _SNOWFLAKE_SANITIZER.sanitize_alias(raw)


def _compat_collect_manual_mapping_overrides(project_id: str) -> List[Dict[str, str]]:
    overrides: List[Dict[str, str]] = []
    for mapping in _compat_mappings.values():
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("project_id") or "") != str(project_id):
            continue
        if not bool(mapping.get("is_user_edited")):
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        target_name = _compat_sanitize_target_name_for_project(
            project_id,
            str(mapping.get("target_name") or "").strip(),
        )
        if not source_path or not target_name:
            continue
        overrides.append({
            "source_path": source_path,
            "target_name": target_name,
            "entity_kind": str(mapping.get("entity_kind") or "").strip().lower(),
            "source_name": str(mapping.get("source_name") or "").strip(),
        })
    return overrides


def _compat_apply_manual_mapping_overrides_to_cfg(config_yaml: str, project_id: str) -> str:
    parsed = _compat_parse_project_cfg_dict(config_yaml) or {}
    overrides = _compat_collect_manual_mapping_overrides(project_id)
    if not overrides:
        return config_yaml
    parsed["mappings_overrides"] = overrides
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)


def _compat_existing_entity_mappings(project_id: str) -> Dict[str, Dict[str, Any]]:
    existing: Dict[str, Dict[str, Any]] = {}
    for mapping in _compat_mappings.values():
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("project_id") or "") != str(project_id):
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        if not source_path:
            continue
        existing[source_path] = dict(mapping)
    return existing


def _compat_hydrate_missing_metrics_from_manual_mappings(
    latest_model: Dict[str, Any],
    existing_mappings: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    import logging
    logger = logging.getLogger(__name__)

    model = dict(latest_model or {})
    metrics = model.get("metrics") if isinstance(model.get("metrics"), list) else []
    metrics = [row for row in metrics if isinstance(row, dict)]
    present_metric_names = {
        str(row.get("unique_name") or row.get("name") or row.get("label") or "").strip()
        for row in metrics
    }
    present_metric_names.discard("")

    hydrated = list(metrics)
    added = 0
    for mapping in existing_mappings.values():
        if not isinstance(mapping, dict):
            continue
        if not bool(mapping.get("is_user_edited")):
            continue
        if str(mapping.get("entity_kind") or "").lower() != "metric":
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        if not source_path.startswith("metrics."):
            continue
        metric_name = source_path[len("metrics."):].strip()
        if not metric_name or metric_name in present_metric_names:
            continue
        hydrated.append({
            "unique_name": metric_name,
            "data_type": mapping.get("source_data_type") or "decimal",
        })
        present_metric_names.add(metric_name)
        added += 1

    if added:
        logger.debug("Hydrated %s manual metric mapping(s) into latest model state", added)
    model["metrics"] = hydrated
    return model


def _compat_scope_model_for_dry_run(
    latest_model: Dict[str, Any],
    selected_model_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    model = dict(latest_model or {})
    selected = [str(item or "").strip() for item in (selected_model_names or []) if str(item or "").strip()]
    if not selected:
        return model

    selected_lookup = {name.lower() for name in selected}
    datasets = model.get("datasets") if isinstance(model.get("datasets"), list) else []
    filtered_datasets = [
        row
        for row in datasets
        if isinstance(row, dict) and str(row.get("unique_name") or "").strip().lower() in selected_lookup
    ]
    if not filtered_datasets:
        return model

    model["datasets"] = filtered_datasets

    # Keep metrics that can be attributed to the selected dataset scope.
    metrics = model.get("metrics") if isinstance(model.get("metrics"), list) else []
    if metrics:
        dataset_lookup = {
            str(row.get("unique_name") or "").strip().lower(): str(row.get("unique_name") or "").strip()
            for row in filtered_datasets
            if isinstance(row, dict) and str(row.get("unique_name") or "").strip()
        }
        scoped_metrics: List[Dict[str, Any]] = []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            source_tables = _extract_metric_source_tables(metric, dataset_lookup)
            if source_tables:
                scoped_metrics.append(metric)
        model["metrics"] = scoped_metrics
    else:
        model["metrics"] = []

    return model


def _compat_preferred_snapshot_id_from_sync_result(
    sync_result: Dict[str, Any],
    selected_model_names: Optional[List[str]] = None,
) -> str:
    if not isinstance(sync_result, dict):
        return ""

    selected_lookup = {
        str(item or "").strip().lower()
        for item in (selected_model_names or [])
        if str(item or "").strip()
    }

    results = sync_result.get("results") if isinstance(sync_result.get("results"), list) else []
    if selected_lookup and results:
        for row in results:
            if not isinstance(row, dict):
                continue
            model_name = str(row.get("model") or "").strip().lower()
            if model_name not in selected_lookup:
                continue
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            sid = str(summary.get("sml_snapshot_id") or "").strip()
            if sid:
                return sid

    summary = sync_result.get("summary") if isinstance(sync_result.get("summary"), dict) else {}
    sid = str(summary.get("sml_snapshot_id") or "").strip()
    if sid:
        return sid

    for row in results:
        if not isinstance(row, dict):
            continue
        item_summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        sid = str(item_summary.get("sml_snapshot_id") or "").strip()
        if sid:
            return sid

    return ""


def _compat_build_project_entity_mappings(
    project_id: str,
    save_store: bool = True,
    target_connector: Optional[str] = None,
    selected_model_names: Optional[List[str]] = None,
    preferred_snapshot_id: str = "",
) -> Dict[str, Any]:
    from semabridge.api.services.snapshot_service import _compat_latest_sml_state
    from semabridge.api.services.project_shared import (
        _compat_default_project_yaml,
        _compat_load_repo_yaml_text,
    )

    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise NotFoundError("Project not found")

    try:
        latest_model = _compat_latest_sml_state(project_id, preferred_snapshot_id)
    except TypeError:
        latest_model = _compat_latest_sml_state(project_id)
    if not latest_model:
        project_cfg = (
            _compat_project_configs.get(project_id)
            or _compat_load_repo_yaml_text()
            or _compat_default_project_yaml(_compat_projects[project_id])
        )
        parsed_cfg = _compat_parse_project_cfg_dict(project_cfg)
        fallback_model_name = str(
            parsed_cfg.get("project_name") or _compat_projects[project_id].get("name") or "model"
        )
        fallback_model_names = _compat_source_model_names_from_project_cfg(project_cfg)
        latest_model = {
            "unique_name": fallback_model_name,
            "datasets": [{"unique_name": model_name, "columns": []} for model_name in fallback_model_names],
            "metrics": [],
        }

    existing_mappings = _compat_existing_entity_mappings(project_id)
    latest_model = _compat_hydrate_missing_metrics_from_manual_mappings(latest_model, existing_mappings)
    latest_model = _compat_scope_model_for_dry_run(latest_model, selected_model_names)

    built = build_entity_mappings(
        project_id=project_id,
        model=latest_model,
        existing_mappings=existing_mappings,
        session_key=f"{project_id}-mapping-session",
        target_connector=target_connector,
    )

    persisted: List[Dict[str, Any]] = []
    for mapping in built.get("mappings", []):
        mapping_id = str(mapping.get("id") or "")
        if not mapping_id:
            continue
        existing = _compat_mappings.get(mapping_id, {})
        merged = {**existing, **mapping}
        if existing.get("is_user_edited"):
            manual_target = _compat_sanitize_target_name_for_project(
                project_id,
                str(existing.get("target_name") or "").strip(),
            )
            if manual_target:
                merged["target_name"] = manual_target
                merged["status"] = "manual"
                merged["is_user_edited"] = True
        if save_store:
            _compat_mappings[mapping_id] = merged
        persisted.append(merged)

    if save_store:
        _compat_save_store()
    return {
        "project_id": project_id,
        "session_key": built.get("session_key"),
        "model_name": built.get("model_name"),
        "source_fields": built.get("source_fields", []),
        "target_fields": built.get("target_fields", []),
        "mappings": persisted,
        "collisions": built.get("collisions", []),
    }


def _compat_preview_model_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    selected_model_names = (
        payload.get("selected_model_names")
        if isinstance(payload.get("selected_model_names"), list)
        else []
    )
    normalized_model_names = [str(item or "").strip() for item in selected_model_names if str(item or "").strip()]
    if not normalized_model_names:
        normalized_model_names = _compat_source_model_names_from_project_cfg(str(payload.get("config_yaml") or ""))
    config_project_name = _compat_parse_project_cfg_dict(str(payload.get("config_yaml") or "")).get("project_name")
    project_name = str(
        config_project_name
        or payload.get("project_name")
        or payload.get("name")
        or "model"
    ).strip() or "model"

    datasets = []
    for raw_name in normalized_model_names:
        datasets.append({
            "unique_name": raw_name,
            "columns": [
                {"unique_name": "id", "data_type": "integer"},
                {"unique_name": "name", "data_type": "string"},
                {"unique_name": "created_at", "data_type": "timestamp"},
                {"unique_name": "updated_at", "data_type": "timestamp"},
            ],
        })

    return {
        "unique_name": project_name,
        "datasets": datasets,
        "metrics": [],
    }


def _compat_format_mapping_groups(mapping_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in mapping_payload.get("mappings", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("entity_kind") or "") == "table":
            source_path = str(row.get("source_path") or "")
            grouped[source_path] = {
                "id": row.get("id"),
                "source": row.get("source_name"),
                "target": row.get("target_name"),
                "source_path": source_path,
                "type": "table",
                "status": "manual" if row.get("is_user_edited") else "auto-detected",
                "collision_detected": bool(row.get("collision_detected")),
                "validation_status": row.get("validation_status"),
                "validation_code": row.get("validation_code"),
                "validation_message": row.get("validation_message"),
                "suggested_target_name": row.get("suggested_target_name"),
                "columns": [],
            }

    for row in mapping_payload.get("mappings", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("entity_kind") or "") != "column":
            continue
        parent_key = str(row.get("parent_source_path") or "")
        parent = grouped.get(parent_key)
        if not parent:
            continue
        parent["columns"].append({
            "source": row.get("source_name"),
            "target": row.get("target_name"),
            "source_path": row.get("source_path"),
            "type": row.get("target_data_type") or row.get("source_data_type") or "unknown",
            "key": str(row.get("source_name") or "").lower() == "id",
            "collision_detected": bool(row.get("collision_detected")),
            "validation_status": row.get("validation_status"),
            "validation_code": row.get("validation_code"),
            "validation_message": row.get("validation_message"),
            "suggested_target_name": row.get("suggested_target_name"),
            "resolution_suggestions": list(row.get("resolution_suggestions") or []),
        })

    return list(grouped.values())


def _compat_extract_invalid_identifier(message: str) -> str:
    text = str(message or "")
    if not text:
        return ""
    marker = "invalid identifier '"
    idx = text.lower().find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    end = text.find("'", start)
    if end <= start:
        return ""
    return text[start:end].strip()


def _compat_latest_identifier_diagnostics(project_id: str) -> List[Dict[str, str]]:
    latest_run: Optional[Dict[str, Any]] = None
    for run in _compat_project_runs.get(project_id, []):
        if isinstance(run, dict):
            latest_run = run
            break

    if not latest_run:
        return []

    candidates: List[str] = []
    candidates.extend([str(item) for item in (latest_run.get("logs") or []) if str(item or "").strip()])
    if isinstance(latest_run.get("summary"), dict):
        for err in (latest_run.get("summary", {}).get("errors") or []):
            if isinstance(err, dict):
                candidates.append(str(err.get("message") or ""))
    candidates.append(str(latest_run.get("error") or ""))
    candidates.append(str(latest_run.get("message") or ""))

    diagnostics: List[Dict[str, str]] = []
    seen: set = set()
    for candidate in candidates:
        actual_identifier = _compat_extract_invalid_identifier(candidate)
        if not actual_identifier:
            continue
        key = actual_identifier.upper()
        if key in seen:
            continue
        seen.add(key)
        parts = actual_identifier.split(".")
        source_hint = (parts[-1] if parts else actual_identifier).replace('"', "").strip().upper()
        diagnostics.append({
            "code": "INVALID_IDENTIFIER_REFERENCE",
            "actual_identifier": actual_identifier,
            "source_hint": source_hint,
            "message": f"Deploy SQL references invalid identifier {actual_identifier}.",
        })

    if diagnostics:
        return diagnostics
    return []


def _compat_apply_identifier_diagnostics_to_mappings(
    mappings: List[Dict[str, Any]],
    diagnostics: List[Dict[str, str]],
) -> None:
    if not diagnostics:
        return

    def _metric_expression_references_identifier(expression: str, identifier: str) -> bool:
        expr = str(expression or "")
        ident = str(identifier or "").strip()
        if not expr or not ident or "." not in ident:
            return False

        table_name, column_name = ident.rsplit(".", 1)
        table_name = table_name.strip().strip('"').strip("'").strip()
        column_name = column_name.strip().strip('"').strip("'").strip()
        if not table_name or not column_name:
            return False

        patterns = [
            rf"\b{re.escape(table_name)}\s*\.\s*{re.escape(column_name)}\b",
            rf"'{re.escape(table_name)}'\s*\[\s*{re.escape(column_name)}\s*\]",
            rf"\b{re.escape(table_name)}\s*\[\s*{re.escape(column_name)}\s*\]",
            rf"\"{re.escape(table_name)}\"\s*\.\s*\"{re.escape(column_name)}\"",
        ]
        return any(re.search(pattern, expr, flags=re.IGNORECASE) for pattern in patterns)

    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("entity_kind") or "").lower() != "metric":
            continue

        source_name = str(mapping.get("source_name") or "").upper()
        source_path = str(mapping.get("source_path") or "").upper()
        source_expression = str(mapping.get("source_expression") or "")

        for diag in diagnostics:
            actual_identifier = str(diag.get("actual_identifier") or "").strip()
            hint = str(diag.get("source_hint") or "").upper()
            if not hint:
                continue
            expression_match = _metric_expression_references_identifier(source_expression, actual_identifier)
            path_or_name_match = bool(hint in source_name or hint in source_path)
            if expression_match or (not source_expression.strip() and path_or_name_match):
                mapping["collision_detected"] = True
                mapping["validation_status"] = "invalid"
                mapping["validation_code"] = str(diag.get("code") or "INVALID_IDENTIFIER_REFERENCE")
                mapping["validation_message"] = str(
                    diag.get("message") or "Invalid identifier reference during deploy."
                )
                mapping["validation_debug"] = {
                    "actual_identifier": str(diag.get("actual_identifier") or ""),
                    "source_hint": hint,
                    "stage": "deploy_sql_compile",
                }
                break


def _compat_is_blocking_mapping(mapping: Dict[str, Any]) -> bool:
    if not isinstance(mapping, dict):
        return False

    validation_code = str(mapping.get("validation_code") or "").strip().upper()
    target_name = str(mapping.get("target_name") or "").strip()
    validation_message = str(mapping.get("validation_message") or "").strip().lower()
    status = str(mapping.get("status") or "").strip().lower()

    # Deterministic NAME_COLLISION rows are auto-resolved by suffixing the
    # identifier; they should not block deploy when a target exists.
    if (
        validation_code == "NAME_COLLISION"
        and target_name
        and ("resolved" in validation_message or status in {"auto", "manual"})
    ):
        return False

    if status in {"collision", "unmapped"}:
        return True

    validation_status = str(mapping.get("validation_status") or "").strip().lower()
    if validation_status in {"invalid", "collision"}:
        return True

    if validation_code and validation_code != "OK":
        return True

    if not target_name:
        return True

    if bool(mapping.get("collision_detected")):
        return True

    return False


def _compat_collect_dry_run_blockers(preview_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    mappings = preview_result.get("entity_mappings") if isinstance(preview_result, dict) else []
    if not isinstance(mappings, list):
        return blockers

    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        if not _compat_is_blocking_mapping(mapping):
            continue
        blockers.append({
            "id": str(mapping.get("id") or "").strip(),
            "source_path": str(mapping.get("source_path") or "").strip(),
            "source_name": str(mapping.get("source_name") or "").strip(),
            "target_name": str(mapping.get("target_name") or "").strip(),
            "status": str(mapping.get("status") or "").strip().lower(),
            "validation_status": str(mapping.get("validation_status") or "").strip().lower(),
            "validation_code": str(mapping.get("validation_code") or "").strip().upper(),
            "validation_message": str(mapping.get("validation_message") or "").strip(),
        })
    return blockers


def _compat_is_field_entity(mapping: Dict[str, Any]) -> bool:
    kind = str(mapping.get("entity_kind") or "").strip().lower()
    return kind in {"column", "metric", "measure"} or kind not in {"", "table", "dataset", "model"}


def _compat_serialize_auto_map_entity_mappings(
    mappings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    entity_mappings: List[Dict[str, Any]] = []

    for index, row in enumerate(mappings):
        if not isinstance(row, dict) or not _compat_is_field_entity(row):
            continue

        source_name = str(row.get("source_name") or row.get("name") or f"field_{index + 1}").strip()
        source_path = str(row.get("source_path") or "").strip()
        data_type = str(
            row.get("source_data_type") or row.get("data_type") or row.get("target_data_type") or "unknown"
        ).strip() or "unknown"
        target_name = str(row.get("target_name") or "").strip() or sanitize_identifier(source_name)
        validation_status = str(row.get("validation_status") or "valid").strip().lower() or "valid"
        validation_code = str(row.get("validation_code") or "OK").strip().upper() or "OK"
        validation_message = str(row.get("validation_message") or "").strip()
        suggested_target_name = str(row.get("suggested_target_name") or target_name).strip()
        # Trust build_entity_mappings()'s collision/reserved-keyword verdict
        # (project_mapping_engine.py) rather than re-deriving it here — a
        # second, independently-scoped check was the source of dry-run vs
        # deploy naming drift this serializer used to introduce.
        collision_detected = bool(row.get("collision_detected"))
        if validation_code in {"RESERVED_KEYWORD", "COLLISION", "NAME_COLLISION"} or validation_status == "collision":
            collision_detected = True
        if validation_code == "OK" and validation_status == "valid":
            validation_message = ""

        entity_mappings.append({
            "id": str(row.get("id") or f"mapping-{index + 1}"),
            "source_name": source_name,
            "target_name": target_name,
            "source_data_type": data_type,
            "target_data_type": str(row.get("target_data_type") or data_type).strip() or "unknown",
            "entity_kind": str(row.get("entity_kind") or "column").strip().lower() or "column",
            "source_path": source_path,
            "parent_source_path": str(row.get("parent_source_path") or ""),
            "validation_status": validation_status,
            "validation_code": validation_code,
            "validation_message": validation_message,
            "collision_detected": collision_detected,
            "suggested_target_name": suggested_target_name,
            "resolution_suggestions": list(row.get("resolution_suggestions") or []),
            "measure_source_tables": list(row.get("measure_source_tables") or []),
            "source_expression": str(row.get("source_expression") or ""),
            "status": str(row.get("status") or "auto").strip().lower() or "auto",
            "advisory_notes": list(row.get("advisory_notes") or []),
            "advisory_categories": list(row.get("advisory_categories") or []),
            "target_expression": row.get("target_expression") or "",
            "sync_enabled": bool(row.get("sync_enabled")) if row.get("sync_enabled") is not None else True,
            "sync_failure_reason": row.get("sync_failure_reason") or "",
            "depends_on_measures": list(row.get("depends_on_measures") or []),
            "synonyms": list(row.get("synonyms") or []),
            "synonym_sources": dict(row.get("synonym_sources") or {}),
            "has_report_alias": bool(row.get("has_report_alias")),
            "complexity_tier": row.get("complexity_tier"),
            # Static risk tier (metric-only; None for tables/columns) and
            # the Tier-5 provider's own self-reported estimate (metric-
            # only, Tier-5-only; None otherwise) -- see
            # project_mapping_engine.py's _compute_static_risk_tier.
            "static_risk_tier": row.get("static_risk_tier"),
            "static_risk_label": row.get("static_risk_label"),
            "llm_self_reported_confidence": row.get("llm_self_reported_confidence"),
        })

    for mapping in entity_mappings:
        if not str(mapping.get("validation_status") or "").strip():
            mapping["validation_status"] = "invalid" if mapping.get("collision_detected") else "valid"
        if not str(mapping.get("validation_code") or "").strip():
            mapping["validation_code"] = "COLLISION" if mapping.get("collision_detected") else "OK"

    return entity_mappings


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

async def list_mappings_compat(project_id: Optional[str] = None):
    _compat_ensure_loaded()
    project_ids = _compat_mapping_project_ids(str(project_id or "").strip())
    if not project_ids:
        raise ValidationError("project_id is required")

    if len(project_ids) == 1:
        data = _compat_build_project_entity_mappings(project_ids[0])
        return {
            "project_id": data.get("project_id"),
            "session_key": data.get("session_key"),
            "model_name": data.get("model_name"),
            "source_fields": data.get("source_fields", []),
            "target_fields": data.get("target_fields", []),
            "mappings": _compat_format_mapping_groups(data),
            "entity_mappings": data.get("mappings", []),
            "collisions": data.get("collisions", []),
        }

    combined_mappings: List[Dict[str, Any]] = []
    combined_source_fields: List[Dict[str, Any]] = []
    combined_target_fields: List[Dict[str, Any]] = []
    collisions: List[Dict[str, Any]] = []
    for pid in project_ids:
        data = _compat_build_project_entity_mappings(pid, save_store=False)
        combined_mappings.extend(data.get("mappings", []))
        combined_source_fields.extend(data.get("source_fields", []))
        combined_target_fields.extend(data.get("target_fields", []))
        collisions.extend(data.get("collisions", []))

    _compat_save_store()

    return {
        "project_id": None,
        "source_fields": combined_source_fields,
        "target_fields": combined_target_fields,
        "mappings": combined_mappings,
        "entity_mappings": combined_mappings,
        "collisions": collisions,
    }


async def auto_map_compat(payload: dict):
    import logging
    logger = logging.getLogger(__name__)

    project_id = str((payload or {}).get("project_id") or "").strip()
    user_id = (payload or {}).get("user_id")
    selected_model_names = (
        (payload or {}).get("selected_model_names")
        if isinstance((payload or {}).get("selected_model_names"), list)
        else []
    )
    target_connectors = (
        (payload or {}).get("target_connectors")
        if isinstance((payload or {}).get("target_connectors"), list)
        else []
    )
    explicit_target = str((payload or {}).get("target_connector") or "").strip()
    target_connector = explicit_target or (str(target_connectors[0]).strip() if target_connectors else "")
    dry_run = bool((payload or {}).get("dry_run", False))
    config_yaml = str((payload or {}).get("config_yaml") or "").strip()
    if project_id and not config_yaml:
        config_yaml = str(_compat_project_configs.get(project_id) or "").strip()

    if not project_id and not selected_model_names and _compat_projects:
        project_id = next(iter(_compat_projects.keys()))
    if not project_id and not selected_model_names:
        raise ValidationError("project_id or selected_model_names is required")

    reset_manual = bool((payload or {}).get("reset_manual", False))
    # Only use synthetic preview mode when there is no concrete project context.
    # For project-scoped dry runs, use project-backed mappings so measure rows
    # from the latest model state are preserved.
    preview_mode = (not project_id) and dry_run and (bool(config_yaml) or bool(selected_model_names))
    sync_result: Dict[str, Any] = {}  # populated in project-backed branch; empty otherwise
    if project_id and not preview_mode:
        from semabridge.api.services.core_domain_service import sync_models
        preferred_snapshot_id = ""
        try:
            sync_result = await sync_models({
                "project_id": project_id,
                "content": config_yaml or None,
                "dry_run": True,
                "user_id": user_id,
            })
            preferred_snapshot_id = _compat_preferred_snapshot_id_from_sync_result(sync_result, selected_model_names)
        except Exception as exc:
            has_existing_project_mappings = any(
                str(mapping.get("project_id") or "") == project_id
                for mapping in _compat_mappings.values()
                if isinstance(mapping, dict)
            )
            if not has_existing_project_mappings:
                logger.warning("Auto-map pre-sync failed for project %s: %s", project_id, exc)
                raise ExternalServiceError(
                    f"Dry-run sync failed before auto-map for project '{project_id}': {exc}"
                )
            logger.warning(
                "Auto-map pre-sync failed for project %s; falling back to existing mapping state: %s",
                project_id,
                exc,
            )

        if not dry_run:
            for mapping_id, mapping in list(_compat_mappings.items()):
                if str(mapping.get("project_id") or "") != project_id:
                    continue
                if reset_manual:
                    _compat_mappings.pop(mapping_id, None)
                    continue
                mapping["status"] = "auto"
                mapping["is_user_edited"] = False
                mapping["target_name"] = ""

        data = _compat_build_project_entity_mappings(
            project_id,
            save_store=not dry_run,
            target_connector=target_connector,
            selected_model_names=selected_model_names if dry_run else None,
            preferred_snapshot_id=preferred_snapshot_id,
        )
    else:
        preview_seed = config_yaml or "|".join(str(item) for item in selected_model_names)
        preview_project_id = f"preview-{uuid.uuid5(uuid.NAMESPACE_DNS, preview_seed or project_id or 'preview').hex[:12]}"
        preview_model = _compat_preview_model_from_payload(payload or {})
        data = build_entity_mappings(
            project_id=preview_project_id,
            model=preview_model,
            existing_mappings={},
            session_key=f"{preview_project_id}-mapping-session",
            target_connector=target_connector,
        )
        data = {
            "project_id": preview_project_id,
            "session_key": data.get("session_key"),
            "model_name": data.get("model_name"),
            "source_fields": data.get("source_fields", []),
            "target_fields": data.get("target_fields", []),
            "mappings": data.get("mappings", []),
            "collisions": data.get("collisions", []),
        }

    diagnostics = _compat_latest_identifier_diagnostics(project_id) if project_id else []
    _compat_apply_identifier_diagnostics_to_mappings(data.get("mappings", []), diagnostics)

    grouped_mappings = _compat_format_mapping_groups(data)
    entity_mappings = _compat_serialize_auto_map_entity_mappings(
        data.get("mappings", []),
    )
    collision_names = [
        str(mapping.get("source_name") or mapping.get("target_name") or "").strip()
        for mapping in entity_mappings
        if mapping.get("collision_detected")
    ]
    collision_names = [name for name in collision_names if name]

    # ── Schema conflict detection (dimension gaps + compatibility score) ──────
    schema_conflicts: list = []
    # Pull missing_dims surfaced by the DDL builder during the dry-run sync.
    _missing_dims: dict = {}
    if isinstance(sync_result, dict):
        for _row in (sync_result.get("results") or []):
            if isinstance(_row, dict):
                _md = _row.get("missing_dims") or {}
                for _ds, _cols in (_md.items() if isinstance(_md, dict) else []):
                    _missing_dims.setdefault(_ds, [])
                    for _c in (_cols if isinstance(_cols, list) else []):
                        if _c not in _missing_dims[_ds]:
                            _missing_dims[_ds].append(_c)
    # Also check model columns against known synthetic date-intelligence patterns
    # (these are flagged regardless of whether a Snowflake connection was made).
    _LIVE_ONLY_COLS = {"MONTHINDEX", "YEARINDEX", "QUARTERINDEX", "WEEKINDEX",
                       "MAX_MONTHINDEX", "MAX_YEARINDEX", "MAX_DATE"}
    _model_cols = data.get("source_fields") or []
    for _field in _model_cols:
        _col_name = str(_field.get("name") or _field.get("unique_name") or "").upper() if isinstance(_field, dict) else str(_field).upper()
        _dataset = str(_field.get("dataset") or _field.get("table") or "") if isinstance(_field, dict) else ""
        if _col_name in _LIVE_ONLY_COLS:
            _missing_dims.setdefault(_dataset or _col_name, [])
            if _col_name not in _missing_dims[_dataset or _col_name]:
                _missing_dims[_dataset or _col_name].append(_col_name)

    for _ds_name, _cols in _missing_dims.items():
        for _col in _cols:
            schema_conflicts.append({
                "conflict_id": f"dim_missing_{_ds_name}_{_col}",
                "type": "dimension_missing",
                "severity": "warning",
                "model_name": _ds_name,
                "column_name": _col,
                "description": (
                    f"Column '{_col}' exists in the source model (dataset '{_ds_name}') "
                    f"but was not found in the Snowflake physical table. "
                    f"It has been excluded from the semantic view DIMENSIONS clause."
                ),
                "resolution_options": [
                    {
                        "id": "skip",
                        "label": "Skip — exclude from Snowflake view",
                        "description": "Leave as-is. The column will be absent from Snowflake queries.",
                    },
                    {
                        "id": "auto_add",
                        "label": "Auto-add to Snowflake table",
                        "description": (
                            f"Run ALTER TABLE ADD COLUMN '{_col}' VARCHAR on the Snowflake "
                            f"physical table and re-sync so the column is included in DIMENSIONS."
                        ),
                    },
                ],
            })

    # ── Compatibility score ──────────────────────────────────────────────────────
    # Only compute a meaningful score when the dry-run actually ran against a
    # real project (sync_result is populated). In preview/no-project mode there
    # is nothing to measure, so leave it null — the UI should not show a bar.
    compatibility_score: Optional[float] = None
    _did_run = bool(sync_result and isinstance(sync_result, dict) and sync_result.get("results"))
    if _did_run:
        # Start from 100 and deduct points for each real problem signal.
        score_deductions: float = 0.0

        # Signal 1: Failed / conflicted model syncs (each -20, max -60)
        _model_results = sync_result.get("results") or []
        _failed_models = sum(
            1 for r in _model_results
            if isinstance(r, dict) and r.get("status") not in ("success",)
        )
        score_deductions += min(60.0, _failed_models * 20.0)

        # Signal 2: Schema conflicts — each warning -5, each critical -20
        for _sc in schema_conflicts:
            _sev = _sc.get("severity", "warning")
            score_deductions += 20.0 if _sev == "critical" else 5.0

        # Signal 3: Field mapping collisions — each -2 (capped at -20)
        _collision_count = sum(
            1 for m in entity_mappings
            if m.get("collision_detected") or m.get("status") in ("collision", "unmapped")
        )
        score_deductions += min(20.0, _collision_count * 2.0)

        # Signal 4: Untranslated measures (sync_disabled flag or status=failed on metrics)
        _untranslated = sum(
            1 for m in entity_mappings
            if m.get("entity_kind") in ("measure", "metric")
            and (m.get("sync_disabled") or m.get("translation_failed"))
        )
        score_deductions += min(15.0, _untranslated * 5.0)

        compatibility_score = round(max(0.0, 100.0 - score_deductions), 1)

    return {
        "project_id": data.get("project_id") or project_id,
        "source_fields": data.get("source_fields", []),
        "target_fields": data.get("target_fields", []),
        "mappings": grouped_mappings,
        "entity_mappings": entity_mappings,
        "collisions": collision_names,
        "diagnostics": diagnostics,
        "schema_conflicts": schema_conflicts,
        "compatibility_score": compatibility_score,
        "status": "ok",
    }


async def update_mapping_compat(mapping_id: str, payload: dict):
    import logging
    logger = logging.getLogger(__name__)

    _compat_ensure_loaded()
    incoming = payload or {}
    existing = _compat_mappings.get(mapping_id, {"id": mapping_id, "project_id": incoming.get("project_id")})

    # Backfill canonical identity fields for clients that only send target_name.
    if not str(existing.get("source_path") or "").strip():
        project_id = str(incoming.get("project_id") or existing.get("project_id") or "").strip()
        if project_id:
            try:
                built = _compat_build_project_entity_mappings(project_id, save_store=False)
                for candidate in built.get("mappings", []):
                    if str(candidate.get("id") or "") == mapping_id:
                        existing = {**candidate, **existing}
                        break
            except Exception as exc:
                logger.debug("Mapping identity backfill skipped for %s: %s", mapping_id, exc)

    existing.update(incoming)
    existing["id"] = mapping_id
    if "target_name" in incoming:
        project_id = str(existing.get("project_id") or incoming.get("project_id") or "").strip()
        existing["target_name"] = _compat_sanitize_target_name_for_project(
            project_id,
            str(incoming.get("target_name") or "").strip(),
        )
        existing["status"] = "manual"
        existing["is_user_edited"] = True
    existing["updated_at"] = _compat_now_iso()
    _compat_mappings[mapping_id] = existing
    _compat_save_store()
    return existing


async def delete_mappings_compat(project_id: Optional[str] = None):
    if project_id:
        for mapping_id in [
            k for k, v in _compat_mappings.items()
            if str(v.get("project_id") or "") == str(project_id)
        ]:
            _compat_mappings.pop(mapping_id, None)
    else:
        _compat_mappings.clear()
    _compat_save_store()
    return Response(status_code=204)
