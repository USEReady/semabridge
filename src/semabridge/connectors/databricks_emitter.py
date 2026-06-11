"""Databricks Emitter for pure OSI deployments."""

from typing import Any, Dict, List, Optional
import time

from semabridge.utils.logger import get_logger
from semabridge.intermediate.models import OSIModel
from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.extractor.dynamic_extractor import DynamicSchemaExtractor

logger = get_logger(__name__)

class MockDatabricksCursor:
    def __init__(self, publisher: DatabricksPublisher):
        self.publisher = publisher
        self._results = []
        self.description = []

    def execute(self, query: str):
        try:
            rows = self.publisher.execute_statements([query])
            if rows:
                payload = rows[0] if isinstance(rows[0], dict) else {}
                result_block = payload.get("result", {})
                
                # Fetch description / schema
                manifest = payload.get("manifest", {})
                schema_block = manifest.get("schema", {})
                columns = schema_block.get("columns", [])
                self.description = [(col.get("name", ""),) for col in columns]
                
                # Fetch data array
                self._results = result_block.get("data_array", [])
        except Exception as e:
            logger.error(f"MockDatabricksCursor error executing '{query[:50]}...': {e}")
            self._results = []
            self.description = []

    def fetchall(self):
        return self._results

    def fetchone(self):
        return self._results[0] if self._results else None


class MockDatabricksConnection:
    def __init__(self, publisher: DatabricksPublisher):
        self.publisher = publisher

    def cursor(self):
        return MockDatabricksCursor(self.publisher)


class DatabricksEmitter:
    """Emits OSI models natively to Databricks."""

    def __init__(self, config: Any, behavior: Any = None):
        self.config = config
        self.behavior = behavior
        self.publisher = DatabricksPublisher(config, behavior)
        self.last_deployment_error: Optional[str] = None
        self._live_schema_metadata: Dict[str, set[str]] = {}

    def deploy_from_osi(self, osi: OSIModel) -> bool:
        """Core entry point for OSI native deployment to Databricks."""
        try:
            self.last_deployment_error = None
            logger.info(f"Starting OSI deployment to Databricks for '{osi.unique_name}'")
            
            # 1. Identify fact table and numeric columns dynamically
            conn = MockDatabricksConnection(self.publisher)
            
            # The dynamic extractor queries system.information_schema
            # We must override the query in DynamicSchemaExtractor to point to catalog.information_schema 
            # if we are doing it properly, or just use it as is if catalog defaults apply.
            extractor = DynamicSchemaExtractor(conn)
            
            # Temporarily patch extractor to use catalog name for Databricks
            catalog = self.config.catalog
            schema_name = self.config.schema_name
            
            def dbx_get_all_tables():
                extractor.cursor.execute(f"SELECT table_name FROM {catalog}.information_schema.tables WHERE table_schema = '{schema_name}' AND table_type = 'BASE TABLE'")
                return [row[0] for row in extractor.cursor.fetchall()]
                
            def dbx_get_table_columns(table_name):
                extractor.cursor.execute(f"SELECT column_name, data_type, is_nullable, column_default, character_maximum_length, numeric_precision, numeric_scale FROM {catalog}.information_schema.columns WHERE table_schema = '{schema_name}' AND table_name = '{table_name}' ORDER BY ordinal_position")
                columns = {}
                for row in extractor.cursor.fetchall():
                    dt = row[1]
                    columns[row[0]] = {
                        'data_type': dt,
                        'is_nullable': row[2] == 'YES',
                        'default': row[3],
                        'max_length': row[4],
                        'precision': row[5],
                        'scale': row[6],
                        'is_numeric': str(dt).upper() in ['INT', 'BIGINT', 'DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE', 'TINYINT', 'SMALLINT'],
                        'is_date': str(dt).upper() in ['DATE', 'DATETIME', 'TIMESTAMP'],
                        'is_string': str(dt).upper() in ['VARCHAR', 'CHAR', 'TEXT', 'STRING'],
                    }
                return columns
                
            def dbx_get_primary_keys(table_name):
                extractor.cursor.execute(f"SELECT column_name FROM {catalog}.information_schema.key_column_usage WHERE table_schema = '{schema_name}' AND table_name = '{table_name}' AND constraint_name LIKE 'PK_%'")
                return [row[0] for row in extractor.cursor.fetchall()]
                
            def dbx_get_foreign_keys(table_name):
                extractor.cursor.execute(f"SELECT fk.column_name, pk.table_name AS referenced_table, pk.column_name AS referenced_column FROM {catalog}.information_schema.key_column_usage fk JOIN {catalog}.information_schema.key_column_usage pk ON fk.constraint_name = pk.constraint_name WHERE fk.table_schema = '{schema_name}' AND fk.table_name = '{table_name}' AND fk.constraint_name LIKE 'FK_%'")
                fks = []
                for row in extractor.cursor.fetchall():
                    fks.append({'column': row[0], 'referenced_table': row[1], 'referenced_column': row[2]})
                return fks

            # Monkey patch the databricks specific queries
            extractor._get_all_tables = dbx_get_all_tables
            extractor._get_table_columns = dbx_get_table_columns
            extractor._get_primary_keys = dbx_get_primary_keys
            extractor._get_foreign_keys = dbx_get_foreign_keys

            schema = extractor.extract_full_schema()
            
            fact_tables = [(t, s) for t, s in schema['table_types'].items() if s == 'fact']
            if not fact_tables:
                logger.warning("No fact tables identified dynamically.")
                return True # Nothing to enrich
                
            fact_table = fact_tables[0][0]
            logger.info(f"Dynamically identified Fact Table: {fact_table}")
            
            # Generate Enriched View
            enriched_view_name = f"{fact_table}_ENRICHED"
            fact_source_ref = f"{catalog}.{schema_name}.{fact_table}"
            
            numeric_cols = extractor.infer_numeric_columns(fact_table)
            
            select_parts = ["SELECT f.*"]
            for col in numeric_cols[:3]:
                agg_name = f"TOTAL_{col}_ALL"
                select_parts.append(f'        , (SELECT SUM({col}) FROM {fact_source_ref}) AS {agg_name}')
                
            query = f"CREATE OR REPLACE VIEW {catalog}.{schema_name}.{enriched_view_name} AS\n" + "\n".join(select_parts) + f"\nFROM {fact_source_ref} f"
            
            logger.info(f"Executing Enriched View for Databricks: {enriched_view_name}")
            self.publisher.execute_statements([query])
            return True
        except Exception as e:
            logger.error(f"Databricks OSI deployment failed: {e}")
            self.last_deployment_error = str(e)
            return False

