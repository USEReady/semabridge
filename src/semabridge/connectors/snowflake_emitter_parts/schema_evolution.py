"""Schema evolution, type inference, and CTAS utilities for Snowflake."""

import re
from typing import Any, Dict, Optional, List
from decimal import Decimal
from datetime import date, datetime

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def get_source_column_types(emitter, cursor, safe_table_name: str) -> dict[str, str]:
    """Return Snowflake DATA_TYPE by column for the given physical table."""
    query = (
        "SELECT COLUMN_NAME, DATA_TYPE "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s AND TABLE_NAME = %s"
    )
    emitter._execute_sql(
        cursor,
        query,
        (
            emitter.config.database.upper(),
            emitter.config.schema_name.upper(),
            safe_table_name.upper(),
        ),
        context=f"INFORMATION_SCHEMA.COLUMNS {safe_table_name}",
    )
    rows = cursor.fetchall() or []
    col_types = {str(name).upper(): str(dtype).upper() for name, dtype in rows}
    logger.debug("Source column types for %s: %s", safe_table_name, col_types)
    return col_types


def infer_type_from_values(values: list[Any]) -> str:
    """Infer normalized type from sampled Python/Snowflake values."""
    if not values:
        return "VARCHAR"

    def _kind(v: Any) -> str:
        if isinstance(v, bool):
            return "BOOLEAN"
        if isinstance(v, int) and not isinstance(v, bool):
            return "INTEGER"
        if isinstance(v, (float, Decimal)):
            return "FLOAT"
        if isinstance(v, datetime):
            return "TIMESTAMP"
        if isinstance(v, date):
            return "DATE"

        s = str(v).strip()
        if not s:
            return "NULL"

        sl = s.lower()
        if sl in {"true", "false", "yes", "no", "y", "n", "t", "f"}:
            return "BOOLEAN"
        if re.fullmatch(r"[+-]?\d+", s):
            return "INTEGER"
        if re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)", s):
            return "FLOAT"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return "DATE"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", s):
            return "TIMESTAMP"
        return "VARCHAR"

    kinds = {_kind(v) for v in values if v is not None}
    kinds.discard("NULL")
    if not kinds:
        return "VARCHAR"
    if kinds == {"BOOLEAN"}:
        return "BOOLEAN"
    if kinds == {"INTEGER"}:
        return "INTEGER"
    if kinds.issubset({"INTEGER", "FLOAT"}):
        return "FLOAT"
    if kinds == {"DATE"}:
        return "DATE"
    if kinds.issubset({"DATE", "TIMESTAMP"}):
        return "TIMESTAMP"
    return "VARCHAR"


def fallback_type_from_name(col_name: str) -> str:
    """Fallback inference for all-VARCHAR scenarios."""
    n = col_name.lower()
    tokens = [t for t in re.split(r"[^a-z0-9$]+", n.replace("_", " ")) if t]
    token_set = set(tokens)

    if any(k in n for k in ("timestamp", "datetime", "_ts")) or "ts" in token_set:
        return "TIMESTAMP"
    if "date" in n:
        return "DATE"
    if "time" in token_set:
        return "FLOAT"
    if any(k in n for k in ("amount", "price", "cost", "rate", "pct", "percent", "score", "revenue", "total", "qty", "quantity")):
        return "FLOAT"
    if any(k in n for k in ("is_", "has_", "flag", "active", "enabled", "deleted", "valid", "bool", "boolean")):
        return "BOOLEAN"
    if any(k in token_set for k in ("count", "num", "year", "month", "day")):
        return "INTEGER"
    if any(k in token_set for k in ("id", "key")) or n.endswith("_id"):
        return "VARCHAR"
    return "VARCHAR"


