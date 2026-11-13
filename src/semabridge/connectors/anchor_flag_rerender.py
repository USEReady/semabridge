"""Re-render anchor-dependent metrics' `sql_expression` once a
time-intelligence flag-column map — predicted or real, see
converter/time_intelligence_shapes.py — becomes available.

Background: Step 6 (osi_to_sml.py / tmsl_to_sml.py) translates every
metric's DAX fresh, target-agnostically, with zero knowledge of
Snowflake's enriched-view flag columns — `anchor_flag_map` is a
Snowflake-specific concept that doesn't exist at that point in the
pipeline. So a time-intelligence metric always gets Stage 1's safe,
CURRENT_DATE()-anchored fallback rendering at Step 6, never a flag-column
reference.

This module re-translates just the metrics whose DAX actually needs a
time-intelligence shape, now WITH an anchor_flag_map, and overwrites their
`sql_expression` in place on the model the caller already holds — so a
later, more informed pass (predicted before Step 7 persists; real, inside
Step 9, once _create_enriched_view has actually run) can upgrade Stage
1's safe-but-approximate rendering to the more accurate flag-column
rendering. This never touches metrics_clause_builder.py's `:688`
"already have sql_expression, use it directly" shortcut — by the time
that code runs, `sql_expression` has already been corrected in place, so
there's nothing for that shortcut to get wrong.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.time_intelligence_shapes import (
    enrichment_flag_column_if_referenced,
    metrics_with_time_intelligence_shapes,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def rerender_anchor_dependent_metrics(
    model: Any,
    anchor_flag_map: Dict[str, Dict[Any, str]],
    dataset_col_lookup: Dict[str, set],
    dataset_aliases: Dict[str, str],
    label: str = "",
    failed_enrichment_datasets: Optional[Set[str]] = None,
    failed_enrichment_errors: Optional[Dict[str, str]] = None,
) -> int:
    """Re-translate every metric whose DAX resolves to a time-intelligence
    shape, passing `anchor_flag_map`, and overwrite `metric.sql_expression`
    in place on `model` — the SAME object instance the caller already
    holds (e.g. `context.sml_model`, which survives Step 6 through Step 9
    unmutated by anything else — see core/engine/context.py).

    Scoped to shaped metrics only, via
    `time_intelligence_shapes.metrics_with_time_intelligence_shapes` —
    never every metric in the model. Retranslating a metric with no
    time-intelligence shape would be a wasted, purely-mechanical no-op at
    best (anchor_flag_map has nothing to change about its rendering), but
    at worst risks silently downgrading a metric that Step 6 originally
    resolved via a later tier (rule-based, AST, or LLM) to whatever a
    fresh Tier 1-4-only retranslation produces here instead — this scoping
    is what keeps "the vast majority of metrics, which don't need a flag
    column, completely unaffected" true in practice, not just in theory.

    Deliberately does NOT skip retranslation just because `anchor_flag_map`
    (or this metric's own entry within it) is empty — an empty/absent
    entry is retranslated too, with an empty map, which deterministically
    reproduces Stage 1's CURRENT_DATE() fallback. This matters for the
    Step 9 (confirmed) call site specifically: if an earlier, predicted
    pass (Step 6b) upgraded a metric to a flag-column reference for a
    fact table whose enrichment then failed for real (cursor error,
    `auto_create_enriched_view` toggled off, live schema missing the date
    column), the confirmed map has no entry for that fact table — and
    this pass must be able to DOWNGRADE that metric back to the safe
    CURRENT_DATE() fallback, not merely skip it and leave a reference to
    a flag column that will never exist. Skipping on emptiness would
    silently prevent exactly that correction.

    A metric IS a shaped-metric candidate but is still left untouched
    (whatever rendering it already has stays exactly as-is) whenever:
      - it has no DAX expression to retranslate from, or
      - retranslation declines or fails (skip_tier5=True — see below —
        means this never silently regresses a metric Step 6 could only
        resolve via Tier 5/LLM; failure here just leaves Step 6's already-
        successful rendering alone), or
      - retranslation succeeds but produces the exact same SQL already
        stored (a no-op, not counted as "rerendered").

    One `DAXTranslator()` instance is constructed for the whole pass, not
    per metric — the same reuse discipline osi_to_sml.py/tmsl_to_sml.py
    already apply to their own single per-converter instance. `skip_tier5`
    is always True: this pass never makes an LLM call, and never competes
    with or duplicates osi_to_sml.py's/tmsl_to_sml.py's own Tier-5 batching
    (`batch_translate_tier5`) — it runs strictly AFTER Step 6's conversion
    (batching included) has already fully completed, and only touches
    metrics that conversion already finished with.

    `label` is a free-text tag for log lines only (e.g. "predicted" or
    "confirmed") — has no effect on behavior.

    Returns the number of metrics actually changed, for logging/
    diagnostics.
    """
    anchor_flag_map = anchor_flag_map or {}

    metrics = list(getattr(model, "metrics", []) or [])
    if not metrics:
        return 0

    shaped = metrics_with_time_intelligence_shapes(metrics)
    if not shaped:
        return 0

    metric_by_name = {str(getattr(m, "unique_name", "") or ""): m for m in metrics}
    translator = DAXTranslator()
    rerendered = 0
    tag = f"[{label}] " if label else ""

    for name in shaped:
        metric = metric_by_name.get(name)
        if metric is None:
            continue
        dax_expr = getattr(metric, "expression", None)
        if not dax_expr or not str(dax_expr).strip():
            continue

        dataset_name = getattr(metric, "dataset", None)
        target_ds = dataset_name
        flag_map_for_dataset = anchor_flag_map.get(str(dataset_name or "").casefold(), {})
        if not flag_map_for_dataset and anchor_flag_map:
            if len(anchor_flag_map) == 1:
                target_ds, flag_map_for_dataset = next(iter(anchor_flag_map.items()))
            else:
                for k, v in anchor_flag_map.items():
                    if v:
                        target_ds, flag_map_for_dataset = k, v
                        break

        # If enriched view creation failed for this dataset, emit NULL with the real Snowflake error (no CURRENT_DATE fallback)
        failed_sets = failed_enrichment_datasets or set()
        failed_errs = failed_enrichment_errors or {}
        ds_cf = str(target_ds or dataset_name or "").casefold()
        if ds_cf in {str(s).casefold() for s in failed_sets} or ds_cf in {str(k).casefold() for k in failed_errs}:
            real_err = failed_errs.get(ds_cf) or next((v for k, v in failed_errs.items() if str(k).casefold() == ds_cf), "Enriched view creation failed")
            metric.sql_expression = None
            metric.sync_failure_reason = f"Enriched view creation failed: {real_err}"
            metric.sync_enabled = False
            logger.info("Enriched view failed for '%s'; metric '%s' emitted as NULL: %s", target_ds, name, real_err)
            rerendered += 1
            continue

        if not flag_map_for_dataset:
            # No confirmed flag map: leave the metric's existing rendering
            # alone (no re-render to CURRENT_DATE() just because a map is
            # missing) -- UNLESS that rendering cites this metric's own flag
            # column (an earlier predicted pass upgraded it), which will never
            # exist. Then retranslate with the empty map to restore the same
            # rendering a never-enriched fact table gets, instead of leaving a
            # dangling column reference.
            current_sql = getattr(metric, "sql_expression", None)
            if not enrichment_flag_column_if_referenced(shaped.get(name), current_sql):
                continue

        # If metric belongs to a virtual measure table, resolve table_alias to the fact table where flags reside
        table_alias = (
            dataset_aliases.get(target_ds)
            or dataset_aliases.get(dataset_name, dataset_name)
        )
        effective_dataset = target_ds if (target_ds and target_ds in (dataset_col_lookup or {})) else dataset_name
        translation = translator.translate(
            dax_expr,
            table_alias,
            effective_dataset,
            dataset_col_lookup=dataset_col_lookup,
            dataset_aliases=dataset_aliases,
            metric_name=name,
            anchor_flag_map=flag_map_for_dataset,
            skip_tier5=True,
            metrics_context=metrics,
        )
        if not translation or not translation.is_success or not translation.sql:
            continue
        if translation.sql == getattr(metric, "sql_expression", None):
            continue

        logger.info(
            "%sRe-rendered anchor-dependent metric '%s' using %s anchor_flag_map: %s",
            tag,
            name,
            label or "supplied",
            translation.sql[:160],
        )
        metric.sql_expression = translation.sql
        sync_time_anchor_advisory(metric, translation.sql)
        rerendered += 1

    return rerendered


ADVISORY_CATEGORY_TI_ANCHORED_TO_DATA = "time_intelligence_anchored_to_latest_data_date"


_PRIOR_PERIOD_FLAG_RE = re.compile(r"(SPLY|_PY_|_PY$|PRIOR|PREV|LAST_YEAR|_LY_|_LY$)")


def _ti_data_anchor_note(flags: List[str]) -> str:
    note = (
        "Time-intelligence window is anchored to the latest date in the data "
        f"(enriched-view period flag {', '.join(flags)}), as DAX does with no date filter; "
        "the window does not move with a period chosen in the query."
    )
    if any(_PRIOR_PERIOD_FLAG_RE.search(f) for f in flags):
        # E18: a per-period "same period last year" (shifting with each
        # year/month in the query) is not expressible as a semantic-view
        # metric: a window LAG metric only sees rows left after the query's
        # own filters (a query filtered to one year gets NULL), skips gaps
        # in the calendar silently, and window metrics cannot be referenced
        # by other metrics (so YoY metrics could not use it).
        note += (
            " Grouped by year or month it still returns the latest period's prior-year "
            "value, not each row's own prior year: a per-period same-period-last-year is "
            "not expressible as a Snowflake semantic-view metric."
        )
    return note


def sync_time_anchor_advisory(metric: Any, sql: Optional[str]) -> None:
    """Keep the metric's time-anchor advisory in line with the SQL it now has
    (E17). A metric first rendered with the CURRENT_DATE() fallback and then
    re-rendered against the enriched view's data-anchored period flags must
    not keep saying it is anchored to CURRENT_DATE(), and vice versa."""
    from semabridge.converter.dax_ast_parser import (
        ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE,
        ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE,
    )
    notes = list(getattr(metric, "advisory_notes", None) or [])
    cats = list(getattr(metric, "advisory_categories", None) or [])
    if not hasattr(metric, "advisory_notes"):
        return
    notes = [
        n for n in notes
        if n != ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE
        and not str(n).startswith("Time-intelligence window is anchored to the latest date in the data")
    ]
    cats = [c for c in cats if c not in (ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE, ADVISORY_CATEGORY_TI_ANCHORED_TO_DATA)]
    sql_u = str(sql or "").upper()
    flags = sorted(set(re.findall(r'\b(IS_[A-Z0-9_]+)\b', sql_u)))
    if "CURRENT_DATE()" in sql_u:
        notes.append(ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE)
        cats.append(ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE)
    elif flags:
        notes.append(_ti_data_anchor_note(flags))
        cats.append(ADVISORY_CATEGORY_TI_ANCHORED_TO_DATA)
    try:
        metric.advisory_notes = notes
        metric.advisory_categories = cats
    except Exception:  # noqa: BLE001
        pass
