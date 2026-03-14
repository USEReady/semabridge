"""
Sentinel Governance Module.

Handles schema drift detection and baseline management.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from semabridge.core.settings import SnowflakeConfig
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DriftReport:
    """Report on schema drift."""
    breaking_changes: List[str]
    non_breaking_changes: List[str]
    
    @property
    def has_drift(self) -> bool:
        return bool(self.breaking_changes or self.non_breaking_changes)
    
    @property
    def is_breaking(self) -> bool:
        return bool(self.breaking_changes)


class DriftDetector:
    """Detects changes between current schema and baseline."""
    
    def __init__(self, config: SnowflakeConfig):
        self.config = config
        self.extractor = SnowflakeExtractor(config)

    def take_snapshot(self, output_path: Path) -> Dict[str, Any]:
        """Capture current schema state as baseline."""
        logger.info("Taking schema snapshot...")
        metadata = self.extractor.extract_all()
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)
            
        return metadata

    def check_drift(self, baseline_path: Path) -> DriftReport:
        """Compare current schema against baseline."""
        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline file not found: {baseline_path}")
            
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)
            
        current = self.extractor.extract_all()
        
        return self._compare(baseline, current)

    def _compare(self, baseline: Dict[str, Any], current: Dict[str, Any]) -> DriftReport:
        """Compare two metadata dictionaries."""
        breaking = []
        non_breaking = []
        
        # 1. Compare Tables
        base_tables = set(baseline.get("tables", {}).keys())
        curr_tables = set(current.get("tables", {}).keys())
        
        # Dropped Tables (Breaking)
        dropped = base_tables - curr_tables
        for t in dropped:
            breaking.append(f"Table dropped: {t}")
            
        # New Tables (Non-Breaking)
        added = curr_tables - base_tables
        for t in added:
            non_breaking.append(f"Table added: {t}")
            
        # 2. Compare Columns for common tables
        common_tables = base_tables & curr_tables
        
        for table in common_tables:
            base_cols = {c["name"]: c for c in baseline.get("columns", {}).get(table, [])}
            curr_cols = {c["name"]: c for c in current.get("columns", {}).get(table, [])}
            
            # Dropped Columns (Breaking)
            dropped_cols = set(base_cols.keys()) - set(curr_cols.keys())
            for c in dropped_cols:
                breaking.append(f"Column dropped in {table}: {c}")
                
            # New Columns (Non-Breaking)
            added_cols = set(curr_cols.keys()) - set(base_cols.keys())
            for c in added_cols:
                non_breaking.append(f"Column added to {table}: {c}")
                
            # Changed Columns
            common_cols = set(base_cols.keys()) & set(curr_cols.keys())
            for c in common_cols:
                b_type = base_cols[c]["data_type"]
                c_type = curr_cols[c]["data_type"]
                
                if b_type != c_type:
                    # Type change is potentially breaking
                    # We could be smarter here (e.g. int -> bigint is safe), but assume breaking for now
                    breaking.append(f"Column type changed in {table}.{c}: {b_type} -> {c_type}")
                    
        return DriftReport(breaking_changes=breaking, non_breaking_changes=non_breaking)