def normalize_declared_type(raw_type: str) -> str:
    """Normalize model-declared types to CTAS inference type family."""
    t = (raw_type or "").upper()
    mapping = {
        "INT64": "INTEGER",
        "INT": "INTEGER",
        "INTEGER": "INTEGER",
        "DOUBLE": "FLOAT",
        "FLOAT": "FLOAT",
        "DECIMAL": "DECIMAL",
        "NUMBER": "NUMBER",
        "BOOLEAN": "BOOLEAN",
        "BOOL": "BOOLEAN",
        "DATE": "DATE",
        "DATETIME": "DATETIME",
        "TIMESTAMP": "TIMESTAMP",
        "TIMESTAMP_NTZ": "TIMESTAMP",
        "TIMESTAMP_LTZ": "TIMESTAMP",
        "TIMESTAMP_TZ": "TIMESTAMP",
        "STRING": "VARCHAR",
        "TEXT": "VARCHAR",
        "VARCHAR": "VARCHAR",
        "VARIANT": "VARCHAR",
    }
    return mapping.get(t, "VARCHAR")


def business_rule_type(table_name: str, col_name: str) -> Optional[str]:
    """Business-rule layer for known Salesforce semantic fields."""
    t = (table_name or "").upper()
    c = (col_name or "").strip().upper()
    c_norm = re.sub(r"[^A-Z0-9]", "", c)

    if c_norm == "FIRMNESSOFFIRSTDELIVERYDATE":
        return "VARCHAR"
    if c == "DELETED":
        return "BOOLEAN"
    if "MODSTAMP" in c_norm:
        return "TIMESTAMP"
    if "VOLUME" in c_norm or "AMOUNT" in c_norm:
        return "FLOAT"
    if "DATE" in c_norm and not t.endswith("FIELDHISTORY"):
        return "DATE"

    return None


def build_cast_expression(
    emitter,
    quoted_name: str,
    target_type: str,
    source_type: str,
    col_name: str,
) -> str:
    """Build a safe cast expression for CTAS based on source and target types."""
    t = (target_type or "").upper()
    s = (source_type or "").upper()

    numeric_targets = {"INTEGER", "FLOAT", "DECIMAL", "NUMBER"}
    timestamp_targets = {"TIMESTAMP", "DATETIME", "TIME", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
    boolean_targets = {"BOOLEAN", "BOOL"}

    is_source_number = any(k in s for k in ("NUMBER", "DECIMAL", "NUMERIC", "INT", "FLOAT", "DOUBLE", "REAL"))
    is_source_timestamp = "TIMESTAMP" in s
    is_source_date = s.startswith("DATE")
    is_source_varchar = any(k in s for k in ("VARCHAR", "TEXT", "STRING", "CHAR"))
    is_source_boolean = "BOOLEAN" in s or s == "BOOL"

    # Same type -> no cast
    if s == t:
        return quoted_name

    # Guard unsafe conversions
    if is_source_number and (t == "DATE" or t in timestamp_targets):
        logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
        return quoted_name
    if is_source_timestamp and t in numeric_targets:
        logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
        return quoted_name
    if is_source_date and t in numeric_targets:
        logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
        return quoted_name

    if t in boolean_targets:
        if is_source_boolean:
            return quoted_name
        if is_source_number:
            return f"IFF({quoted_name} IS NULL, NULL, IFF({quoted_name} = 0, FALSE, TRUE))"
        if is_source_varchar:
            return (
                f"IFF({quoted_name} IS NULL, NULL, "
                f"IFF(LOWER(TRIM({quoted_name})) IN ('true','1','t','yes','y'), TRUE, "
                f"IFF(LOWER(TRIM({quoted_name})) IN ('false','0','f','no','n'), FALSE, NULL)))"
            )
        logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
        return quoted_name

    # Safe conversions
    if is_source_timestamp and t == "DATE":
        return f"CAST({quoted_name} AS DATE)"
    if is_source_date and t in timestamp_targets:
        return f"TO_TIMESTAMP({quoted_name})"
    if is_source_varchar and t == "DATE":
        return f"TRY_TO_DATE({quoted_name})"
    if is_source_varchar and t in timestamp_targets:
        return f"TRY_TO_TIMESTAMP({quoted_name})"

    if t in numeric_targets:
        if is_source_number:
            return quoted_name
        return f"TRY_TO_NUMBER({quoted_name})"
    if t == "DATE":
        if is_source_timestamp:
            return f"CAST({quoted_name} AS DATE)"
        if is_source_date:
            return quoted_name
        return f"TRY_TO_DATE({quoted_name})"
    if t in timestamp_targets:
        if is_source_timestamp:
            return quoted_name
        if is_source_date:
            return f"TO_TIMESTAMP({quoted_name})"
        return f"TRY_TO_TIMESTAMP({quoted_name})"

    return quoted_name


def generate_ctas_sql(
    emitter,
    table_name: str,
    columns: list[dict[str, str]],
    schema_name: Optional[str] = None,
    source_types: Optional[dict[str, str]] = None,
) -> str:
    """Generate CTAS SQL that applies safe datatype conversions."""
    if not columns:
        raise ValueError("No columns inferred")

    varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}
    if all((c.get("type", "").upper() in varchar_types) for c in columns):
        logger.warning(
            "All columns are VARCHAR for %s. Generating pass-through CTAS.",
            table_name,
        )

    select_parts: list[str] = []
    source_types = source_types or {}
    for col in columns:
        name = col["name"]
        dtype = col["type"].upper()
        quoted_name = f'"{name}"'
        source_type = (source_types.get(name) or source_types.get(name.upper()) or "").upper()

        cast_expr = build_cast_expression(
            emitter,
            quoted_name=quoted_name,
            target_type=dtype,
            source_type=source_type,
            col_name=name,
        )
        select_parts.append(f"{cast_expr} AS {quoted_name}")

    select_sql = ",\n    ".join(select_parts)

    schema = schema_name or emitter.config.schema_name
    quoted_source = f'{schema}."{table_name}"'
    quoted_fixed = f'{schema}."{table_name}__FIXED"'
    return (
        f"CREATE OR REPLACE TABLE {quoted_fixed} AS\n"
        f"SELECT\n    {select_sql}\n"
        f"FROM {quoted_source};"
    )


