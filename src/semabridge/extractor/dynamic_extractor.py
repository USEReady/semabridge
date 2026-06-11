"""Dynamic extractor that works with ANY schema - no hardcoded assumptions."""

from typing import Dict, List, Any, Optional, Set
from collections import defaultdict
import re

class DynamicSchemaExtractor:
    """
    Extracts schema information dynamically without hardcoded table/column names.
    Works with ANY database schema.
    """
    
    def __init__(self, connection):
        self.conn = connection
        self.cursor = connection.cursor()
    
    def extract_full_schema(self) -> Dict[str, Any]:
        """
        Extract complete schema dynamically.
        Returns structure with NO hardcoded assumptions.
        """
        schema = {
            'tables': {},
            'relationships': [],
            'primary_keys': {},
            'foreign_keys': {},
            'column_statistics': {},
        }
        
        # Get all tables
        tables = self._get_all_tables()
        
        for table in tables:
            # Extract table metadata
            schema['tables'][table] = {
                'columns': self._get_table_columns(table),
                'row_count': self._get_row_count(table),
                'sample_data': self._get_sample_data(table, limit=5),
                'primary_keys': self._get_primary_keys(table),
                'indexes': self._get_indexes(table),
            }
        
        # Infer relationships dynamically
        schema['relationships'] = self._infer_relationships(schema['tables'])
        
        # Infer fact/dimension tables dynamically
        schema['table_types'] = self._infer_table_types(schema['tables'])
        
        return schema
    
    def _get_all_tables(self) -> List[str]:
        """Get ALL tables - no filtering."""
        self.cursor.execute("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """)
        return [row[0] for row in self.cursor.fetchall()]
    
    def _get_table_columns(self, table_name: str) -> Dict[str, Any]:
        """Get ALL columns with their metadata."""
        self.cursor.execute(f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = '{table_name}'
            ORDER BY ORDINAL_POSITION
        """)
        
        columns = {}
        for row in self.cursor.fetchall():
            columns[row[0]] = {
                'data_type': row[1],
                'is_nullable': row[2] == 'YES',
                'default': row[3],
                'max_length': row[4],
                'precision': row[5],
                'scale': row[6],
                'is_numeric': row[1].upper() in ['INT', 'BIGINT', 'DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE'],
                'is_date': row[1].upper() in ['DATE', 'DATETIME', 'TIMESTAMP'],
                'is_string': row[1].upper() in ['VARCHAR', 'CHAR', 'TEXT', 'STRING'],
            }
        
        return columns
    
    def _get_primary_keys(self, table_name: str) -> List[str]:
        """Get primary keys dynamically."""
        self.cursor.execute(f"""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
            WHERE TABLE_NAME = '{table_name}'
            AND CONSTRAINT_NAME LIKE 'PK_%'
        """)
        return [row[0] for row in self.cursor.fetchall()]
    
    def _get_foreign_keys(self, table_name: str) -> List[Dict]:
        """Get foreign keys dynamically."""
        self.cursor.execute(f"""
            SELECT 
                FK.COLUMN_NAME,
                PK.TABLE_NAME AS REFERENCED_TABLE,
                PK.COLUMN_NAME AS REFERENCED_COLUMN
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE FK
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE PK 
                ON FK.CONSTRAINT_NAME = PK.CONSTRAINT_NAME
            WHERE FK.TABLE_NAME = '{table_name}'
            AND FK.CONSTRAINT_NAME LIKE 'FK_%'
        """)
        
        fks = []
        for row in self.cursor.fetchall():
            fks.append({
                'column': row[0],
                'referenced_table': row[1],
                'referenced_column': row[2]
            })
        return fks

    def _get_indexes(self, table_name: str) -> List[str]:
        """Get indexes dynamically (placeholder for Snowflake compatibility)."""
        return []
    
    def _get_row_count(self, table_name: str) -> int:
        """Get row count dynamically."""
        try:
            self.cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            return self.cursor.fetchone()[0]
        except Exception:
            return 0
    
    def _get_sample_data(self, table_name: str, limit: int = 5) -> List[Dict]:
        """Get sample data for type inference."""
        try:
            self.cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
            columns = [desc[0] for desc in self.cursor.description]
            rows = []
            for row in self.cursor.fetchall():
                rows.append(dict(zip(columns, row)))
            return rows
        except Exception:
            return []
    
    def _infer_relationships(self, tables: Dict) -> List[Dict]:
        """Infer relationships dynamically by analyzing data patterns."""
        relationships = []
        
        for table_name, table_info in tables.items():
            # Check for foreign key constraints first
            fks = self._get_foreign_keys(table_name)
            for fk in fks:
                relationships.append({
                    'from_table': table_name,
                    'from_column': fk['column'],
                    'to_table': fk['referenced_table'],
                    'to_column': fk['referenced_column'],
                    'type': 'foreign_key',
                    'confidence': 1.0
                })
            
            # If no foreign keys, try to infer from column naming patterns
            if not fks:
                inferred = self._infer_relationships_by_naming(table_name, table_info, tables)
                relationships.extend(inferred)
        
        return relationships
    
    def _infer_relationships_by_naming(self, table_name: str, table_info: Dict, all_tables: Dict) -> List[Dict]:
        """Infer relationships from column naming patterns."""
        relationships = []
        
        for col_name, col_info in table_info['columns'].items():
            # Pattern: column ends with 'ID' or '_ID'
            if col_name.upper().endswith('ID') or col_name.upper().endswith('_ID'):
                # Try to find matching primary key in another table
                potential_target = col_name.upper().replace('ID', '').replace('_ID', '').rstrip('_')
                
                for other_table, other_info in all_tables.items():
                    if other_table == table_name:
                        continue
                    
                    # Check if potential target table exists
                    if other_table.upper() == potential_target or potential_target in other_table.upper():
                        # Look for matching primary key
                        for pk in other_info.get('primary_keys', []):
                            relationships.append({
                                'from_table': table_name,
                                'from_column': col_name,
                                'to_table': other_table,
                                'to_column': pk,
                                'type': 'inferred',
                                'confidence': 0.7
                            })
        
        return relationships
    
    def _infer_table_types(self, tables: Dict) -> Dict[str, str]:
        """Dynamically infer fact vs dimension tables."""
        table_types = {}
        
        # Calculate metrics for each table
        for table_name, table_info in tables.items():
            score = {
                'fact': 0,
                'dimension': 0
            }
            
            # Fact table indicators
            if table_info.get('row_count', 0) > 10000:
                score['fact'] += 1
            
            # Has numeric columns (potential measures)
            numeric_cols = sum(1 for col in table_info['columns'].values() if col.get('is_numeric'))
            if numeric_cols > 3:
                score['fact'] += 1
            
            # Has foreign keys (relationships)
            fk_count = len(self._get_foreign_keys(table_name))
            if fk_count > 2:
                score['fact'] += 1
            
            # Dimension table indicators
            if table_info.get('row_count', 0) < 10000:
                score['dimension'] += 1
            
            # Has primarily string columns
            string_cols = sum(1 for col in table_info['columns'].values() if col.get('is_string'))
            if string_cols > numeric_cols:
                score['dimension'] += 1
            
            # Has primary key
            if table_info.get('primary_keys'):
                score['dimension'] += 1
            
            # Determine type
            if score['fact'] > score['dimension']:
                table_types[table_name] = 'fact'
            elif score['dimension'] > score['fact']:
                table_types[table_name] = 'dimension'
            else:
                table_types[table_name] = 'unknown'
        
        return table_types
    
    def infer_numeric_columns(self, table_name: str) -> List[str]:
        """Dynamically find columns suitable for aggregation."""
        columns = self._get_table_columns(table_name)
        
        numeric_cols = []
        for col_name, col_info in columns.items():
            # By data type
            if col_info.get('is_numeric'):
                numeric_cols.append(col_name)
            # By sample data pattern
            elif col_info.get('is_string'):
                sample = self._get_sample_data(table_name, limit=10)
                # Check if all samples are numeric
                all_numeric = all(
                    str(row.get(col_name, '')).replace('.', '').replace('-', '').isdigit()
                    for row in sample if row.get(col_name)
                )
                if all_numeric and len(sample) > 0:
                    numeric_cols.append(col_name)
        
        return numeric_cols
    
    def infer_date_columns(self, table_name: str) -> List[str]:
        """Dynamically find date columns."""
        columns = self._get_table_columns(table_name)
        
        date_cols = []
        for col_name, col_info in columns.items():
            # By data type
            if col_info.get('is_date'):
                date_cols.append(col_name)
            # By name pattern
            elif any(pattern in col_name.upper() for pattern in ['DATE', 'DAY', 'MONTH', 'YEAR', 'CAL']):
                date_cols.append(col_name)
        
        return date_cols


class DynamicValueExtractor:
    """
    Extracts values dynamically without assuming column names.
    Uses schema introspection to find appropriate columns.
    """
    
    def __init__(self, extractor: DynamicSchemaExtractor):
        self.extractor = extractor
    
    def extract_measures(self, table_name: str) -> List[Dict]:
        """Extract potential measures dynamically."""
        numeric_cols = self.extractor.infer_numeric_columns(table_name)
        
        measures = []
        for col in numeric_cols:
            measures.append({
                'name': col,
                'source_column': col,
                'aggregations': ['SUM', 'AVG', 'COUNT', 'MIN', 'MAX'],
                'suggested_aggregation': self._suggest_aggregation(col),
            })
        
        return measures
    
    def _suggest_aggregation(self, column_name: str) -> str:
        """Suggest aggregation based on column name patterns."""
        col_upper = column_name.upper()
        
        if 'PRICE' in col_upper or 'AMOUNT' in col_upper or 'REVENUE' in col_upper:
            return 'SUM'
        elif 'QTY' in col_upper or 'UNITS' in col_upper or 'COUNT' in col_upper:
            return 'SUM'
        elif 'RATING' in col_upper or 'SCORE' in col_upper:
            return 'AVG'
        elif 'ID' in col_upper or 'KEY' in col_upper:
            return 'COUNT'
        else:
            return 'SUM'  # Default
    
    def extract_dimensions(self, table_name: str) -> List[Dict]:
        """Extract potential dimensions dynamically."""
        columns = self.extractor._get_table_columns(table_name)
        
        dimensions = []
        for col_name, col_info in columns.items():
            # Skip obviously numeric columns
            if col_info.get('is_numeric') and not self._is_id_column(col_name):
                continue
            
            dimensions.append({
                'name': col_name,
                'source_column': col_name,
                'data_type': col_info['data_type'],
                'is_hierarchy': self._is_hierarchy_column(col_name),
            })
        
        return dimensions
    
    def _is_id_column(self, column_name: str) -> bool:
        """Check if column looks like an ID."""
        col_upper = column_name.upper()
        return col_upper.endswith('ID') or col_upper.endswith('_ID') or col_upper == 'ID'
    
    def _is_hierarchy_column(self, column_name: str) -> bool:
        """Check if column might be part of a hierarchy."""
        col_upper = column_name.upper()
        hierarchy_keywords = ['CATEGORY', 'SEGMENT', 'GROUP', 'TYPE', 'CLASS']
        return any(kw in col_upper for kw in hierarchy_keywords)


# Usage - Completely dynamic, no hardcoded assumptions
def extract_any_model(connection):
    """Extract ANY model dynamically."""
    extractor = DynamicSchemaExtractor(connection)
    value_extractor = DynamicValueExtractor(extractor)
    
    # Get all tables
    schema = extractor.extract_full_schema()
    
    # For each table, extract measures and dimensions
    all_measures = []
    all_dimensions = []
    
    for table_name, table_type in schema['table_types'].items():
        if table_type == 'fact':
            measures = value_extractor.extract_measures(table_name)
            all_measures.extend(measures)
        else:
            dimensions = value_extractor.extract_dimensions(table_name)
            all_dimensions.extend(dimensions)
    
    return {
        'schema': schema,
        'measures': all_measures,
        'dimensions': all_dimensions,
        'relationships': schema['relationships']
    }
