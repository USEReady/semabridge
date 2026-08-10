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

from typing import Any, Dict

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.time_intelligence_shapes import metrics_with_time_intelligence_shapes
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def rerender_anchor_dependent_metrics(
    model: Any,
    anchor_flag_map: Dict[str, Dict[Any, str]],
    dataset_col_lookup: Dict[str, set],
    dataset_aliases: Dict[str, str],
    label: str = "",
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
        # Deliberately not skipped when empty — see the docstring above:
        # retranslating with an empty map reproduces (predicted pass) or
        # restores (confirmed pass, correcting a divergent prediction)
        # Stage 1's safe CURRENT_DATE() fallback.
        flag_map_for_dataset = anchor_flag_map.get(str(dataset_name or "").casefold(), {})

        table_alias = dataset_aliases.get(dataset_name, dataset_name)
        translation = translator.translate(
            dax_expr,
            table_alias,
            dataset_name,
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
        rerendered += 1

    return rerendered
