"""
OSI to SML Converter.

Converts OSI (Open Semantic Interchange) canonical models into the SML (Semantic Modeling Language)
intermediate representation, applying semantic enrichment like DAX translation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
from semabridge.connectors.dataset_classification_keywords import is_calendar_like_name
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIDimension,
    OSIRelationship,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    SMLDimension,
    SMLAttribute,
    SMLHierarchy,
    SMLLevel,
    DataType as SMLDataType,
    AggregationType as SMLAggregationType,
    Cardinality as SMLCardinality,
    CrossFilterDirection as SMLCrossFilterDirection,
    SourcePlatform,
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
    ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK,
    ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED,
)
from semabridge.connectors.inference_engine import SmlInferenceEngine
from semabridge.connectors.measure_detector import MeasureDetector
from semabridge.utils.logger import get_logger
from semabridge.utils.naming import to_alias

logger = get_logger(__name__)

# Maps translation.advisory_categories entries to the advisory_notes text
# appended in lockstep (advisory_notes/advisory_categories are same-index
# parallel lists -- see SMLMetric.advisory_categories docstring).
_TRANSLATION_ADVISORY_NOTES = {
    ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK: ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK,
}


def _apply_translation_advisories(metric, translation) -> None:
    """Mirror translation.advisory_categories onto metric.advisory_categories/notes.

    Idempotent (checked by category membership) so re-running a convergence
    pass over an already-translated metric doesn't duplicate entries.
    """
    for category in translation.advisory_categories or []:
        if category in metric.advisory_categories:
            continue
        metric.advisory_categories.append(category)
        metric.advisory_notes.append(_TRANSLATION_ADVISORY_NOTES.get(category, category))


class OSIToSMLConverter(BaseConverter):
    """
    Transforms OSI Model into SML Model with semantic enrichment.
    """

    def __init__(self):
        self.dax_translator = DAXTranslator()

    def to_osi(self, sml_model: SMLModel) -> OSIModel:
        """
        Convert SMLModel object to OSIModel object.
        
        Delegates to SMLToOSIConverter for the actual conversion.
        This method exists to satisfy the BaseConverter interface.
        
        Args:
            sml_model: SMLModel object
            
        Returns:
            OSIModel object
        """
        from semabridge.converter.sml_to_osi import SMLToOSIConverter
        return SMLToOSIConverter().to_osi(sml_model)


    def from_osi(
        self,
        osi_model: OSIModel,
        row_counts: Optional[Dict[str, int]] = None,
    ) -> SMLModel:
        """
        Convert OSIModel object to SMLModel object.

        Args:
            osi_model: OSIModel object

        Returns:
            SMLModel object
        """
        try:
            sml = SMLModel(
                unique_name=osi_model.unique_name,
                label=osi_model.label,
                description=osi_model.description or "",
                source_system=osi_model.source_platform or "unknown",
                source_platform=SourcePlatform.FABRIC if osi_model.source_platform == "fabric" else SourcePlatform.SNOWFLAKE,
                version=osi_model.version
            )

            # 1. Convert Datasets
            for osi_ds in osi_model.datasets:
                sml.datasets.append(self._convert_dataset(osi_ds))

            # 2. Convert Dimensions
            for osi_dim in osi_model.dimensions:
                sml.dimensions.append(self._convert_dimension(osi_dim))

            # Source-schema lookup for Tier 5's semantic validator (Step 3 of
            # the DAX translation consolidation). Built once here, from the
            # source datasets/columns just converted above — no
            # Snowflake-side physical schema exists yet at this stage.
            # General/schema-shape-derived (sanitize_column/to_alias, same
            # utilities already used throughout this module) — not
            # hardcoded to any table/column/model name.
            dax_dataset_col_lookup, dax_dataset_aliases = DAXTranslator.build_schema_lookup(sml.datasets)

            # 3. Convert Metrics with automated translation pipeline
            # Step 3a: Convert all metrics individually for Tier 1-4 translations

            for osi_metric in osi_model.metrics:
                sml_metric = self._convert_metric(
                    osi_metric,
                    dataset_col_lookup=dax_dataset_col_lookup,
                    dataset_aliases=dax_dataset_aliases,
                )
                sml.metrics.append(sml_metric)

            # Step 3b: Resolve inter-measure dependencies now that all metrics are loaded.
            # This helps Category 2/3 formulas like [A]-[B], DIVIDE([A],[B]), TOTALYTD([A], ...)
            # when dependencies appear later in source order.
            # skip_tier5=True: deterministic (Tier 1-4) convergence only here --
            # a dependency chain through a metric that itself needs Tier 5 is
            # picked up by the second call below, AFTER the Step 3d batch has
            # had a chance to resolve that upstream metric. Without this, each
            # pass here could issue its own individual Tier5Service call per
            # metric (the dry-run-timeout root cause this fixes).
            self._resolve_metric_dependencies(
                sml,
                dataset_col_lookup=dax_dataset_col_lookup,
                dataset_aliases=dax_dataset_aliases,
                skip_tier5=True,
            )

            # Step 3c: Collect unresolved metrics for Tier-5 batch fallback.
            from semabridge.converter.dax_ast_parser import is_by_design_excluded
            tier5_candidates = []  # (metric_name, dax, table_alias, dataset_name)
            for sml_metric in sml.metrics:
                if (
                    not sml_metric.sql_expression
                    and sml_metric.expression
                    and sml_metric.expression.strip()
                    and not is_by_design_excluded(sml_metric.sync_failure_reason)
                ):
                    dax = sml_metric.expression.strip()
                    table_alias = to_alias(sml_metric.dataset)
                    tier5_candidates.append((sml_metric.unique_name, dax, table_alias, sml_metric.dataset))

            # Step 3d: Batch translate all Tier 5 candidates at once (reduces API calls by 90%)
            if tier5_candidates:
                logger.info(f"📦 Batch translating {len(tier5_candidates)} Tier 5 metrics...")
                batch_results = self.dax_translator.batch_translate_tier5(
                    tier5_candidates,
                    dataset_col_lookup=dax_dataset_col_lookup,
                    dataset_aliases=dax_dataset_aliases,
                    # osi_model.relationships (not sml.relationships) --
                    # available from the start, before "4. Convert
                    # Relationships" below runs. Lets Tier 5 legally
                    # reference a relationship-reachable dimension's
                    # column directly instead of always declining
                    # cross-table filters (see batch_translate_tier5's
                    # docstring).
                    relationships=osi_model.relationships,
                )
                
                # Apply batch translation results back to metrics
                for metric in sml.metrics:
                    if metric.unique_name in batch_results and batch_results[metric.unique_name]:
                        translation = batch_results[metric.unique_name]
                        if translation and translation.is_success:
                            metric.sql_expression = translation.sql
                            metric.complexity_tier = translation.tier
                            metric.sync_enabled = True
                            metric.sync_failure_reason = None
                            # Display-only Tier-5 metadata -- None for
                            # Tiers 1-4, since translation.llm_self_reported_
                            # confidence/validation_notes are only ever
                            # populated by Tier5Service (tier == 5).
                            metric.llm_self_reported_confidence = translation.llm_self_reported_confidence
                            metric.validation_notes = list(translation.validation_notes or [])
                            _apply_translation_advisories(metric, translation)
                            logger.debug(f"✓ Applied batch translation for '{metric.unique_name}'")

                # Step 3e: one more deterministic-only convergence pass now that
                # the batch call may have populated sql_expression for metrics
                # that needed Tier 5 -- lets any purely Tier 1-4 metric whose
                # DAX references one of *those* (e.g. [A]-[B] where A needed an
                # LLM and B didn't) resolve via cheap substitution instead of
                # being sent to the LLM itself with an unresolved [A] reference
                # it has no schema context to make sense of. skip_tier5=True:
                # this is convergence only, never a second individual Tier-5
                # attempt for whatever the batch still left unresolved.
                self._resolve_metric_dependencies(
                    sml,
                    dataset_col_lookup=dax_dataset_col_lookup,
                    dataset_aliases=dax_dataset_aliases,
                    skip_tier5=True,
                )

                # Step 3f: real incident -- '% Unit Market Share YOY Change'
                # ([% Units Market Share]-[% Units Market Share SPLY]) kept
                # showing the stale placeholder reason set BEFORE the batch
                # even ran ("DAX translation deferred to Tier-5 batch"),
                # even after the batch completed and genuinely couldn't
                # resolve it -- nothing in Step 3d/3e ever revisits a
                # metric's OWN reason once it's known the deferral is over.
                # Replace that specific stale placeholder (never a more
                # specific reason something else already set) with the
                # real, general explanation: either this metric is blocked
                # by an unresolved DEPENDENCY (the actual cause here --
                # this metric's own bracket-arithmetic DAX has nothing
                # wrong with it, it just can't substitute a sibling that
                # has no SQL of its own) or, if every dependency DID
                # resolve, the batch attempt for this metric specifically
                # produced nothing usable.
                self._replace_stale_tier5_deferred_reason(sml)

            # 4. Convert Relationships
            for osi_rel in osi_model.relationships:
                sml_rel = self._convert_relationship(osi_rel)
                if sml_rel:
                    sml.relationships.append(sml_rel)

            # 4a. Advisory-only: flag metrics whose DAX will fail Snowflake's
            # semantic-view compiler for a relationship-graph reason, even
            # though translation itself succeeds — see
            # _flag_unreachable_dimension_calculates' docstring. Must run
            # AFTER relationships are converted (immediately above) since
            # the check needs sml.relationships, and after all metrics
            # exist (Step 3 above) since it needs every metric's own
            # dataset for the optional "does the referenced measure reach
            # it instead" enrichment. Never touches sync_enabled/
            # sync_failure_reason/sql_expression — this changes only what
            # a user sees at mapping/dry-run time, never what deploys.
            self._flag_unreachable_dimension_calculates(sml)

            # 4a2. Decompose "disconnected selector table" metrics (e.g. a
            # Power BI field-parameter/what-if table with no real business
            # key) whose DAX switches between OTHER tables' aggregates
            # based on the selector's own value -- a shape no single
            # Snowflake metric can express, but whose individual branches
            # (selector condition stripped) are real, deployable metrics
            # on their own. Must run AFTER 4a (needs sml.relationships to
            # confirm genuine disconnection -- see
            # try_decompose_disconnected_selector_metric's docstring on why
            # translation "succeeding" doesn't mean this metric is safe)
            # and takes priority over 4a's generic advisory for any metric
            # it successfully handles (removes it, see below) -- a
            # successful decomposition is a strictly more specific,
            # actionable diagnosis than a bare "unreachable dimension" note
            # with no real fix.
            self._decompose_disconnected_selector_metrics(
                sml,
                dataset_col_lookup=dax_dataset_col_lookup,
                dataset_aliases=dax_dataset_aliases,
            )

            # 4b. Advisory-only, opposite outcome of 4a: a metric whose
            # CALCULATE(...) filters a table that DOES have a relationship
            # path, but whose translation still failed today because
            # dry-run has no live connection to exploit that path (see
            # _flag_reachable_dimension_filters_pending_enrichment). Must
            # run after 4a and after Step 3 for the same reasons.
            self._flag_reachable_dimension_filters_pending_enrichment(sml)

            # 4b. Fabric/PBIX extractions can legitimately surface 0 explicit
            # measures in TMSL. Reuse the older heuristic detector so downstream
            # publishers still have simple metrics to materialize.
            self._auto_detect_metrics(sml, row_counts=row_counts)

            # 5. Inject Calendar Dimension if missing (SML Requirement for Cortex)
            self._inject_calendar_dimension(sml)

            return sml

        except Exception as e:
            logger.error(f"OSI to SML conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert OSI to SML: {e}",
                source_format="osi",
                target_format="sml",
                details={"error": str(e)}
            )

    def _convert_dataset(self, osi_ds: OSIDataset) -> SMLDataset:
        columns = [self._convert_column(c) for c in osi_ds.columns]
        
        return SMLDataset(
            unique_name=osi_ds.unique_name,
            label=osi_ds.label,
            description=osi_ds.description or "",
            source_table=osi_ds.source_table,
            source_schema=osi_ds.source_schema or "",
            columns=columns,
            is_hidden=osi_ds.is_hidden,
            is_fact=osi_ds.is_fact,
        )

    def _convert_column(self, osi_col: OSIColumn) -> SMLColumn:
        # Dynamic lookup using name matching (e.g. INTEGER -> INTEGER)
        sml_type = getattr(SMLDataType, osi_col.data_type.name, SMLDataType.STRING)

        return SMLColumn(
            unique_name=osi_col.unique_name,
            label=osi_col.label,
            data_type=sml_type,
            description=osi_col.description or "",
            is_hidden=osi_col.is_hidden,
            is_key=osi_col.is_key,
            is_measure_candidate=getattr(osi_col, "is_measure_candidate", False),
            format_string=osi_col.format_string,
            # Cortex AI metadata (propagated from OSI)
            synonyms=list(osi_col.synonyms),
            synonym_sources=dict(osi_col.synonym_sources),
            has_report_alias=osi_col.has_report_alias,
            is_enum=osi_col.is_enum,
            cortex_search_service=osi_col.cortex_search_service,
            sample_values=list(osi_col.sample_values),
        )

    def _convert_dimension(self, osi_dim: OSIDimension) -> SMLDimension:
        attributes = []
        for attr in osi_dim.attributes:
            attributes.append(SMLAttribute(
                unique_name=attr.unique_name,
                label=attr.label,
                dataset=attr.dataset,
                dataset_column=attr.source_column,
                is_hidden=attr.is_hidden
            ))
            
        return SMLDimension(
            unique_name=osi_dim.unique_name,
            label=osi_dim.label,
            description=osi_dim.description or "",
            dataset=osi_dim.dataset,
            attributes=attributes,
            is_hidden=osi_dim.is_hidden
        )

    def _convert_metric(
        self,
        osi_metric: OSIMetric,
        dataset_col_lookup: Optional[Dict[str, set]] = None,
        dataset_aliases: Optional[Dict[str, str]] = None,
    ) -> SMLMetric:
        # --- Snowflake-sourced metrics already have sql_expression ---
        # When the source is a Snowflake semantic view, the expression is already
        # valid SQL. Use SQLToDAXConverter to produce a DAX representation for
        # Fabric publishing, and preserve the sql_expression directly.
        # NOTE: The OSIMetric validator copies sql_expression → expression when
        # expression is absent, so we detect this path by checking if sql_expression
        # equals expression (i.e., no separate DAX was ever set).
        _has_separate_dax = (
            osi_metric.expression
            and osi_metric.sql_expression
            and osi_metric.expression != osi_metric.sql_expression
        )
        if osi_metric.sql_expression and not _has_separate_dax:
            from semabridge.converter.sql_to_dax import SQLToDAXConverter
            _sql_to_dax = SQLToDAXConverter()
            _dax, _tier = _sql_to_dax.translate(osi_metric.sql_expression, osi_metric.dataset)
            sml_agg = getattr(SMLAggregationType, osi_metric.aggregation.name, SMLAggregationType.SUM)
            metric = SMLMetric(
                unique_name=osi_metric.unique_name,
                label=osi_metric.label,
                description=osi_metric.description or "",
                dataset=osi_metric.dataset,
                expression=_dax if _tier > 0 else osi_metric.sql_expression,
                sql_expression=osi_metric.sql_expression,
                aggregation=sml_agg,
                format_string=osi_metric.format_string,
                is_hidden=osi_metric.is_hidden,
                complexity_tier=_tier if _tier > 0 else osi_metric.complexity_tier or 1,
                sync_enabled=True,
                access_modifier=osi_metric.access_modifier,
                synonyms=list(osi_metric.synonyms),
                synonym_sources=dict(osi_metric.synonym_sources),
                has_report_alias=osi_metric.has_report_alias,
            )
            if _tier == 0:
                metric.sync_failure_reason = "SQL→DAX reverse translation not available for this expression; raw SQL preserved"
            return metric

        # DAX Translation Logic
        expression = osi_metric.expression or ""

        # Constant expressions and string-producing root expressions are a
        # modeling-classification mismatch, not a DAX translation gap — a
        # Snowflake semantic-view metric must be an aggregate expression.
        # Detected purely from AST shape (no field/model names involved).
        if expression:
            from semabridge.converter.dax_ast_parser import (
                BY_DESIGN_EXCLUDED_PREFIX,
                dax_has_zero_data_dependencies,
                dax_root_is_string_producing,
            )
            by_design_reason = None
            if dax_has_zero_data_dependencies(expression):
                by_design_reason = (
                    f"{BY_DESIGN_EXCLUDED_PREFIX}: expression has no column/measure/table "
                    "reference (a compile-time constant) — there is nothing to "
                    "aggregate, so this is not a DAX translation failure."
                )
            elif dax_root_is_string_producing(expression):
                by_design_reason = (
                    f"{BY_DESIGN_EXCLUDED_PREFIX}: expression's root operation produces a "
                    "string value, not a numeric aggregate — Snowflake "
                    "semantic-view metrics must be aggregate expressions. This "
                    "field should be modeled as a dimension attribute instead."
                )
            if by_design_reason:
                sml_agg = getattr(SMLAggregationType, osi_metric.aggregation.name, SMLAggregationType.SUM)
                metric = SMLMetric(
                    unique_name=osi_metric.unique_name,
                    label=osi_metric.label,
                    description=osi_metric.description or "",
                    dataset=osi_metric.dataset,
                    expression=expression,
                    aggregation=sml_agg,
                    format_string=osi_metric.format_string,
                    is_hidden=osi_metric.is_hidden,
                    complexity_tier=1,
                    sync_enabled=False,
                    sync_failure_reason=by_design_reason,
                    access_modifier=osi_metric.access_modifier,
                    synonyms=list(osi_metric.synonyms),
                    synonym_sources=dict(osi_metric.synonym_sources),
                    has_report_alias=osi_metric.has_report_alias,
                )
                return metric

        # Analyze Complexity
        complexity = self.dax_translator.analyze_complexity(expression)
        
        sml_agg = getattr(SMLAggregationType, osi_metric.aggregation.name, SMLAggregationType.SUM)
        
        metric = SMLMetric(
            unique_name=osi_metric.unique_name,
            label=osi_metric.label,
            description=osi_metric.description or "",
            dataset=osi_metric.dataset,
            expression=expression,
            aggregation=sml_agg,
            format_string=osi_metric.format_string,
            is_hidden=osi_metric.is_hidden,
            # Sync Metadata
            complexity_tier=complexity["tier"],
            requires_time_intel=complexity["requires_time_intel"],
            group_by_dimensions=complexity["group_by_dimensions"],
            depends_on_measures=complexity["depends_on_measures"],
            sync_enabled=complexity["sync_enabled"],
            sync_failure_reason=complexity["failure_reason"],
            partition_dimension="'Date'[Year]" if complexity["requires_time_intel"] else None,
        )
        
        # Attempt Translation using automated deterministic/AST/rule tiers only
        # (Tiers 1-4). skip_tier5=True: this runs once per metric for every
        # metric in the model, so resolving Tier 5 here too would mean one
        # sequential Tier5Service.translate() API call per hard-to-translate
        # metric -- exactly the multi-minute dry-run cost this was fixed to
        # avoid. Anything Tiers 1-4 decline falls through to Step 3c/3d below
        # (from_osi()), which now collects every such metric across the whole
        # model and resolves them all in one batch_translate_tier5() call.
        if expression:
            from semabridge.utils.naming import to_alias
            safe_alias = to_alias(osi_metric.dataset)
            translation = self.dax_translator.translate(
                expression,
                safe_alias,
                osi_metric.dataset,
                metric_name=metric.unique_name,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                skip_tier5=True,
            )
            
            if translation.is_success:
                metric.sql_expression = translation.sql
                metric.complexity_tier = translation.tier
                metric.sync_enabled = True
                metric.sync_failure_reason = None
                # skip_tier5=True above means this call never reaches
                # Tier5Service, so these are always None/empty here --
                # copied through anyway for consistency with the other two
                # translation.* -> metric.* assignment sites.
                metric.llm_self_reported_confidence = translation.llm_self_reported_confidence
                metric.validation_notes = list(translation.validation_notes or [])
                _apply_translation_advisories(metric, translation)
            elif metric.sync_enabled:
                from semabridge.converter.dax_ast_parser import (
                    dax_context_transition_failure_reason,
                    dax_lag_period_of_measure_reference_failure_reason,
                )
                context_transition_reason = dax_context_transition_failure_reason(expression)
                lag_period_reason = dax_lag_period_of_measure_reference_failure_reason(expression)
                if context_transition_reason:
                    metric.sync_failure_reason = context_transition_reason
                elif lag_period_reason:
                    metric.sync_failure_reason = lag_period_reason
                else:
                    metric.sync_failure_reason = f"DAX translation deferred to Tier-5 batch (Tier {translation.tier})"

        # Propagate Cortex AI metadata from OSI layer
        metric.access_modifier = osi_metric.access_modifier
        metric.synonyms = list(osi_metric.synonyms)
        metric.synonym_sources = dict(osi_metric.synonym_sources)
        metric.has_report_alias = osi_metric.has_report_alias

        return metric

    def _resolve_metric_dependencies(
        self,
        sml: SMLModel,
        max_passes: int = 3,
        dataset_col_lookup: Optional[Dict[str, set]] = None,
        dataset_aliases: Optional[Dict[str, str]] = None,
        skip_tier5: bool = False,
    ) -> None:
        """Resolve unresolved metrics using full metric context in deterministic passes.

        This performs lightweight topological convergence: each pass can unlock
        downstream expressions once upstream measures receive SQL.

        dataset_col_lookup/dataset_aliases: forwarded to DAXTranslator's
        Tier 5 semantic validator (Step 3 of the DAX translation
        consolidation); rebuilt from sml.datasets if not supplied.

        skip_tier5: forwarded to DAXTranslator.translate() -- see its
        docstring. from_osi() calls this method twice: once with
        skip_tier5=True *before* the Step 3c/3d Tier-5 batch call (so this
        method's own per-metric loop, run up to `max_passes` times, never
        issues an individual Tier5Service call itself), and once more with
        skip_tier5=True *after* the batch call, so a metric that is a pure
        deterministic (Tier 1-4) dependency on a metric the batch just
        resolved still converges correctly -- without either call spending
        a second, redundant per-metric Tier-5 attempt.
        """
        if not sml.metrics:
            return

        if dataset_col_lookup is None or dataset_aliases is None:
            dataset_col_lookup, dataset_aliases = DAXTranslator.build_schema_lookup(sml.datasets)

        from semabridge.converter.dax_ast_parser import is_by_design_excluded

        for pass_idx in range(max_passes):
            resolved_this_pass = 0
            for metric in sml.metrics:
                if metric.sql_expression or not (metric.expression or "").strip():
                    continue
                # A by-design-excluded metric (constant expression /
                # string-producing root) was never a translation candidate —
                # re-attempting here could silently un-exclude it.
                if is_by_design_excluded(metric.sync_failure_reason):
                    continue

                safe_alias = to_alias(metric.dataset)
                translation = self.dax_translator.translate(
                    metric.expression,
                    safe_alias,
                    metric.dataset,
                    metric_name=metric.unique_name,
                    metrics_context=sml.metrics,
                    dataset_col_lookup=dataset_col_lookup,
                    dataset_aliases=dataset_aliases,
                    skip_tier5=skip_tier5,
                )
                if translation.is_success and translation.sql:
                    metric.sql_expression = translation.sql
                    metric.complexity_tier = translation.tier
                    metric.sync_enabled = True
                    metric.sync_failure_reason = None
                    metric.llm_self_reported_confidence = translation.llm_self_reported_confidence
                    metric.validation_notes = list(translation.validation_notes or [])
                    _apply_translation_advisories(metric, translation)
                    resolved_this_pass += 1

            if resolved_this_pass == 0:
                break

            logger.info(
                "OSI->SML dependency resolution pass %d resolved %d metrics",
                pass_idx + 1,
                resolved_this_pass,
            )

    # Real incident: a placeholder reason set BEFORE the Tier-5 batch call
    # even runs ("DAX translation deferred to Tier-5 batch (Tier N)", see
    # _convert_metric) is only ever cleared on SUCCESS -- a metric the
    # batch (and every later convergence pass) still couldn't resolve keeps
    # showing that placeholder forever, which reads as "still pending"
    # long after the attempt is over and reveals nothing about why it
    # actually failed. Matched by substring, not equality, since the
    # placeholder's own tier number varies.
    _STALE_TIER5_DEFERRED_MARKER = "deferred to Tier-5 batch"

    def _replace_stale_tier5_deferred_reason(self, sml: SMLModel) -> None:
        """Replace the stale Tier-5-deferred placeholder on any metric
        still unresolved after the batch call and every convergence pass,
        with an honest, general (never metric-name-keyed) explanation:

        - If the metric's own DAX references another metric that itself
          has no sql_expression, report THAT as the cause — the real,
          general shape behind '% Unit Market Share YOY Change' ([% Units
          Market Share]-[% Units Market Share SPLY]): this metric's own
          bracket-arithmetic DAX has nothing wrong with it, it simply
          can't substitute a sibling ('% Units Market Share SPLY') that
          never got SQL of its own (a deterministic-renderer limitation
          for CALCULATE(SAMEPERIODLASTYEAR)-wrapping-a-ratio-measure —
          see dax_translator.py's _try_dependency_translation and
          tmsl_to_sml.py's TIME_INTELLIGENCE failure-reason map).
        - Otherwise, the metric's OWN Tier-5 batch attempt is what
          produced nothing usable — say that plainly instead of the
          stale "deferred" wording.
        """
        if not sml.metrics:
            return

        by_name = {
            str(m.unique_name or "").casefold(): m
            for m in sml.metrics
            if getattr(m, "unique_name", None)
        }

        for metric in sml.metrics:
            reason = metric.sync_failure_reason or ""
            if metric.sql_expression or self._STALE_TIER5_DEFERRED_MARKER not in reason:
                continue

            unresolved_deps = []
            for ref in re.findall(r"\[([^\]]+)\]", metric.expression or ""):
                dep = by_name.get(ref.strip().casefold())
                if dep is not None and dep is not metric and not dep.sql_expression:
                    unresolved_deps.append(dep)

            if unresolved_deps:
                dep_text = "; ".join(
                    f"'{dep.unique_name}' ({dep.sync_failure_reason or 'no SQL yet'})"
                    for dep in unresolved_deps
                )
                metric.sync_failure_reason = (
                    f"Cannot compute — depends on unresolved metric(s): {dep_text}. "
                    f"This metric's own DAX has no translation issue of its own."
                )
            else:
                metric.sync_failure_reason = (
                    "Tier-5 (LLM) batch translation was attempted for this metric and "
                    "did not produce a usable result."
                )

    def _decompose_disconnected_selector_metrics(
        self,
        sml: SMLModel,
        dataset_col_lookup: Optional[Dict[str, set]] = None,
        dataset_aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        """For any metric whose DAX matches the disconnected-selector-table
        IF-chain shape (see dax_ast_parser.try_decompose_disconnected_
        selector_metric), create one new, standalone metric per
        decomposable branch -- each translated and deployed exactly like
        any ordinary metric -- and record an advisory on the original
        pointing at its new companions.

        Deliberately NOT gated on translation having already failed: this
        codebase's own AST renderer doesn't validate cross-table
        reachability for an inlined measure reference, so a metric
        matching this shape typically translates "successfully"
        (sql_expression populated, sync_enabled=True) by this codebase's
        own check even though it's genuinely broken -- confirmed against
        a real customer deploy where Snowflake rejected exactly this shape
        at DDL-execution time (error 010211) despite dry-run showing it as
        clean. The detector itself (via has_relationship_path) is what
        makes this safe to attempt broadly: it declines unless every
        non-trivial branch's table is genuinely unreachable from the
        metric's own dataset.

        The original metric's sync_enabled/sql_expression are left
        completely untouched either way (matching
        _flag_unreachable_dimension_calculates' same discipline) -- only
        advisory_categories/advisory_notes change. When a decomposition
        succeeds, the now-redundant, less-specific
        ADVISORY_CATEGORY_UNREACHABLE_DIMENSION entry (if 4a already added
        one) is removed and replaced with this one, since a working set of
        replacement metrics is a strictly more actionable diagnosis than a
        bare "this will fail, no real fix" note.

        Only ever ADDS metrics to sml.metrics; never mutates or removes an
        existing one apart from its own advisory fields, so this cannot
        change what any other metric does.
        """
        from semabridge.converter.dax_ast_parser import (
            try_decompose_disconnected_selector_metric,
            ADVISORY_CATEGORY_UNREACHABLE_DIMENSION,
        )

        if not sml.metrics:
            return

        metric_datasets = {m.unique_name: m.dataset for m in sml.metrics if m.dataset}
        existing_names_cf = {m.unique_name.casefold() for m in sml.metrics}

        for metric in list(sml.metrics):  # snapshot -- this loop appends to sml.metrics
            if not metric.expression or not metric.dataset:
                continue

            try:
                decomposition = try_decompose_disconnected_selector_metric(
                    metric.expression, metric.dataset, sml.relationships, metric_datasets,
                )
            except Exception as exc:  # noqa: BLE001 - best-effort, never fail conversion over it
                logger.debug(
                    "Disconnected-selector decomposition skipped for '%s' (non-fatal): %s",
                    metric.unique_name, exc,
                )
                continue
            if not decomposition:
                continue

            companion_names: List[str] = []
            for index, branch in enumerate(decomposition.branches, start=1):
                base_name = f"{metric.unique_name}_BRANCH_{index}"
                new_name = base_name
                suffix = 2
                while new_name.casefold() in existing_names_cf:
                    new_name = f"{base_name}_{suffix}"
                    suffix += 1
                existing_names_cf.add(new_name.casefold())

                new_metric = SMLMetric(
                    unique_name=new_name,
                    label=f"{metric.label or metric.unique_name} ({branch.condition_dax})",
                    description=(
                        f"Auto-generated from '{metric.unique_name}' -- the value shown "
                        f"when {branch.condition_dax}."
                    ),
                    dataset=branch.branch_dataset,
                    expression=branch.branch_dax,
                    aggregation=SMLAggregationType.NONE,
                    complexity_tier=1,
                    sync_enabled=True,
                )

                safe_alias = to_alias(branch.branch_dataset)
                translation = self.dax_translator.translate(
                    branch.branch_dax,
                    safe_alias,
                    branch.branch_dataset,
                    metric_name=new_metric.unique_name,
                    metrics_context=sml.metrics,
                    dataset_col_lookup=dataset_col_lookup,
                    dataset_aliases=dataset_aliases,
                    skip_tier5=False,
                )
                if translation.is_success:
                    new_metric.sql_expression = translation.sql
                    new_metric.complexity_tier = translation.tier
                    new_metric.sync_enabled = True
                    new_metric.sync_failure_reason = None
                    new_metric.llm_self_reported_confidence = translation.llm_self_reported_confidence
                    new_metric.validation_notes = list(translation.validation_notes or [])
                    _apply_translation_advisories(new_metric, translation)
                else:
                    new_metric.sync_enabled = False
                    new_metric.sync_failure_reason = (
                        f"Auto-generated branch of '{metric.unique_name}' could not be "
                        "translated on its own either."
                    )

                new_metric.advisory_categories.append(ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED)
                new_metric.advisory_notes.append(
                    f"Auto-generated from '{metric.unique_name}', which mixes a selector "
                    f"value from '{decomposition.selector_table}' with an aggregate from "
                    "a different, unrelated table in one expression -- something "
                    "Snowflake cannot compile as a single metric. This is the real "
                    f"value for the '{branch.condition_dax}' branch, standing alone."
                )
                sml.metrics.append(new_metric)
                companion_names.append(new_name)

            if companion_names:
                # Supersede 4a's generic advisory, if it already fired for
                # this metric -- remove the (note, category) pair together
                # (they're parallel, same-index lists) so this metric ends
                # up with exactly one, more specific/actionable diagnosis
                # instead of two overlapping ones.
                if ADVISORY_CATEGORY_UNREACHABLE_DIMENSION in metric.advisory_categories:
                    stale_index = metric.advisory_categories.index(ADVISORY_CATEGORY_UNREACHABLE_DIMENSION)
                    metric.advisory_categories.pop(stale_index)
                    if stale_index < len(metric.advisory_notes):
                        metric.advisory_notes.pop(stale_index)
                metric.advisory_categories.append(ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED)
                skipped_note = (
                    f" ({decomposition.skipped_trivial_count} branch(es) were a "
                    "constant value with nothing to translate, and were skipped)"
                    if decomposition.skipped_trivial_count else ""
                )
                metric.advisory_notes.append(
                    f"This metric mixes a selector value from '{decomposition.selector_table}' "
                    "with an aggregate from a different, unrelated table in one "
                    "expression, which Snowflake cannot compile as a single metric. "
                    f"It has been decomposed into standalone metrics: "
                    f"{', '.join(companion_names)}{skipped_note}. Recreate the "
                    "original selector logic in your reporting layer using these "
                    "instead."
                )

    def _flag_unreachable_dimension_calculates(self, sml: SMLModel) -> None:
        """Advisory-only: append a well-explained note to any metric whose
        DAX contains CALCULATE(...) filtering by a table its own dataset
        cannot reach via sml.relationships — the shape Snowflake's
        semantic-view compiler rejects as "a metric cannot refer to
        another dimension from an unrelated entity" (error 010211),
        regardless of whether translation itself succeeds (it can, and
        did, for the case this was written for — see
        dax_calculate_filters_unreachable_dimension's module-level
        comment: rewriting the SQL text doesn't fix which table the
        metric is declared under).

        Deliberately advisory, not corrective: this NEVER sets
        sync_enabled=False, never clears/overwrites sql_expression or
        sync_failure_reason, and never removes the metric from
        sml.metrics. The metric proceeds through translation and DDL
        emission exactly as it would without this check.

        IMPORTANT — this is NOT caught by DDL-deployment-time auto-
        remediation. connection_manager.py's _extract_invalid_identifier()
        only pattern-matches Snowflake's "invalid identifier '...'"
        (error 000904) and "Invalid metric definition for '...'"
        (error 010220) message shapes; it does not recognize error 010211
        ("a metric cannot refer to another dimension from an unrelated
        entity"), so a metric hitting this case that reaches a real
        deploy will most likely abort the whole DDL statement rather than
        being cleanly caught and dropped. This advisory note — surfaced
        with its structural category via SMLMetric.advisory_categories
        (see ADVISORY_CATEGORY_UNREACHABLE_DIMENSION) into a distinct
        "predicted_failure" status by project_mapping_engine.py — is
        therefore the ONLY pre-deploy signal for this failure class today.
        A user inspecting the model at mapping/dry-run time sees why,
        before spending a deploy attempt to find out.
        """
        from semabridge.converter.dax_ast_parser import dax_calculate_filters_unreachable_dimension

        if not sml.metrics:
            return

        metric_datasets = {m.unique_name: m.dataset for m in sml.metrics if m.dataset}

        for metric in sml.metrics:
            if not metric.expression or not metric.dataset:
                continue
            try:
                hit = dax_calculate_filters_unreachable_dimension(
                    metric.expression,
                    metric.dataset,
                    sml.relationships,
                    metric_datasets=metric_datasets,
                )
            except Exception as exc:  # noqa: BLE001 - advisory-only, never fail conversion over it
                logger.debug(
                    "Unreachable-dimension advisory check skipped for '%s' (non-fatal): %s",
                    metric.unique_name, exc,
                )
                continue
            if hit:
                metric.advisory_notes.append(hit.reason)
                metric.advisory_categories.append(hit.category)

    def _flag_reachable_dimension_filters_pending_enrichment(self, sml: SMLModel) -> None:
        """Advisory-only, mirrors _flag_unreachable_dimension_calculates
        but for the opposite reachability outcome: a metric whose
        CALCULATE(...) filters a table that DOES have a relationship path,
        yet whose translation still failed today — dry-run has no live
        connection, so it can't exploit the same precomputed-column
        rewrite that resolves this shape at real-deploy time (see
        dax_calculate_filters_reachable_dimension_pending_enrichment's
        docstring). A real deploy may well succeed where dry-run could
        not, so this is surfaced as STATUS_NEEDS_REVIEW, not
        STATUS_PREDICTED_FAILURE (see project_mapping_engine.py).

        Only fires for a metric whose translation actually failed — if
        some tier already produced valid SQL, this note would be
        misleading noise, not a signal. Never touches sync_enabled/
        sync_failure_reason/sql_expression itself.
        """
        from semabridge.converter.dax_ast_parser import (
            dax_calculate_filters_reachable_dimension_pending_enrichment,
        )

        if not sml.metrics:
            return

        for metric in sml.metrics:
            if not metric.expression or not metric.dataset:
                continue
            if metric.sql_expression:
                continue  # translation already succeeded -- nothing to flag
            if metric.sync_enabled is not False and not str(metric.sync_failure_reason or "").strip():
                continue  # not actually a failed metric
            if ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED in metric.advisory_categories:
                continue  # already handled with a more specific diagnosis -- see the sibling check above
            try:
                hit = dax_calculate_filters_reachable_dimension_pending_enrichment(
                    metric.expression,
                    metric.dataset,
                    sml.relationships,
                )
            except Exception as exc:  # noqa: BLE001 - advisory-only, never fail conversion over it
                logger.debug(
                    "Pending-enrichment advisory check skipped for '%s' (non-fatal): %s",
                    metric.unique_name, exc,
                )
                continue
            if hit:
                metric.advisory_notes.append(hit.reason)
                metric.advisory_categories.append(hit.category)

    def _convert_relationship(self, osi_rel: OSIRelationship) -> Optional[SMLRelationship]:
        try:
             sml_card = getattr(SMLCardinality, osi_rel.cardinality.name, SMLCardinality.MANY_TO_ONE)
             sml_cf = getattr(SMLCrossFilterDirection, osi_rel.cross_filter_direction.name, SMLCrossFilterDirection.SINGLE)

             return SMLRelationship(
                 unique_name=osi_rel.unique_name,
                 from_dataset=osi_rel.from_dataset,
                 from_columns=osi_rel.from_columns,
                 to_dataset=osi_rel.to_dataset,
                 to_columns=osi_rel.to_columns,
                 cardinality=sml_card,
                 cross_filter=sml_cf,
                 is_active=osi_rel.is_active
             )
        except Exception as e:
            logger.warning(f"Failed to convert relationship {osi_rel.unique_name}: {e}")
            return None

    def _inject_calendar_dimension(self, sml: SMLModel) -> None:
        # Same logic as TMSLTransformer
        if any(is_calendar_like_name(ds.unique_name) for ds in sml.datasets):
            return
            
        # Create standard Date column definitions
        cols = [
            SMLColumn(unique_name="Date", data_type=SMLDataType.DATE, is_key=True),
            SMLColumn(unique_name="Year", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="Quarter", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="Month", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="MonthName", data_type=SMLDataType.STRING),
            SMLColumn(unique_name="DayOfWeek", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="DayName", data_type=SMLDataType.STRING),
        ]
        
        date_ds = SMLDataset(
            unique_name="Date",
            label="Date",
            description="Auto-generated Calendar Dimension",
            source_table="DIM_DATE",
            columns=cols,
            is_fact=False
        )
        sml.datasets.append(date_ds)

    def _auto_detect_metrics(
        self,
        sml: SMLModel,
        row_counts: Optional[Dict[str, int]] = None,
    ) -> None:
        """Backfill simple metrics when the source provided none explicitly."""
        if sml.metrics:
            return

        tables_meta = {
            ds.unique_name: {"row_count": (row_counts or {}).get(ds.unique_name, 0)}
            for ds in sml.datasets
        }
        columns_meta = {
            ds.unique_name: [
                {"name": col.unique_name, "data_type": col.data_type.value}
                for col in ds.columns
            ]
            for ds in sml.datasets
        }
        relationships_meta = [
            {
                "from_table": rel.from_dataset,
                "from_column": rel.from_columns[0] if rel.from_columns else "",
                "to_table": rel.to_dataset,
                "to_column": rel.to_columns[0] if rel.to_columns else "",
            }
            for rel in sml.relationships
            if rel.from_columns and rel.to_columns
        ]

        classifier = SmlInferenceEngine(
            tables=tables_meta,
            columns=columns_meta,
            relationships=relationships_meta,
            primary_keys={},
        )
        scores = classifier.classify()

        classification_map: Dict[str, str] = {}
        for ds in sml.datasets:
            score = scores.get(ds.unique_name)
            if score:
                ds.is_fact = score.classification == "FACT"
                classification_map[ds.unique_name] = score.classification
            else:
                classification_map[ds.unique_name] = "FACT" if ds.is_fact else "DIMENSION"

        detector = MeasureDetector(
            tables=tables_meta,
            columns=columns_meta,
            relationships=relationships_meta,
        )
        detected = detector.detect_all_measures(classification=classification_map)
        existing_metrics = {metric.unique_name.upper() for metric in sml.metrics}

        added = 0
        for table_name, measures in detected.items():
            for measure in measures:
                measure_name = str(measure.get("name", "")).strip()
                column_name = str(measure.get("column", "")).strip()
                if not measure_name or not column_name:
                    continue
                if measure_name.upper() in existing_metrics:
                    continue

                agg = SMLAggregationType(measure["aggregation"])
                aggregation_sql = agg.value.upper()
                table_alias = to_alias(table_name)

                sml.metrics.append(
                    SMLMetric(
                        unique_name=measure_name,
                        label=measure_name,
                        dataset=table_name,
                        source_column=column_name,
                        expression=f"{aggregation_sql}([{column_name}])",
                        sql_expression=f'{aggregation_sql}({table_alias}."{column_name}")',
                        aggregation=agg,
                        confidence=float(measure.get("confidence", 0.7)),
                        sync_enabled=True,
                        complexity_tier=1,
                    )
                )
                existing_metrics.add(measure_name.upper())
                added += 1

        if added:
            logger.info("Auto-detected %d simple metrics because OSI contained no explicit measures", added)
