"""SML to CSM Converter."""

from typing import Dict, Any

from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric
from semabridge.csm.models import (
    CSMModel, CSMDataset, CSMColumn, CSMMetric,
    CSMDimension, CSMAttribute, CSMRelationship,
    CSMAggregationType, CSMCardinality,
)

class SMLToCSMConverter:
    def convert(self, sml_model: SMLModel) -> CSMModel:
        datasets = [self._convert_dataset(ds) for ds in sml_model.datasets]
        metrics = [self._convert_metric(m) for m in sml_model.metrics]
        dimensions = [self._convert_dimension(d) for d in getattr(sml_model, "dimensions", [])]
        relationships = [self._convert_relationship(r) for r in getattr(sml_model, "relationships", [])]
        
        extensions: Dict[str, Any] = getattr(sml_model, "extensions", {}).copy()
        
        return CSMModel(
            unique_name=sml_model.unique_name,
            label=sml_model.label,
            description=sml_model.description or "",
            version=getattr(sml_model, "version", "1.0.0"),
            datasets=datasets,
            metrics=metrics,
            dimensions=dimensions,
            relationships=relationships,
            source_platform=getattr(sml_model, "source_platform", "snowflake"),
            extensions=extensions,
        )

    def _convert_dataset(self, ds) -> CSMDataset:
        extensions = getattr(ds, "extensions", {}).copy()
        return CSMDataset(
            unique_name=ds.unique_name,
            label=ds.label,
            description=ds.description or "",
            source_table=ds.source_table,
            source_schema=ds.source_schema,
            source_database=getattr(ds, "source_database", None),
            columns=[self._convert_column(col) for col in ds.columns],
            is_fact=ds.is_fact,
            is_hidden=ds.is_hidden,
            row_count=getattr(ds, 'row_count', None),
            extensions=extensions,
        )

    def _convert_column(self, col) -> CSMColumn:
        extensions = getattr(col, "extensions", {}).copy()
        
        return CSMColumn(
            unique_name=col.unique_name,
            label=col.label,
            data_type=getattr(col, 'source_type', getattr(col.data_type, 'value', str(col.data_type))) or "string",
            description=col.description or "",
            is_key=col.is_key,
            is_hidden=col.is_hidden,
            is_measure_candidate=col.is_measure_candidate,
            format_string=col.format_string,
            cortex_search_service=col.cortex_search_service,
            sample_values=col.sample_values,
            synonyms=col.synonyms,
            is_enum=col.is_enum,
            extensions=extensions,
        )

    def _convert_metric(self, m) -> CSMMetric:
        extensions = getattr(m, "extensions", {}).copy()
        agg_type = CSMAggregationType.NONE
        if getattr(m, 'aggregation', None):
            try:
                agg_type = CSMAggregationType(m.aggregation.lower())
            except ValueError:
                pass
        
        dialects = {}
        if getattr(m, 'expression', None):
            dialects["DAX"] = m.expression
        if getattr(m, 'sql_expression', None):
            dialects["SNOWFLAKE_SQL"] = m.sql_expression
            
        extensions["sml_sync_enabled"] = getattr(m, "sync_enabled", True)
            
        return CSMMetric(
            unique_name=m.unique_name,
            label=m.label,
            description=m.description or "",
            dataset=m.dataset,
            expression_dialects=dialects,
            source_column=m.source_column,
            aggregation=agg_type,
            format_string=m.format_string,
            is_hidden=m.is_hidden,
            synonyms=m.synonyms,
            extensions=extensions,
        )

    def _convert_dimension(self, d) -> CSMDimension:
        extensions = getattr(d, "extensions", {}).copy()
        return CSMDimension(
            unique_name=d.unique_name,
            label=d.label,
            description=d.description or "",
            dataset=d.dataset,
            attributes=[
                CSMAttribute(
                    unique_name=attr.unique_name,
                    label=attr.label,
                    description=getattr(attr, 'description', ""),
                    dataset=attr.dataset,
                    dataset_column=getattr(attr, 'dataset_column', attr.unique_name),
                    is_hidden=attr.is_hidden,
                )
                for attr in d.attributes
            ],
            hierarchies=[],
            is_hidden=d.is_hidden,
            extensions=extensions,
        )

    def _convert_relationship(self, r) -> CSMRelationship:
        extensions = getattr(r, "extensions", {}).copy()
        cardinality = CSMCardinality.MANY_TO_ONE
        if getattr(r, "cardinality", None):
            card_str = str(r.cardinality).lower()
            if "one-to-one" in card_str or "one_to_one" in card_str:
                cardinality = CSMCardinality.ONE_TO_ONE
            elif "many-to-many" in card_str or "many_to_many" in card_str:
                cardinality = CSMCardinality.MANY_TO_MANY
        
        return CSMRelationship(
            unique_name=r.unique_name,
            from_dataset=r.from_dataset,
            from_columns=r.from_columns,
            to_dataset=r.to_dataset,
            to_columns=r.to_columns,
            cardinality=cardinality,
            cross_filter_direction=getattr(r, "cross_filter", "single"),
            is_active=r.is_active,
            extensions=extensions,
        )
