from __future__ import annotations

class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass

class AuthenticationError(Exception):
    """Raised when authentication resolution fails."""
    pass

class ExtractionError(Exception):
    """Raised when source extraction fails."""
    pass

class SourceFormatError(Exception):
    """Raised when source format validation fails."""
    pass

class ConversionError(Exception):
    """Raised when SML conversion fails."""
    pass

class PersistenceError(Exception):
    """Raised when artifact persistence fails."""
    pass

class DeploymentError(Exception):
    """Raised when target deployment fails."""
    pass

class SemanticValidationError(Exception):
    """Raised when canonical intermediate semantic model validation fails."""
    pass

