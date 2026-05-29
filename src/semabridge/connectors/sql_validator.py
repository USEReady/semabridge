from __future__ import annotations

from dataclasses import dataclass, field

from semabridge.compiler.sql_validator import validate_sql_expression as _validate_sql_expression


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_sql_expression(sql: str) -> ValidationResult:
    result = _validate_sql_expression(sql)
    return ValidationResult(result.is_valid, list(result.errors), list(result.warnings))


def ensure_failed_measures_table(cur, database: str, schema: str) -> None:
    cur.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{database}"."{schema}"."_FAILED_MEASURES" (
            measure_name VARCHAR(500),
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
        )
        '''
    )
