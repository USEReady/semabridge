"""Configuration-driven registry for optional metric templates.

This module no longer ships built-in business-specific fallback formulas.
Any templates must be provided explicitly through configuration.
"""

from typing import Dict, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MetricFormulaTemplate:
    """Template for a known metric with placeholder support."""
    
    metric_names: set[str]  # e.g. {"TOTAL_COGS", "COGS"}
    formula_template: str  # e.g. "SUM({fact}.MATERIAL_COSTS) + SUM({fact}.LABOR_COSTS_VARIABLE) + ..."
    required_columns: set[str]  # Columns that must exist for this formula to apply
    required_aliases: set[str]  # Aliases (fact, calendar, scenario) that must be present
    description: str = ""


class KnownMetricsRegistry:
    """Registry for metric templates loaded from configuration.
    
    Supports:
    - Template placeholders: {fact}, {calendar}, {scenario}
    - Column name resolution: validates columns exist before applying formula
    - Alias availability: only applies formula when required aliases are present
    """
    
    def __init__(self):
        self.templates: Dict[str, MetricFormulaTemplate] = {}
    
    def _load_defaults(self) -> None:
        """Do not load built-in metric templates."""
        return
    
    def register(
        self,
        metric_names: set[str],
        formula_template: str,
        required_columns: set[str],
        required_aliases: set[str],
        description: str = "",
    ) -> None:
        """Register a metric formula template."""
        template = MetricFormulaTemplate(
            metric_names=metric_names,
            formula_template=formula_template,
            required_columns=required_columns,
            required_aliases=required_aliases,
            description=description,
        )
        
        for metric_name in metric_names:
            self.templates[metric_name.upper()] = template
            logger.debug(f"Registered metric template: {metric_name} - {description}")
    
    def resolve(
        self,
        metric_name: str,
        fact_alias: str,
        scenario_alias: Optional[str],
        calendar_alias: Optional[str],
        available_columns: Dict[str, set[str]],
    ) -> Optional[str]:
        """Resolve a metric formula, validating that required columns and aliases exist.
        
        Args:
            metric_name: Name of the metric (e.g., "TOTAL_COGS")
            fact_alias: Table alias for fact table (e.g., "f")
            scenario_alias: Table alias for scenario dimension (or None)
            calendar_alias: Table alias for calendar dimension (or None)
            available_columns: Dict[dataset_name, set[column_names]] for all datasets
            
        Returns:
            Formatted SQL formula or None if template not found or requirements not met
        """
        metric_key = (metric_name or "").upper()
        template = self.templates.get(metric_key)
        
        if not template:
            logger.debug(f"No known metric template for: {metric_key}")
            return None
        
        # Validate required aliases are available
        aliases_available = {"fact": fact_alias}
        if scenario_alias:
            aliases_available["scenario"] = scenario_alias
        if calendar_alias:
            aliases_available["calendar"] = calendar_alias
        
        missing_aliases = template.required_aliases - set(aliases_available.keys())
        if missing_aliases:
            logger.debug(
                f"Metric {metric_key} requires aliases {missing_aliases}, "
                f"but only have {set(aliases_available.keys())}"
            )
            return None
        
        # Validate required columns exist in available datasets
        # For now, we'll collect all available columns across all datasets
        fact_dataset_cols = set()
        for cols in available_columns.values():
            fact_dataset_cols.update(cols)
        
        missing_columns = template.required_columns - fact_dataset_cols
        if missing_columns:
            logger.debug(
                f"Metric {metric_key} requires columns {missing_columns}, "
                f"but only have columns: {fact_dataset_cols}"
            )
            return None
        
        # Substitute aliases into template
        formula = template.formula_template
        formula = formula.replace("{fact}", fact_alias)
        if scenario_alias:
            formula = formula.replace("{scenario}", scenario_alias)
        if calendar_alias:
            formula = formula.replace("{calendar}", calendar_alias)
        
        logger.info(f"Resolved metric template {metric_key} from configuration")
        return formula
    
    def load_from_config(self, config_dict: Dict[str, Any]) -> None:
        """Load metric templates from configuration dictionary.
        
        Expected format:
        {
            "TOTAL_COGS": {
                "formula": "SUM(...)",
                "required_columns": ["COL1", "COL2"],
                "required_aliases": ["fact"],
                "description": "..."
            },
            ...
        }
        """
        for metric_key, spec in (config_dict or {}).items():
            try:
                metric_names = {spec.get("name", metric_key)} | set(spec.get("aliases", []))
                formula_template = spec.get("formula")
                required_columns = set(spec.get("required_columns", []))
                required_aliases = set(spec.get("required_aliases", ["fact"]))
                description = spec.get("description", "")
                
                self.register(
                    metric_names=metric_names,
                    formula_template=formula_template,
                    required_columns=required_columns,
                    required_aliases=required_aliases,
                    description=description,
                )
            except Exception as e:
                logger.warning(f"Failed to load metric template for {metric_key}: {e}")


# Global singleton
_REGISTRY = None


def get_known_metrics_registry() -> KnownMetricsRegistry:
    """Get or create the global KnownMetricsRegistry."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = KnownMetricsRegistry()
    return _REGISTRY
