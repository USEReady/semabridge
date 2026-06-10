"""Domain exceptions for SemaBridge.

These are raised by service and application code. They are mapped to HTTP
status codes only at the presentation layer (api/error_handlers.py).
This means services can be used from CLI, background tasks, and tests
without any FastAPI dependency.
"""

class SemaBridgeError(Exception):
    """Base class for all SemaBridge domain errors."""

class NotFoundError(SemaBridgeError):
    """Resource not found. Maps to HTTP 404."""

class ValidationError(SemaBridgeError):
    """Input validation failed. Maps to HTTP 400/422."""

class AuthenticationError(SemaBridgeError):
    """Authentication failed or credentials invalid. Maps to HTTP 401."""

class PermissionError(SemaBridgeError):
    """Access denied for authenticated user. Maps to HTTP 403."""

class ExternalServiceError(SemaBridgeError):
    """Upstream service (Snowflake, Fabric, Databricks) unavailable or returned error. Maps to HTTP 503."""

class ConflictError(SemaBridgeError):
    """Resource conflict (duplicate, already exists). Maps to HTTP 409."""

class InternalError(SemaBridgeError):
    """Unexpected internal error. Maps to HTTP 500."""

class ConfigurationError(SemaBridgeError):
    """Invalid or missing configuration. Maps to HTTP 422."""

class RateLimitError(SemaBridgeError):
    """Too many requests. Maps to HTTP 429."""
