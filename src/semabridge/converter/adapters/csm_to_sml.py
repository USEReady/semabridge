"""CSM to SML Converter."""

from typing import Dict, Any

from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric
from semabridge.csm.models import (
    CSMModel, CSMDataset, CSMColumn, CSMMetric,
    CSMDimension, CSMAttribute, CSMRelationship,
    CSMAggregationType, CSMCardinality,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class CSMToSMLConverter:
    def __init__(self, translator=None):
        self.translator = translator

    def convert(self, csm_model: CSMModel) -> SMLModel:
        datasets = [self._convert_dataset(ds) for ds in csm_model.datasets]
        metrics = []
        for m in csm_model.metrics:
            metrics.append(self._convert_metric(m, csm_model.metrics))
        
        return SMLModel(
            unique_name=csm_model.unique_name,
            label=csm_model.label,
            description=csm_model.description,
            version=csm_model.version,
            datasets=datasets,
            metrics=metrics,
            source_platform=csm_model.source_platform,
        )

    def _convert_dataset(self, ds: CSMDataset) -> SMLDataset:
        extensions = getattr(ds, "extensions", {}).copy()
        
        return SMLDataset(
            unique_name=ds.unique_name,
            label=ds.label,
            description=ds.description,
            source_table=ds.source_table,
            source_schema=ds.source_schema or "",
            source_database=ds.source_database or "",
            columns=[self._convert_column(col) for col in ds.columns],
            is_fact=ds.is_fact,
            is_hidden=ds.is_hidden,
            row_count=ds.row_count,
        )

    def _convert_column(self, col: CSMColumn) -> SMLColumn:
        extensions = getattr(col, "extensions", {}).copy()
        
        from semabridge.sml.models import DataType as SMLDataType
        try:
            sml_data_type = SMLDataType(col.data_type.lower())
        except ValueError:
            sml_data_type = getattr(SMLDataType, col.data_type.upper(), SMLDataType.STRING)

        return SMLColumn(
            unique_name=col.unique_name,
            label=col.label,
            data_type=sml_data_type,
            source_type=col.data_type,
            description=col.description,
            is_key=col.is_key,
            is_hidden=col.is_hidden,
            is_measure_candidate=col.is_measure_candidate,
            format_string=col.format_string,
            cortex_search_service=col.cortex_search_service,
            sample_values=col.sample_values,
            synonyms=col.synonyms,
            is_enum=col.is_enum,
        )

    def _convert_metric(self, m: CSMMetric, all_csm_metrics: list[CSMMetric] = None) -> SMLMetric:
        extensions = getattr(m, "extensions", {}).copy()
        
        dax = m.expression_dialects.get("DAX", "")
        sql_expr = m.expression_dialects.get("SNOWFLAKE_SQL", "")
        
        if not sql_expr and self.translator and dax:
            try:
                result = self.translator.translate(
                    dax=dax,
                    table_alias=m.dataset.upper(),
                    dataset_name=m.dataset,
                    metric_name=m.unique_name,
                    metrics_context=all_csm_metrics or [],
                )
                if result and result.sql:
                    sql_expr = result.sql
                    logger.debug(f"Translated {m.unique_name}: {sql_expr[:80]}...")
            except Exception as e:
                logger.warning(f"Translation failed for {m.unique_name}: {e}")
        
        return SMLMetric(
            unique_name=m.unique_name,
            label=m.label,
            description=m.description,
            dataset=m.dataset,
            expression=dax,
            sql_expression=sql_expr,
            aggregation=m.aggregation.value if m.aggregation else "none",
            source_column=m.source_column,
            format_string=m.format_string,
            is_hidden=m.is_hidden,
            synonyms=m.synonyms,
            sync_enabled=extensions.get("sml_sync_enabled", True),
        )
