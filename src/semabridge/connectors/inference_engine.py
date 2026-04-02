"""
Module: inference_engine
Purpose: Infer table roles and structure from discovered metadata.
Responsibilities:
- Score tables using relationship and schema heuristics.
- Classify tables into semantic roles used by downstream modeling.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from semabridge.sml.models import DataType
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

@dataclass
class TableScore:
    """Scoring metrics for table classification."""
    name: str
    column_count: int = 0
    row_count: int = 0
    outgoing_fks: int = 0
    incoming_fks: int = 0
    measure_candidates: int = 0
    date_columns: int = 0
    id_columns: int = 0
    
    # Confidences (0.0 - 1.0)
    fact_confidence: float = 0.0
    dim_confidence: float = 0.0
    classification: str = "UNKNOWN"  # FACT, DIMENSION, BRIDGE, TIME

class SmlInferenceEngine:
    """
    Unified Inference Engine for Semantic Modeling.
    Classifies tables and detects model structure using heuristic scoring.
    """
    
    def __init__(
        self,
        tables: Dict[str, Dict[str, Any]],
        columns: Dict[str, List[Dict[str, Any]]],
        relationships: List[Dict[str, Any]],
        primary_keys: Dict[str, List[str]]
    ):
        self.tables = tables
        self.columns = columns
        self.relationships = relationships
        self.primary_keys = primary_keys
        self.scores: Dict[str, TableScore] = {}

    def classify(self) -> Dict[str, TableScore]:
        """Execute classification pipeline."""
        self._calculate_base_metrics()
        self._calculate_scores()
        self._assign_classifications()
        return self.scores

    def _calculate_base_metrics(self):
        """Compute raw metrics for each table."""
        # 1. Initialize Scores
        for table_name in self.tables:
            self.scores[table_name] = TableScore(name=table_name)
            
            # Row Counts
            idx_info = self.tables[table_name]
            self.scores[table_name].row_count = idx_info.get("row_count") or 0
            
            # Column Analysis
            cols = self.columns.get(table_name, [])
            self.scores[table_name].column_count = len(cols)
            
            for col in cols:
                dtype = DataType.from_snowflake(col.get("data_type", "VARCHAR"))
                col_upper = col["name"].upper()
                
                # Check for Date
                if dtype in (DataType.DATE, DataType.DATETIME) or "DATE" in col_upper:
                    self.scores[table_name].date_columns += 1
                
                # Check for IDs
                if col_upper.endswith("_ID") or col_upper.endswith("_KEY") or col_upper == "ID":
                    self.scores[table_name].id_columns += 1
                
                # Check for Measures (Numeric + Measure Pattern)
                if dtype in (DataType.INTEGER, DataType.DECIMAL, DataType.FLOAT):
                    # Exclude likely Keys/IDs from measures
                    if not (col_upper.endswith("_ID") or col_upper.endswith("KEY")):
                        self.scores[table_name].measure_candidates += 1

        # 2. Relationship Analysis
        for rel in self.relationships:
            from_t = rel["from_table"]
            to_t = rel["to_table"]
            
            if from_t in self.scores:
                self.scores[from_t].outgoing_fks += 1
            if to_t in self.scores:
                self.scores[to_t].incoming_fks += 1

    def _calculate_scores(self):
        """Calculate confidence scores."""
        for name, score in self.scores.items():
            # Factor 1: Naming Convention
            name_upper = name.upper()
            if name_upper.startswith("FACT_") or name_upper.startswith("F_"):
                score.fact_confidence += 0.4
            if name_upper.startswith("DIM_") or name_upper.startswith("D_"):
                score.dim_confidence += 0.4
            
            # Factor 2: Relationships
            # Facts typically point to many dimensions (High Outgoing)
            if score.outgoing_fks >= 2:
                score.fact_confidence += 0.3
            # Dimensions are typically pointed TO (High Incoming)
            if score.incoming_fks >= 1:
                score.dim_confidence += 0.3
                
            # Factor 3: Content (Measures)
            # Facts have measures
            if score.measure_candidates > 0:
                score.fact_confidence += 0.2
                # Boost if multiple potential measures found (e.g. Amt, Qty)
                if score.measure_candidates >= 2:
                    score.fact_confidence += 0.1
                    
            # Facts often define events in time (Has Date Column)
            if score.date_columns > 0:
                 score.fact_confidence += 0.1
            
            # Dimensions have descriptive attributes (Low IDs vs Cols)
            # If (Cols - IDs) > 2, likely has descriptions
            if (score.column_count - score.id_columns) > 2:
                score.dim_confidence += 0.2
            
            # Factor 4: Date Dimension Specific
            if score.date_columns >= 1 and (score.column_count < 15):
                # High density of date columns relative to size suggests Date Dim
                if (score.date_columns / score.column_count) > 0.2 or "DATE" in name_upper:
                     score.classification = "TIME" # Override

    def _assign_classifications(self):
        """Finalize classification based on scores."""
        for name, score in self.scores.items():
            if score.classification != "UNKNOWN":
                continue # Already handled (e.g. TIME)
            
            # Bridge Table Detection
            # Mostly IDs, few other columns
            if score.id_columns == score.column_count and score.column_count > 1:
                 score.classification = "BRIDGE"
                 continue

            # Compare Scores
            if score.fact_confidence > score.dim_confidence:
                score.classification = "FACT"
            elif score.dim_confidence > score.fact_confidence:
                score.classification = "DIMENSION"
            else:
                # Tie-breaker: Facts usually have more rows than dimensions
                # This is heuristic and might be wrong for tiny facts
                # Default to Dimension if unsure as it's safer
                score.classification = "DIMENSION" 
            
            logger.info(f"Classified {name}: {score.classification} (Fact: {score.fact_confidence:.2f}, Dim: {score.dim_confidence:.2f})")
