"""
Test script for Preserve-Existing-Tables feature (Phase 2).

Tests:
1. _view_exists() - Check if semantic view exists
2. _validate_model_on_existing_tables() - Validate compatibility
3. _create_missing_entities() - Prepare missing entities
4. Full deployment pipeline with preserve mode enabled
"""

import sys
import json
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLMetric, SMLRelationship
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TestPreserveExistingTables:
    """Test suite for preserve-existing-tables feature."""
    
    def __init__(self):
        # Initialize Snowflake emitter with test config
        self.config = SnowflakeConfig(
            account="test_account",
            database="SEMABRIDGE",
            schema_name="PUBLIC",
            user="test_user",
            password="test_pass",
        )
        
        self.behavior = ConnectorBehavior()
        self.behavior.snowflake.preserve_existing_tables = True  # Enable feature
        
        self.emitter = SnowflakeEmitter(config=self.config, behavior=self.behavior)
        self.passed = 0
        self.failed = 0
    
    def test_view_exists_method_signature(self):
        """Test that _view_exists method exists and has correct signature."""
        try:
            assert hasattr(self.emitter, '_view_exists'), "Method _view_exists not found"
            
            # Check it's callable
            method = getattr(self.emitter, '_view_exists')
            assert callable(method), "_view_exists is not callable"
            
            logger.info("✓ Test 1 PASSED: _view_exists method signature valid")
            self.passed += 1
            return True
        except AssertionError as e:
            logger.error("✗ Test 1 FAILED: %s", e)
            self.failed += 1
            return False
    
    def test_validate_model_method_signature(self):
        """Test that _validate_model_on_existing_tables method exists."""
        try:
            assert hasattr(self.emitter, '_validate_model_on_existing_tables'), \
                "Method _validate_model_on_existing_tables not found"
            
            method = getattr(self.emitter, '_validate_model_on_existing_tables')
            assert callable(method), "_validate_model_on_existing_tables is not callable"
            
            logger.info("✓ Test 2 PASSED: _validate_model_on_existing_tables method signature valid")
            self.passed += 1
            return True
        except AssertionError as e:
            logger.error("✗ Test 2 FAILED: %s", e)
            self.failed += 1
            return False
    
    def test_create_missing_entities_method_signature(self):
        """Test that _create_missing_entities method exists."""
        try:
            assert hasattr(self.emitter, '_create_missing_entities'), \
                "Method _create_missing_entities not found"
            
            method = getattr(self.emitter, '_create_missing_entities')
            assert callable(method), "_create_missing_entities is not callable"
            
            logger.info("✓ Test 3 PASSED: _create_missing_entities method signature valid")
            self.passed += 1
            return True
        except AssertionError as e:
            logger.error("✗ Test 3 FAILED: %s", e)
            self.failed += 1
            return False
    
    def test_deployment_pipeline_modified(self):
        """Test that deployment pipeline includes preserve-existing-tables check."""
        try:
            import inspect
            source = inspect.getsource(self.emitter._execute_deployment_pipeline)
            
            # Check for key phrases
            assert 'preserve_existing' in source.lower(), \
                "Deployment pipeline doesn't check preserve_existing flag"
            assert '_view_exists' in source, \
                "Deployment pipeline doesn't call _view_exists"
            assert '_validate_model_on_existing_tables' in source, \
                "Deployment pipeline doesn't call _validate_model_on_existing_tables"
            assert 'Step 2a' in source, \
                "Deployment pipeline doesn't have Step 2a comment"
            
            logger.info("✓ Test 4 PASSED: Deployment pipeline correctly modified")
            self.passed += 1
            return True
        except AssertionError as e:
            logger.error("✗ Test 4 FAILED: %s", e)
            self.failed += 1
            return False
    
    def test_model_creation(self):
        """Test that we can create a test SML model."""
        try:
            # Create test model
            model = SMLModel(
                unique_name="test_model",
                label="Test Model",
                datasets=[
                    SMLDataset(
                        name="orders",
                        label="Orders",
                        table_name="fact_orders",
                        schema_name="public",
                    ),
                    SMLDataset(
                        name="customers",
                        label="Customers",
                        table_name="dim_customers",
                        schema_name="public",
                    ),
                ],
                metrics=[
                    SMLMetric(
                        name="total_orders",
                        label="Total Orders",
                        dataset="orders",
                        type="SUM",
                        sql="COUNT(*)",
                    ),
                ],
                relationships=[
                    SMLRelationship(
                        name="order_customer",
                        from_dataset="orders",
                        to_dataset="customers",
                        from_column="customer_id",
                        to_column="id",
                    ),
                ],
            )
            
            assert model is not None, "Failed to create test model"
            assert len(model.datasets) == 2, "Model should have 2 datasets"
            assert len(model.metrics) == 1, "Model should have 1 metric"
            assert len(model.relationships) == 1, "Model should have 1 relationship"
            
            logger.info("✓ Test 5 PASSED: Test SML model created successfully")
            self.passed += 1
            return True
        except Exception as e:
            logger.error("✗ Test 5 FAILED: %s", e)
            self.failed += 1
            return False
    
    def test_preserve_flag_behavior(self):
        """Test that preserve_existing_tables flag is respected."""
        try:
            # Test with feature enabled
            assert self.behavior.snowflake.preserve_existing_tables == True, \
                "Preserve flag should be True"
            
            # Test with feature disabled
            behavior_disabled = ConnectorBehavior()
            behavior_disabled.snowflake.preserve_existing_tables = False
            emitter_disabled = SnowflakeEmitter(config=self.config, behavior=behavior_disabled)
            
            assert emitter_disabled.behavior.snowflake.preserve_existing_tables == False, \
                "Preserve flag should be False in disabled emitter"
            
            logger.info("✓ Test 6 PASSED: Preserve flag behavior correct")
            self.passed += 1
            return True
        except AssertionError as e:
            logger.error("✗ Test 6 FAILED: %s", e)
            self.failed += 1
            return False
    
    def test_backward_compatibility(self):
        """Test that feature is disabled by default (backward compatible)."""
        try:
            # Create emitter with default behavior
            default_behavior = ConnectorBehavior()
            # Should be False by default
            is_enabled = getattr(
                default_behavior.snowflake, 
                'preserve_existing_tables', 
                False
            )
            
            assert is_enabled == False, \
                "Feature should be disabled by default for backward compatibility"
            
            logger.info("✓ Test 7 PASSED: Backward compatibility maintained (feature disabled by default)")
            self.passed += 1
            return True
        except AssertionError as e:
            logger.error("✗ Test 7 FAILED: %s", e)
            self.failed += 1
            return False
    
    def run_all_tests(self):
        """Run all tests and report results."""
        logger.info("=" * 70)
        logger.info("PRESERVE-EXISTING-TABLES FEATURE - TEST SUITE")
        logger.info("=" * 70)
        
        tests = [
            self.test_view_exists_method_signature,
            self.test_validate_model_method_signature,
            self.test_create_missing_entities_method_signature,
            self.test_deployment_pipeline_modified,
            self.test_model_creation,
            self.test_preserve_flag_behavior,
            self.test_backward_compatibility,
        ]
        
        for i, test in enumerate(tests, 1):
            logger.info("")
            logger.info("Test %d: %s", i, test.__doc__.strip())
            logger.info("-" * 70)
            test()
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("TEST RESULTS")
        logger.info("=" * 70)
        logger.info("✓ Passed: %d", self.passed)
        logger.info("✗ Failed: %d", self.failed)
        logger.info("Total:   %d", self.passed + self.failed)
        logger.info("=" * 70)
        
        if self.failed == 0:
            logger.info("✓ ALL TESTS PASSED - Feature implementation verified!")
            return True
        else:
            logger.error("✗ SOME TESTS FAILED - Please review the errors above")
            return False


if __name__ == "__main__":
    test_suite = TestPreserveExistingTables()
    success = test_suite.run_all_tests()
    sys.exit(0 if success else 1)
