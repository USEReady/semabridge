"""
SemaBridge Custom Exceptions.

This module defines the exception hierarchy for SemaBridge.
All exceptions inherit from SemaBridgeError to enable unified error handling.

Usage:
    from semabridge.core.exceptions import ConnectorError, MissingCredentialError

    if not os.environ.get("SNOWFLAKE_PASSWORD_ENV"):
        raise MissingCredentialError("SNOWFLAKE_PASSWORD_ENV")
"""

from typing import Any, Dict, List, Optional


class SemaBridgeError(Exception):
    """
    Base exception for all SemaBridge errors.

    All custom exceptions inherit from this class to enable unified
    error handling and consistent error message formatting.

    Args:
        message: Human-readable error description.
        details: Optional dictionary with additional context.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initialize base exception.

        Args:
            message: Human-readable error description.
            details: Optional dictionary with additional context.
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return formatted error message."""
        if self.details:
            detail_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({detail_str})"
        return self.message


class ConnectorError(SemaBridgeError):
    """
    Error during connector operations.

    Raised when a connector fails to connect, authenticate,
    or perform operations on the target system.

    Args:
        message: Error description.
        connector_name: Name of the connector that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        connector_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize connector error.

        Args:
            message: Error description.
            connector_name: Name of the connector that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if connector_name:
            full_details["connector"] = connector_name
        super().__init__(message, full_details)
        self.connector_name = connector_name


class MissingCredentialError(ConnectorError):
    """
    Required environment variable is not set.

    Raised during authentication when a required credential
    environment variable is missing or empty.

    Args:
        env_var_name: Name of the missing environment variable.
        connector_name: Optional connector that requires this credential.
    """

    def __init__(
        self,
        env_var_name: str,
        connector_name: Optional[str] = None,
    ):
        """
        Initialize missing credential error.

        Args:
            env_var_name: Name of the missing environment variable.
            connector_name: Optional connector that requires this credential.
        """
        message = f"Required environment variable '{env_var_name}' is not set"
        super().__init__(
            message,
            connector_name=connector_name,
            details={"env_var": env_var_name},
        )
        self.env_var_name = env_var_name


class ConversionError(SemaBridgeError):
    """
    Error during format conversion.

    Raised when converting between formats (e.g., TMSL to SML,
    SML to Snowflake) fails due to incompatible data or logic errors.

    Args:
        message: Error description.
        source_format: Source format being converted from.
        target_format: Target format being converted to.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        source_format: Optional[str] = None,
        target_format: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize conversion error.

        Args:
            message: Error description.
            source_format: Source format being converted from.
            target_format: Target format being converted to.
            details: Optional additional context.
        """
        full_details = details or {}
        if source_format:
            full_details["source_format"] = source_format
        if target_format:
            full_details["target_format"] = target_format
        super().__init__(message, full_details)
        self.source_format = source_format
        self.target_format = target_format


class ValidationError(SemaBridgeError):
    """
    Schema or semantic validation failed.

    Raised when a model or configuration fails validation checks,
    including Pydantic schema validation and semantic integrity checks.

    Args:
        message: Error description.
        field: Optional field name that failed validation.
        line_number: Optional YAML line number where error occurred.
        errors: Optional list of validation error messages.
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        line_number: Optional[int] = None,
        errors: Optional[List[str]] = None,
    ):
        """
        Initialize validation error.

        Args:
            message: Error description.
            field: Optional field name that failed validation.
            line_number: Optional YAML line number where error occurred.
            errors: Optional list of validation error messages.
        """
        details: Dict[str, Any] = {}
        if field:
            details["field"] = field
        if line_number:
            details["line"] = line_number
        if errors:
            details["error_count"] = len(errors)
        super().__init__(message, details)
        self.field = field
        self.line_number = line_number
        self.errors = errors or []


class RepositoryError(SemaBridgeError):
    """
    Error during repository operations.

    Raised when DuckDB repository operations fail, including
    versioning, migration, or rollback operations.

    Args:
        message: Error description.
        operation: Type of operation that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize repository error.

        Args:
            message: Error description.
            operation: Type of operation that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if operation:
            full_details["operation"] = operation
        super().__init__(message, full_details)
        self.operation = operation


class PluginError(SemaBridgeError):
    """
    Error during plugin operations.

    Raised when plugin loading, initialization, or execution fails.

    Args:
        message: Error description.
        plugin_name: Name of the plugin that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        plugin_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize plugin error.

        Args:
            message: Error description.
            plugin_name: Name of the plugin that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if plugin_name:
            full_details["plugin"] = plugin_name
        super().__init__(message, full_details)
        self.plugin_name = plugin_name


