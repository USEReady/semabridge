"""TMDL to CSM Direct Converter.

Parses TMDL (Tabular Model Definition Language) files and converts
directly to CSM without intermediate OSI step.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Any

from semabridge.csm.models import (
    CSMModel, CSMDataset, CSMColumn, CSMMetric,
    CSMRelationship, CSMAggregationType, CSMCardinality,
)
from semabridge.converter.tmdl_grammar import TmdlGrammarValidator
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TmdlToCsmConverter:
    """Convert TMDL files directly to CSM."""
    
    def __init__(self):
        self._current_indent = 0
        self._tables: Dict[str, Dict] = {}
        self.validator = TmdlGrammarValidator()
    
    def convert(self, tmdl_files: Dict[str, str]) -> CSMModel:
        """Convert TMDL files (dict of path->content) to CSM."""
        
        # Parse each .tmdl file
        for path, content in tmdl_files.items():
            # Validate grammar first
            is_valid, errors = self.validator.validate(content)
            if not is_valid:
                logger.error(f"TMDL validation failed for {path}: {errors}")
                # For now, we continue parsing but log errors
            
            if path.endswith('model.tmdl'):
                self._parse_model(content)
            elif 'tables/' in path:
                self._parse_table(content)
            elif 'relationships.tmdl' in path:
                self._parse_relationships(content)
        
        # Build CSM model
        return self._build_csm_model()
    
    def _parse_model(self, content: str) -> None:
        """Parse top-level model.tmdl file."""
        # Future implementation
        pass

    def _parse_relationships(self, content: str) -> None:
        """Parse relationships.tmdl file."""
        # Future implementation
        pass

    def _parse_table(self, content: str) -> None:
        """Parse a table.tmdl file."""
        lines = content.split('\n')
        current_table = None
        
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('//'):
                continue
            
            # Table definition: "table TableName"
            if stripped.startswith('table '):
                # Handle names with spaces properly, e.g. table 'Sales Fact'
                table_name_raw = stripped[6:].strip()
                if table_name_raw.startswith("'") and table_name_raw.endswith("'"):
                    current_table = table_name_raw[1:-1]
                else:
                    current_table = table_name_raw
                    
                self._tables[current_table] = {
                    'columns': [],
                    'measures': [],
                    'description': '',
                }
                continue
            
            # Column definition
            if stripped.startswith('column '):
                # Parse: "column ColumnName: dataType" or "column 'Column Name': dataType"
                match = re.match(r"column\s+'?([^']+)'?\s*:\s*(\w+)", stripped)
                if match:
                    col_name, data_type = match.groups()
                    if current_table:
                        self._tables[current_table]['columns'].append({
                            'name': col_name,
                            'data_type': data_type,
                        })
                continue
            
            # Measure definition
            if stripped.startswith("measure '") or stripped.startswith("measure "):
                # Parse: "measure 'Measure Name' = Expression"
                match = re.match(r"measure\s+'?([^'=]+)'?\s*=\s*(.+)$", stripped)
                if match:
                    measure_name, expression = match.groups()
                    if current_table:
                        self._tables[current_table]['measures'].append({
                            'name': measure_name.strip(),
                            'expression': expression.strip(),
                        })
                continue
    
    def _build_csm_model(self) -> CSMModel:
        """Build CSM model from parsed TMDL."""
        datasets = []
        metrics = []
        
        for table_name, table_data in self._tables.items():
            # Create dataset
            dataset = CSMDataset(
                unique_name=table_name,
                label=table_name,
                source_table=table_name,
                columns=[
                    CSMColumn(
                        unique_name=col['name'],
                        label=col['name'],
                        data_type=col['data_type'],
                        is_key=(col['name'].endswith('ID')),
                    )
                    for col in table_data['columns']
                ],
                is_fact=('FACT' in table_name.upper()),
            )
            datasets.append(dataset)
            
            # Create metrics
            for measure in table_data['measures']:
                metric = CSMMetric(
                    unique_name=measure['name'].upper().replace(' ', '_'),
                    label=measure['name'],
                    dataset=table_name,
                    expression=measure['expression'],
                    aggregation=CSMAggregationType.SUM,
                )
                metrics.append(metric)
        
        return CSMModel(
            unique_name="model",
            label="Semantic Model",
            datasets=datasets,
            metrics=metrics,
            relationships=[],
            source_platform="fabric",
        )
