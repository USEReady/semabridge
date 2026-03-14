"""
Hierarchy Detector.

Detects dimension hierarchies in tables based on:
1. Date/time column patterns
2. Geographic column patterns
3. Category/subcategory patterns
4. Custom patterns via configuration
"""

from __future__ import annotations

import re
from typing import Any

from semabridge.sml.models import DataType
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class HierarchyDetector:
    """
    Detects hierarchies in dimension tables.
    
    Supports common patterns:
    - Date: Year -> Quarter -> Month -> Day
    - Geography: Country -> State/Region -> City
    - Category: Category -> Subcategory
    - Organization: Department -> Team -> Employee
    """
    
    # Date hierarchy patterns
    DATE_PATTERNS = {
        "year": ["YEAR", "YR", "YYYY", "CALENDAR_YEAR", "FISCAL_YEAR"],
        "quarter": ["QUARTER", "QTR", "Q", "CALENDAR_QUARTER", "FISCAL_QUARTER"],
        "month": ["MONTH", "MON", "MM", "MONTH_NAME", "MONTH_NUM"],
        "week": ["WEEK", "WK", "WEEK_NUM", "WEEK_OF_YEAR"],
        "day": ["DAY", "DY", "DD", "DAY_OF_MONTH", "DAY_NUM"],
        "date": ["DATE", "DT", "FULL_DATE", "CALENDAR_DATE"],
    }
    
    # Date hierarchy order
    DATE_HIERARCHY_ORDER = ["year", "quarter", "month", "week", "day", "date"]
    
    # Geography patterns
    GEO_PATTERNS = {
        "continent": ["CONTINENT", "REGION_GLOBAL"],
        "country": ["COUNTRY", "COUNTRY_NAME", "COUNTRY_CODE", "NATION"],
        "state": ["STATE", "STATE_NAME", "STATE_CODE", "PROVINCE", "REGION"],
        "city": ["CITY", "CITY_NAME", "MUNICIPALITY", "TOWN"],
        "postal": ["POSTAL_CODE", "ZIP", "ZIP_CODE", "POSTCODE"],
    }
    
    # Geography hierarchy order
    GEO_HIERARCHY_ORDER = ["continent", "country", "state", "city", "postal"]
    
    # Category patterns
    CATEGORY_PATTERNS = {
        "category": ["CATEGORY", "CATEGORY_NAME", "CAT", "PRODUCT_CATEGORY", "TYPE"],
        "subcategory": ["SUBCATEGORY", "SUBCATEGORY_NAME", "SUBCAT", "PRODUCT_SUBCATEGORY", "SUBTYPE"],
        "group": ["GROUP", "GROUP_NAME", "PRODUCT_GROUP"],
    }
    
    CATEGORY_HIERARCHY_ORDER = ["category", "subcategory", "group"]
    
    # Organization patterns
    ORG_PATTERNS = {
        "company": ["COMPANY", "COMPANY_NAME", "ORGANIZATION", "ORG"],
        "division": ["DIVISION", "DIVISION_NAME", "BUSINESS_UNIT"],
        "department": ["DEPARTMENT", "DEPT", "DEPARTMENT_NAME"],
        "team": ["TEAM", "TEAM_NAME", "UNIT"],
    }
    
    ORG_HIERARCHY_ORDER = ["company", "division", "department", "team"]
    
    def __init__(
        self,
        tables: dict[str, dict[str, Any]],
        columns: dict[str, list[dict[str, Any]]],
    ):
        """
        Initialize the detector.
        
        Args:
            tables: Dict of table_name -> table metadata
            columns: Dict of table_name -> list of column metadata
        """
        self.tables = tables
        self.columns = columns
    
    def detect_all(self) -> dict[str, list[dict[str, Any]]]:
        """
        Detect all hierarchies in all tables.
        
        Returns:
            Dict of table_name -> list of hierarchy definitions
        """
        results = {}
        
        for table_name, cols in self.columns.items():
            hierarchies = self.detect_for_table(table_name, cols)
            if hierarchies:
                results[table_name] = hierarchies
        
        total = sum(len(h) for h in results.values())
        logger.info(f"Detected {total} hierarchies across {len(results)} tables")
        
        return results
    
    def detect_for_table(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Detect hierarchies for a specific table.
        
        Args:
            table_name: Table name
            columns: List of column metadata
            
        Returns:
            List of hierarchy definitions
        """
        hierarchies = []
        
        # Check for date hierarchy
        date_hierarchy = self._detect_pattern_hierarchy(
            table_name,
            columns,
            self.DATE_PATTERNS,
            self.DATE_HIERARCHY_ORDER,
            "Date",
        )
        if date_hierarchy:
            hierarchies.append(date_hierarchy)
        
        # Check for geography hierarchy
        geo_hierarchy = self._detect_pattern_hierarchy(
            table_name,
            columns,
            self.GEO_PATTERNS,
            self.GEO_HIERARCHY_ORDER,
            "Geography",
        )
        if geo_hierarchy:
            hierarchies.append(geo_hierarchy)
        
        # Check for category hierarchy
        cat_hierarchy = self._detect_pattern_hierarchy(
            table_name,
            columns,
            self.CATEGORY_PATTERNS,
            self.CATEGORY_HIERARCHY_ORDER,
            "Category",
        )
        if cat_hierarchy:
            hierarchies.append(cat_hierarchy)
        
        # Check for organization hierarchy
        org_hierarchy = self._detect_pattern_hierarchy(
            table_name,
            columns,
            self.ORG_PATTERNS,
            self.ORG_HIERARCHY_ORDER,
            "Organization",
        )
        if org_hierarchy:
            hierarchies.append(org_hierarchy)
        
        return hierarchies
    
    def _detect_pattern_hierarchy(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
        patterns: dict[str, list[str]],
        order: list[str],
        hierarchy_type: str,
    ) -> dict[str, Any] | None:
        """
        Detect a hierarchy based on column patterns.
        
        Args:
            table_name: Table name
            columns: Column list
            patterns: Dict of level_name -> list of patterns to match
            order: Order of levels (coarsest to finest)
            hierarchy_type: Type name for the hierarchy
            
        Returns:
            Hierarchy definition or None
        """
        col_names = {col["name"].upper(): col["name"] for col in columns}
        
        # Find matching columns for each level
        matched_levels = []
        for level_name in order:
            level_patterns = patterns.get(level_name, [])
            for pattern in level_patterns:
                # Try exact match
                if pattern.upper() in col_names:
                    matched_levels.append({
                        "name": level_name.title(),
                        "attribute": col_names[pattern.upper()],
                    })
                    break
                
                # Try suffix match
                for col_upper, col_original in col_names.items():
                    if col_upper.endswith(f"_{pattern.upper()}") or col_upper == pattern.upper():
                        matched_levels.append({
                            "name": level_name.title(),
                            "attribute": col_original,
                        })
                        break
                else:
                    continue
                break
        
        # Need at least 2 levels for a hierarchy
        if len(matched_levels) >= 2:
            return {
                "name": f"{table_name} {hierarchy_type} Hierarchy",
                "label": f"{hierarchy_type} Hierarchy",
                "type": hierarchy_type.lower(),
                "levels": matched_levels,
            }
        
        return None
    
    def detect_date_dimension(
        self,
        columns: list[dict[str, Any]],
    ) -> bool:
        """
        Check if columns indicate a date dimension table.
        Robust check using Data Types and/or Naming.
        """
        date_cols = 0
        total_cols = len(columns)
        
        col_names_upper = {col["name"].upper() for col in columns}
        
        # 1. Check Data Types if available
        for col in columns:
            dtype = DataType.from_snowflake(col.get("data_type", "VARCHAR"))
            if dtype in (DataType.DATE, DataType.DATETIME):
                date_cols += 1
            elif "DATE" in col["name"].upper():
                 date_cols += 1
                 
        # 2. Heuristics
        # If > 30% of columns are date-related, it's likely a date dimension
        # Or if it contains specific standard keys
        is_high_date_density = (date_cols / total_cols > 0.3) if total_cols > 0 else False
        
        has_standard_keys = any(k in col_names_upper for k in ["DATE_KEY", "DATEID", "FULL_DATE", "CALENDAR_DATE"])
        
        return is_high_date_density or has_standard_keys
    
    def suggest_role_playing_dimensions(
        self,
        date_table: str,
        fact_tables: list[str],
    ) -> list[dict[str, Any]]:
        """
        Suggest role-playing dimensions for date columns in fact tables.
        
        Args:
            date_table: Name of the date dimension table
            fact_tables: List of fact table names
            
        Returns:
            List of role-playing dimension suggestions
        """
        suggestions = []
        
        # Common date role-playing patterns
        role_patterns = {
            "ORDER": ["ORDER_DATE", "ORDERDATE", "ORDER_DT", "SALES_DATE"],
            "SHIP": ["SHIP_DATE", "SHIPDATE", "SHIP_DT", "SHIPPING_DATE"],
            "DUE": ["DUE_DATE", "DUEDATE", "DUE_DT"],
            "CREATE": ["CREATE_DATE", "CREATEDATE", "CREATED_DATE", "CREATED_AT"],
            "MODIFIED": ["MODIFIED_DATE", "MODIFIEDDATE", "UPDATED_DATE", "UPDATED_AT"],
        }
        
        for fact_table in fact_tables:
            cols = self.columns.get(fact_table, [])
            col_names_upper = {col["name"].upper(): col["name"] for col in cols}
            
            for role_name, patterns in role_patterns.items():
                for pattern in patterns:
                    if pattern in col_names_upper:
                        suggestions.append({
                            "role": role_name,
                            "fact_table": fact_table,
                            "fact_column": col_names_upper[pattern],
                            "dimension": date_table,
                        })
                        break
        
        return suggestions