def infer_columns_from_table_samples(
    emitter,
    cursor,
    safe_table_name: str,
    columns: list[dict[str, str]],
    sample_limit: int = 200,
) -> tuple[list[dict[str, str]], list[str]]:
    """Resolve per-column types using model metadata first, sampling second."""
    if not columns:
        raise ValueError("No columns inferred")

    source_types = get_source_column_types(emitter, cursor, safe_table_name)
    source_names = list(source_types.keys())

    def _norm(name: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())

    resolved_pairs: list[tuple[str, str]] = []
    fallback_logs: list[str] = []
    for c in columns:
        requested = str(c["name"])
        requested_upper = requested.upper()
        source_name = None

        if requested_upper in source_types:
            source_name = requested_upper
        else:
            requested_norm = _norm(requested)
            matches = [src for src in source_names if _norm(src) == requested_norm]
            if len(matches) == 1:
                source_name = matches[0]

        if source_name:
            resolved_pairs.append((requested, source_name))
        else:
            fallback_logs.append(
                f"Skipped non-physical column during sampling: {safe_table_name}.{requested}"
            )

    if not resolved_pairs:
        logger.warning(
            "Skipping sample type inference for %s: no model columns matched physical source columns",
            safe_table_name,
        )
        return [], fallback_logs

    def _q(ident: str) -> str:
        escaped = str(ident).replace('"', '""')
        return f'"{escaped}"'

    col_refs = ", ".join([f"{_q(src)} AS {_q(req)}" for req, src in resolved_pairs])
    sample_sql = (
        f'SELECT {col_refs} FROM {emitter.config.schema_name}."{safe_table_name}" '
        f'LIMIT {sample_limit}'
    )
    emitter._execute_sql(cursor, sample_sql, context=f"SAMPLE QUERY {safe_table_name}")
    rows = cursor.fetchall() or []
    logger.info("Sample rows fetched for %s: %s", safe_table_name, len(rows))

    inferred: list[dict[str, str]] = []
    varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}

    sampled_columns = [
        c for c in columns if any(req == c["name"] for req, _src in resolved_pairs)
    ]

    for idx, col in enumerate(sampled_columns):
        declared_type = normalize_declared_type(col.get("type", ""))
        values = [r[idx] for r in rows if len(r) > idx and r[idx] is not None]
        logger.debug("%s.%s sample values: %s", safe_table_name, col["name"], values[:5])
        sampled_type = infer_type_from_values(values)
        biz_type = business_rule_type(safe_table_name, col["name"])

        if biz_type is not None:
            resolved_type = biz_type
        elif declared_type not in varchar_types:
            resolved_type = declared_type
            if sampled_type != "VARCHAR" and sampled_type != declared_type:
                logger.warning(
                    "Type mismatch for %s.%s (model=%s sample=%s); using model type",
                    safe_table_name,
                    col["name"],
                    declared_type,
                    sampled_type,
                )
        else:
            resolved_type = sampled_type

        inferred.append({"name": col["name"], "type": resolved_type})
        logger.info(
            "Resolved datatype: %s.%s -> %s (model=%s sample=%s sampled_rows=%s non_null=%s)",
            safe_table_name,
            col["name"],
            resolved_type,
            declared_type,
            sampled_type,
            len(rows),
            len(values),
        )

    logger.info("Inferred types for %s:", safe_table_name)
    for col in inferred:
        logger.info("- %s -> %s", col["name"], col["type"])

    if all(c["type"] == "VARCHAR" for c in inferred):
        logger.warning(
            "All sampled columns resolved to VARCHAR for %s. Applying fallback name-pattern inference.",
            safe_table_name,
        )
        for c in inferred:
            fb = fallback_type_from_name(c["name"])
            if fb != "VARCHAR":
                fallback_logs.append(f"{c['name']}: VARCHAR -> {fb} (name-pattern fallback)")
                c["type"] = fb

    for entry in fallback_logs:
        logger.info("Fallback decision: %s", entry)

    if all(c["type"] == "VARCHAR" for c in inferred):
        logger.warning(
            "%s: only text-like columns detected after sampling/fallback; keeping VARCHAR types",
            safe_table_name,
        )

    return inferred, fallback_logs


