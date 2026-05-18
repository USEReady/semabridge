"""
SemaBridge Abstract Base Classes (Interfaces).

This module defines the contracts that all connectors and converters must follow.
These interfaces ensure consistent behavior across different platform integrations.

Key Principles:
- Connectors MUST NOT accept actual secrets, only environment variable names.
- All conversions MUST pass through the OSI intermediate layer.
- Implementations must raise specific exceptions from core.exceptions.

Usage:
    from semabridge.core.interfaces import BaseConnector

    class SnowflakeConnector(BaseConnector):
        def authenticate(self) -> None:
            password = os.environ.get(self.config["password_env"])
            if not password:
                raise MissingCredentialError("password_env")
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from semabridge.core.exceptions import MissingCredentialError


class BaseConnector(ABC):
    """
    Abstract base class for all system integrations.

    Connectors handle communication with external platforms like
    Microsoft Fabric, Snowflake, Power BI, etc.

    Security Mandate:
        Connectors MUST NOT accept actual secrets as parameters.
        All credentials must be read from environment variables
        inside the authenticate() method.

    Example:
        class FabricConnector(BaseConnector):
            def __init__(self, config: Dict[str, Any]):
                self.config = config
                self._session = None

            def authenticate(self) -> None:
                token = os.environ.get(self.config.get("token_env"))
                if not token:
                    raise MissingCredentialError("token_env", "FabricConnector")
                self._session = create_session(token)

            def discover(self) -> Dict[str, Any]:
                return {"models": [...], "datasets": [...]}
    """

    @abstractmethod
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize connector with configuration.

        MUST NOT accept actual secrets here, only environment variable names.
        Configuration keys ending with '_env' indicate environment variable names.

        Args:
            config: Configuration dictionary with connection parameters.
                    Credential keys should use '_env' suffix
                    (e.g., password_env, token_env).

        Raises:
            ValidationError: If required configuration is missing.
        """
        pass

    @abstractmethod
    def authenticate(self) -> None:
        """
        Resolve environment variables and establish a session.

        This method reads credentials from os.environ using the
        environment variable names provided in the config.

        Raises:
            MissingCredentialError: If required environment variable is not set.
            ConnectorError: If authentication fails.
        """
        pass

    @abstractmethod
    def discover(self) -> Dict[str, Any]:
        """
        List available models/objects in the source system.

        Returns:
            Dictionary containing discovered resources, typically:
            {
                "models": List of available semantic models,
                "datasets": List of available tables/datasets,
                "metadata": Additional platform-specific information
            }

        Raises:
            ConnectorError: If discovery operation fails.
        """
        pass

    def validate_config(self, required_keys: List[str]) -> None:
        """
        Validate that required configuration keys are present.

        Args:
            required_keys: List of required configuration key names.

        Raises:
            ValidationError: If any required key is missing.
        """
        from semabridge.core.exceptions import ValidationError

        config = getattr(self, "config", {})
        missing = [k for k in required_keys if k not in config]
        if missing:
            raise ValidationError(
                f"Missing required configuration keys: {', '.join(missing)}",
                field="config",
            )


class BaseExtractor(BaseConnector):
    """
    Abstract base class for source extractors.

    Extractors read semantic metadata from source platforms
    and convert it to the OSI intermediate format.
    """

    @abstractmethod
    def extract(self, model_name: str) -> Dict[str, Any]:
        """
        Extract semantic model metadata from the source.

        Args:
            model_name: Name or identifier of the model to extract.

        Returns:
            Dictionary containing extracted metadata in source format.

        Raises:
            ConnectorError: If extraction fails.
        """
        pass

    @abstractmethod
    def extract_to_osi(self, model_name: str) -> Any:
        """
        Extract and convert to OSI intermediate format.

        This method combines extraction and conversion in a single
        operation for convenience.

        Args:
            model_name: Name or identifier of the model to extract.

        Returns:
            OSIModel instance representing the extracted model.

        Raises:
            ConnectorError: If extraction fails.
            ConversionError: If conversion to OSI fails.
        """
        pass


class BaseEmitter(BaseConnector):
    """
    Abstract base class for target emitters.

    Emitters write semantic metadata to target platforms
    from the OSI intermediate format.
    """

    @abstractmethod
    def emit(self, osi_model: Any) -> Dict[str, Any]:
        """
        Emit OSI model to the target platform.

        Args:
            osi_model: OSIModel instance to deploy.

        Returns:
            Dictionary with deployment results and metadata.

        Raises:
            ConnectorError: If emission fails.
            ConversionError: If converting from OSI fails.
        """
        pass

    @abstractmethod
    def validate_target(self) -> bool:
        """
        Validate that the target platform is ready for deployment.

        Returns:
            True if target is ready, False otherwise.

        Raises:
            ConnectorError: If validation check fails.
        """
        pass


class BaseConverter(ABC):
    """
    Abstract base class for format converters.

    Converters transform data between source/target formats
    and the OSI intermediate representation.

    Architecture Mandate:
        All conversions MUST be:
        - Source → OSI (extraction)
        - OSI → Target (emission)

        Direct Source → Target conversion is STRICTLY FORBIDDEN.
    """

    @abstractmethod
    def to_osi(self, source_data: Any) -> Any:
        """
        Convert source format to OSI intermediate.

        Args:
            source_data: Data in source format (e.g., TMDL, Power BI).

        Returns:
            OSIModel instance.

        Raises:
            ConversionError: If conversion fails.
        """
        pass

    @abstractmethod
    def from_osi(self, osi_model: Any) -> Any:
        """
        Convert OSI intermediate to target format.

        Args:
            osi_model: OSIModel instance.

        Returns:
            Data in target format (e.g., SML YAML, Snowflake DDL).

        Raises:
            ConversionError: If conversion fails.
        """
        pass

    def validate(self, data: Any) -> bool:
        """
        Validate data before conversion.

        Override this method to add custom validation logic.

        Args:
            data: Data to validate.

        Returns:
            True if valid, False otherwise.
        """
        return True
