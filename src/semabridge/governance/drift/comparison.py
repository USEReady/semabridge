"""Common Comparison Engine for Drift Monitoring.

Compares two normalized ViewDefinition objects (baseline vs. live) and produces
structured DriftFinding results in the four canonical categories:
- ADDITION
- DELETION
- EXPRESSION_DRIFT
- DESCRIPTION_DRIFT

Eliminates false positives by leveraging dialect-aware expression normalization
and excluding objects marked as not deployed during deploy remediation.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Set, Tuple

from semabridge.governance.drift.models import (
    DriftCategory,
    DriftFinding,
    ObjectDef,
    ObjectKind,
    ParserOrBaselineMismatchError,
    SourcePathMismatchError,
    ViewDefinition,
)
from semabridge.governance.drift.normalize import (
    is_placeholder_expression,
    normalize_description,
    normalize_expression,
    normalize_expression_fallback,
)
from semabridge.governance.drift.description_diff import classify_and_diff_description

logger = logging.getLogger("semabridge.governance.drift.comparison")


def _normalize_join_expression(expr: str) -> str:
    """Normalize join conditions or REFERENCES clauses into order-independent canonical form."""
    if not expr:
        return ""
    # Snowflake REFERENCES: FROM_TABLE (c1, c2) REFERENCES TO_TABLE (tc1, tc2) [extra conditions]
    ref_match = re.search(r'([A-Za-z0-9_]+)\s*\((.*?)\)\s*REFERENCES\s*([A-Za-z0-9_]+)\s*\((.*?)\)(.*)', expr, re.IGNORECASE | re.DOTALL)
    if ref_match:
        from_tbl = ref_match.group(1).upper()
        from_cols = [c.strip().strip('"').upper() for c in ref_match.group(2).split(",") if c.strip()]
        to_tbl = ref_match.group(3).upper()
        to_cols = [c.strip().strip('"').upper() for c in ref_match.group(4).split(",") if c.strip()]
        extra = re.sub(r'\s+', ' ', ref_match.group(5).strip()).upper()
        pairs = sorted(zip(from_cols, to_cols))
        sorted_f_cols = ", ".join(f[0] for f in pairs)
        sorted_t_cols = ", ".join(f[1] for f in pairs)
        res = f"{from_tbl} ({sorted_f_cols}) REFERENCES {to_tbl} ({sorted_t_cols})"
        if extra:
            res = f"{res} {extra}"
        return res

    # SQL / YAML equality join: tbl1.c1 = tbl2.c2 AND ...
    eq_matches = re.findall(r'([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)', expr)
    if eq_matches:
        pairs = []
        for t1, c1, t2, c2 in eq_matches:
            if t1.upper() <= t2.upper():
                pairs.append((f"{t1.upper()}.{c1.upper()}", f"{t2.upper()}.{c2.upper()}"))
            else:
                pairs.append((f"{t2.upper()}.{c2.upper()}", f"{t1.upper()}.{c1.upper()}"))
        pairs.sort()
        return " AND ".join(f"{p[0]} = {p[1]}" for p in pairs)

    return re.sub(r'\s+', ' ', expr).strip().upper()


# Stable Relationship Match Key:
# In various semantic formats (Snowflake Semantic Views, Databricks Unity Catalog Metric Views),
# a join or relationship may have an arbitrary name, an auto-generated name, or no name at all.
# To ensure renames do not trigger false DELETION + ADDITION drift, the canonical identity of a
# relationship is defined strictly by its participating tables and join column pairs (order-independent):
#   f"{FROM_TABLE}->{TO_TABLE}::{JOIN_COLUMN_PAIRS}"
def _build_relationship_match_key(obj: ObjectDef) -> str:
    expr = (obj.expression or "").strip()
    props = getattr(obj, "properties", {}) or {}
    from_tbl = props.get("from_table", "").upper()
    to_tbl = props.get("to_table", "").upper()

    ref_match = re.search(r'([A-Za-z0-9_]+)\s*\((.*?)\)\s*REFERENCES\s*([A-Za-z0-9_]+)\s*\((.*?)\)(.*)', expr, re.IGNORECASE | re.DOTALL)
    if ref_match:
        from_tbl = ref_match.group(1).upper()
        from_cols = [c.strip().strip('"').upper() for c in ref_match.group(2).split(",") if c.strip()]
        to_tbl = ref_match.group(3).upper()
        to_cols = [c.strip().strip('"').upper() for c in ref_match.group(4).split(",") if c.strip()]
        extra = re.sub(r'\s+', ' ', ref_match.group(5).strip()).upper()
        pairs = sorted(zip(from_cols, to_cols))
        pair_str = ",".join(f"{c1}={c2}" for c1, c2 in pairs)
        if extra:
            pair_str += f"::{extra}"
        return f"{from_tbl}->{to_tbl}::{pair_str}"

    eq_matches = re.findall(r'([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)', expr)
    if eq_matches:
        pairs = []
        tables = set()
        for t1, c1, t2, c2 in eq_matches:
            tables.add(t1.upper())
            tables.add(t2.upper())
            if t1.upper() <= t2.upper():
                pairs.append(f"{c1.upper()}={c2.upper()}")
            else:
                pairs.append(f"{c2.upper()}={c1.upper()}")
        sorted_tables = sorted(tables)
        f_tbl = sorted_tables[0] if sorted_tables else from_tbl or "UNKNOWN"
        t_tbl = sorted_tables[1] if len(sorted_tables) > 1 else to_tbl or "UNKNOWN"
        pairs.sort()
        return f"{f_tbl}->{t_tbl}::{','.join(pairs)}"

    clean_name = str(obj.name or "").strip().upper()
    if from_tbl and to_tbl:
        return f"{from_tbl}->{to_tbl}::{clean_name}"
    return clean_name


def _obj_key(obj: ObjectDef) -> Tuple[str, str, str]:
    """Formulate canonical matching key: (kind, table, name)."""
    kind = str(obj.kind.value if hasattr(obj.kind, "value") else (obj.kind or "")).upper().strip()
    table = str(obj.table or "").upper().strip()
    if kind == "RELATIONSHIP":
        clean_name = _build_relationship_match_key(obj)
    else:
        clean_name = str(obj.name or "").strip().upper()
    return (kind, table, clean_name)


def compare_views(
    baseline: ViewDefinition,
    live: ViewDefinition,
) -> List[DriftFinding]:
    """Compare baseline and live view definitions and return all detected drift findings.

    Args:
        baseline: The canonical baseline ViewDefinition recorded at deploy.
        live: The current live ViewDefinition introspected from the warehouse.

    Returns:
        List of DriftFinding objects.
    """
    platform = (live.platform or baseline.platform or "").strip().upper()
    findings: List[DriftFinding] = []

    # 0. Source Path Consistency Check
    if baseline.source_path and live.source_path:
        b_sp = baseline.source_path.lower().strip()
        l_sp = live.source_path.lower().strip()
        if b_sp != "unknown" and l_sp != "unknown" and b_sp != l_sp:
            logger.warning(
                "Source path mismatch for %s: baseline used %s, live used %s",
                live.view_name or baseline.view_name, b_sp, l_sp,
            )
            raise SourcePathMismatchError(
                f"not checked: definition read in a different form (baseline: {b_sp}, live: {l_sp})"
            )

    # Map baseline objects by key (deterministic: first wins, duplicates logged)
    baseline_map: Dict[Tuple[str, str, str], ObjectDef] = {}
    for obj in baseline.objects:
        k = _obj_key(obj)
        if k in baseline_map:
            logger.warning("Duplicate key in baseline definition: %s (first wins)", k)
            continue
        baseline_map[k] = obj

    # Map live objects by key (deterministic: first wins, duplicates logged)
    live_map: Dict[Tuple[str, str, str], ObjectDef] = {}
    for obj in live.objects:
        k = _obj_key(obj)
        if k in live_map:
            logger.warning("Duplicate key in live definition: %s (first wins)", k)
            continue
        live_map[k] = obj

    # Not deployed objects from baseline
    not_deployed_set: Set[str] = {
        str(o).upper().strip() for o in (baseline.not_deployed_objects or [])
    }

    # Align objects if one side omitted table, uses view tail, or table qualification differs
    for k_base in list(baseline_map.keys()):
        if k_base not in live_map:
            kind_b, table_b, name_b = k_base
            candidates = [k_l for k_l in live_map.keys() if k_l[0] == kind_b and k_l[2] == name_b and k_l not in baseline_map]
            if len(candidates) == 1:
                c_k = candidates[0]
                obj = baseline_map.pop(k_base)
                obj.table = c_k[1]
                baseline_map[c_k] = obj

    # Align relationships by explicit name if present and unique
    for k_base in list(baseline_map.keys()):
        if k_base not in live_map and k_base[0] == "RELATIONSHIP":
            base_obj = baseline_map[k_base]
            base_name = re.sub(r"[\s_]+", "_", str(base_obj.name or "").strip()).upper()
            if base_name and not base_name.startswith("A->") and "::" not in base_name:
                candidates = [
                    k_l for k_l in live_map.keys()
                    if k_l[0] == "RELATIONSHIP" and k_l not in baseline_map
                    and base_name == re.sub(r"[\s_]+", "_", str(live_map[k_l].name or "").strip()).upper()
                ]
                if len(candidates) == 1:
                    c_k = candidates[0]
                    obj = baseline_map.pop(k_base)
                    baseline_map[c_k] = obj

    base_caps = dict(baseline.capabilities or {})
    live_caps = dict(live.capabilities or {})

    # If baseline didn't explicitly declare capabilities (e.g. legacy metric-only baseline), infer from present objects
    if not base_caps and baseline.objects:
        present_kinds = {o.kind.value if hasattr(o.kind, "value") else str(o.kind) for o in baseline.objects}
        base_caps = {}
        for pk in present_kinds:
            has_desc = any(bool((o.description or "").strip()) for o in baseline.objects if (o.kind.value if hasattr(o.kind, "value") else str(o.kind)) == pk)
            base_caps[pk] = {"expression", "description"} if has_desc else {"expression"}

    all_keys = sorted(
        list(set(baseline_map.keys()) | set(live_map.keys())),
        key=lambda k: (k[0], k[1], k[2]),
    )

    for key in all_keys:
        kind, table, name = key

        # If a kind cannot be reported by either source path, do not evaluate drift for it
        if base_caps and kind not in base_caps:
            continue
        if live_caps and kind not in live_caps:
            continue

        in_baseline = key in baseline_map
        in_live = key in live_map

        if in_live and not in_baseline:
            # Object added directly in warehouse
            live_obj = live_map[key]
            k_val = live_obj.kind.value if hasattr(live_obj.kind, "value") else str(live_obj.kind)
            findings.append(
                DriftFinding(
                    category=DriftCategory.ADDITION,
                    kind=k_val,
                    table=live_obj.table,
                    name=str(live_obj.name or "").strip(),
                    baseline_value=None,
                    live_value=live_obj.expression,
                    baseline_description=None,
                    live_description=live_obj.description,
                )
            )

        elif in_baseline and not in_live:
            # Object missing in warehouse: verify if it was omitted during deployment remediation
            if name in not_deployed_set:
                logger.info(
                    "Skipping DELETION drift for object '%s': recorded as not-deployed during deploy",
                    name,
                )
                continue

            base_obj = baseline_map[key]
            if is_placeholder_expression(base_obj.expression):
                # Intentional null placeholder; cannot be missing in warehouse
                continue

            k_val = base_obj.kind.value if hasattr(base_obj.kind, "value") else str(base_obj.kind)
            findings.append(
                DriftFinding(
                    category=DriftCategory.DELETION,
                    kind=k_val,
                    table=base_obj.table,
                    name=str(base_obj.name or "").strip(),
                    baseline_value=base_obj.expression,
                    live_value=None,
                    baseline_description=base_obj.description,
                    live_description=None,
                )
            )

        else:
            # Object present on both sides: compare expression and description
            base_obj = baseline_map[key]
            live_obj = live_map[key]

            # 1. Compare normalized expressions if supported by both source paths
            can_report_expr_base = "expression" in base_caps.get(kind, {"expression"}) if base_caps else True
            can_report_expr_live = "expression" in live_caps.get(kind, {"expression"}) if live_caps else True

            if can_report_expr_base and can_report_expr_live:
                if kind == "RELATIONSHIP":
                    base_norm_expr = _normalize_join_expression(base_obj.expression)
                    live_norm_expr = _normalize_join_expression(live_obj.expression)
                elif kind == ObjectKind.FACT.value:
                    base_norm_expr = re.sub(r'["`]', '', (base_obj.expression or "")).strip().upper()
                    live_norm_expr = re.sub(r'["`]', '', (live_obj.expression or "")).strip().upper()
                else:
                    dialect = getattr(live, "dialect", None) or getattr(baseline, "dialect", None) or (platform.lower() if platform else None)
                    if not dialect:
                        raise ValueError("Comparison requires a SQL dialect (live.dialect, baseline.dialect, or platform)")
                    base_norm_expr = normalize_expression(base_obj.expression, dialect=dialect, platform=platform, table=table)
                    live_norm_expr = normalize_expression(live_obj.expression, dialect=dialect, platform=platform, table=table)

                    # Edge case from spec: if one side parses and the other doesn't,
                    # compare both using text fallback so mixed methods never create false drift
                    if getattr(base_norm_expr, "method", "parsed") != getattr(live_norm_expr, "method", "parsed"):
                        base_fb = normalize_expression_fallback(base_obj.expression, table=table)
                        live_fb = normalize_expression_fallback(live_obj.expression, table=table)
                        if base_fb == live_fb:
                            base_norm_expr = base_fb
                            live_norm_expr = live_fb

                # Check for expression drift unless both are placeholders
                base_is_ph = is_placeholder_expression(base_norm_expr)
                live_is_ph = is_placeholder_expression(live_norm_expr)
                if not (base_is_ph and live_is_ph) and (base_norm_expr != live_norm_expr):
                    legacy_type = "NULL_IN_SNOWFLAKE" if (live_is_ph and not base_is_ph) else None
                    finding_details = {
                        "baseline_normalized": str(base_norm_expr),
                        "live_normalized": str(live_norm_expr),
                    }
                    if kind == ObjectKind.FACT.value:
                        finding_details["drift_type"] = "FACT_REPOINTED"
                    elif legacy_type:
                        finding_details["drift_type"] = legacy_type

                    orig_k = live_obj.kind or base_obj.kind
                    k_val = orig_k.value if hasattr(orig_k, "value") else str(orig_k)
                    findings.append(
                        DriftFinding(
                            category=DriftCategory.EXPRESSION_DRIFT,
                            kind=k_val,
                            table=live_obj.table or base_obj.table,
                            name=str(live_obj.name or base_obj.name or "").strip(),
                            baseline_value=base_obj.expression,
                            live_value=live_obj.expression,
                            baseline_description=base_obj.description,
                            live_description=live_obj.description,
                            details=finding_details,
                        )
                    )

            # 2. Compare normalized descriptions if supported by both source paths
            can_report_desc_base = "description" in base_caps.get(kind, {"description"}) if base_caps else True
            can_report_desc_live = "description" in live_caps.get(kind, {"description"}) if live_caps else True

            if can_report_desc_base and can_report_desc_live:
                base_norm_desc = normalize_description(base_obj.description)
                live_norm_desc = normalize_description(live_obj.description)

                if base_norm_desc != live_norm_desc:
                    orig_k = live_obj.kind or base_obj.kind
                    k_val = orig_k.value if hasattr(orig_k, "value") else str(orig_k)
                    diff_info = classify_and_diff_description(base_obj.description, live_obj.description)
                    modifier = (
                        (live_obj.properties or {}).get("modified_by")
                        or (live_obj.properties or {}).get("last_altered_by")
                        or (live_obj.properties or {}).get("owner")
                        or (live_obj.properties or {}).get("comment_modified_by")
                    )
                    findings.append(
                        DriftFinding(
                            category=DriftCategory.DESCRIPTION_DRIFT,
                            kind=k_val,
                            table=live_obj.table or base_obj.table,
                            name=str(live_obj.name or base_obj.name or "").strip(),
                            baseline_value=base_obj.description,
                            live_value=live_obj.description,
                            baseline_description=base_obj.description,
                            live_description=live_obj.description,
                            details={
                                "baseline_description_normalized": base_norm_desc,
                                "live_description_normalized": live_norm_desc,
                                "baseline_expression": base_obj.expression,
                                "live_expression": live_obj.expression,
                                "description_classification": diff_info["classification"],
                                "description_severity": diff_info["severity"],
                                "baseline_marked": diff_info["baseline_marked"],
                                "live_marked": diff_info["live_marked"],
                                "diff_segments": diff_info["diff_segments"],
                                "removed_caveats": diff_info.get("removed_caveats", []),
                                "similarity_ratio": diff_info.get("similarity_ratio"),
                                "description_summary": diff_info.get("summary", ""),
                                "modified_by": modifier,
                            },
                        )
                    )

    # 3. Engine Sanity Rule: Parser or Baseline Naming Mismatch Guard
    # If a drift check produces zero EXPRESSION_DRIFT while every baseline object is reported
    # as DELETION and every live object is reported as ADDITION, the engine refuses to return those findings.
    deletions = [f for f in findings if f.category == DriftCategory.DELETION]
    additions = [f for f in findings if f.category == DriftCategory.ADDITION]
    expr_drifts = [f for f in findings if f.category == DriftCategory.EXPRESSION_DRIFT]
    desc_drifts = [f for f in findings if f.category == DriftCategory.DESCRIPTION_DRIFT]

    num_baseline = len(baseline_map)
    num_live = len(live_map)

    if (
        num_baseline >= 2
        and num_live >= 2
        and len(expr_drifts) == 0
        and len(desc_drifts) == 0
        and len(deletions) == num_baseline
        and len(additions) == num_live
    ):
        logger.critical(
            "Drift check aborted: baseline and live definitions appear to have mismatched naming conventions. 0 expression matches out of %d objects.",
            num_baseline + num_live,
        )
        raise ParserOrBaselineMismatchError(
            f"Drift check aborted: baseline and live definitions appear to have mismatched naming conventions. 0 expression matches out of {num_baseline + num_live} objects."
        )

    return findings


compare = compare_views


def group_counts(findings: List[DriftFinding]) -> Dict[str, Dict[str, int]]:
    """Return count of findings grouped by kind, then category: counts[kind][category] = count."""
    counts: Dict[str, Dict[str, int]] = {}
    for f in findings:
        k = f.kind.value if hasattr(f.kind, "value") else str(f.kind)
        cat = f.category.value if hasattr(f.category, "value") else str(f.category)
        counts.setdefault(k, {})[cat] = counts.setdefault(k, {}).get(cat, 0) + 1
    return counts
