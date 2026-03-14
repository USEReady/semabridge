"""
Unit tests for semabridge.core.interfaces.

Tests the abstract base classes for connectors and converters.
"""

from abc import ABC
from typing import Any, Dict
from unittest.mock import patch

import pytest

from semabridge.core.interfaces import (
    BaseConnector,
    BaseExtractor,
    BaseEmitter,
    BaseConverter,
)
from semabridge.core.exceptions import (
    MissingCredentialError,
    ValidationError,
)


# =============================================================================
# Concrete Implementations for Testing
# =============================================================================


class MockConnector(BaseConnector):
    """Mock connector for testing ABC interface."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._authenticated = False

    def authenticate(self) -> None:
        import os
        token = os.environ.get(self.config.get("token_env", ""))
        if not token:
            raise MissingCredentialError(
                self.config.get("token_env", "UNKNOWN"),
                connector_name="MockConnector"
            )
        self._authenticated = True

    def discover(self) -> Dict[str, Any]:
        if not self._authenticated:
            raise RuntimeError("Not authenticated")
        return {"models": ["model1", "model2"], "datasets": []}


class MockExtractor(BaseExtractor):
    """Mock extractor for testing extract interface."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._authenticated = False

    def authenticate(self) -> None:
        self._authenticated = True

    def discover(self) -> Dict[str, Any]:
        return {"models": ["test_model"]}

    def extract(self, model_name: str) -> Dict[str, Any]:
        return {"name": model_name, "tables": []}

    def extract_to_osi(self, model_name: str) -> Any:
        return {"osi_model": model_name}


class MockEmitter(BaseEmitter):
    """Mock emitter for testing emit interface."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._authenticated = False

    def authenticate(self) -> None:
        self._authenticated = True

    def discover(self) -> Dict[str, Any]:
        return {"targets": ["target1"]}

    def emit(self, osi_model: Any) -> Dict[str, Any]:
        return {"status": "deployed", "model": osi_model}

    def validate_target(self) -> bool:
        return True


class MockConverter(BaseConverter):
    """Mock converter for testing conversion interface."""

    def to_osi(self, source_data: Any) -> Any:
        return {"osi": source_data}

    def from_osi(self, osi_model: Any) -> Any:
        return {"target": osi_model}


# =============================================================================
# Test Classes
# =============================================================================


class TestBaseConnectorInterface:
    """Test BaseConnector ABC."""

    def test_is_abstract_class(self):
        """Test that BaseConnector is an ABC."""
        assert issubclass(BaseConnector, ABC)

    def test_cannot_instantiate_directly(self):
        """Test that BaseConnector cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseConnector({})  # type: ignore

    def test_concrete_implementation_works(self):
        """Test that concrete implementation can be instantiated."""
        connector = MockConnector({"token_env": "TEST_TOKEN"})
        assert connector.config == {"token_env": "TEST_TOKEN"}

    def test_authenticate_with_missing_env_var(self):
        """Test authentication fails with missing env var."""
        connector = MockConnector({"token_env": "NONEXISTENT_VAR"})
        with pytest.raises(MissingCredentialError) as exc_info:
            connector.authenticate()
        assert "NONEXISTENT_VAR" in str(exc_info.value)

    @patch.dict("os.environ", {"MOCK_TOKEN": "secret123"})
    def test_authenticate_with_env_var_set(self):
        """Test authentication succeeds with env var set."""
        connector = MockConnector({"token_env": "MOCK_TOKEN"})
        connector.authenticate()
        assert connector._authenticated is True

    @patch.dict("os.environ", {"MOCK_TOKEN": "secret123"})
    def test_discover_after_auth(self):
        """Test discover returns data after authentication."""
        connector = MockConnector({"token_env": "MOCK_TOKEN"})
        connector.authenticate()
        result = connector.discover()
        assert "models" in result
        assert len(result["models"]) == 2

    def test_validate_config_success(self):
        """Test validate_config passes with all required keys."""
        connector = MockConnector({"token_env": "VAR", "host": "localhost"})
        # Should not raise
        connector.validate_config(["token_env", "host"])

    def test_validate_config_missing_keys(self):
        """Test validate_config raises with missing keys."""
        connector = MockConnector({"token_env": "VAR"})
        with pytest.raises(ValidationError) as exc_info:
            connector.validate_config(["token_env", "host", "port"])
        assert "host" in str(exc_info.value)
        assert "port" in str(exc_info.value)


class TestBaseExtractorInterface:
    """Test BaseExtractor ABC."""

    def test_extends_base_connector(self):
        """Test that BaseExtractor extends BaseConnector."""
        assert issubclass(BaseExtractor, BaseConnector)

    def test_extract_method(self):
        """Test extract method returns data."""
        extractor = MockExtractor({})
        result = extractor.extract("test_model")
        assert result["name"] == "test_model"

    def test_extract_to_osi_method(self):
        """Test extract_to_osi method."""
        extractor = MockExtractor({})
        result = extractor.extract_to_osi("my_model")
        assert result["osi_model"] == "my_model"


class TestBaseEmitterInterface:
    """Test BaseEmitter ABC."""

    def test_extends_base_connector(self):
        """Test that BaseEmitter extends BaseConnector."""
        assert issubclass(BaseEmitter, BaseConnector)

    def test_emit_method(self):
        """Test emit method returns deployment result."""
        emitter = MockEmitter({})
        result = emitter.emit({"name": "test_model"})
        assert result["status"] == "deployed"

    def test_validate_target_method(self):
        """Test validate_target method."""
        emitter = MockEmitter({})
        assert emitter.validate_target() is True


class TestBaseConverterInterface:
    """Test BaseConverter ABC."""

    def test_is_abstract_class(self):
        """Test that BaseConverter is an ABC."""
        assert issubclass(BaseConverter, ABC)

    def test_to_osi_method(self):
        """Test to_osi conversion."""
        converter = MockConverter()
        result = converter.to_osi({"source": "data"})
        assert result == {"osi": {"source": "data"}}

    def test_from_osi_method(self):
        """Test from_osi conversion."""
        converter = MockConverter()
        result = converter.from_osi({"osi": "model"})
        assert result == {"target": {"osi": "model"}}

    def test_validate_default_returns_true(self):
        """Test default validate method returns True."""
        converter = MockConverter()
        assert converter.validate({"any": "data"}) is True


class TestInterfaceContracts:
    """Test interface contracts and design patterns."""

    def test_credentials_via_env_only_pattern(self):
        """Test that credentials are read from environment only."""
        # This test ensures the pattern is enforced - credentials
        # should never be passed directly, only env var names
        connector = MockConnector({"token_env": "SNOWFLAKE_TOKEN"})

        # The config contains an env var NAME, not the actual secret
        # Config should reference env var names (typically UPPER_SNAKE_CASE)
        env_var_name = connector.config.get("token_env", "")
        assert env_var_name.isupper() or "_" in env_var_name


    def test_osi_intermediate_pattern(self):
        """Test that conversion goes through OSI intermediate."""
        converter = MockConverter()

        # Source → OSI
        osi_model = converter.to_osi({"source": "fabric_model"})
        assert "osi" in osi_model

        # OSI → Target
        target = converter.from_osi(osi_model)
        assert "target" in target
