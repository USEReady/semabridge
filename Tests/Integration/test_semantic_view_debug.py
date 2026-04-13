#!/usr/bin/env python3
"""
Test script to trigger semantic view generation with comprehensive debugging.
"""
import logging
import sys
from pathlib import Path

# Configure logging to show ERROR level messages
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('semantic_view_debug.log')
    ]
)

logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_semantic_view_debug():
    try:
        from src.semabridge.formats.sml.loader import SMLLoader
        from src.semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        from src.semabridge.core.settings import SnowflakeConfig
        from src.semabridge.core.behavior import ConnectorBehavior, SnowflakeBehavior
        from src.semabridge.utils.identifiers import IdentifierSanitizer
        
        # Load the SML model
        logger.error("Loading SML model...")
        loader = SMLLoader()
        
        # Find the SML file from your workspace
        sml_path = Path(__file__).parent / "semabridge.yaml"  # Adjust path as needed
        if not sml_path.exists():
            print(f"SML file not found at {sml_path}")
            return
        
        sml_model = loader.load(str(sml_path))
        logger.error(f"Loaded model: {sml_model.label}")
        
        # Create emitter with dummy Snowflake config (we won't actually connect)
        config = SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            database="MOCK_DB",
            schema_name="MOCK_SCHEMA"
        )
        behavior = ConnectorBehavior()
        id_sanitizer = IdentifierSanitizer()
        
        emitter = SnowflakeEmitter(config, behavior, id_sanitizer)
        
        # Generate DDLs - this will trigger all the logging
        logger.error("Generating semantic view DDL...")
        ddls = emitter.generate_ddls(sml_model)
        
        if ddls:
            logger.error(f"\nGenerated {len(ddls)} DDL statements")
            for i, ddl in enumerate(ddls, 1):
                logger.error(f"\n===== DDL {i} =====")
                logger.error(ddl)
        
        logger.error("\n=== DEBUG COMPLETE ===")
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise
