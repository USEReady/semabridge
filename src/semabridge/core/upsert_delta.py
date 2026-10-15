"""UPSERT delta sync: compare a freshly extracted SML model with the last
deployed snapshot of the same project/model, and build the model to deploy.

Pure logic — no I/O. The engine loads the baseline snapshot (see
``ModelRepository.get_last_deployed_snapshot``), calls :func:`compute_delta`
and :func:`merge_upsert`, and skips the deploy when the delta has no
added/modified items.

Items are matched by normalized name (case-, underscore- and
space-insensitive), so ``Total Units`` / ``TOTAL_UNITS`` / ``total_units``
are the same item and never produce a duplicate.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from semabridge.sml.models import (
    SMLAttribute,
    SMLColumn,
    SMLDataset,
    SMLDimension,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)

# Report order and the SMLModel attribute that holds each kind.
DELTA_KINDS: Tuple[Tuple[str, str], ...] = (
    ("metrics", "metrics"),
    ("dimensions", "dimensions"),
    ("relationships", "relationships"),
    ("tables", "datasets"),
)

_ITEM_CLASSES = {
    "metrics": SMLMetric,
    "dimensions": SMLDimension,
    "relationships": SMLRelationship,
    "tables": SMLDataset,
}

# Upper bound on names listed per bucket in the one-line summary.
_SUMMARY_NAME_LIMIT = 5


def normalize_name(value: Any) -> str:
    """Case/underscore/space-insensitive identity key for an SML item."""
    return re.sub(r"[\s_]+", "", str(value or "")).casefold()


def normalize_model_name(value: Any) -> str:
    """Model identity key: alphanumerics only, so a PBIX file name
    ("Sales (EU)_v2") and its deployed form ("SALES_EU_V2") match."""
    return re.sub(r"[^0-9a-z]+", "", str(value or "").casefold())


def _ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _display_name(item: Any) -> str:
    return str(_get(item, "label") or _get(item, "unique_name") or "")


def _signature(kind: str, item: Any) -> Dict[str, Any]:
    """Definition fields that decide whether an item changed.

    Only source-defined fields take part. Derived/deploy-time fields
    (sql_expression, synonyms, confidence, advisory notes, ...) are left
    out so a re-extraction of an unchanged PBIX compares equal.
    """
    if kind == "metrics":
        return {
            "expression": _ws(_get(item, "expression")),
            "dataset": normalize_name(_get(item, "dataset")),
            "aggregation": _enum_text(_get(item, "aggregation")),
            "source_column": normalize_name(_get(item, "source_column")),
            "format_string": _get(item, "format_string") or "",
            "is_hidden": bool(_get(item, "is_hidden", False)),
        }
    if kind == "dimensions":
        attributes = sorted(
            (
                normalize_name(_get(a, "unique_name")),
                normalize_name(_get(a, "dataset")),
                normalize_name(_get(a, "dataset_column")),
                bool(_get(a, "is_hidden", False)),
            )
            for a in (_get(item, "attributes") or [])
        )
        return {
            "dataset": normalize_name(_get(item, "dataset")),
            "attributes": [list(a) for a in attributes],
            "is_hidden": bool(_get(item, "is_hidden", False)),
        }
    if kind == "relationships":
        return {
            "from_dataset": normalize_name(_get(item, "from_dataset")),
            "from_columns": [normalize_name(c) for c in (_get(item, "from_columns") or [])],
            "to_dataset": normalize_name(_get(item, "to_dataset")),
            "to_columns": [normalize_name(c) for c in (_get(item, "to_columns") or [])],
            "cardinality": _enum_text(_get(item, "cardinality")),
            "cross_filter": _enum_text(_get(item, "cross_filter")),
            "is_active": bool(_get(item, "is_active", True)),
        }
    # tables
    columns = sorted(
        (
            normalize_name(_get(c, "unique_name")),
            _enum_text(_get(c, "data_type")),
            bool(_get(c, "is_hidden", False)),
        )
        for c in (_get(item, "columns") or [])
    )
    return {
        "source_table": normalize_name(_get(item, "source_table")),
        "columns": [list(c) for c in columns],
        "is_hidden": bool(_get(item, "is_hidden", False)),
    }


def _index(items: Iterable[Any]) -> Dict[str, Any]:
    """Normalized name -> item. First occurrence wins (never duplicates)."""
    indexed: Dict[str, Any] = {}
    for item in items or []:
        key = normalize_name(_get(item, "unique_name"))
        if key and key not in indexed:
            indexed[key] = item
    return indexed


def _item_entry(kind: str, item: Any) -> Dict[str, Any]:
    """Name and owning table of one item (plus source DAX for metrics)."""
    if kind == "relationships":
        table = f"{_get(item, 'from_dataset')} -> {_get(item, 'to_dataset')}"
    elif kind == "tables":
        table = _get(item, "source_table") or _get(item, "unique_name")
    else:
        table = _get(item, "dataset")
    entry: Dict[str, Any] = {"name": _display_name(item), "table": str(table or "")}
    if kind == "metrics":
        entry["expression"] = str(_get(item, "expression") or "")
    return entry


def _modified_entry(kind: str, old: Any, new: Any, old_sig: Dict[str, Any], new_sig: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        **_item_entry(kind, new),
        "changed_fields": sorted(k for k in new_sig if new_sig.get(k) != old_sig.get(k)),
    }
    if kind == "metrics":
        entry["old_expression"] = str(_get(old, "expression") or "")
        entry["new_expression"] = str(_get(new, "expression") or "")
    elif kind == "tables":
        old_cols = {c[0] for c in old_sig["columns"]}
        new_cols = {c[0] for c in new_sig["columns"]}
        entry["columns_added"] = sorted(
            _get(c, "unique_name") for c in (_get(new, "columns") or [])
            if normalize_name(_get(c, "unique_name")) in new_cols - old_cols
        )
        entry["columns_removed"] = sorted(
            _get(c, "unique_name") for c in (_get(old, "columns") or [])
            if normalize_name(_get(c, "unique_name")) in old_cols - new_cols
        )
    elif kind == "relationships":
        entry["old_definition"] = _relationship_text(old)
        entry["new_definition"] = _relationship_text(new)
    return entry


def _relationship_text(rel: Any) -> str:
    return (
        f"{_get(rel, 'from_dataset')}[{', '.join(_get(rel, 'from_columns') or [])}] -> "
        f"{_get(rel, 'to_dataset')}[{', '.join(_get(rel, 'to_columns') or [])}] "
        f"({_enum_text(_get(rel, 'cardinality'))}, active={bool(_get(rel, 'is_active', True))})"
    )


def compute_delta(
    new_model: SMLModel,
    baseline: Optional[Dict[str, Any]],
    *,
    baseline_snapshot_id: Optional[str] = None,
    baseline_timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Per-kind added / modified / removed / unchanged between baseline and new.

    ``baseline`` is the SML dict of the last deployed snapshot, or None when
    this model was never deployed (everything is then "added").
    """
    kinds: Dict[str, Dict[str, Any]] = {}
    for kind, attr in DELTA_KINDS:
        new_items = _index(getattr(new_model, attr, []) or [])
        old_items = _index((baseline or {}).get(attr) or [])
        added: List[Dict[str, Any]] = []
        modified: List[Dict[str, Any]] = []
        unchanged = 0
        for key, new_item in new_items.items():
            old_item = old_items.get(key)
            if old_item is None:
                added.append(_item_entry(kind, new_item))
                continue
            old_sig, new_sig = _signature(kind, old_item), _signature(kind, new_item)
            if old_sig == new_sig:
                unchanged += 1
            else:
                modified.append(_modified_entry(kind, old_item, new_item, old_sig, new_sig))
        removed = [
            _item_entry(kind, old_item)
            for key, old_item in old_items.items()
            if key not in new_items
        ]
        kinds[kind] = {
            "added": added,
            "modified": modified,
            "removed": removed,
            "unchanged": unchanged,
        }

    added_total = sum(len(k["added"]) for k in kinds.values())
    modified_total = sum(len(k["modified"]) for k in kinds.values())
    delta = {
        "sync_mode": "upsert",
        "first_deployment": baseline is None,
        "baseline_snapshot_id": baseline_snapshot_id,
        "baseline_timestamp": baseline_timestamp,
        # Removed items are kept in the target (upsert), so only added or
        # modified items change what gets deployed.
        "has_changes": bool(added_total or modified_total),
        "totals": {
            "added": added_total,
            "modified": modified_total,
            "removed": sum(len(k["removed"]) for k in kinds.values()),
            "unchanged": sum(k["unchanged"] for k in kinds.values()),
        },
        "kinds": kinds,
    }
    delta["summary_text"] = format_delta_summary(delta)
    return delta


