"""CSM to SML Converter - Adds target-specific translations."""

from semabridge.csm.models import CSMModel, CSMMetric
from semabridge.sml.models import SMLModel, SMLMetric
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class CsmToSmlConverter:
    """Convert CSM to SML - preserve source, add translations."""
    
    def __init__(self, translator=None):
        self.translator = translator
    
    def convert(self, csm_model: CSMModel) -> SMLModel:
        logger.info(f"Converting CSM to SML: {csm_model.unique_name}")
        
        # Convert datasets (preserve structure)
        datasets = [self._convert_dataset(ds) for ds in csm_model.datasets]
        
        # Convert metrics with SQL translation
        metrics = [self._convert_metric(m) for m in csm_model.metrics]
        
        # Convert dimensions and relationships
        dimensions = [self._convert_dimension(d) for d in csm_model.dimensions]
        relationships = [self._convert_relationship(r) for r in csm_model.relationships]
        
        return SMLModel(
            unique_name=csm_model.unique_name,
            label=csm_model.label,
            description=csm_model.description,
            version=csm_model.version,
            datasets=datasets,
            metrics=metrics,
            dimensions=dimensions,
            relationships=relationships,
            source_system=csm_model.source_platform,
            source_platform=csm_model.source_platform,
        )
    
    def _convert_dataset(self, ds):
        """Convert CSMDataset to SMLDataset."""
        from semabridge.sml.models import SMLDataset, SMLColumn
        
        return SMLDataset(
            unique_name=ds.unique_name,
            label=ds.label,
            description=ds.description,
            source_table=ds.source_table,
            source_schema=ds.source_schema or "",
            source_database=ds.source_database or "",
            columns=[
                SMLColumn(
                    unique_name=col.unique_name,
                    label=col.label,
                    data_type=self._map_data_type(col.data_type),
                    source_type=col.data_type,
                    description=col.description,
                    is_hidden=col.is_hidden,
                    is_key=col.is_key,
                    is_measure_candidate=col.is_measure_candidate,
                    format_string=col.format_string,
                    synonyms=col.synonyms,
                    is_enum=col.is_enum,
                    cortex_search_service=col.cortex_search_service,
                    sample_values=col.sample_values,
                )
                for col in ds.columns
            ],
            is_hidden=ds.is_hidden,
            is_fact=ds.is_fact,
            row_count=ds.row_count,
        )
    
    def _convert_metric(self, m: CSMMetric) -> SMLMetric:
        """Convert CSMMetric to SMLMetric - add SQL translation."""
        
        # Translate DAX to SQL if translator available
        sql_expr = m.sql_expression
        if not sql_expr and self.translator and m.expression:
            try:
                result = self.translator.translate(
                    dax=m.expression,
                    table_alias=m.dataset.upper(),
                    dataset_name=m.dataset,
                    metric_name=m.unique_name,
                )
                if result and result.sql:
                    sql_expr = result.sql
                    logger.debug(f"Translated {m.unique_name}: {sql_expr[:80]}...")
            except Exception as e:
                logger.warning(f"Translation failed for {m.unique_name}: {e}")
        
        from semabridge.sml.models import SMLMetric
        
        return SMLMetric(
            unique_name=m.unique_name,
            label=m.label,
            description=m.description,
            dataset=m.dataset,
            expression=m.expression,  # Original DAX preserved
            sql_expression=sql_expr,   # Translated SQL
            aggregation=m.aggregation.value,
            source_column=m.source_column,
            format_string=m.format_string,
            is_hidden=m.is_hidden,
            complexity_tier=m.complexity_tier,
            depends_on_measures=m.depends_on_measures,
            sync_enabled=m.sync_enabled,
            sync_failure_reason=m.sync_failure_reason,
            synonyms=m.synonyms,
        )
    
    def _convert_dimension(self, d):
        """Convert CSMDimension to SMLDimension."""
        from semabridge.sml.models import SMLDimension, SMLAttribute
        
        return SMLDimension(
            unique_name=d.unique_name,
            label=d.label,
            description=d.description,
            dataset=d.dataset,
            attributes=[
                SMLAttribute(
                    unique_name=attr.unique_name,
                    label=attr.label,
                    dataset=attr.dataset,
                    dataset_column=attr.dataset_column,
                    description=attr.description,
                    is_hidden=attr.is_hidden,
                )
                for attr in d.attributes
            ],
            is_hidden=d.is_hidden,
        )
    
    def _convert_relationship(self, r):
        """Convert CSMRelationship to SMLRelationship."""
        from semabridge.sml.models import SMLRelationship
        
        return SMLRelationship(
            unique_name=r.unique_name,
            from_dataset=r.from_dataset,
            from_columns=r.from_columns,
            to_dataset=r.to_dataset,
            to_columns=r.to_columns,
            cardinality=r.cardinality.value,
            cross_filter=r.cross_filter_direction,
            is_active=r.is_active,
        )
    
    def _map_data_type(self, osi_type: str) -> str:
        mapping = {
            "string": "string",
            "integer": "integer",
            "float": "float",
            "double": "float",
            "decimal": "decimal",
            "boolean": "boolean",
            "date": "date",
            "datetime": "datetime",
        }
        return mapping.get(osi_type.lower(), "string")
