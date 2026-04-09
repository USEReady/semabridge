"""
Model-Specific Validation & Remediation Configuration
Allows safe fixes at model level without core pipeline changes
"""

from dataclasses import dataclass
from typing import Set, Dict, Optional, List

@dataclass
class ModelSpecificConfig:
    """Configuration for model-level fixes."""
    model_name: str
    skip_identifiers_with_chars: Set[str] = None  # Characters to skip in identifiers
    column_remap: Dict[str, str] = None  # Old column -> New column mapping
    skip_relationships: Set[str] = None  # Relationship IDs to skip
    skip_metrics: Set[str] = None  # Metric names to skip
    skip_dimensions: Set[str] = None  # Dimension names to skip
    
    def __post_init__(self):
        if self.skip_identifiers_with_chars is None:
            self.skip_identifiers_with_chars = set()
        if self.column_remap is None:
            self.column_remap = {}
        if self.skip_relationships is None:
            self.skip_relationships = set()
        if self.skip_metrics is None:
            self.skip_metrics = set()
        if self.skip_dimensions is None:
            self.skip_dimensions = set()

# ============================================================================
# Model-Specific Configurations (Safe, non-invasive fixes)
# ============================================================================

CLIENT_DATA_CONFIG = ModelSpecificConfig(
    model_name="Client Data",
    # Skip any identifier containing $ (Snowflake semantic view incompatibility)
    skip_identifiers_with_chars={'$'},
    # Skip relationships that fail validation
    skip_relationships=set(),
    # Skip problematic metrics
    skip_metrics={
        # Add metric names if needed based on debug output
        # "METRIC_NAME_WITH_ISSUE",
    },
    # Skip problematic dimensions
    skip_dimensions=set(),
    # Column name remapping if physical schema changed
    column_remap={}
)

INVENTORY_SEMANTIC_MODEL_CONFIG = ModelSpecificConfig(
    model_name="Inventory Semantic Model",
    skip_identifiers_with_chars=set(),
    column_remap={}
)

# Registry of all model configurations
MODEL_CONFIGS: Dict[str, ModelSpecificConfig] = {
    "Client Data": CLIENT_DATA_CONFIG,
    "Inventory Semantic Model": INVENTORY_SEMANTIC_MODEL_CONFIG,
}

def get_model_config(model_name: str) -> Optional[ModelSpecificConfig]:
    """Get configuration for a specific model."""
    return MODEL_CONFIGS.get(model_name)

def should_skip_identifier(model_name: str, identifier: str) -> bool:
    """Check if identifier should be skipped for this model."""
    config = get_model_config(model_name)
    if not config:
        return False
    
    for char in config.skip_identifiers_with_chars:
        if char in identifier:
            return True
    return False

def should_skip_metric(model_name: str, metric_name: str) -> bool:
    """Check if metric should be skipped for this model."""
    config = get_model_config(model_name)
    if not config:
        return False
    return metric_name in config.skip_metrics

def should_skip_dimension(model_name: str, dimension_name: str) -> bool:
    """Check if dimension should be skipped for this model."""
    config = get_model_config(model_name)
    if not config:
        return False
    return dimension_name in config.skip_dimensions

def get_column_remapping(model_name: str) -> Dict[str, str]:
    """Get column name remapping for this model."""
    config = get_model_config(model_name)
    if not config:
        return {}
    return config.column_remap

def remap_column_name(model_name: str, original_name: str) -> str:
    """Remap column name if needed for this model."""
    remapping = get_column_remapping(model_name)
    return remapping.get(original_name, original_name)
