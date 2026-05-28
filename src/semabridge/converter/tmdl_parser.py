from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class TMDLParser:
    """
    Parses native Microsoft Fabric TMDL files to extract structural elements:
    - Tables
    - Columns
    - Measures (with multiline DAX, format strings, and metadata)
    - Relationships
    """

    @staticmethod
    def derive_table_name(path: str, content: str) -> str:
        """Derive the table name from TMDL table content or the file path."""
        # Try quoted table name first: table 'Sales' or table "Sales"
        quoted_match = re.search(r"table\s+['\"]([^'\"]+)['\"]", content)
        if quoted_match:
            return quoted_match.group(1).strip()
        
        # Try unquoted table name (letters, digits, underscores, hyphens, spaces, tabs) up to the end of the line
        unquoted_match = re.search(r"table\s+([a-zA-Z0-9_#@ \t-]+)", content)
        if unquoted_match:
            return unquoted_match.group(1).strip()
        
        # Fall back to filename stem
        return Path(path).stem

    @classmethod
    def parse_table_file(cls, content: str) -> Dict[str, Any]:
        """
        Parse a single table TMDL file content.
        
        Returns:
            Dict containing:
                - columns: List of parsed column dictionaries
                - measures: List of parsed measure dictionaries
        """
        columns = cls.parse_columns(content)
        measures = cls.parse_measures(content)
        return {
            "columns": columns,
            "measures": measures
        }

    @staticmethod
    def parse_columns(content: str) -> List[Dict[str, Any]]:
        """Parse columns from TMDL content."""
        columns = []
        col_pattern = r"column\s+(?:'([^']+)'|([a-zA-Z0-9_#@]+))\s*(.+?)(?=\n\s*(?:column|measure|partition|change|lineageTag|annotation)|\n\s*$|\Z)"
        matches = re.finditer(col_pattern, content, re.DOTALL)
        
        for match in matches:
            name = match.group(1) or match.group(2)
            body = match.group(3)
            
            # Defaults
            data_type = "string"
            format_string = None
            summarize_by = None
            source_column = None
            expression = None
            is_hidden = False
            
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("dataType:"):
                    data_type = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("formatString:"):
                    format_string = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("summarizeBy:"):
                    summarize_by = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("sourceColumn:"):
                    source_column = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("expression:"):
                    expression = stripped.split(":", 1)[1].strip()
                elif stripped == "isHidden" or stripped.startswith("isHidden:"):
                    is_hidden = True

            columns.append({
                "name": name,
                "dataType": data_type,
                "formatString": format_string,
                "summarizeBy": summarize_by,
                "sourceColumn": source_column,
                "expression": expression,
                "isHidden": is_hidden,
            })
            
        return columns

    @staticmethod
    def parse_measures(content: str) -> List[Dict[str, Any]]:
        """Parse measures from TMDL content, supporting multiline DAX and metadata extraction."""
        measures = []
        measure_pattern = r"measure\s+(?:'([^']+)'|([a-zA-Z0-9_#@]+))\s*=\s*(.+?)(?=\n\s*(?:measure|column|partition|change|lineageTag|annotation|displayFolder)|\n\s*$|\Z)"
        matches = re.finditer(measure_pattern, content, re.DOTALL)
        
        for match in matches:
            name = match.group(1) or match.group(2)
            raw_expr = match.group(3)
            
            # Clean expression and extract formatString/description/isHidden from indented body
            lines = raw_expr.splitlines()
            dax_lines = []
            format_string = None
            description = None
            is_hidden = False
            
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("formatString:"):
                    format_string = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("description:"):
                    description = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped == "isHidden" or stripped.startswith("isHidden:"):
                    is_hidden = True
                elif stripped.startswith("displayFolder:"):
                    pass
                else:
                    dax_lines.append(line)
            
            clean_expr = "\n".join(dax_lines).strip()
            
            measures.append({
                "name": name,
                "expression": clean_expr,
                "formatString": format_string,
                "description": description,
                "isHidden": is_hidden,
            })
            
        return measures

    @staticmethod
    def parse_relationships(content: str) -> List[Dict[str, Any]]:
        """Parse relationship blocks from TMDL content."""
        relationships = []
        rel_pattern = r"relationship\s+(?:'([^']+)'|([a-zA-Z0-9_#-]+))\s*(.+?)(?=\n\s*(?:relationship|table|column|measure|partition|change|lineageTag)|\n\s*$|\Z)"
        matches = re.finditer(rel_pattern, content, re.DOTALL)
        
        for match in matches:
            rel_name = match.group(1) or match.group(2)
            body = match.group(3)
            
            from_table = ""
            from_column = ""
            to_table = ""
            to_column = ""
            cardinality = ""
            cross_filtering = ""
            join_on_date_behavior = ""
            is_active = True
            
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("fromTable:"):
                    from_table = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("fromColumn:"):
                    from_column = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("toTable:"):
                    to_table = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("toColumn:"):
                    to_column = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("cardinality:"):
                    cardinality = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("crossFilteringBehavior:") or stripped.startswith("crossFiltering:"):
                    cross_filtering = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("joinOnDateBehavior:"):
                    join_on_date_behavior = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("isActive:") or stripped == "isActive":
                    if "false" in stripped.lower():
                        is_active = False

            # Support native TMDL format (e.g. fromColumn: Fact.Date)
            if not from_table and "." in from_column:
                from_table, from_column = from_column.rsplit(".", 1)
                from_table = from_table.strip().strip('"').strip("'")
                from_column = from_column.strip().strip('"').strip("'")
            if not to_table and "." in to_column:
                to_table, to_column = to_column.rsplit(".", 1)
                to_table = to_table.strip().strip('"').strip("'")
                to_column = to_column.strip().strip('"').strip("'")

            if from_table and to_table and from_column and to_column:
                relationships.append({
                    "name": rel_name,
                    "fromTable": from_table,
                    "fromColumn": from_column,
                    "toTable": to_table,
                    "toColumn": to_column,
                    "isActive": is_active,
                    "cardinality": cardinality,
                    "crossFiltering": cross_filtering,
                    "joinOnDateBehavior": join_on_date_behavior,
                })
                
        return relationships
