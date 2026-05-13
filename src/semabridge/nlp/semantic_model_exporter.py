"""
Semantic Model Exporter for NLP.

Exports the project's semantic model (measures, dimensions, tables, 
relationships) into a JSON format for LLM context.
"""

import json
from typing import List, Dict, Any, Optional
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class SemanticModelExporter:
    """
    Exports semantic metadata for consumption by NLP engines.
    """
    
    def export_project_model(self, project_id: str, metadata: Dict[str, Any]) -> str:
        """
        Transforms internal metadata into the NLP JSON format.
        """
        nlp_model = {
            "project_id": project_id,
            "measures": self._extract_measures(metadata),
            "dimensions": self._extract_dimensions(metadata),
            "tables": metadata.get("tables", []),
            "relationships": self._extract_relationships(metadata)
        }
        
        return json.dumps(nlp_model, indent=2)

    def _extract_measures(self, metadata: Dict) -> List[Dict]:
        measures = []
        for m in metadata.get("measures", []):
            measures.append({
                "name": m.get("name"),
                "expression": m.get("expression"),
                "description": m.get("description", "")
            })
        return measures

    def _extract_dimensions(self, metadata: Dict) -> List[Dict]:
        dimensions = []
        for table in metadata.get("table_schemas", {}):
            for col in metadata["table_schemas"][table].get("columns", []):
                # Simple heuristic: non-numeric columns are dimensions
                dimensions.append({
                    "name": col.get("name"),
                    "table": table,
                    "column": col.get("name")
                })
        return dimensions

    def _extract_relationships(self, metadata: Dict) -> List[Dict]:
        relationships = []
        for rel in metadata.get("relationships", []):
            relationships.append({
                "from": f"{rel['from_table']}[{rel['from_column']}]",
                "to": f"{rel['to_table']}[{rel['to_column']}]"
            })
        return relationships
