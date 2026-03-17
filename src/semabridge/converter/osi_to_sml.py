"""
OSI to SML Converter.

Converts OSI (Open Semantic Interchange) canonical models into the SML (Semantic Modeling Language)
intermediate representation, applying semantic enrichment like DAX translation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
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
from semabridge.utils.logger import get_logger
from semabridge.utils.naming import (
    to_alias,
    build_alias_rewrite_map,
    extract_prefixes_from_expressions,
    infer_override_alias_map,
    sanitize_sql_expression,
)

logger = get_logger(__name__)


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
        metric_overrides: Optional[Dict[str, str]] = None,
        override_alias_map: Optional[Dict[str, str]] = None,
    ) -> SMLModel:
        """
        Convert OSIModel object to SMLModel object.

        Args:
            osi_model: OSIModel object
            metric_overrides: Optional dict of metric_name -> SQL override string,
                sourced from behavior.yaml policy. Takes precedence over DAX translation.
            override_alias_map: Optional dict of ``{SHORT_PREFIX: logical_dataset_name}``
                used to pre-sanitize metric_overrides before the DAX translator sees
                them.  Sourced from ``semantic_model.override_alias_map`` in
                behavior.yaml.

        Returns:
            SMLModel object
        """
        raw_overrides = metric_overrides or {}

        # ── Pre-sanitize metric_overrides ─────────────────────────────────
        # Derive dataset aliases from the OSI model so we can rewrite any
        # invalid prefixes in override SQL before they propagate downstream.
        dataset_aliases: Dict[str, str] = {
            ds.unique_name: to_alias(ds.unique_name)
            for ds in osi_model.datasets
        }

        # Build extra_prefix_map: user-declared + auto-inferred short aliases
        declared_extra: Dict[str, str] = {}
        if override_alias_map:
            for short_prefix, logical_name in override_alias_map.items():
                declared_extra[short_prefix.upper()] = to_alias(logical_name)

        if raw_overrides:
            override_exprs = list(raw_overrides.values())
            discovered = extract_prefixes_from_expressions(override_exprs)
            known_map = build_alias_rewrite_map(dataset_aliases)
            unknown = {
                p for p in discovered
                if p not in declared_extra and p not in known_map
            }
            if unknown:
                inferred = infer_override_alias_map(unknown, dataset_aliases)
                for prefix, alias_val in inferred.items():
                    declared_extra.setdefault(prefix, alias_val)
                for prefix in unknown - set(inferred):
                    logger.warning(
                        f"metric_overrides: prefix '{prefix}' could not be resolved. "
                        f"Add it to semantic_model.override_alias_map in behavior.yaml."
                    )

        extra_prefix_map: Optional[Dict[str, str]] = declared_extra if declared_extra else None

        # Sanitize each override expression
        overrides: Dict[str, str] = {}
        for name, sql in raw_overrides.items():
            sanitized = sanitize_sql_expression(
                expr=sql,
                dataset_aliases=dataset_aliases,
                extra_prefix_map=extra_prefix_map,
                force_uppercase=True,
            )
            overrides[name] = sanitized

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

            # 3. Convert Metrics (with DAX Translation + behavior overrides)
            # Step 3a: Convert all metrics individually for Tier 1-4 translations
            tier5_candidates = []  # (metric_name, dax, table_alias, dataset_name)
            
            for osi_metric in osi_model.metrics:
                sml_metric = self._convert_metric(osi_metric, overrides=overrides)
                sml.metrics.append(sml_metric)
                
                # If metric still has no SQL, collect for Tier-5 batch.
                # Do not gate on sync_enabled here; initial complexity heuristics
                # are conservative and can be recovered by LLM translation.
                if (not sml_metric.sql_expression and 
                    sml_metric.expression and sml_metric.expression.strip()):
                    dax = sml_metric.expression.strip()
                    table_alias = to_alias(sml_metric.dataset)
                    tier5_candidates.append((sml_metric.unique_name, dax, table_alias, sml_metric.dataset))
            
            # Step 3b: Batch translate all Tier 5 candidates at once (reduces API calls by 90%)
            if tier5_candidates:
                logger.info(f"📦 Batch translating {len(tier5_candidates)} Tier 5 metrics...")
                batch_results = self.dax_translator.batch_translate_tier5(tier5_candidates)
                
                # Apply batch translation results back to metrics
                for metric in sml.metrics:
                    if metric.unique_name in batch_results and batch_results[metric.unique_name]:
                        translation = batch_results[metric.unique_name]
                        if translation and translation.is_success:
                            metric.sql_expression = translation.sql
                            metric.complexity_tier = translation.tier
                            metric.sync_enabled = True
                            metric.sync_failure_reason = None
                            logger.debug(f"✓ Applied batch translation for '{metric.unique_name}'")

            # 4. Convert Relationships
            for osi_rel in osi_model.relationships:
                sml_rel = self._convert_relationship(osi_rel)
                if sml_rel:
                    sml.relationships.append(sml_rel)

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
            format_string=osi_col.format_string,
            # Cortex AI metadata (propagated from OSI)
            synonyms=list(osi_col.synonyms),
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

    def _convert_metric(self, osi_metric: OSIMetric, overrides: Optional[Dict[str, str]] = None) -> SMLMetric:
        # DAX Translation Logic
        expression = osi_metric.expression or ""
        metric_overrides = overrides or {}

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
        
        # Attempt Translation (behavior policy overrides take precedence as Tier 0)
        if expression:
            from semabridge.utils.naming import to_alias
            safe_alias = to_alias(osi_metric.dataset)
            translation = self.dax_translator.translate(
                expression,
                safe_alias,
                osi_metric.dataset,
                overrides=metric_overrides,
                metric_name=metric.unique_name
            )
            
            if translation.is_success:
                metric.sql_expression = translation.sql
                metric.complexity_tier = translation.tier
                metric.sync_enabled = True
            elif metric.sync_enabled:
                 metric.sync_failure_reason = f"DAX translation deferred to Tier-5 batch (Tier {translation.tier})"

        # Propagate Cortex AI metadata from OSI layer
        metric.access_modifier = osi_metric.access_modifier
        metric.synonyms = list(osi_metric.synonyms)

        return metric

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
        if any("DATE" in ds.unique_name.upper() or "CALENDAR" in ds.unique_name.upper() for ds in sml.datasets):
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

