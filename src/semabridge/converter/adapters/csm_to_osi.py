"""CSM to OSI Converter."""

from typing import Dict, Any

from semabridge.intermediate.models import OSIModel, OSIDataset, OSIColumn, OSIMetric
from semabridge.csm.models import (
    CSMModel, CSMDataset, CSMColumn, CSMMetric,
    CSMDimension, CSMAttribute, CSMRelationship,
    CSMAggregationType, CSMCardinality,
)

class CSMToOSIConverter:
    def convert(self, csm_model: CSMModel) -> OSIModel:
        datasets = [self._convert_dataset(ds) for ds in csm_model.datasets]
        metrics = [self._convert_metric(m) for m in csm_model.metrics]
        
        extensions: Dict[str, Any] = getattr(csm_model, "extensions", {}).copy()
        
        # Provide relationships and dimensions as kwargs to avoid issues if OSIModel isn't fully updated
        kwargs = {}
        if getattr(csm_model, "dimensions", None) is not None:
            kwargs["dimensions"] = csm_model.dimensions
        if getattr(csm_model, "relationships", None) is not None:
            kwargs["relationships"] = csm_model.relationships
        
        return OSIModel(
            unique_name=csm_model.unique_name,
            label=csm_model.label,
            description=csm_model.description,
            version=csm_model.version,
            datasets=datasets,
            metrics=metrics,
            source_platform=csm_model.source_platform,
            metadata=extensions,
            **kwargs
        )

    def _convert_dataset(self, ds: CSMDataset) -> OSIDataset:
        extensions = getattr(ds, "extensions", {}).copy()
        
        return OSIDataset(
            unique_name=ds.unique_name,
            label=ds.label,
            description=ds.description,
            source_table=ds.source_table,
            source_schema=ds.source_schema,
            columns=[self._convert_column(col) for col in ds.columns],
            is_fact=ds.is_fact,
            is_hidden=ds.is_hidden,
            # OSI uses metadata instead of extensions for custom data, though OSIDataset doesn't have metadata natively.
            # Assuming OSI doesn't explicitly store extensions on objects besides OSIModel
        )

    def _convert_column(self, col: CSMColumn) -> OSIColumn:
        extensions = getattr(col, "extensions", {}).copy()
        
        from semabridge.intermediate.models import OSIDataType, OSIAggregationType
        try:
            osi_data_type = OSIDataType(col.data_type.lower())
        except ValueError:
            osi_data_type = getattr(OSIDataType, col.data_type.upper(), OSIDataType.STRING)

        agg_type = None
        if getattr(col, "default_aggregation", None):
            try:
                agg_type = OSIAggregationType(col.default_aggregation.value.lower())
            except ValueError:
                pass

        return OSIColumn(
            unique_name=col.unique_name,
            label=col.label,
            data_type=osi_data_type,
            description=col.description,
            is_key=col.is_key,
            is_hidden=col.is_hidden,
            is_measure_candidate=col.is_measure_candidate,
            source_expression=col.source_expression,
            default_aggregation=agg_type,
            format_string=col.format_string,
            cortex_search_service=col.cortex_search_service,
            sample_values=col.sample_values,
            synonyms=col.synonyms,
            is_enum=col.is_enum,
        )

    def _convert_metric(self, m: CSMMetric) -> OSIMetric:
        extensions = getattr(m, "extensions", {}).copy()
        
        expression = m.expression_dialects.get("DAX", "")
        sql_expression = m.expression_dialects.get("SNOWFLAKE_SQL", "")
        
        return OSIMetric(
            unique_name=m.unique_name,
            label=m.label,
            description=m.description,
            dataset=m.dataset,
            expression=expression,
            source_column=m.source_column,
            format_string=m.format_string,
            business_owner=m.business_owner,
            is_hidden=m.is_hidden,
            synonyms=m.synonyms,
        )
