"""
Test Snowflake semantic model name propagation.

Ensures that the Snowflake model name (from config or dataset_id) is correctly
propagated to the SML model name and label during extraction and conversion.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from semabridge.core.engine.context import RunContext
from semabridge.core.source_format import SourceFormat
from semabridge.core.settings import Settings
from semabridge.core.behavior import ConnectorBehavior


def test_convert_snowflake_to_sml_uses_semantic_view_name():
    """
    Ensure SMLModel is named after sf.semantic_view_name if available.
    """
    from semabridge.core.engine.conversion import snowflake as snowflake_conv_module

    # Mock SourceFormat
    sf = MagicMock()
    sf.semantic_view_ddl = None
    sf.semantic_view_name = "PROBABILITY_SEMANTIC"
    sf.database = "SEMABRIDGE_DB"
    sf.schema_name = "PUBLIC"
    sf.tables = {"Table1": MagicMock(description="", row_count=10)}
    sf.columns = {"Table1": []}
    sf.foreign_keys = []
    sf.primary_keys = {}

    # Mock RunContext
    config = MagicMock(spec=Settings)
    config.model = SimpleNamespace(description="Test description")
    context = RunContext(
        project_id="proj-stof",
        run_id="run-test",
        config=config,
        start_time=1.0,
        source_type="snowflake",
    )
    context.source_format = sf

    # Mock SmlInferenceEngine and MeasureDetector to return simple values
    with patch("semabridge.connectors.inference_engine.SmlInferenceEngine") as MockEngine, \
         patch("semabridge.connectors.measure_detector.MeasureDetector") as MockDetector, \
         patch.object(snowflake_conv_module.logger, "debug"), \
         patch.object(snowflake_conv_module.logger, "info"):
        
        # Setup mock inference returning empty classification
        MockEngine.return_value.classify.return_value = {}
        MockDetector.return_value.detect_all_measures.return_value = {}

        # SMLAssembler mock roundtrip to bypass actual conversion dependencies if any,
        # but let's test it directly:
        sml = snowflake_conv_module._convert_snowflake_to_sml(MagicMock(), context)

        assert sml.unique_name == "PROBABILITY_SEMANTIC"
        assert sml.label == "PROBABILITY_SEMANTIC"


def test_convert_snowflake_to_sml_uses_config_source_model_fallback():
    """
    Ensure SMLModel falls back to config.source.model when sf.semantic_view_name is absent.
    """
    from semabridge.core.engine.conversion import snowflake as snowflake_conv_module

    # Mock SourceFormat
    sf = MagicMock()
    sf.semantic_view_ddl = None
    sf.semantic_view_name = None  # absent
    sf.database = "SEMABRIDGE_DB"
    sf.schema_name = "PUBLIC"
    sf.tables = {"Table1": MagicMock(description="", row_count=10)}
    sf.columns = {"Table1": []}
    sf.foreign_keys = []
    sf.primary_keys = {}

    # Mock config.source.model
    config = MagicMock(spec=Settings)
    config.model = SimpleNamespace(description="Test description")
    config.source = SimpleNamespace(model="FALLBACK_MODEL_NAME")

    # Mock RunContext
    context = RunContext(
        project_id="proj-stof",
        run_id="run-test",
        config=config,
        start_time=1.0,
        source_type="snowflake",
    )
    context.source_format = sf

    with patch("semabridge.connectors.inference_engine.SmlInferenceEngine") as MockEngine, \
         patch("semabridge.connectors.measure_detector.MeasureDetector") as MockDetector, \
         patch.object(snowflake_conv_module.logger, "debug"), \
         patch.object(snowflake_conv_module.logger, "info"):
        
        MockEngine.return_value.classify.return_value = {}
        MockDetector.return_value.detect_all_measures.return_value = {}

        sml = snowflake_conv_module._convert_snowflake_to_sml(MagicMock(), context)

        assert sml.unique_name == "FALLBACK_MODEL_NAME"
        assert sml.label == "FALLBACK_MODEL_NAME"
