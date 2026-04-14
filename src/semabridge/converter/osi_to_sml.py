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
from semabridge.connectors.inference_engine import SmlInferenceEngine
from semabridge.connectors.measure_detector import MeasureDetector
from semabridge.utils.logger import get_logger
from semabridge.utils.naming import to_alias

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

            # 3. Convert Metrics with automated translation pipeline
            # Step 3a: Convert all metrics individually for Tier 1-4 translations
            
            for osi_metric in osi_model.metrics:
                sml_metric = self._convert_metric(osi_metric)
                sml.metrics.append(sml_metric)

            # Step 3b: Resolve inter-measure dependencies now that all metrics are loaded.
            # This helps Category 2/3 formulas like [A]-[B], DIVIDE([A],[B]), TOTALYTD([A], ...)
            # when dependencies appear later in source order.
            self._resolve_metric_dependencies(sml)

            # Step 3c: Collect unresolved metrics for Tier-5 batch fallback.
            tier5_candidates = []  # (metric_name, dax, table_alias, dataset_name)
            for sml_metric in sml.metrics:
                if (
                    not sml_metric.sql_expression
                    and sml_metric.expression
                    and sml_metric.expression.strip()
                ):
                    dax = sml_metric.expression.strip()
                    table_alias = to_alias(sml_metric.dataset)
                    tier5_candidates.append((sml_metric.unique_name, dax, table_alias, sml_metric.dataset))
            
            # Step 3d: Batch translate all Tier 5 candidates at once (reduces API calls by 90%)
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

    def _convert_metric(self, osi_metric: OSIMetric) -> SMLMetric:
        # DAX Translation Logic
        expression = osi_metric.expression or ""

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
        
        # Attempt Translation using automated deterministic/AST/rule/LLM tiers
        if expression:
            from semabridge.utils.naming import to_alias
            safe_alias = to_alias(osi_metric.dataset)
            translation = self.dax_translator.translate(
                expression,
                safe_alias,
                osi_metric.dataset,
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

    def _resolve_metric_dependencies(self, sml: SMLModel, max_passes: int = 3) -> None:
        """Resolve unresolved metrics using full metric context in deterministic passes.

        This performs lightweight topological convergence: each pass can unlock
        downstream expressions once upstream measures receive SQL.
        """
        if not sml.metrics:
            return

        for pass_idx in range(max_passes):
            resolved_this_pass = 0
            for metric in sml.metrics:
                if metric.sql_expression or not (metric.expression or "").strip():
                    continue

                safe_alias = to_alias(metric.dataset)
                translation = self.dax_translator.translate(
                    metric.expression,
                    safe_alias,
                    metric.dataset,
                    metric_name=metric.unique_name,
                    metrics_context=sml.metrics,
                )
                if translation.is_success and translation.sql:
                    metric.sql_expression = translation.sql
                    metric.complexity_tier = translation.tier
                    metric.sync_enabled = True
                    metric.sync_failure_reason = None
                    resolved_this_pass += 1

            if resolved_this_pass == 0:
                break

            logger.info(
                "OSI->SML dependency resolution pass %d resolved %d metrics",
                pass_idx + 1,
                resolved_this_pass,
            )

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