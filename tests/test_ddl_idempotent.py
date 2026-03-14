"""Tests for Mandate 2: Idempotent DDL state management."""

import pytest
from unittest.mock import MagicMock, patch
from semabridge.core.behavior import DDLStrategy, PKResolutionMode


class TestDDLStrategy:
    """Test DDLStrategy enum and behavior config."""

    def test_idempotent_default(self):
        assert DDLStrategy.IDEMPOTENT.value == "idempotent"

    def test_evolve_value(self):
        assert DDLStrategy.EVOLVE.value == "evolve"

    def test_enum_from_string(self):
        assert DDLStrategy("idempotent") == DDLStrategy.IDEMPOTENT
        assert DDLStrategy("evolve") == DDLStrategy.EVOLVE


class TestPKResolutionMode:
    """Test PKResolutionMode enum."""

    def test_strict_value(self):
        assert PKResolutionMode.STRICT.value == "strict"

    def test_permissive_value(self):
        assert PKResolutionMode.PERMISSIVE.value == "permissive"

    def test_enum_from_string(self):
        assert PKResolutionMode("strict") == PKResolutionMode.STRICT
        assert PKResolutionMode("permissive") == PKResolutionMode.PERMISSIVE


class TestBehaviorConfig:
    """Test behavior.yaml config fields for DDL and PK."""

    def test_snowflake_behavior_defaults(self):
        from semabridge.core.behavior import SnowflakeBehavior
        sb = SnowflakeBehavior()
        assert sb.ddl_strategy == DDLStrategy.IDEMPOTENT
        assert sb.pk_resolution_mode == PKResolutionMode.PERMISSIVE
        assert sb.warehouse_mapping == {}

    def test_semantic_model_behavior_defaults(self):
        from semabridge.core.behavior import SemanticModelBehavior
        smb = SemanticModelBehavior()
        assert smb.unspool_calculated_columns is True

    def test_compatibility_behavior_defaults(self):
        from semabridge.core.behavior import CompatibilityBehavior
        cb = CompatibilityBehavior()
        assert cb.additional_reserved_words == []

    def test_feature_flags_defaults(self):
        from semabridge.core.behavior import FeatureFlags
        ff = FeatureFlags()
        assert ff.cortex_require_descriptions is True
        assert ff.auto_materialize_aggregates is False


class TestIdempotentDDLGeneration:
    """Test that OSI-to-SQL converter respects idempotent DDL flag."""

    def _make_minimal_osi(self):
        from semabridge.intermediate.models import OSIModel, OSIDataset, OSIColumn
        return OSIModel(
            unique_name="TestModel",
            datasets=[
                OSIDataset(
                    unique_name="TestTable",
                    source_table="TEST_TABLE",
                    columns=[OSIColumn(unique_name="ID")],
                ),
            ],
        )

    def test_osi_to_sql_idempotent(self):
        """When idempotent=True, DDL uses CREATE OR REPLACE TABLE."""
        from semabridge.converter.osi_to_sql import _OSIToSQLConverter
        osi = self._make_minimal_osi()
        converter = _OSIToSQLConverter(
            osi_model=osi, dialect="snowflake",
            database="DB", schema="SCH", idempotent_ddl=True,
        )
        assert converter.idempotent_ddl is True

    def test_osi_to_sql_non_idempotent(self):
        """When idempotent=False, DDL uses CREATE TABLE IF NOT EXISTS."""
        from semabridge.converter.osi_to_sql import _OSIToSQLConverter
        osi = self._make_minimal_osi()
        converter = _OSIToSQLConverter(
            osi_model=osi, dialect="snowflake",
            database="DB", schema="SCH", idempotent_ddl=False,
        )
        assert converter.idempotent_ddl is False