def _names(entries: List[Dict[str, Any]]) -> str:
    names = [str(e.get("name") or "") for e in entries]
    shown = ", ".join(names[:_SUMMARY_NAME_LIMIT])
    if len(names) > _SUMMARY_NAME_LIMIT:
        shown += f" (+{len(names) - _SUMMARY_NAME_LIMIT} more)"
    return shown


def format_delta_summary(delta: Dict[str, Any]) -> str:
    """One line, e.g. "1 added: Avg Revenue per Unit · 1 modified: Total Units YTD · 57 unchanged"."""
    if delta.get("first_deployment"):
        return "First deployment: no previous deployed snapshot, full model deployed"
    all_added: List[Dict[str, Any]] = []
    all_modified: List[Dict[str, Any]] = []
    all_removed: List[Dict[str, Any]] = []
    for kind, _ in DELTA_KINDS:
        bucket = (delta.get("kinds") or {}).get(kind) or {}
        all_added += bucket.get("added") or []
        all_modified += bucket.get("modified") or []
        all_removed += bucket.get("removed") or []
    parts: List[str] = []
    if all_added:
        parts.append(f"{len(all_added)} added: {_names(all_added)}")
    if all_modified:
        parts.append(f"{len(all_modified)} modified: {_names(all_modified)}")
    if all_removed:
        parts.append(f"{len(all_removed)} removed from source (kept in target): {_names(all_removed)}")
    parts.append(f"{(delta.get('totals') or {}).get('unchanged', 0)} unchanged")
    line = " · ".join(parts)
    if not delta.get("has_changes"):
        line = f"No changes · {line}"
    return line


