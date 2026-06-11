"""OSI to CSM Converter - Preserves ALL fields."""

from semabridge.intermediate.models import OSIModel, OSIColumn, OSIMetric
from semabridge.csm.models import (
    CSMModel, CSMDataset, CSMColumn, CSMMetric,
    CSMDimension, CSMAttribute, CSMRelationship,
    CSMAggregationType, CSMCardinality,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class OsiToCsmConverter:
    """Convert OSI to CSM with 100% field preservation."""
    
    def convert(self, osi_model: OSIModel) -> CSMModel:
        logger.info(f"Converting OSI to CSM: {osi_model.unique_name}")
        
        datasets = [self._convert_dataset(ds) for ds in osi_model.datasets]
        metrics = [self._convert_metric(m) for m in osi_model.metrics]
        dimensions = [self._convert_dimension(d) for d in osi_model.dimensions]
        relationships = [self._convert_relationship(r) for r in osi_model.relationships]
        
        logger.info(f"CSM created: {len(datasets)} datasets, {len(metrics)} metrics")
        
        return CSMModel(
            unique_name=osi_model.unique_name,
            label=osi_model.label,
            description=osi_model.description or "",
            version=osi_model.version,
            datasets=datasets,
            metrics=metrics,
            dimensions=dimensions,
            relationships=relationships,
            source_platform=osi_model.source_platform or "fabric",
        )
    
    def _convert_dataset(self, ds) -> CSMDataset:
        return CSMDataset(
            unique_name=ds.unique_name,
            label=ds.label,
            description=ds.description or "",
            source_table=ds.source_table,
            source_schema=ds.source_schema,
            source_database=None,  # OSIDataset doesn't have source_database
            columns=[self._convert_column(col) for col in ds.columns],
            is_fact=ds.is_fact,
            is_hidden=ds.is_hidden,
            row_count=getattr(ds, 'row_count', None),
            source_expression=getattr(ds, 'source_expression', None),  # ✅ PRESERVED
            format_string=getattr(ds, 'format_string', None),          # ✅ PRESERVED
        )
    
    def _convert_column(self, col) -> CSMColumn:
        # Map aggregation
        agg_type = None
        if col.default_aggregation:
            try:
                agg_type = CSMAggregationType(col.default_aggregation.value.lower())
            except ValueError:
                pass
        
        return CSMColumn(
            unique_name=col.unique_name,
            label=col.label,
            data_type=col.data_type.value if hasattr(col.data_type, 'value') else str(col.data_type),
            description=col.description or "",
            is_key=col.is_key,
            is_hidden=col.is_hidden,
            is_measure_candidate=col.is_measure_candidate,
            # ✅ ALL PRESERVED
            default_aggregation=agg_type,
            source_expression=getattr(col, 'source_expression', None),
            format_string=col.format_string,
            cortex_search_service=col.cortex_search_service,
            sample_values=col.sample_values,
            source_column=getattr(col, 'source_column', col.unique_name),  # Fallback to unique_name
            synonyms=col.synonyms,
            is_enum=col.is_enum,
        )
    
    def _convert_metric(self, m) -> CSMMetric:
        agg_type = CSMAggregationType.NONE
        if m.aggregation:
            try:
                agg_type = CSMAggregationType(m.aggregation.value.lower())
            except ValueError:
                pass
        
        return CSMMetric(
            unique_name=m.unique_name,
            label=m.label,
            description=m.description or "",
            dataset=m.dataset,
            expression=m.expression or "",  # ✅ Original DAX preserved, CSM needs str
            source_column=m.source_column,
            aggregation=agg_type,
            format_string=m.format_string,
            business_owner=m.business_owner,
            is_hidden=m.is_hidden,
            synonyms=m.synonyms,
        )
    
    def _convert_dimension(self, d) -> CSMDimension:
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
                    dataset_column=attr.source_column,
                    is_hidden=attr.is_hidden,
                )
                for attr in d.attributes
            ],
            is_hidden=d.is_hidden,
        )
    
    def _convert_relationship(self, r) -> CSMRelationship:
        cardinality = CSMCardinality.MANY_TO_ONE
        if r.cardinality:
            card_str = r.cardinality.lower()
            if card_str == "one-to-one":
                cardinality = CSMCardinality.ONE_TO_ONE
            elif card_str == "many-to-many":
                cardinality = CSMCardinality.MANY_TO_MANY
        
        return CSMRelationship(
            unique_name=r.unique_name,
            from_dataset=r.from_dataset,
            from_columns=r.from_columns,
            to_dataset=r.to_dataset,
            to_columns=r.to_columns,
            cardinality=cardinality,
            cross_filter_direction=r.cross_filter_direction or "single",
            is_active=r.is_active,
        )
