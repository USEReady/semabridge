"""Groups the dry-run preview's existing drop/collision signals into a
severity-tiered "needs attention" summary.

Purely additive, read-only over data that already exists: DropLedger records
already carry a `severity` (see core/drop_ledger.py's `_classify_severity`),
computed centrally and reaching this module for free via
`DropLedger.to_json()`. project_mapping_engine.py's field-level validation
codes (NAME_COLLISION, EMPTY_TARGET, ...) have no such field, so this module
adds a small parallel lookup for those instead of touching that detection
logic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# project_mapping_engine.py's validation_code values that represent a real
# problem. "OK" (the default, no issue) is deliberately absent -- anything
# not in this map is treated as informational (see _validation_severity).
_VALIDATION_CODE_SEVERITY: Dict[str, str] = {
    "NAME_COLLISION": "critical",
    "EMPTY_TARGET": "critical",
    "RESERVED_KEYWORD": "warning",
    "UNSUPPORTED_CHARACTERS": "warning",
}


def _validation_severity(validation_code: str) -> Optional[str]:
    return _VALIDATION_CODE_SEVERITY.get(str(validation_code or "").upper())


def build_needs_attention_summary(
    dropped_entities: List[Dict[str, Any]],
    filtered_mappings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the `needs_attention` block for a dry-run response.

    Args:
        dropped_entities: DropLedger.to_json() output (each item already has
            a `severity` key) -- the same list already returned verbatim as
            the response's existing `dropped_entities` key.
        filtered_mappings: the field-level rows already returned as
            `entity_mappings` -- each may carry a `validation_code`.

    Returns a dict with `critical`/`warning`/`info` counts and a flat
    `items` list carrying enough context to render without a second lookup.
    Never raises -- a malformed/missing field in either input is skipped
    for that one item rather than failing the whole summary.
    """
    items: List[Dict[str, Any]] = []

    for record in dropped_entities or []:
        if not isinstance(record, dict):
            continue
        severity = str(record.get("severity") or "warning")
        items.append({
            "source": "dropped_entity",
            "severity": severity,
            "entity_kind": record.get("entity_kind"),
            "entity_name": record.get("entity_name"),
            "dataset": record.get("dataset"),
            "stage": record.get("stage"),
            "reason": record.get("reason"),
            "detail": record.get("detail"),
            "by_design": bool(record.get("by_design")),
        })

    for row in filtered_mappings or []:
        if not isinstance(row, dict):
            continue
        validation_code = row.get("validation_code")
        severity = _validation_severity(validation_code)
        if severity is None:
            continue  # "OK" or an unrecognized code with no real issue
        items.append({
            "source": "collision",
            "severity": severity,
            "entity_kind": row.get("entity_kind"),
            "entity_name": row.get("source_name") or row.get("target_name"),
            "dataset": None,
            "validation_code": validation_code,
            "reason": row.get("validation_message") or validation_code,
        })

    counts = {"critical": 0, "warning": 0, "info": 0}
    for item in items:
        sev = item.get("severity")
        if sev in counts:
            counts[sev] += 1

    return {**counts, "items": items}
