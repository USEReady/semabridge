"""
Snowflake Semantic View → OSI Converter.

Parses the DDL of a Snowflake ``CREATE OR REPLACE SEMANTIC VIEW`` statement
and converts it into the OSI (Open Semantic Interchange) canonical
intermediate representation.

DDL structure handled::

    CREATE OR REPLACE SEMANTIC VIEW <db>.<schema>.<name>
    TABLES (
        <alias> AS <db>.<schema>."<table>" PRIMARY KEY ("<col>"),
        ...
    )
    RELATIONSHIPS (
        <from_alias> ("<fk_col>") REFERENCES <to_alias>,
        ...
    )
    DIMENSIONS (
        <alias>."<col>" AS "<semantic_name>",
        ...
    )
    MEASURES (
        <alias>."<col>" AS <expr>,
        ...
    )

Design notes:
- Uses a regex / state-machine approach (no DDL grammar library dependency).
- Tolerates missing optional clauses (a view may have no MEASURES, for example).
- Enriches column metadata with INFORMATION_SCHEMA when a live connection is
  supplied or ``enrich_from_db=True`` (requires SnowflakeExtractor).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIAttribute,
    OSICardinality,
    OSICrossFilterDirection,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIDimension,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_IDENTIFIER = r'"?([^",\s\)]+)"?'
_QUALIFIED = r'"?([^".\s]+)"?\."?([^".\s]+)"?\."?([^".\s\(]+)"?'


def _strip_quotes(value: str) -> str:
    """Remove surrounding double-quotes from an identifier."""
    return value.strip().strip('"')


def _extract_clause(ddl: str, clause_name: str) -> Optional[str]:
    """
    Extract the content between a named clause and the next top-level clause.

    Handles nested parentheses so inner commas don't break extraction.
    Returns ``None`` if the clause is absent.
    """
    pattern = rf"(?i)\b{clause_name}\s*\("
    m = re.search(pattern, ddl)
    if not m:
        return None

    start = m.end()          # position after the opening '('
    depth = 1
    pos = start
    while pos < len(ddl) and depth > 0:
        if ddl[pos] == "(":
            depth += 1
        elif ddl[pos] == ")":
            depth -= 1
        pos += 1

    return ddl[start : pos - 1].strip()


def _split_clause_entries(content: str) -> list[str]:
    """
    Split clause content by commas that are NOT inside parentheses.
    """
    entries: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in content:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1

        if ch == "," and depth == 0:
            entry = "".join(current).strip()
            if entry:
                entries.append(entry)
            current = []
        else:
            current.append(ch)

    last = "".join(current).strip()
    if last:
        entries.append(last)
    return entries


# ---------------------------------------------------------------------------
# Snowflake → OSI type mapping
# ---------------------------------------------------------------------------

_SF_TYPE_MAP: dict[str, OSIDataType] = {
    "NUMBER": OSIDataType.INTEGER,
    "DECIMAL": OSIDataType.DECIMAL,
    "NUMERIC": OSIDataType.DECIMAL,
    "INT": OSIDataType.INTEGER,
    "INTEGER": OSIDataType.INTEGER,
    "BIGINT": OSIDataType.INTEGER,
    "SMALLINT": OSIDataType.INTEGER,
    "TINYINT": OSIDataType.INTEGER,
    "BYTEINT": OSIDataType.INTEGER,
    "FLOAT": OSIDataType.FLOAT,
    "FLOAT4": OSIDataType.FLOAT,
    "FLOAT8": OSIDataType.FLOAT,
    "DOUBLE": OSIDataType.FLOAT,
    "REAL": OSIDataType.FLOAT,
    "VARCHAR": OSIDataType.STRING,
    "CHAR": OSIDataType.STRING,
    "CHARACTER": OSIDataType.STRING,
    "STRING": OSIDataType.STRING,
    "TEXT": OSIDataType.STRING,
    "BINARY": OSIDataType.BINARY,
    "VARBINARY": OSIDataType.BINARY,
    "BOOLEAN": OSIDataType.BOOLEAN,
    "DATE": OSIDataType.DATE,
    "DATETIME": OSIDataType.DATETIME,
    "TIME": OSIDataType.DATETIME,
    "TIMESTAMP": OSIDataType.DATETIME,
    "TIMESTAMP_LTZ": OSIDataType.DATETIME,
    "TIMESTAMP_NTZ": OSIDataType.DATETIME,
    "TIMESTAMP_TZ": OSIDataType.DATETIME,
    "VARIANT": OSIDataType.VARIANT,
    "OBJECT": OSIDataType.VARIANT,
    "ARRAY": OSIDataType.VARIANT,
    "GEOGRAPHY": OSIDataType.STRING,
    "GEOMETRY": OSIDataType.STRING,
}


def _map_sf_type(sf_type: str) -> OSIDataType:
    base = sf_type.upper().split("(")[0].strip()
    return _SF_TYPE_MAP.get(base, OSIDataType.STRING)


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


class SemanticViewToOSIConverter(BaseConverter):
    """
    Converts a Snowflake Semantic View DDL string into an ``OSIModel``.

    Usage::

        converter = SemanticViewToOSIConverter()
        osi = converter.to_osi({
            "ddl": "<full DDL string>",
            "view_name": "Sales_semantic",       # optional fallback name
            "column_metadata": {...},             # optional INFORMATION_SCHEMA map
        })
    """

    def to_osi(self, source_data: Dict[str, Any]) -> OSIModel:
        """
        Parse a Snowflake semantic view DDL and produce an ``OSIModel``.

        Args:
            source_data: Dict with:
                - ``ddl`` (str): Full ``CREATE OR REPLACE SEMANTIC VIEW`` DDL.
                - ``view_name`` (str, optional): Fallback name if DDL parsing fails.
                - ``column_metadata`` (dict, optional): Map of
                  ``{table_name: [{name, data_type, is_nullable, comment}, ...]}``.
                  Used to enrich column information beyond what the DDL contains.

        Returns:
            ``OSIModel`` populated from the DDL.

        Raises:
            ``ConversionError``: If parsing fails unrecoverably.
        """
        ddl: str = source_data.get("ddl", "")
        fallback_name: str = source_data.get("view_name", "unnamed_view")
        col_meta: dict[str, list[dict[str, Any]]] = source_data.get(
            "column_metadata", {}
        )

        if not ddl:
            raise ConversionError(
                "SemanticViewToOSIConverter requires 'ddl' in source_data"
            )

        try:
            view_name = self._parse_view_name(ddl) or fallback_name
            table_map = self._parse_tables_clause(ddl)          # alias → table info
            relationships = self._parse_relationships_clause(ddl, table_map)
            datasets = self._build_datasets(table_map, col_meta, ddl)
            metrics = self._parse_measures_clause(ddl, table_map)
            dimensions = self._parse_dimensions_clause(ddl, table_map)

            osi = OSIModel(
                unique_name=view_name,
                label=view_name.replace("_", " ").title(),
                source_platform="snowflake_semantic_view",
                datasets=datasets,
                relationships=relationships,
                metrics=metrics,
                dimensions=dimensions,
            )

            logger.info(
                f"Parsed semantic view '{view_name}' → OSI: "
                f"{len(datasets)} datasets, {len(relationships)} relationships, "
                f"{len(metrics)} metrics, {len(dimensions)} dimensions"
            )
            return osi

        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError(
                f"Failed to parse semantic view DDL: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # from_osi — not applicable for this direction
    # ------------------------------------------------------------------

    def from_osi(self, osi_model: OSIModel) -> Dict[str, Any]:  # type: ignore[override]
        """Not implemented — this converter is read-only (DDL → OSI)."""
        raise NotImplementedError(
            "SemanticViewToOSIConverter does not support from_osi. "
            "Use SnowflakeEmitter to generate semantic view DDL from OSI."
        )

    def validate(self, data: Any) -> bool:
        """Validate that input is a dict with a 'ddl' key."""
        return isinstance(data, dict) and bool(data.get("ddl"))

    # ------------------------------------------------------------------
    # Internal parsers
    # ------------------------------------------------------------------

    def _parse_view_name(self, ddl: str) -> Optional[str]:
        """Extract the semantic view name from the CREATE statement."""
        m = re.search(
            r"(?i)CREATE\s+(?:OR\s+REPLACE\s+)?SEMANTIC\s+VIEW\s+"
            r"(?:[^\s.]+\.)?(?:[^\s.]+\.)?([^\s(;]+)",
            ddl,
        )
        if not m:
            return None
        return _strip_quotes(m.group(1))

    def _parse_tables_clause(self, ddl: str) -> dict[str, dict[str, Any]]:
        """
        Parse the TABLES clause.

        Returns:
            Dict mapping alias → {alias, table_name, pk_columns}.
        """
        content = _extract_clause(ddl, "TABLES")
        if not content:
            return {}

        table_map: dict[str, dict[str, Any]] = {}
        for entry in _split_clause_entries(content):
            entry = entry.strip()
            if not entry:
                continue

            # Pattern: alias AS db.schema."TABLE" PRIMARY KEY ("col1", "col2")
            alias_match = re.match(
                r'(\w+)\s+AS\s+' + _QUALIFIED, entry, re.IGNORECASE
            )
            if not alias_match:
                # Try without db/schema qualification
                alias_match = re.match(
                    r'(\w+)\s+AS\s+"?(\w+)"?', entry, re.IGNORECASE
                )
                if not alias_match:
                    logger.debug(f"Could not parse TABLES entry: {entry!r}")
                    continue
                alias = alias_match.group(1)
                table_name = _strip_quotes(alias_match.group(2))
            else:
                alias = alias_match.group(1)
                table_name = _strip_quotes(alias_match.group(4))

            # Extract PRIMARY KEY columns
            pk_match = re.search(
                r'PRIMARY\s+KEY\s*\(([^)]+)\)', entry, re.IGNORECASE
            )
            pk_columns: list[str] = []
            if pk_match:
                pk_columns = [
                    _strip_quotes(c.strip())
                    for c in pk_match.group(1).split(",")
                    if c.strip()
                ]

            table_map[alias] = {
                "alias": alias,
                "table_name": table_name,
                "pk_columns": pk_columns,
            }

        return table_map

    def _parse_relationships_clause(
        self,
        ddl: str,
        table_map: dict[str, dict[str, Any]],
    ) -> list[OSIRelationship]:
        """
        Parse the RELATIONSHIPS clause.

        DDL pattern::

            from_alias ("fk_col") REFERENCES to_alias
        """
        content = _extract_clause(ddl, "RELATIONSHIPS")
        if not content:
            return []

        rels: list[OSIRelationship] = []
        for entry in _split_clause_entries(content):
            entry = entry.strip()
            if not entry:
                continue

            # Pattern: [relationship_name AS] alias ("col") REFERENCES alias2
            m = re.match(
                r'(?:(\w+)\s+AS\s+)?(\w+)\s*\(\s*"?(\w+)"?\s*\)\s*REFERENCES\s+(\w+)',
                entry,
                re.IGNORECASE,
            )
            if not m:
                logger.debug(f"Could not parse RELATIONSHIPS entry: {entry!r}")
                continue

            rel_identifier, from_alias, fk_col, to_alias = m.group(1), m.group(2), m.group(3), m.group(4)
            from_table = table_map.get(from_alias, {}).get("table_name", from_alias)
            to_table = table_map.get(to_alias, {}).get("table_name", to_alias)
            to_pk_cols = table_map.get(to_alias, {}).get("pk_columns", [])
            to_col = to_pk_cols[0] if to_pk_cols else fk_col

            if rel_identifier:
                rel_name = rel_identifier.upper()
                # Snowflake layer may intentionally omit REL_. Keep canonical
                # REL_ prefix inside OSI/SML models.
                if not rel_name.startswith("REL_"):
                    rel_name = f"REL_{rel_name}"
            else:
                rel_name = generate_relationship_name(
                    from_table,
                    fk_col,
                    to_table,
                    to_col,
                )

            rels.append(
                OSIRelationship(
                    unique_name=rel_name,
                    from_dataset=from_table,
                    from_columns=[fk_col],
                    to_dataset=to_table,
                    to_columns=[to_col],
                    cardinality=OSICardinality.MANY_TO_ONE,
                    cross_filter_direction=OSICrossFilterDirection.SINGLE,
                    is_active=True,
                )
            )

        return rels

    def _build_datasets(
        self,
        table_map: dict[str, dict[str, Any]],
        col_meta: dict[str, list[dict[str, Any]]],
        ddl: str,
    ) -> list[OSIDataset]:
        """
        Build ``OSIDataset`` objects from the TABLES clause + optional column metadata.
        """
        # Collect columns referenced in DIMENSIONS clause per alias
        dim_columns = self._collect_dimension_columns(ddl)

        datasets: list[OSIDataset] = []
        for alias, info in table_map.items():
            table_name = info["table_name"]
            pk_columns = set(info["pk_columns"])

            raw_cols: list[dict[str, Any]] = col_meta.get(table_name, [])
            osi_columns: list[OSIColumn] = []

            if raw_cols:
                for col in raw_cols:
                    osi_columns.append(
                        OSIColumn(
                            unique_name=col["name"],
                            label=col["name"].replace("_", " ").title(),
                            data_type=_map_sf_type(col.get("data_type", "VARCHAR")),
                            description=col.get("comment") or "",
                            is_key=col["name"].upper() in {p.upper() for p in pk_columns},
                            is_hidden=False,
                        )
                    )
            else:
                # Derive columns from dimension references in DDL
                for col_name in dim_columns.get(alias, []):
                    osi_columns.append(
                        OSIColumn(
                            unique_name=col_name,
                            label=col_name.replace("_", " ").title(),
                            data_type=OSIDataType.STRING,
                            is_key=col_name.upper() in {p.upper() for p in pk_columns},
                        )
                    )
                # Always include PK columns even if not in dimensions
                existing = {c.unique_name.upper() for c in osi_columns}
                for pk in pk_columns:
                    if pk.upper() not in existing:
                        osi_columns.append(
                            OSIColumn(
                                unique_name=pk,
                                label=pk.replace("_", " ").title(),
                                data_type=OSIDataType.INTEGER,
                                is_key=True,
                            )
                        )

            datasets.append(
                OSIDataset(
                    unique_name=table_name,
                    label=table_name.replace("_", " ").title(),
                    source_table=table_name,
                    columns=osi_columns,
                )
            )

        return datasets

    def _collect_dimension_columns(
        self, ddl: str
    ) -> dict[str, list[str]]:
        """
        Collect { alias: [col_name, ...] } from the DIMENSIONS clause.

        Handles: ``alias."col" AS "label"`` and ``alias."col"``.
        """
        content = _extract_clause(ddl, "DIMENSIONS")
        if not content:
            return {}

        result: dict[str, list[str]] = {}
        for entry in _split_clause_entries(content):
            entry = entry.strip()
            m = re.match(r'(\w+)\."?(\w+)"?', entry)
            if m:
                alias, col = m.group(1), m.group(2)
                result.setdefault(alias, []).append(col)

        return result

    def _parse_dimensions_clause(
        self,
        ddl: str,
        table_map: dict[str, dict[str, Any]],
    ) -> list[OSIDimension]:
        """
        Parse the DIMENSIONS clause into ``OSIDimension`` objects.
        """
        content = _extract_clause(ddl, "DIMENSIONS")
        if not content:
            return []

        # Group by alias → attributes
        alias_attrs: dict[str, list[OSIAttribute]] = {}
        for entry in _split_clause_entries(content):
            entry = entry.strip()
            # alias."phys_col" AS "semantic_name"
            m = re.match(
                r'(\w+)\."?(\w+)"?\s+AS\s+"?([^",\)]+)"?', entry, re.IGNORECASE
            )
            if not m:
                m = re.match(r'(\w+)\."?(\w+)"?', entry)
                if not m:
                    continue
                alias, col, label = m.group(1), m.group(2), m.group(2)
            else:
                alias, col, label = m.group(1), m.group(2), m.group(3)

            table_name = table_map.get(alias, {}).get("table_name", alias)
            attr = OSIAttribute(
                unique_name=f"{table_name}.{col}",
                label=_strip_quotes(label).replace("_", " ").title(),
                dataset=table_name,
                source_column=col,
            )
            alias_attrs.setdefault(alias, []).append(attr)

        dimensions: list[OSIDimension] = []
        for alias, attrs in alias_attrs.items():
            table_name = table_map.get(alias, {}).get("table_name", alias)
            dimensions.append(
                OSIDimension(
                    unique_name=f"{table_name}_dim",
                    label=table_name.replace("_", " ").title(),
                    dataset=table_name,
                    attributes=attrs,
                )
            )

        return dimensions

    def _parse_measures_clause(
        self,
        ddl: str,
        table_map: dict[str, dict[str, Any]],
    ) -> list[OSIMetric]:
        """
        Parse the MEASURES clause into ``OSIMetric`` objects.
        """
        content = _extract_clause(ddl, "MEASURES")
        if not content:
            return []

        metrics: list[OSIMetric] = []
        for entry in _split_clause_entries(content):
            entry = entry.strip()
            # alias."col" AS <expr>  OR  alias."col" AS "label"
            m = re.match(
                r'(\w+)\."?(\w+)"?\s+AS\s+(.+)', entry, re.IGNORECASE
            )
            if not m:
                logger.debug(f"Could not parse MEASURES entry: {entry!r}")
                continue

            alias, col, expr = m.group(1), m.group(2), m.group(3).strip()
            table_name = table_map.get(alias, {}).get("table_name", alias)
            # If expression is a quoted string without parens, treat as label alias
            is_label_alias = re.match(r'^"[^"]+"$', expr)
            label = _strip_quotes(expr) if is_label_alias else col.replace("_", " ").title()
            sql_expr = None if is_label_alias else expr

            metrics.append(
                OSIMetric(
                    unique_name=f"{table_name}.{col}",
                    label=label,
                    dataset=table_name,
                    source_column=col,
                    expression=sql_expr,
                    description=f"Measure from Snowflake semantic view",
                )
            )

        return metrics
