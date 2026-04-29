"""Snowflake emitter warnings and exceptions."""


class MissingSourceTableWarning(UserWarning):
    """Warning raised when a source table referenced in a model is missing."""
