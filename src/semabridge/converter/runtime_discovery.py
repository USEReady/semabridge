import re
from typing import Dict, List, Optional, Any
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class RuntimeColumnDiscovery:
    """
    Dynamically scans a model's schema to find columns that match given patterns.
    This enables true dynamic behavior without hardcoding exact column names.
    """
    
    @staticmethod
    def _matches_any(text: str, patterns: List[str]) -> bool:
        if not patterns or not text:
            return False
        text_lower = text.lower()
        return any(p.lower() in text_lower for p in patterns)

    @classmethod
    def discover_column_mappings(cls, model: Any, detection_patterns: Dict[str, Dict]) -> Dict[str, str]:
        """
        Scan the model datasets and columns using detection_patterns.
        
        detection_patterns example:
        {
            "vanarsdel_flag": {
                "column_keywords": ["vanarsdel", "isvanarsdel"],
                "table_keywords": ["product"],
                "value_set": ["Yes", "No"]
            }
        }
        
        Returns a resolved column_mappings dict:
        {
            "vanarsdel_flag_table": "PRODUCT",
            "vanarsdel_flag_column": "ISVANARSDEL"
        }
        """
        if not detection_patterns:
            return {}
            
        resolved = {}
        
        for role, patterns in detection_patterns.items():
            col_keywords = patterns.get("column_keywords", [])
            tbl_keywords = patterns.get("table_keywords", [])
            
            found_table = None
            found_column = None
            
            if not getattr(model, "datasets", None):
                continue
                
            for dataset in model.datasets:
                ds_name = dataset.unique_name or dataset.name or ""
                
                # Check table keywords (if any are specified)
                if tbl_keywords and not cls._matches_any(ds_name, tbl_keywords):
                    continue
                    
                for col in getattr(dataset, "columns", []):
                    col_name = col.unique_name or col.name or ""
                    
                    if cls._matches_any(col_name, col_keywords):
                        # We could optionally check col.sample_values here if available
                        found_table = ds_name
                        found_column = col_name
                        break
                        
                if found_column:
                    break
                    
            if found_column and found_table:
                resolved[f"{role}_table"] = found_table
                resolved[f"{role}_column"] = found_column
                logger.debug(f"Discovered {role} -> {found_table}.{found_column}")
                
        return resolved
