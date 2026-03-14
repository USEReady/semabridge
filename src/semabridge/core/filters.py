"""
Semantic Object Filtering.

Provides filtering logic for semantic objects (tables, columns, measures)
during the conversion pipeline. Applies exclusion rules defined in ProjectOptions.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, List, Callable

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SemanticFilter:
    """
    Applies exclusion rules to semantic objects.
    
    Supports:
    - table:<pattern> - Exclude entire tables
    - column:<pattern> - Exclude columns (critical for PII protection)
    - measure:<pattern> - Exclude measures
    
    Exclusion ALWAYS overrides inclusion.
    """
    
    def __init__(
        self,
        table_patterns: List[str] = None,
        column_patterns: List[str] = None,
        measure_patterns: List[str] = None,
    ):
        """
        Initialize the filter with exclusion patterns.
        
        Args:
            table_patterns: Glob patterns for table exclusion
            column_patterns: Glob patterns for column exclusion
            measure_patterns: Glob patterns for measure exclusion
        """
        self.table_patterns = table_patterns or []
        self.column_patterns = column_patterns or []
        self.measure_patterns = measure_patterns or []
        
        logger.debug(
            f"SemanticFilter initialized: "
            f"tables={len(self.table_patterns)}, "
            f"columns={len(self.column_patterns)}, "
            f"measures={len(self.measure_patterns)}"
        )
    
    @classmethod
    def from_exclusion_rules(cls, rules: List[str]) -> 'SemanticFilter':
        """
        Create a SemanticFilter from a list of prefixed exclusion rules.
        
        Args:
            rules: List of strings like "table:Sys_*", "column:PII_SSN"
            
        Returns:
            Configured SemanticFilter instance
        """
        tables = []
        columns = []
        measures = []
        
        for rule in rules:
            if ':' not in rule:
                logger.warning(f"Invalid exclusion rule (missing colon): {rule}")
                continue
            
            prefix, pattern = rule.split(':', 1)
            prefix = prefix.strip().lower()
            pattern = pattern.strip()
            
            if prefix == 'table':
                tables.append(pattern)
            elif prefix == 'column':
                columns.append(pattern)
            elif prefix == 'measure':
                measures.append(pattern)
            else:
                logger.warning(f"Unknown exclusion type: {prefix}")
        
        return cls(
            table_patterns=tables,
            column_patterns=columns,
            measure_patterns=measures,
        )
    
    def _matches_any(self, name: str, patterns: List[str]) -> bool:
        """Check if name matches any pattern (case-insensitive)."""
        name_lower = name.lower()
        return any(
            fnmatch.fnmatch(name_lower, p.lower())
            for p in patterns
        )
    
    def is_table_excluded(self, table_name: str) -> bool:
        """Check if a table should be excluded."""
        return self._matches_any(table_name, self.table_patterns)
    
    def is_column_excluded(self, column_name: str) -> bool:
        """
        Check if a column should be excluded.
        
        This is critical for PII protection (e.g., column:PII_SSN).
        """
        return self._matches_any(column_name, self.column_patterns)
    
    def is_measure_excluded(self, measure_name: str) -> bool:
        """Check if a measure should be excluded."""
        return self._matches_any(measure_name, self.measure_patterns)
    
    def filter_osi_model(self, osi_model: Any) -> Any:
        """
        Apply exclusion filters to an OSI model.
        
        Modifies the model in-place by removing excluded objects.
        Filtering is applied as early as possible in the pipeline.
        
        Args:
            osi_model: OSIModel instance
            
        Returns:
            Filtered OSIModel
        """
        from semabridge.intermediate.osi_model import OSIModel
        
        if not isinstance(osi_model, OSIModel):
            logger.warning(f"Expected OSIModel, got {type(osi_model)}")
            return osi_model
        
        original_datasets = len(osi_model.datasets)
        original_metrics = len(osi_model.metrics)
        
        # Filter datasets (tables)
        osi_model.datasets = [
            ds for ds in osi_model.datasets
            if not self.is_table_excluded(ds.unique_name)
        ]
        
        # Filter columns within remaining datasets
        for dataset in osi_model.datasets:
            original_cols = len(dataset.columns)
            dataset.columns = [
                col for col in dataset.columns
                if not self.is_column_excluded(col.unique_name)
            ]
            if len(dataset.columns) < original_cols:
                logger.info(
                    f"Filtered {original_cols - len(dataset.columns)} "
                    f"columns from {dataset.unique_name}"
                )
        
        # Filter metrics (measures)
        osi_model.metrics = [
            m for m in osi_model.metrics
            if not self.is_measure_excluded(m.unique_name)
        ]
        
        logger.info(
            f"Filtered: datasets {original_datasets} -> {len(osi_model.datasets)}, "
            f"metrics {original_metrics} -> {len(osi_model.metrics)}"
        )
        
        return osi_model
    
    def filter_sml_model(self, sml_model: Any) -> Any:
        """
        Apply exclusion filters to an SML model.
        
        Args:
            sml_model: SMLModel instance
            
        Returns:
            Filtered SMLModel
        """
        from semabridge.formats.sml.models import SMLModel
        
        if not isinstance(sml_model, SMLModel):
            logger.warning(f"Expected SMLModel, got {type(sml_model)}")
            return sml_model
        
        original_datasets = len(sml_model.datasets)
        original_metrics = len(sml_model.metrics)
        
        # Filter datasets
        sml_model.datasets = [
            ds for ds in sml_model.datasets
            if not self.is_table_excluded(ds.unique_name)
        ]
        
        # Filter columns within datasets
        for dataset in sml_model.datasets:
            if hasattr(dataset, 'columns') and dataset.columns:
                dataset.columns = [
                    col for col in dataset.columns
                    if not self.is_column_excluded(col.unique_name)
                ]
        
        # Filter metrics
        sml_model.metrics = [
            m for m in sml_model.metrics
            if not self.is_measure_excluded(m.unique_name)
        ]
        
        logger.info(
            f"SML filtered: datasets {original_datasets} -> {len(sml_model.datasets)}, "
            f"metrics {original_metrics} -> {len(sml_model.metrics)}"
        )
        
        return sml_model


def create_filter_from_options(options: Dict[str, Any]) -> SemanticFilter:
    """
    Create a SemanticFilter from project options dictionary.
    
    Args:
        options: Dictionary with 'exclude_semantics' key
        
    Returns:
        Configured SemanticFilter
    """
    rules = options.get('exclude_semantics', [])
    return SemanticFilter.from_exclusion_rules(rules)