class MigrationError(RepositoryError):
    """
    Error during SQLite → DuckDB migration.

    Raised when the migration pipeline fails during ATTACH, CTAS streaming,
    type-cast validation, or post-migration verification.

    Args:
        message: Error description.
        table_name: The table being migrated when the error occurred.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        table_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize migration error.

        Args:
            message: Error description.
            table_name: The table being migrated when the error occurred.
            details: Optional additional context.
        """
        full_details = details or {}
        if table_name:
            full_details["table"] = table_name
        super().__init__(message, operation="migration", details=full_details)
        self.table_name = table_name


class PBIXParsingError(SemaBridgeError):
    """
    Error during local .pbix file extraction.

    Raised when the ZIP archive cannot be unpacked, the DataModelSchema
    is missing or malformed, or Connections.json cannot be parsed for
    composite model resolution.

    Args:
        message: Error description.
        pbix_path: Path to the .pbix file that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        pbix_path: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize PBIX parsing error.

        Args:
            message: Error description.
            pbix_path: Path to the .pbix file that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if pbix_path:
            full_details["pbix_path"] = pbix_path
        super().__init__(message, full_details)
        self.pbix_path = pbix_path


class RateLimitError(ConnectorError):
    """
    HTTP 429 Too Many Requests from Microsoft Fabric API.

    Raised when exponential backoff retries are exhausted after
    hitting the Fabric REST API rate limit. Contains the Retry-After
    header value for diagnostic reporting.

    Args:
        message: Error description.
        retry_after_seconds: The server-mandated wait duration.
        connector_name: Connector that hit the rate limit.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        retry_after_seconds: Optional[int] = None,
        connector_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize rate limit error.

        Args:
            message: Error description.
            retry_after_seconds: The server-mandated wait duration.
            connector_name: Connector that hit the rate limit.
            details: Optional additional context.
        """
        full_details = details or {}
        if retry_after_seconds is not None:
            full_details["retry_after_seconds"] = retry_after_seconds
        super().__init__(message, connector_name=connector_name, details=full_details)
        self.retry_after_seconds = retry_after_seconds


# =============================================================================
# Sync Engine Exceptions
# =============================================================================


class SyncError(SemaBridgeError):
    """
    Error during synchronization operations.

    Base exception for all sync-engine failures including orchestration,
    conflict resolution, and schema evolution errors.

    Args:
        message: Error description.
        job_id: Optional sync job ID for traceability.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        job_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        full_details = details or {}
        if job_id:
            full_details["job_id"] = job_id
        super().__init__(message, full_details)
        self.job_id = job_id


class ConflictError(SyncError):
    """
    Raised when unresolved conflicts block synchronization.

    Contains the list of conflict IDs that must be resolved before
    the sync job can continue.

    Args:
        message: Error description.
        conflict_ids: List of unresolved conflict identifiers.
        job_id: Sync job ID.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        conflict_ids: Optional[List[str]] = None,
        job_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        full_details = details or {}
        if conflict_ids:
            full_details["conflict_ids"] = conflict_ids
            full_details["conflict_count"] = len(conflict_ids)
        super().__init__(message, job_id=job_id, details=full_details)
        self.conflict_ids = conflict_ids or []


class SchemaEvolutionError(SyncError):
    """
    Raised when schema evolution detection or migration fails.

    Args:
        message: Error description.
        model_name: Model whose schema evolution failed.
        job_id: Optional sync job ID.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        model_name: Optional[str] = None,
        job_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        full_details = details or {}
        if model_name:
            full_details["model_name"] = model_name
        super().__init__(message, job_id=job_id, details=full_details)
        self.model_name = model_name


class AmbiguousColumnReferenceError(SemaBridgeError):
    """
    Raised when a raw/un-suffixed column reference cannot be resolved to a
    single physical column because two or more distinct columns in the same
    dataset are genuinely indistinguishable from that reference alone (e.g.
    two duplicate-named physical columns disambiguated with a `_1`/`_2`
    suffix by schema_manager._collect_physical_source_columns, where the
    reference itself gives no way to tell which sibling was meant).

    This exists specifically so an indeterminate case fails closed with a
    clear reason instead of silently guessing one of the candidates (which
    could reference the wrong physical column's data) -- see
    schema_manager._resolve_duplicate_sibling_physical_name, the resolver
    that raises it.

    Args:
        message: Error description.
        dataset_name: Dataset the ambiguous reference belongs to.
        raw_col_name: The raw/un-suffixed reference that couldn't be resolved.
        candidates: Physical (possibly suffixed) names of every column the
            reference could plausibly mean.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        dataset_name: Optional[str] = None,
        raw_col_name: Optional[str] = None,
        candidates: Optional[List[str]] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        full_details = details or {}
        if dataset_name:
            full_details["dataset_name"] = dataset_name
        if raw_col_name:
            full_details["raw_col_name"] = raw_col_name
        if candidates:
            full_details["candidates"] = candidates
        super().__init__(message, full_details)
        self.dataset_name = dataset_name
        self.raw_col_name = raw_col_name
        self.candidates = candidates or []
