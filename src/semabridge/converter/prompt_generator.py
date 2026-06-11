"""
Generates conversion prompts dynamically based on actual OSI model content.
"""
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

@dataclass
class ConversionRequirements:
    """Dynamic requirements based on actual OSI content."""
    preserve_source_expressions: bool = False
    preserve_default_aggregations: bool = False
    preserve_descriptions: bool = False
    columns_with_expressions: List[str] = None
    columns_with_aggregations: List[str] = None
    datasets_with_descriptions: List[str] = None
    
    def __post_init__(self):
        self.columns_with_expressions = self.columns_with_expressions or []
        self.columns_with_aggregations = self.columns_with_aggregations or []
        self.datasets_with_descriptions = self.datasets_with_descriptions or []


class DynamicPromptGenerator:
    """
    Generates conversion prompts dynamically based on actual OSI model content.
    No hardcoded assumptions about specific models.
    """
    
    def __init__(self):
        self._detected_patterns = {}
    
    def analyze_osi_model(self, osi_model: Dict[str, Any]) -> ConversionRequirements:
        """
        Analyze OSI model to determine what needs to be preserved.
        Returns requirements based on actual content, not assumptions.
        """
        requirements = ConversionRequirements()
        
        # Analyze columns for source_expressions
        columns = osi_model.get('columns', [])
        for col in columns:
            if col.get('source_expression'):
                requirements.preserve_source_expressions = True
                requirements.columns_with_expressions.append(col.get('unique_name'))
            
            if col.get('default_aggregation'):
                requirements.preserve_default_aggregations = True
                requirements.columns_with_aggregations.append(col.get('unique_name'))
        
        # Analyze datasets for descriptions
        datasets = osi_model.get('datasets', [])
        for ds in datasets:
            if ds.get('description'):
                requirements.preserve_descriptions = True
                requirements.datasets_with_descriptions.append(ds.get('unique_name'))
        
        # Analyze metrics for aggregations
        metrics = osi_model.get('metrics', [])
        for metric in metrics:
            if metric.get('default_aggregation'):
                requirements.preserve_default_aggregations = True
        
        logger.info(f"Dynamic analysis complete: {requirements}")
        return requirements
    
    def generate_conversion_prompt(self, osi_model: Dict[str, Any]) -> str:
        """
        Generate a dynamic conversion prompt based on actual OSI content.
        No hardcoded examples - uses real data from the model.
        """
        
        requirements = self.analyze_osi_model(osi_model)
        
        # Get actual examples from the model (not hardcoded)
        sample_columns = self._get_sample_columns(osi_model, requirements)
        sample_datasets = self._get_sample_datasets(osi_model, requirements)
        
        prompt = f"""
# OSI to SML Conversion - Dynamic Requirements

## Model Statistics
- Total Datasets: {len(osi_model.get('datasets', []))}
- Total Columns: {len(osi_model.get('columns', []))}
- Total Metrics: {len(osi_model.get('metrics', []))}
- Total Relationships: {len(osi_model.get('relationships', []))}

## Preservation Requirements (Auto-Detected)

{self._generate_preservation_requirements(requirements)}

## Actual Examples from Source Model

### Columns with Source Expressions:
{self._format_column_examples(sample_columns['with_expressions'], 'source_expression')}

### Columns with Default Aggregations:
{self._format_column_examples(sample_columns['with_aggregations'], 'default_aggregation')}

### Datasets with Descriptions:
{self._format_dataset_examples(sample_datasets)}

## Conversion Rules

1. **Structure Preservation (ALWAYS)**
   - Preserve all dataset structures
   - Preserve all column definitions
   - Preserve all relationship definitions
   - Preserve all metric definitions

2. **Expression Preservation (WHEN PRESENT)**
   {self._generate_expression_rule(requirements)}

3. **Aggregation Preservation (WHEN PRESENT)**
   {self._generate_aggregation_rule(requirements)}

4. **Description Preservation (WHEN PRESENT)**
   {self._generate_description_rule(requirements)}

## Validation Rules (Post-Conversion)

After conversion, verify:

{self._generate_validation_checks(requirements)}

## Critical: No Information Loss

The conversion MUST be functionally equivalent to the source OSI model.

**Current Status:**
✅ Structure preserved
{self._get_status_checkmark(requirements.preserve_source_expressions, 'source_expression')}
{self._get_status_checkmark(requirements.preserve_default_aggregations, 'default_aggregation')}
{self._get_status_checkmark(requirements.preserve_descriptions, 'descriptions')}

**Target Status:**
✅ Structure preserved
✅ source_expression preserved (when present)
✅ default_aggregation preserved (when present)
✅ descriptions preserved (when present)
"""
        
        return prompt
    
    def _get_sample_columns(self, osi_model: Dict, requirements: ConversionRequirements) -> Dict:
        """Extract actual column examples from the model."""
        columns = osi_model.get('columns', [])
        
        with_expressions = []
        with_aggregations = []
        
        for col in columns:
            if col.get('source_expression') and len(with_expressions) < 3:
                with_expressions.append({
                    'name': col.get('unique_name'),
                    'expression': str(col.get('source_expression'))[:100]  # Truncate for readability
                })
            
            if col.get('default_aggregation') and len(with_aggregations) < 3:
                with_aggregations.append({
                    'name': col.get('unique_name'),
                    'aggregation': col.get('default_aggregation')
                })
        
        return {
            'with_expressions': with_expressions,
            'with_aggregations': with_aggregations
        }
    
    def _get_sample_datasets(self, osi_model: Dict, requirements: ConversionRequirements) -> List:
        """Extract actual dataset examples with descriptions."""
        datasets = osi_model.get('datasets', [])
        samples = []
        
        for ds in datasets:
            if ds.get('description') and len(samples) < 3:
                samples.append({
                    'name': ds.get('unique_name'),
                    'description': str(ds.get('description'))[:100]
                })
        
        return samples
    
    def _generate_preservation_requirements(self, requirements: ConversionRequirements) -> str:
        """Generate dynamic requirement statements."""
        lines = []
        
        if requirements.preserve_source_expressions:
            lines.append(f"✓ **MUST preserve source_expressions** - Found {len(requirements.columns_with_expressions)} columns with expressions")
        else:
            lines.append("○ No source_expressions detected - skipping expression preservation")
        
        if requirements.preserve_default_aggregations:
            lines.append(f"✓ **MUST preserve default_aggregations** - Found {len(requirements.columns_with_aggregations)} columns with aggregations")
        else:
            lines.append("○ No default_aggregations detected - skipping aggregation preservation")
        
        if requirements.preserve_descriptions:
            lines.append(f"✓ **MUST preserve descriptions** - Found {len(requirements.datasets_with_descriptions)} datasets with descriptions")
        else:
            lines.append("○ No descriptions detected - skipping description preservation")
        
        return "\n".join(lines)
    
    def _format_column_examples(self, examples: List, field: str) -> str:
        """Format actual column examples."""
        if not examples:
            return "   (None found in source model)"
        
        lines = []
        for ex in examples:
            if field == 'source_expression':
                lines.append(f"   - {ex['name']}: {ex['expression']}")
            else:
                lines.append(f"   - {ex['name']}: {ex['aggregation']}")
        
        return "\n".join(lines)
    
    def _format_dataset_examples(self, examples: List) -> str:
        """Format actual dataset examples."""
        if not examples:
            return "   (None found in source model)"
        
        lines = []
        for ex in examples:
            lines.append(f"   - {ex['name']}: \"{ex['description']}\"")
        
        return "\n".join(lines)
    
    def _generate_expression_rule(self, requirements: ConversionRequirements) -> str:
        """Generate expression preservation rule."""
        if not requirements.preserve_source_expressions:
            return "   Not required (no source_expressions in source model)"
        
        return f"""   For each of the {len(requirements.columns_with_expressions)} columns containing source_expression:
   - Copy the expression exactly as-is from OSI to SML
   - Do NOT modify, optimize, or translate the expression
   - Preserve the original DAX/formula syntax
   - Example columns: {', '.join(requirements.columns_with_expressions[:5])}"""
    
    def _generate_aggregation_rule(self, requirements: ConversionRequirements) -> str:
        """Generate aggregation preservation rule."""
        if not requirements.preserve_default_aggregations:
            return "   Not required (no default_aggregations in source model)"
        
        return f"""   For each of the {len(requirements.columns_with_aggregations)} columns with default_aggregation:
   - Preserve the aggregation type exactly as specified
   - Supported: sum, count, distinctcount, avg, min, max
   - Example columns: {', '.join(requirements.columns_with_aggregations[:5])}"""
    
    def _generate_description_rule(self, requirements: ConversionRequirements) -> str:
        """Generate description preservation rule."""
        if not requirements.preserve_descriptions:
            return "   Not required (no descriptions in source model)"
        
        return f"""   For each of the {len(requirements.datasets_with_descriptions)} datasets with descriptions:
   - Preserve the description text exactly
   - Do not truncate or reformat
   - Example datasets: {', '.join(requirements.datasets_with_descriptions[:5])}"""
    
    def _generate_validation_checks(self, requirements: ConversionRequirements) -> str:
        """Generate validation checks based on actual content."""
        checks = []
        
        checks.append("1. Column count matches between OSI and SML")
        
        if requirements.preserve_source_expressions:
            checks.append(f"2. All {len(requirements.columns_with_expressions)} source_expressions are preserved")
        
        if requirements.preserve_default_aggregations:
            checks.append(f"3. All {len(requirements.columns_with_aggregations)} default_aggregations are preserved")
        
        if requirements.preserve_descriptions:
            checks.append(f"4. All {len(requirements.datasets_with_descriptions)} descriptions are preserved")
        
        checks.append("5. Relationship cardinality and direction are preserved")
        checks.append("6. Metric aggregation types match source")
        
        return "\n".join(checks)
    
    def _get_status_checkmark(self, required: bool, feature: str) -> str:
        """Get status checkmark based on requirement."""
        if required:
            return f"❌ {feature} lost"
        else:
            return f"○ {feature} not applicable"


