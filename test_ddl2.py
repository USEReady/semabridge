import os
import sys
import logging
import json

logging.basicConfig(level=logging.DEBUG)
sys.path.insert(0, os.path.abspath('src'))

from semabridge.api.models.semantic_model import SemanticModel
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.utils.identifiers import IdentifierSanitizer
import pydantic

def main():
    model_path = r'output\debug\8c8482d3-98b8-4609-945d-a1e91023fe90\transformed_sml.json'
    with open(model_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    model = SemanticModel(**data)
    
    class MockConfig:
        database = "SEMABRIDGE_DB"
        schema_name = "PUBLIC"
        role = "SYSADMIN"
        warehouse = "COMPUTE_WH"

    class MockBehaviorSnowflake:
        source_table_mapping = {}
        auto_add_anchors = True
        dimension_resolution_mode = "strict"

    class MockBehavior:
        snowflake = MockBehaviorSnowflake()

    emitter = SnowflakeEmitter(MockConfig(), MockBehavior())
    emitter.identifier_sanitizer = IdentifierSanitizer(suppress_reserved=True)
    
    # Need to properly mock context or use the real emit method
    ddls = emitter.generate_ddls(model)
    with open("ddl_output.sql", "w", encoding="utf-8") as f:
        for ddl in ddls:
            f.write(ddl + "\n\n")

if __name__ == '__main__':
    main()
