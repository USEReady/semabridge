"""
SML to OSI Converter.

Converts SML (Semantic Modeling Language) intermediate representation into
OSI (Open Semantic Interchange) canonical models.
"""

from __future__ import annotations

from typing import Optional

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
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SMLToOSIConverter(BaseConverter):
    """
    Transforms SML Model into OSI Model.
    """

    def to_osi(self, sml_model: SMLModel) -> OSIModel:
        """
        Convert SMLModel object to OSIModel object.
        
        Args:
            sml_model: SMLModel object
            
        Returns:
            OSIModel object
        """
        try:
            # Determine source platform string
            source_platform = sml_model.source_platform.value if sml_model.source_platform else "unknown"
            
            osi = OSIModel(
                unique_name=sml_model.unique_name,
                label=sml_model.label,
                description=sml_model.description or None,
                version=sml_model.version,
                source_platform=source_platform,
            )
            
            # 1. Convert Datasets
            for sml_ds in sml_model.datasets:
                logger.info(
                    "Converting SML dataset to OSI logical table: %s (columns=%s, hidden=%s)",
                    sml_ds.unique_name,
                    len(sml_ds.columns),
                    sml_ds.is_hidden,
                )
                osi.datasets.append(self._convert_dataset_to_osi(sml_ds))
            
            # 2. Convert Dimensions
            for sml_dim in sml_model.dimensions:
                osi.dimensions.append(self._convert_dimension_to_osi(sml_dim))
            
            # 3. Convert Metrics
            for sml_metric in sml_model.metrics:
                osi.metrics.append(self._convert_metric_to_osi(sml_metric))
            
            # 4. Convert Relationships
            for sml_rel in sml_model.relationships:
                osi_rel = self._convert_relationship_to_osi(sml_rel)
                if osi_rel:
                    osi.relationships.append(osi_rel)
            
            return osi
            
        except Exception as e:
            logger.error(f"SML to OSI conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert SML to OSI: {e}",
                source_format="sml",
                target_format="osi",
                details={"error": str(e)}
            )

    def from_osi(self, osi_model: OSIModel) -> SMLModel:
        """
        Convert OSIModel to SMLModel.
        
        Not implemented in this unidirectional converter.
        Use OSIToSMLConverter for OSI -> SML.
        """
        raise NotImplementedError("SMLToOSIConverter only supports SML -> OSI conversion.")

    def _convert_dataset_to_osi(self, sml_ds: SMLDataset) -> OSIDataset:
        """Convert SMLDataset to OSIDataset."""
        columns = [self._convert_column_to_osi(c) for c in sml_ds.columns]

        if not columns:
            logger.warning(
                "Retaining logical table '%s' with no columns in OSI output",
                sml_ds.unique_name,
            )
        
        return OSIDataset(
            unique_name=sml_ds.unique_name,
            label=sml_ds.label,
            description=sml_ds.description or None,
            source_table=sml_ds.source_table or None,
            source_schema=sml_ds.source_schema or None,
            columns=columns,
            is_fact=sml_ds.is_fact,
            is_hidden=sml_ds.is_hidden,
        )

    def _convert_column_to_osi(self, sml_col: SMLColumn) -> OSIColumn:
        """Convert SMLColumn to OSIColumn."""
        # Map SML DataType to OSI DataType by enum name
        osi_type = getattr(OSIDataType, sml_col.data_type.name, OSIDataType.STRING)
        
        return OSIColumn(
            unique_name=sml_col.unique_name,
            label=sml_col.label,
            data_type=osi_type,
            description=sml_col.description or None,
            is_key=sml_col.is_key,
            is_hidden=sml_col.is_hidden,
            format_string=sml_col.format_string,
        )

    def _convert_metric_to_osi(self, sml_metric: SMLMetric) -> OSIMetric:
        """Convert SMLMetric to OSIMetric."""
        # Map SML AggregationType to OSI AggregationType by enum name
        osi_agg = getattr(OSIAggregationType, sml_metric.aggregation.name, OSIAggregationType.SUM)
        
        # OSI requires either source_column or expression
        source_col = sml_metric.source_column
        expression = sml_metric.expression if sml_metric.expression else None
        
        # If neither is available, we need to provide at least expression
        # Generate from metric name as a placeholder
        if not source_col and not expression:
            expression = f"[{sml_metric.unique_name}]"
        
        return OSIMetric(
            unique_name=sml_metric.unique_name,
            label=sml_metric.label,
            dataset=sml_metric.dataset,
            source_column=source_col,
            expression=expression,
            aggregation=osi_agg,
            description=sml_metric.description or None,
            format_string=sml_metric.format_string,
            is_hidden=sml_metric.is_hidden,
        )

    def _convert_dimension_to_osi(self, sml_dim: SMLDimension) -> OSIDimension:
        """Convert SMLDimension to OSIDimension."""
        attributes = []
        for sml_attr in sml_dim.attributes:
            source_column = getattr(sml_attr, "source_column", None) or getattr(
                sml_attr, "dataset_column", None
            )
            attributes.append(OSIAttribute(
                unique_name=sml_attr.unique_name,
                label=sml_attr.label,
                dataset=sml_attr.dataset,
                source_column=source_column,
                is_hidden=sml_attr.is_hidden,
            ))
        
        hierarchies = []
        for sml_hier in sml_dim.hierarchies:
            levels = [
                OSILevel(
                    unique_name=lvl.unique_name,
                    label=lvl.label,
                    attribute=lvl.attribute,
                )
                for lvl in sml_hier.levels
            ]
            hierarchies.append(OSIHierarchy(
                unique_name=sml_hier.unique_name,
                label=sml_hier.label,
                levels=levels,
            ))
        
        return OSIDimension(
            unique_name=sml_dim.unique_name,
            label=sml_dim.label,
            description=sml_dim.description or None,
            dataset=sml_dim.dataset,
            attributes=attributes,
            hierarchies=hierarchies,
            is_hidden=sml_dim.is_hidden,
        )

    def _convert_relationship_to_osi(self, sml_rel: SMLRelationship) -> Optional[OSIRelationship]:
        """Convert SMLRelationship to OSIRelationship."""
        try:
            osi_card = getattr(OSICardinality, sml_rel.cardinality.name, OSICardinality.MANY_TO_ONE)
            osi_cf = getattr(OSICrossFilterDirection, sml_rel.cross_filter_direction.name, OSICrossFilterDirection.SINGLE)
            
            return OSIRelationship(
                unique_name=sml_rel.unique_name,
                from_dataset=sml_rel.from_dataset,
                from_columns=sml_rel.from_columns,
                to_dataset=sml_rel.to_dataset,
                to_columns=sml_rel.to_columns,
                cardinality=osi_card,
                cross_filter_direction=osi_cf,
                is_active=sml_rel.is_active,
            )
        except Exception as e:
            logger.warning(f"Failed to convert relationship to OSI {sml_rel.unique_name}: {e}")
            return None