class ConversionValidator:
    """Validates conversion completeness dynamically."""
    
    def __init__(self, original_osi: Dict, converted_sml: Dict):
        self.original = original_osi
        self.converted = converted_sml
        self.issues = []
    
    def validate(self) -> Dict[str, Any]:
        """Run all validations and return results."""
        
        self._validate_column_expressions()
        self._validate_column_aggregations()
        self._validate_descriptions()
        self._validate_structure()
        
        return {
            'is_valid': len(self.issues) == 0,
            'issues': self.issues,
            'summary': self._generate_summary()
        }
    
    def _validate_column_expressions(self):
        """Validate source_expressions are preserved."""
        original_cols = {c.get('unique_name'): c for c in self.original.get('columns', [])}
        converted_cols = {c.get('unique_name'): c for c in self.converted.get('columns', [])}
        
        for name, original in original_cols.items():
            if original.get('source_expression'):
                converted = converted_cols.get(name, {})
                if not converted.get('source_expression'):
                    self.issues.append({
                        'type': 'missing_source_expression',
                        'column': name,
                        'expected': original['source_expression'],
                        'actual': None
                    })
                elif converted['source_expression'] != original['source_expression']:
                    self.issues.append({
                        'type': 'mismatched_source_expression',
                        'column': name,
                        'expected': original['source_expression'],
                        'actual': converted['source_expression']
                    })
    
    def _validate_column_aggregations(self):
        """Validate default_aggregations are preserved."""
        original_cols = {c.get('unique_name'): c for c in self.original.get('columns', [])}
        converted_cols = {c.get('unique_name'): c for c in self.converted.get('columns', [])}
        
        for name, original in original_cols.items():
            if original.get('default_aggregation'):
                converted = converted_cols.get(name, {})
                if not converted.get('default_aggregation'):
                    self.issues.append({
                        'type': 'missing_aggregation',
                        'column': name,
                        'expected': original['default_aggregation'],
                        'actual': None
                    })
    
    def _validate_descriptions(self):
        """Validate descriptions are preserved."""
        original_ds = {d.get('unique_name'): d for d in self.original.get('datasets', [])}
        converted_ds = {d.get('unique_name'): d for d in self.converted.get('datasets', [])}
        
        for name, original in original_ds.items():
            if original.get('description'):
                converted = converted_ds.get(name, {})
                if not converted.get('description'):
                    self.issues.append({
                        'type': 'missing_description',
                        'dataset': name,
                        'expected': original['description'],
                        'actual': None
                    })
    
    def _validate_structure(self):
        """Validate basic structure preservation."""
        if len(self.original.get('columns', [])) != len(self.converted.get('columns', [])):
            self.issues.append({
                'type': 'column_count_mismatch',
                'expected': len(self.original.get('columns', [])),
                'actual': len(self.converted.get('columns', []))
            })
        
        if len(self.original.get('datasets', [])) != len(self.converted.get('datasets', [])):
            self.issues.append({
                'type': 'dataset_count_mismatch',
                'expected': len(self.original.get('datasets', [])),
                'actual': len(self.converted.get('datasets', []))
            })
    
    def _generate_summary(self) -> str:
        """Generate validation summary."""
        if not self.issues:
            return "✅ All validations passed - no information loss"
        
        loss_types = {}
        for issue in self.issues:
            loss_types[issue['type']] = loss_types.get(issue['type'], 0) + 1
        
        summary = f"❌ Found {len(self.issues)} issues:\n"
        for loss_type, count in loss_types.items():
            summary += f"   - {loss_type}: {count} occurrence(s)\n"
        
        return summary
