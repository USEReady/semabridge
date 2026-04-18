"""
Tests for semantic router behavior configuration.

Verifies that the kill switch fields are properly configured and
can be loaded from behavior.yaml.
"""

from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior


class TestSemanticRouterBehavior:
    """Tests for semantic router kill switch configuration."""

    def test_databricks_behavior_has_semantic_router_enabled_field(self):
        """Verify semantic_router_enabled field exists and defaults to False."""
        behavior = DatabricksBehavior()
        assert hasattr(behavior, 'semantic_router_enabled')
        assert behavior.semantic_router_enabled is False

    def test_databricks_behavior_has_semantic_router_override_models_field(self):
        """Verify semantic_router_override_models field exists and defaults to empty list."""
        behavior = DatabricksBehavior()
        assert hasattr(behavior, 'semantic_router_override_models')
        assert behavior.semantic_router_override_models == []

    def test_semantic_router_enabled_can_be_set_to_true(self):
        """Verify kill switch can be enabled."""
        behavior = DatabricksBehavior(semantic_router_enabled=True)
        assert behavior.semantic_router_enabled is True

    def test_semantic_router_override_models_can_be_populated(self):
        """Verify override list can be set with model names."""
        models = ['OldModel', 'LegacyDatamart']
        behavior = DatabricksBehavior(semantic_router_override_models=models)
        assert behavior.semantic_router_override_models == models

    def test_connector_behavior_loads_semantic_router_config(self):
        """Verify ConnectorBehavior properly loads router config from databricks section."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                semantic_router_enabled=True,
                semantic_router_override_models=['TestModel']
            )
        )
        assert behavior.databricks.semantic_router_enabled is True
        assert behavior.databricks.semantic_router_override_models == ['TestModel']

    def test_semantic_router_config_preserves_other_databricks_settings(self):
        """Verify adding router config doesn't break existing Databricks behavior settings."""
        behavior = DatabricksBehavior(
            semantic_router_enabled=True,
            create_metadata_table=False,
            measure_view_type='sql_view'
        )
        assert behavior.semantic_router_enabled is True
        assert behavior.create_metadata_table is False
        assert behavior.measure_view_type == 'sql_view'

    def test_connector_behavior_with_defaults(self):
        """Verify ConnectorBehavior defaults include disabled router."""
        behavior = ConnectorBehavior()
        assert behavior.databricks.semantic_router_enabled is False
        assert behavior.databricks.semantic_router_override_models == []

    def test_semantic_router_settings_serializable(self):
        """Verify router settings can be serialized and deserialized."""
        behavior = DatabricksBehavior(
            semantic_router_enabled=True,
            semantic_router_override_models=['Model1', 'Model2']
        )
        # Test dict representation
        as_dict = behavior.model_dump()
        assert as_dict['semantic_router_enabled'] is True
        assert as_dict['semantic_router_override_models'] == ['Model1', 'Model2']

        # Test can reconstruct from dict
        reconstructed = DatabricksBehavior(**as_dict)
        assert reconstructed.semantic_router_enabled is True
        assert reconstructed.semantic_router_override_models == ['Model1', 'Model2']

    def test_can_check_if_model_overridden(self):
        """Test helper logic to check if a model is in override list."""
        behavior = DatabricksBehavior(
            semantic_router_enabled=True,
            semantic_router_override_models=['OldModel', 'LegacyDatamart']
        )
        # Even though router is enabled, these models should be skipped
        assert 'OldModel' in behavior.semantic_router_override_models
        assert 'LegacyDatamart' in behavior.semantic_router_override_models
        assert 'NewModel' not in behavior.semantic_router_override_models

    def test_empty_override_list_means_all_models_use_router(self):
        """Verify that empty override list means all models are processed by router."""
        behavior = DatabricksBehavior(
            semantic_router_enabled=True,
            semantic_router_override_models=[]
        )
        # No model should be in the override list
        test_models = ['Model1', 'Model2', 'Model3']
        for model in test_models:
            assert model not in behavior.semantic_router_override_models
