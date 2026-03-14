"""
MetricFlow Serializer — converts OSI models to dbt MetricFlow format.

Provides:
- Conversion of OSI models to MetricFlow YAML
- Semantic model definition generation
- Metric definition generation
- Column type mapping

MetricFlow format enables dbt and analytics tools to understand
semantic model structure for metrics computation and lineage tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import re


# ───────────────────────────────────────────────────────────────────────────
# Utilities
# ───────────────────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """
    Sanitize a name for MetricFlow YAML compatibility.

    Rules:
    1. Lowercase all letters
    2. Replace spaces and special chars with underscores
    3. Remove leading digits (prefix with '_')
    4. Collapse multiple underscores

    Args:
        name: Raw name string

    Returns:
        Safe name for MetricFlow
    """
    if not name:
        return "column"

    # Lowercase
    name = name.lower()

    # Replace spaces and special chars with underscore
    name = re.sub(r"[^a-z0-9_]", "_", name)

    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)

    # Remove leading/trailing underscores
    name = name.strip("_")

    # Handle leading digits
    if name and name[0].isdigit():
        name = "_" + name

    return name or "column"


def _infer_time_granularity(column_name: str) -> Optional[str]:
    """
    Infer time granularity from column name.

    Common patterns:
    - YEAR, QUARTER, MONTH, WEEK, DAY, HOUR
    - DATE, TIMESTAMP, TIME
    - Created, Modified, Completed (implies day)

    Args:
        column_name: Column name to analyze

    Returns:
        Time granularity string ('DAY', 'MONTH', etc.) or None
    """
    name_upper = column_name.upper()

    # Explicit patterns
    patterns = {
        r'\bYEAR\b': 'YEAR',
        r'\bQUARTER\b': 'QUARTER',
        r'\bMONTH\b': 'MONTH',
        r'\bWEEK\b': 'WEEK',
        r'\bDAY\b': 'DAY',
        r'\bHOUR\b': 'HOUR',
        r'\bMINUTE\b': 'MINUTE',
        r'\b(DATE|TIMESTAMP|TIME)\b': 'DAY',
        r'\b(CREATED|MODIFIED|COMPLETED|UPDATED|OCCURRED)\b': 'DAY',
    }

    for pattern, granularity in patterns.items():
        if re.search(pattern, name_upper):
            return granularity

    return None


# ───────────────────────────────────────────────────────────────────────────
# MetricFlow Serializer
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class MetricFlowSerializer:
    """
    Serializes OSI models to MetricFlow YAML format.
    """

    def serialize_semantic_models(self, model: Any) -> Dict[str, Any]:
        """
        Convert OSI model to MetricFlow semantic_models.yml structure.

        Args:
            model: OSI semantic model

        Returns:
            Dictionary suitable for YAML serialization
        """
        semantic_models = {}

        if hasattr(model, 'datasets'):
            for ds in model.datasets:
                ds_name = getattr(ds, 'unique_name', 'dataset')
                source_table = getattr(ds, 'source_table', ds_name.upper())

                semantic_model = {
                    'name': _safe_name(ds_name),
                    'description': getattr(ds, 'description', ''),
                    'defaults': {
                        'agg_time_dimension': self._get_default_time_dim(ds),
                    },
                    'columns': self._serialize_columns(ds),
                    'entities': self._serialize_entities(ds),
                }

                semantic_models[_safe_name(ds_name)] = semantic_model

        return {'semantic_models': list(semantic_models.values())}

    def serialize_metrics(self, model: Any) -> Dict[str, Any]:
        """
        Convert OSI model to MetricFlow metrics.yml structure.

        Args:
            model: OSI semantic model

        Returns:
            Dictionary suitable for YAML serialization
        """
        metrics_list = []

        if hasattr(model, 'metrics'):
            for metric in model.metrics:
                m_name = getattr(metric, 'unique_name', 'metric')
                ds_name = getattr(metric, 'dataset', 'dataset')
                agg_type = getattr(metric, 'aggregation', 'sum').name.lower()

                metric_def = {
                    'name': _safe_name(m_name),
                    'description': getattr(metric, 'description', ''),
                    'type': 'simple',
                    'label': m_name,
                    'type_params': {
                        'expr': f"{agg_type}(amount)",
                    },
                }

                metrics_list.append(metric_def)

        return {'metrics': metrics_list}

    def _serialize_columns(self, dataset: Any) -> List[Dict[str, Any]]:
        """Serialize dataset columns to MetricFlow format."""
        columns = []

        if hasattr(dataset, 'columns'):
            for col in dataset.columns:
                col_name = getattr(col, 'unique_name', 'column')
                data_type = str(getattr(col, 'data_type', 'string')).lower()
                is_key = getattr(col, 'is_key', False)

                column_def = {
                    'name': _safe_name(col_name),
                    'description': getattr(col, 'description', ''),
                    'data_type': self._map_data_type(data_type),
                }

                if is_key:
                    column_def['type'] = 'time' if self._is_time_col(col_name) else 'entity'

                columns.append(column_def)

        return columns

    def _serialize_entities(self, dataset: Any) -> List[Dict[str, Any]]:
        """Serialize dataset entities."""
        entities = []

        if hasattr(dataset, 'columns'):
            for col in dataset.columns:
                if getattr(col, 'is_key', False):
                    col_name = getattr(col, 'unique_name', 'column')
                    entities.append({
                        'name': _safe_name(col_name + '_id'),
                        'type': _safe_name(col_name),
                    })

        return entities

    def _get_default_time_dim(self, dataset: Any) -> Optional[str]:
        """Find the default time dimension for the dataset."""
        if hasattr(dataset, 'columns'):
            for col in dataset.columns:
                col_name = getattr(col, 'unique_name', '')
                if self._is_time_col(col_name):
                    return _safe_name(col_name)

        return None

    def _is_time_col(self, col_name: str) -> bool:
        """Check if a column appears to be a time dimension."""
        granularity = _infer_time_granularity(col_name)
        return granularity is not None

    def _map_data_type(self, osi_type: str) -> str:
        """Map OSI data type to MetricFlow type."""
        mapping = {
            'string': 'string',
            'integer': 'int',
            'decimal': 'float',
            'float': 'float',
            'boolean': 'boolean',
            'date': 'date',
            'datetime': 'timestamp',
            'time': 'time',
        }

        return mapping.get(osi_type.lower(), 'string')
