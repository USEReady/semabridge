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


class NoSourceTablesError(ExtractionError):
    """The source model has no tables, so there is nothing to deploy.

    ``reason_code`` is ``LIVE_CONNECTION`` (a thin report connected to a
    published model) or ``NO_TABLES``. In a multi-model project such a model
    is SKIPPED (dry run and sync) instead of failing its siblings; a project
    with nothing else to deploy still fails. Callers recognise it from the
    run summary by ``error_type == NoSourceTablesError.__name__``.
    """

    def __init__(self, message: str, reason_code: str = "NO_TABLES"):
        super().__init__(message)
        self.reason_code = reason_code


NO_SOURCE_TABLES_ERROR_TYPE = NoSourceTablesError.__name__


def is_no_source_tables_summary(summary: object) -> bool:
    """True when a run summary (dict) failed ONLY because the model has no
    tables -- i.e. the model should be skipped, not reported as failed."""
    errors = (summary or {}).get("errors") if isinstance(summary, dict) else None
    if not errors:
        return False
    return all(
        isinstance(e, dict) and e.get("error_type") == NO_SOURCE_TABLES_ERROR_TYPE
        for e in errors
    )

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