def merge_upsert(new_model: SMLModel, baseline: Optional[Dict[str, Any]]) -> SMLModel:
    """Model to deploy: the new source model plus items only the target has.

    Source definitions win for matched items; baseline-only items (and
    baseline-only columns/attributes of matched tables/dimensions) are kept.
    """
    merged = new_model.model_copy(deep=True)
    if not baseline:
        return merged

    for kind, attr in DELTA_KINDS:
        cls = _ITEM_CLASSES[kind]
        current: List[Any] = getattr(merged, attr)
        current_keys = {normalize_name(_get(i, "unique_name")) for i in current}
        old_items = _index(baseline.get(attr) or [])
        for key, old_item in old_items.items():
            if key in current_keys:
                continue
            current.append(cls.model_validate(old_item))
            current_keys.add(key)

        if kind in ("tables", "dimensions"):
            child_attr, child_cls = ("columns", SMLColumn) if kind == "tables" else ("attributes", SMLAttribute)
            for item in current:
                old_item = old_items.get(normalize_name(item.unique_name))
                if old_item is None:
                    continue
                children = getattr(item, child_attr)
                child_keys = {normalize_name(c.unique_name) for c in children}
                for old_child in (old_item.get(child_attr) or []):
                    child_key = normalize_name(_get(old_child, "unique_name"))
                    if child_key in child_keys:
                        continue
                    children.append(child_cls.model_validate(old_child))
                    child_keys.add(child_key)
    return merged