def evolve_schema(
    emitter,
    cursor,
    table_name: str,
    new_columns: list[tuple[str, str]],
) -> dict[str, str]:
    """Incrementally evolve a Snowflake table schema.

    Applies non-destructive changes:
      - New columns → ALTER TABLE ADD COLUMN
      - Removed columns → Renamed with ``_DEPRECATED_`` prefix (Soft Delete)

    Args:
        cursor: Active Snowflake cursor.
        table_name: Fully qualified table name.
        new_columns: List of (column_name, snowflake_type) tuples.

    Returns:
        Dict summarising actions taken.
    """
    actions: dict[str, list[str]] = {"added": [], "deprecated": []}

    # Fetch existing columns
    try:
        emitter._execute_sql(cursor, f"DESC TABLE {table_name}", context=f"DESC TABLE {table_name}")
        existing = {row[0].upper(): row[1] for row in cursor.fetchall()}
    except Exception:
        logger.warning(
            f"Table {table_name} does not exist — cannot evolve schema"
        )
        return actions

    desired_upper = {col[0].upper(): col[1] for col in new_columns}

    # ADD new columns
    for col_name, col_type in new_columns:
        if col_name.upper() not in existing:
            try:
                ddl = (
                    f'ALTER TABLE {table_name} '
                    f'ADD COLUMN "{col_name.upper()}" {col_type}'
                )
                emitter._execute_sql(cursor, ddl, context=f"ALTER TABLE ADD COLUMN {table_name}.{col_name}")
                actions["added"].append(col_name)
                logger.info(f"Schema evolution: added column {col_name}")
            except Exception as e:
                logger.error(f"Failed to add column {col_name}: {e}")

    # SOFT-DELETE removed columns
    for existing_col in existing:
        if (
            existing_col not in desired_upper
            and not existing_col.startswith("_DEPRECATED_")
        ):
            new_name = f"_DEPRECATED_{existing_col}"
            try:
                ddl = (
                    f'ALTER TABLE {table_name} '
                    f'RENAME COLUMN "{existing_col}" TO "{new_name}"'
                )
                emitter._execute_sql(cursor, ddl, context=f"RENAME COLUMN {table_name}.{existing_col}")
                actions["deprecated"].append(existing_col)
                logger.info(
                    f"Schema evolution: soft-deleted {existing_col} → {new_name}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to deprecate column {existing_col}: {e}"
                )

    if actions["added"] or actions["deprecated"]:
        logger.info(
            f"Schema evolution complete for {table_name}: "
            f"+{len(actions['added'])} / "
            f"-{len(actions['deprecated'])} columns"
        )
    return actions
