from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from semabridge.utils.identifiers import IdentifierSanitizer, SNOWFLAKE_RESERVED_WORDS


_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_MULTI_UNDERSCORE = re.compile(r"_+")
_SNOWFLAKE_SANITIZER = IdentifierSanitizer(suppress_reserved=True)

# Regex used to normalise source names for semantic identity comparison.
# Strips all non-alphanumeric chars and lowercases so that:
#   "Date Today", "date today", "date_today", "DATE_TODAY" → "datetoday"
# This means two entities whose source names reduce to the same token are
# treated as the *same concept*, not a name collision.
_SEMANTIC_NORM = re.compile(r"[^a-z0-9]")


def _semantic_name(name: str) -> str:
    """Return a normalised token used only for semantic identity comparison.

    All of: "Date Today", "date today", "date_today", "DATE TODAY"
    reduce to the same string ("datetoday") so they are never flagged as
    collisions against each other.  A real collision only occurs when two
    *different* source names happen to produce the same sanitised target
    name (e.g. "rev_total" and "revenue_total" both becoming REVENUE_TOTAL).
    """
    return _SEMANTIC_NORM.sub("", str(name or "").lower().strip())
_METRIC_DAX_TABLE_REF = re.compile(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*\[")
_METRIC_SQL_QUOTED_REF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*\"")
_METRIC_SQL_PLAIN_REF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*[A-Za-z_][A-Za-z0-9_]*")


def sanitize_identifier(value: str, *, max_length: int = 120) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "UNNAMED"

    sanitized = _NON_ALNUM.sub("_", raw.upper())
    sanitized = _MULTI_UNDERSCORE.sub("_", sanitized).strip("_")
    if not sanitized:
        sanitized = "UNNAMED"
    if sanitized[0].isdigit():
        sanitized = f"N_{sanitized}"
    return sanitized[:max_length].rstrip("_") or "UNNAMED"


def deterministic_hash_suffix(*parts: str, size: int = 4) -> str:
    joined = "::".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest().upper()
    return digest[: max(1, size)]


def apply_collision_suffix(
    sanitized_name: str,
    *,
    fingerprint: str,
    max_length: int = 120,
    size: int = 4,
) -> Tuple[str, str]:
    suffix = deterministic_hash_suffix(fingerprint, size=size)
    stem_max = max_length - size - 1
    stem = sanitized_name[:stem_max].rstrip("_") or sanitized_name[:stem_max] or "UNNAMED"
    return f"{stem}_{suffix}", suffix


def _iter_datasets(model: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    rows = model.get("datasets") if isinstance(model.get("datasets"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _iter_metrics(model: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    rows = model.get("metrics") if isinstance(model.get("metrics"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _dataset_name(dataset: Dict[str, Any]) -> str:
    return str(
        dataset.get("unique_name")
        or dataset.get("name")
        or dataset.get("label")
        or dataset.get("display_name")
        or dataset.get("source_table")
        or dataset.get("id")
        or dataset.get("table_id")
        or ""
    ).strip()


def _column_name(column: Dict[str, Any]) -> str:
    return str(
        column.get("unique_name")
        or column.get("name")
        or column.get("label")
        or column.get("display_name")
        or column.get("source_column")
        or column.get("id")
        or column.get("field_id")
        or column.get("column_id")
        or ""
    ).strip()


def _metric_name(metric: Dict[str, Any]) -> str:
    return str(
        metric.get("unique_name")
        or metric.get("name")
        or metric.get("label")
        or metric.get("display_name")
        or metric.get("id")
        or metric.get("field_id")
        or metric.get("metric_id")
        or ""
    ).strip()


def _extract_metric_source_tables(
    metric: Dict[str, Any],
    available_dataset_names: Dict[str, str],
) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()

    def _add_candidate(raw_value: Any) -> None:
        value = str(raw_value or "").strip()
        if not value:
            return
        key = value.lower()
        canonical = available_dataset_names.get(key)
        if not canonical:
            return
        if canonical.lower() in seen:
            return
        seen.add(canonical.lower())
        ordered.append(canonical)

    for hint_key in ("dataset", "dataset_name", "source_table", "table_name", "table"):
        _add_candidate(metric.get(hint_key))

    expression = str(metric.get("expression") or "")
    if expression:
        for match in _METRIC_DAX_TABLE_REF.findall(expression):
            _add_candidate(match[0] or match[1])
        for table_name in _METRIC_SQL_QUOTED_REF.findall(expression):
            _add_candidate(table_name)
        for table_name in _METRIC_SQL_PLAIN_REF.findall(expression):
            _add_candidate(table_name)

    return ordered


def extract_model_entities(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    model_name = str(model.get("unique_name") or model.get("name") or model.get("label") or "model").strip()
    dataset_lookup: Dict[str, str] = {}

    for dataset_index, dataset in enumerate(_iter_datasets(model), start=1):
        dataset_name = _dataset_name(dataset) or f"dataset_{dataset_index}"
        dataset_lookup[dataset_name.lower()] = dataset_name
        dataset_path = f"datasets.{dataset_name}"
        entities.append({
            "entity_kind": "table",
            "model_name": model_name,
            "source_name": dataset_name,
            "source_path": dataset_path,
            "parent_source_path": None,
            "data_type": None,
        })

        columns = dataset.get("columns") if isinstance(dataset.get("columns"), list) else []
        for column_index, column in enumerate(columns, start=1):
            if not isinstance(column, dict):
                continue
            column_name = _column_name(column) or f"column_{column_index}"
            entities.append({
                "entity_kind": "column",
                "model_name": model_name,
                "source_name": column_name,
                "source_path": f"{dataset_path}.columns.{column_name}",
                "parent_source_path": dataset_path,
                "data_type": column.get("data_type"),
            })

    for metric_index, metric in enumerate(_iter_metrics(model), start=1):
        metric_name = _metric_name(metric) or f"metric_{metric_index}"
        measure_tables = _extract_metric_source_tables(metric, dataset_lookup)
        metric_parent = f"datasets.{measure_tables[0]}" if len(measure_tables) == 1 else None
        metric_expression = str(metric.get("expression") or "").strip()
        entities.append({
            "entity_kind": "metric",
            "model_name": model_name,
            "source_name": metric_name,
            "source_path": f"metrics.{metric_name}",
            "parent_source_path": metric_parent,
            "data_type": metric.get("data_type"),
            "measure_source_tables": measure_tables,
            "source_expression": metric_expression,
        })

    return entities


def _scope_key(entity: Dict[str, Any]) -> str:
    kind = str(entity.get("entity_kind") or "").lower()
    if kind == "column":
        return f"column::{entity.get('parent_source_path') or 'root'}"
    if kind == "table":
        return f"table::{entity.get('model_name') or 'model'}"
    if kind == "metric":
        return f"metric::{entity.get('model_name') or 'model'}"
    return f"entity::{kind}::{entity.get('model_name') or 'model'}"


def _normalize_connector_name(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _default_target_identifier(source_name: str, target_connector: Optional[str]) -> str:
    connector = _normalize_connector_name(target_connector)
    if connector == "snowflake":
        return _SNOWFLAKE_SANITIZER.sanitize_alias(source_name)
    return sanitize_identifier(source_name)


def _validate_target_name(
    *,
    target_name: str,
    source_name: str,
    collision_detected: bool,
    target_connector: Optional[str],
) -> Dict[str, str]:
    connector = _normalize_connector_name(target_connector)
    resolved_target = str(target_name or "").strip()
    fallback = _default_target_identifier(source_name, target_connector)

    if collision_detected:
        return {
            "validation_status": "collision",
            "validation_code": "NAME_COLLISION",
            "validation_message": "Name collision resolved with deterministic suffix.",
            "suggested_target_name": resolved_target or fallback,
        }

    if not resolved_target:
        return {
            "validation_status": "invalid",
            "validation_code": "EMPTY_TARGET",
            "validation_message": "Target name cannot be empty.",
            "suggested_target_name": fallback,
        }

    if connector == "snowflake":
        upper_name = resolved_target.upper()
        if upper_name.lower() in SNOWFLAKE_RESERVED_WORDS:
            return {
                "validation_status": "invalid",
                "validation_code": "RESERVED_KEYWORD",
                "validation_message": "Target name is a Snowflake reserved keyword.",
                "suggested_target_name": _SNOWFLAKE_SANITIZER.sanitize_alias(resolved_target),
            }

        sanitized = _SNOWFLAKE_SANITIZER.sanitize_alias(resolved_target)
        if sanitized != upper_name:
            return {
                "validation_status": "invalid",
                "validation_code": "UNSUPPORTED_CHARACTERS",
                "validation_message": "Target name has unsupported characters for Snowflake.",
                "suggested_target_name": sanitized,
            }

    return {
        "validation_status": "valid",
        "validation_code": "OK",
        "validation_message": "Identifier is valid.",
        "suggested_target_name": resolved_target,
    }


def build_entity_mappings(
    *,
    project_id: str,
    model: Dict[str, Any],
    existing_mappings: Optional[Dict[str, Dict[str, Any]]] = None,
    session_key: Optional[str] = None,
    target_connector: Optional[str] = None,
) -> Dict[str, Any]:
    existing = existing_mappings or {}
    normalized_target_connector = _normalize_connector_name(target_connector)
    entities = extract_model_entities(model)
    session = session_key or f"map-session-{uuid.uuid4().hex[:12]}"
    # claimed_names[scope][sanitized_key] = {"source_path": ..., "source_name": ...}
    # Stores both the path and the original source name so we can distinguish
    # a true collision (different concepts → same sanitised name) from an
    # apparent collision (same concept, different platform naming conventions).
    claimed_names: Dict[str, Dict[str, Dict[str, str]]] = {}
    generated: List[Dict[str, Any]] = []
    collisions: List[Dict[str, Any]] = []

    for entity in entities:
        source_path = str(entity.get("source_path") or "").strip()
        if not source_path:
            continue

        mapping_id = str(existing.get(source_path, {}).get("id") or f"{project_id}-{uuid.uuid5(uuid.NAMESPACE_URL, f'{project_id}:{source_path}').hex[:16]}")
        source_name = str(entity.get("source_name") or "").strip() or "unnamed"
        sanitized = _default_target_identifier(source_name, normalized_target_connector)
        scope = _scope_key(entity)
        claimed_names.setdefault(scope, {})
        collision_key = sanitized
        prior_claim = claimed_names[scope].get(collision_key)
        existing_mapping = existing.get(source_path, {})
        is_manual = bool(existing_mapping.get("is_user_edited"))
        preferred_target_name = str(existing_mapping.get("target_name") or "").strip()

        collision_detected = False
        hash_suffix = ""
        target_name = preferred_target_name or sanitized

        if not preferred_target_name:
            if prior_claim and prior_claim["source_path"] != source_path:
                # A prior entity already claimed this sanitised name.
                # Only treat it as a REAL collision when the two source names
                # are semantically different concepts.
                #
                # Examples that are NOT collisions (same concept, different conventions):
                #   Power BI "Date Today"  vs Snowflake "date_today"
                #   Power BI "Revenue_YTD" vs Snowflake "revenue_ytd"
                #
                # Examples that ARE real collisions (different concepts, same sanitised name):
                #   "rev_total" vs "revenue_total"  → both → REVENUE_TOTAL
                #   "SalesAmt"  vs "Sales_Amt"      → both → SALES_AMT (ambiguous abbreviation)
                prior_semantic = _semantic_name(prior_claim["source_name"])
                current_semantic = _semantic_name(source_name)
                if prior_semantic != current_semantic:
                    collision_detected = True
                    entity_seed = str(entity.get("parent_source_path") or entity.get("model_name") or "").strip()
                    field_seed = source_name
                    target_name, hash_suffix = apply_collision_suffix(
                        sanitized,
                        fingerprint=f"{entity_seed}::{field_seed}",
                    )
                    collisions.append({
                        "scope": scope,
                        "sanitized_name": sanitized,
                        "first_source_path": prior_claim["source_path"],
                        "first_source_name": prior_claim["source_name"],
                        "second_source_path": source_path,
                        "second_source_name": source_name,
                        "resolved_target_name": target_name,
                    })
                # else: same concept under different naming conventions — not a collision,
                # keep the existing claimed slot and the same sanitised target name.
            claimed_names[scope][collision_key] = {"source_path": source_path, "source_name": source_name}
        else:
            claimed_names[scope][target_name] = {"source_path": source_path, "source_name": source_name}

        validation = _validate_target_name(
            target_name=target_name,
            source_name=source_name,
            collision_detected=collision_detected,
            target_connector=normalized_target_connector,
        )

        generated.append({
            "id": mapping_id,
            "project_id": project_id,
            "session_key": session,
            "model_name": entity.get("model_name") or "model",
            "entity_kind": entity.get("entity_kind"),
            "mapping_scope": scope,
            "source_name": source_name,
            "source_path": source_path,
            "parent_source_path": entity.get("parent_source_path"),
            "source_data_type": entity.get("data_type"),
            "measure_source_tables": list(entity.get("measure_source_tables") or []),
            "source_expression": str(entity.get("source_expression") or ""),
            "sanitized_name": sanitized,
            "target_name": target_name,
            "target_path": source_path,
            "target_parent_path": entity.get("parent_source_path"),
            "target_data_type": entity.get("data_type"),
            "status": "manual" if is_manual else "auto",
            "collision_detected": collision_detected,
            "collision_group": f"{scope}:{sanitized}" if collision_detected else "",
            "hash_suffix": hash_suffix,
            "is_user_edited": is_manual,
            "is_active": True,
            "validation_status": validation["validation_status"],
            "validation_code": validation["validation_code"],
            "validation_message": validation["validation_message"],
            "suggested_target_name": validation["suggested_target_name"],
        })

    return {
        "session_key": session,
        "model_name": str(model.get("unique_name") or model.get("name") or model.get("label") or "model"),
        "mappings": generated,
        "collisions": collisions,
        "source_fields": [
            {
                "name": row["source_name"],
                "type": row["entity_kind"],
                "path": row["source_path"],
                "parent_path": row["parent_source_path"],
            }
            for row in generated
        ],
        "target_fields": [
            {
                "name": row["target_name"],
                "type": row["entity_kind"],
                "path": row["target_path"],
                "parent_path": row["target_parent_path"],
            }
            for row in generated
        ],
    }