def parse_baseline_blob(blob: Any, snapshot_id: str) -> Dict[str, Any]:
    """Validate a stored snapshot blob as SML; raise (never fall back) if unreadable."""
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except Exception as exc:
            raise ValueError(f"Snapshot {snapshot_id} SML payload is not readable: {exc}") from exc
    if not isinstance(blob, dict) or not blob:
        raise ValueError(f"Snapshot {snapshot_id} has no SML payload")
    try:
        SMLModel.model_validate(blob)
    except Exception as exc:
        raise ValueError(f"Snapshot {snapshot_id} SML payload is invalid: {exc}") from exc
    return blob


_DDL_METRIC_LINE = re.compile(r'^\s*"?(\w+)"?\s*\.\s*"?(\w+)"?\s+as\s+(.*?)\s*,?\s*$', re.IGNORECASE)


def _deployed_metrics(ddl: Optional[str]) -> Dict[str, Tuple[str, str, str]]:
    """normalized metric name -> (entity, name, expression) from a semantic-view DDL."""
    found: Dict[str, Tuple[str, str, str]] = {}
    in_metrics = False
    for line in str(ddl or "").splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("metrics"):
            in_metrics = True
            continue
        if in_metrics and re.match(r"^\)\s*;?\s*$|^(comment|with|dimensions|facts)", stripped):
            in_metrics = False
        if not in_metrics:
            continue
        match = _DDL_METRIC_LINE.match(line)
        if match:
            entity, name, expression = match.groups()
            found.setdefault(normalize_name(name), (entity, name, expression))
    return found


def annotate_delta(
    delta: Dict[str, Any],
    model: Optional[SMLModel],
    dropped_entities: List[Dict[str, Any]],
    *,
    deployed: bool,
    deployed_ddl: Optional[str] = None,
    view: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Add per-item status (and, for metrics, the SQL expression and how to
    query it) to a delta, once the run knows what it deployed.

    status: "deployed" / "will be deployed" (dry run), "placeholder" (deployed
    as CAST(NULL AS DOUBLE), reason from the drop ledger), "not deployed",
    or "kept in Snowflake" for items removed from the source.
    """
    drops: Dict[str, Dict[str, Any]] = {}
    for record in dropped_entities or []:
        if isinstance(record, dict) and not record.get("by_design"):
            drops.setdefault(normalize_name(record.get("entity_name")), record)
    ddl_metrics = _deployed_metrics(deployed_ddl)
    sml_metrics = {normalize_name(m.unique_name): m for m in getattr(model, "metrics", []) or []}

    for kind, _ in DELTA_KINDS:
        bucket = (delta.get("kinds") or {}).get(kind) or {}
        for item in bucket.get("removed") or []:
            item["status"] = "kept in Snowflake"
        for item in (bucket.get("added") or []) + (bucket.get("modified") or []):
            key = normalize_name(item.get("name"))
            record = drops.get(key)
            reason = str((record or {}).get("reason") or "")
            if record and reason.lower().startswith("placeholder:"):
                item["status"] = "placeholder"
                item["status_reason"] = reason
            elif record:
                item["status"] = "not deployed"
                item["status_reason"] = reason
            else:
                item["status"] = "deployed" if deployed else "will be deployed"
            if kind != "metrics":
                continue
            deployed_metric = ddl_metrics.get(key)
            if deployed_metric:
                entity, name, expression = deployed_metric
                item["sql_expression"] = expression
                if deployed and item["status"] == "deployed":
                    item["query"] = {"entity": entity, "metric": name}
            elif key in sml_metrics and getattr(sml_metrics[key], "sql_expression", None):
                item["sql_expression"] = sml_metrics[key].sql_expression
    if view:
        delta["view"] = view
    return delta
