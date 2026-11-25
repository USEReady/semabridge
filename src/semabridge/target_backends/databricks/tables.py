"""Unity Catalog table matching and inline stubs (Phase 3.4, Jira CS-12).

For every Power BI table (SML dataset) of a model, decide where its data
comes from:

* bind  -- an existing Unity Catalog table, found by the same rules the
           Snowflake path uses (connectors/table_binding_resolver.py, called
           unchanged): an explicit mapping, else the table named like the
           dataset whose columns match (>= 80%), else the best column match
           (>= 80%); ties pick this model's own table or count as missing,
           otherwise the run stops ("Ambiguous table binding"). Source
           schemas are searched in the order given; the first match wins.
* stub  -- nothing matches and ``on_missing_source`` is "stub" (default): the
           metric view's source will be an inline query with the right
           columns and no rows (``SELECT CAST(NULL AS <type>) AS col ...
           WHERE 1 = 0``, written by the YAML writer in 3.7). Nothing is
           created in Databricks. Numeric columns used in SUM/AVG/... are
           typed DOUBLE / BIGINT, never STRING (CS-12).
* skip  -- nothing matches and ``on_missing_source`` is "skip".
``on_missing_source`` "fail" stops the run, listing the missing tables.

Stubs are not tables, so every run simply re-matches: once the real table
exists the next sync binds to it (CS-12 requirement 4).

For bound tables, every model column gets its real Unity Catalog column
name (case-insensitive, then the resolver's cleaned-name rules), and the
measures' SQL is rewritten to use those names before the snapshot is saved
(see core/engine/targets/databricks.py, Step 6b).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from semabridge.connectors.table_binding_resolver import (
    LiveSchemaMetadata,
    TableBindingAmbiguityError,
    clean_ident,
    compute_column_compatibility,
    normalize_col_name,
    resolve_dataset_to_physical_table,
)
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.naming import sanitize_column, to_alias

ON_MISSING_SOURCE = ("stub", "fail", "skip")
# Error text prefix the dry run maps to HTTP 422 (like Snowflake's
# "Live Snowflake schema unavailable"): never guess without the live schema.
LIVE_SCHEMA_UNAVAILABLE = "Databricks live schema unavailable"
STAGING_MARKER = "__sbstg_"

_SPARK_TYPES = {
    "string": "STRING", "integer": "BIGINT", "decimal": "DOUBLE", "float": "DOUBLE", "boolean": "BOOLEAN",
    "date": "DATE", "datetime": "TIMESTAMP", "time": "STRING", "binary": "BINARY",
}
_NUMERIC_AGGS = ("SUM", "AVG", "STDDEV", "STDDEV_SAMP", "STDDEV_POP", "VARIANCE", "VAR_SAMP", "VAR_POP",
                 "MEDIAN", "PERCENTILE", "TRY_SUM", "TRY_AVG")


class TableMatchingError(RuntimeError):
    """The table plan cannot be made (ambiguous match, or missing tables with on_missing_source=fail)."""


# --- reading Unity Catalog -----------------------------------------------------------


@dataclass
class UcTable:
    catalog: str
    schema: str
    name: str                                   # as stored
    table_type: str = ""
    columns: Dict[str, Tuple[str, str]] = field(default_factory=dict)   # UPPER -> (real name, data_type)

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.name}"


def parse_source_schemas(value: Any, default_catalog: Optional[str], default_schema: Optional[str]) -> List[Tuple[str, str]]:
    """'workspace.sb_tests, other' / ['workspace.sb_tests'] -> [(catalog, schema), ...], in order."""
    items: List[str]
    if value is None or value == "" or value == []:
        items = []
    elif isinstance(value, str):
        items = [v for v in value.split(",")]
    else:
        items = [str(v) for v in value]
    out: List[Tuple[str, str]] = []
    for item in items:
        parts = [p.strip().strip("`") for p in str(item).strip().split(".") if p.strip()]
        if len(parts) == 2:
            pair = (parts[0], parts[1])
        elif len(parts) == 1 and default_catalog:
            pair = (default_catalog, parts[0])
        else:
            continue
        if pair not in out:
            out.append(pair)
    if not out and default_catalog and default_schema:
        out.append((default_catalog, default_schema))
    return out


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _quote(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def read_unity_catalog(client: Any, schemas: Sequence[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, UcTable]]:
    """{(catalog, schema): {TABLE_UPPER: UcTable}} via information_schema (read-only).

    Skips our own staging copies and metric views (never bind to ourselves).
    """
    by_catalog: Dict[str, List[str]] = {}
    for catalog, schema in schemas:
        by_catalog.setdefault(catalog, []).append(schema)
    out: Dict[Tuple[str, str], Dict[str, UcTable]] = {(c, s): {} for c, s in schemas}
    for catalog, schema_list in by_catalog.items():
        in_list = ", ".join(_sql_text(s.lower()) for s in schema_list)
        info = f"{_quote(catalog)}.information_schema"
        tables = client.execute(
            f"SELECT table_schema, table_name, table_type FROM {info}.tables "
            f"WHERE lower(table_schema) IN ({in_list})"
        ).rows
        columns = client.execute(
            f"SELECT table_schema, table_name, column_name, data_type, ordinal_position FROM {info}.columns "
            f"WHERE lower(table_schema) IN ({in_list}) ORDER BY table_schema, table_name, ordinal_position"
        ).rows
        schema_key = {s.lower(): s for s in schema_list}
        for row in tables:
            schema = schema_key.get(str(row.get("table_schema") or "").lower())
            name = str(row.get("table_name") or "")
            ttype = str(row.get("table_type") or "").upper()
            if schema is None or not name or STAGING_MARKER in name.lower() or "METRIC" in ttype:
                continue
            out[(catalog, schema)][name.upper()] = UcTable(catalog, schema, name, ttype)
        for row in columns:
            schema = schema_key.get(str(row.get("table_schema") or "").lower())
            table = out.get((catalog, schema), {}).get(str(row.get("table_name") or "").upper()) if schema else None
            col = str(row.get("column_name") or "")
            if table is not None and col:
                table.columns[col.upper()] = (col, str(row.get("data_type") or ""))
    return out


# --- the plan ---------------------------------------------------------------------------


@dataclass
class TableBinding:
    dataset: str
    alias: str
    action: str                                   # bind | stub | skip
    table: Optional[UcTable] = None
    matched_by: Optional[str] = None
    column_map: Dict[str, str] = field(default_factory=dict)       # SANITIZED_UPPER -> real column name
    missing_columns: List[str] = field(default_factory=list)       # model columns the real table lacks
    stub_columns: List[Tuple[str, str]] = field(default_factory=list)   # (column name, Spark type)
    # Power BI calculated columns the real table has a column of the same name
    # for: usable, but unverified (the values may not match the DAX).
    unverified_calculated: List[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "dataset": self.dataset,
            "action": self.action,
            "table": self.table.full_name if self.table else None,
            "database": self.table.catalog if self.table else None,  # the panel's field name
            "catalog": self.table.catalog if self.table else None,
            "schema": self.table.schema if self.table else None,
            "matched_by": self.matched_by,
            "tie": [],
            "message": self.note or None,
        }
        if self.missing_columns:
            out["missing_columns"] = list(self.missing_columns)
        if self.stub_columns:
            out["stub_columns"] = [{"name": n, "type": t} for n, t in self.stub_columns]
        if self.unverified_calculated:
            out["unverified_calculated_columns"] = list(self.unverified_calculated)
        return out


@dataclass
class TablePlan:
    platform: str = "databricks"
    source_schemas: List[Tuple[str, str]] = field(default_factory=list)
    on_missing_source: str = "stub"
    bindings: List[TableBinding] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "source_schemas": [f"{c}.{s}" for c, s in self.source_schemas],
            "on_missing_source": self.on_missing_source,
            "tables": [b.as_dict() for b in self.bindings],
            "warnings": list(self.warnings),
            "error": self.error,
        }

    def rewrite_map(self) -> Dict[str, Dict[str, str]]:
        """{TABLE_ALIAS: {SANITIZED_COLUMN_UPPER: real column}} for bound tables."""
        return {b.alias.upper(): dict(b.column_map) for b in self.bindings if b.action == "bind" and b.column_map}


def physical_columns(dataset: Any) -> List[Any]:
    """The dataset's physical (non-calculated, non-internal) columns -- the same
    selection as the Snowflake path (SchemaManager._physical_model_column_names)."""
    cols = []
    for col in getattr(dataset, "columns", []) or []:
        name = getattr(col, "unique_name", "") or ""
        if not name or name.startswith(("RowNumber", "_")) or getattr(col, "is_calculated", False):
            continue
        expr = getattr(col, "source_expression", None)
        if expr and not IdentifierSanitizer.is_physical_source_column(expr):
            continue
        cols.append(col)
    return cols


def _real_column(model_col: str, table: UcTable) -> Optional[str]:
    """The real column for a model column: case-insensitive, then the resolver's
    cleaned / normalised names, then its key-suffix rule (X <-> X_ID)."""
    hit = table.columns.get(model_col.upper()) or table.columns.get(sanitize_column(model_col).upper())
    if hit:
        return hit[0]
    clean = clean_ident(model_col)
    norm = normalize_col_name(model_col)
    for upper, (real, _type) in table.columns.items():
        if clean_ident(real) == clean or normalize_col_name(real) == norm:
            return real
    for upper, (real, _type) in table.columns.items():
        c = clean_ident(real)
        if c == clean + "ID" or (clean.endswith("ID") and c == clean[:-2]):
            return real
    return None


def _aggregated_refs(sql_texts: Iterable[str]) -> Dict[str, set]:
    """{ALIAS_UPPER: {COLUMN_UPPER}} referenced inside numeric aggregates."""
    found: Dict[str, set] = {}
    agg = re.compile(r"\b(" + "|".join(_NUMERIC_AGGS) + r")\s*\(", re.IGNORECASE)
    ref = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.`((?:[^`]|``)+)`")
    for sql in sql_texts:
        if not sql:
            continue
        for m in agg.finditer(sql):
            depth, i, n = 0, m.end() - 1, len(sql)
            start = i
            while i < n:
                ch = sql[i]
                if ch == "`":
                    j = sql.find("`", i + 1)
                    i = n if j < 0 else j + 1
                    continue
                if ch == "'":
                    j = sql.find("'", i + 1)
                    i = n if j < 0 else j + 1
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            for r in ref.finditer(sql[start:i + 1]):
                found.setdefault(r.group(1).upper(), set()).add(r.group(2).replace("``", "`").upper())
    return found


def _stub_columns(dataset: Any, alias: str, numeric_refs: Dict[str, set]) -> List[Tuple[str, str]]:
    used_numeric = numeric_refs.get(alias.upper(), set())
    out: List[Tuple[str, str]] = []
    seen = set()
    for col in physical_columns(dataset):
        name = sanitize_column(col.unique_name)
        if name.upper() in seen:
            continue
        seen.add(name.upper())
        dtype = str(getattr(getattr(col, "data_type", None), "value", getattr(col, "data_type", "")) or "").lower()
        spark = _SPARK_TYPES.get(dtype, "STRING")
        if name.upper() in used_numeric and spark not in ("BIGINT", "DOUBLE"):
            spark = "DOUBLE"  # CS-12: numeric aggregates must never read a STRING column
        out.append((name, spark))
    if not out:
        out.append(("_sb_stub", "INT"))
    return out


def _explicit_table(value: str, catalog_meta: Mapping[Tuple[str, str], Mapping[str, UcTable]],
                    default_catalog: Optional[str]) -> Optional[UcTable]:
    parts = [p.strip().strip("`") for p in str(value).split(".") if p.strip()]
    if len(parts) == 3:
        key, name = (parts[0], parts[1]), parts[2]
    elif len(parts) == 2 and default_catalog:
        key, name = (default_catalog, parts[0]), parts[1]
    else:
        return None
    for (cat, sch), tables in catalog_meta.items():
        if cat.lower() == key[0].lower() and sch.lower() == key[1].lower():
            return tables.get(name.upper())
    return None


def plan_tables(
    sml_model: Any,
    catalog_meta: Mapping[Tuple[str, str], Mapping[str, UcTable]],
    *,
    source_schemas: Sequence[Tuple[str, str]],
    on_missing_source: str = "stub",
    table_mapping: Optional[Mapping[str, str]] = None,
) -> TablePlan:
    """The bind / stub / skip decision for every dataset of the model."""
    mode = str(on_missing_source or "stub").strip().lower()
    if mode not in ON_MISSING_SOURCE:
        raise TableMatchingError(f"on_missing_source must be one of {ON_MISSING_SOURCE}, got {on_missing_source!r}")
    plan = TablePlan(source_schemas=list(source_schemas), on_missing_source=mode)
    model_name = getattr(sml_model, "unique_name", None) or getattr(sml_model, "label", None)
    metrics_sql = [getattr(m, "sql_expression", None) or "" for m in getattr(sml_model, "metrics", []) or []]
    numeric_refs = _aggregated_refs(metrics_sql)
    mapping = {str(k): str(v) for k, v in (table_mapping or {}).items() if k and v}
    default_catalog = source_schemas[0][0] if source_schemas else None

    # Datasets whose names clean to the same alias would share SQL references.
    by_alias: Dict[str, List[str]] = {}
    for ds in getattr(sml_model, "datasets", []) or []:
        by_alias.setdefault(to_alias(ds.unique_name).upper(), []).append(ds.unique_name)
    for alias, names in by_alias.items():
        if len(names) > 1:
            plan.warnings.append(f"Tables {names} all become {alias} in SQL; their references can't be told apart.")

    missing: List[str] = []
    for ds in getattr(sml_model, "datasets", []) or []:
        name = ds.unique_name
        alias = to_alias(name)
        cols = [c.unique_name for c in physical_columns(ds)]
        binding: Optional[TableBinding] = None

        explicit = mapping.get(name) or next((v for k, v in mapping.items() if clean_ident(k) == clean_ident(name)), None)
        if explicit:
            table = _explicit_table(explicit, catalog_meta, default_catalog)
            if table is not None:
                binding = TableBinding(name, alias, "bind", table, "mapping")
            else:
                plan.warnings.append(f"{name}: mapped table {explicit} was not found.")

        if binding is None:
            for (catalog, schema) in source_schemas:
                tables = catalog_meta.get((catalog, schema)) or {}
                if not tables:
                    continue
                meta = LiveSchemaMetadata({t: set(tbl.columns) for t, tbl in tables.items()})
                try:
                    resolve_dataset_to_physical_table(name, cols, meta, model_name=model_name)
                except TableBindingAmbiguityError as exc:
                    raise TableMatchingError(f"{exc} (in {catalog}.{schema})") from None
                logged = meta.binding_log.get(clean_ident(name))
                if logged and str(logged.get("table") or "").upper() in tables:
                    binding = TableBinding(name, alias, "bind", tables[str(logged["table"]).upper()],
                                           str(logged.get("matched_by") or "columns"))
                    break
                same = tables.get(name.upper())
                if same is not None and not binding:
                    compat = compute_column_compatibility(cols, set(same.columns)) if cols else 0.0
                    plan.warnings.append(
                        f"{name}: a table named {same.full_name} exists but only {compat:.0%} of its columns match "
                        f"(80% needed), so it was not used."
                    )

        if binding is not None:
            for col in cols:
                real = _real_column(col, binding.table)
                if real is None:
                    binding.missing_columns.append(col)
                else:
                    binding.column_map[sanitize_column(col).upper()] = real
            for col in getattr(ds, "columns", []) or []:
                if getattr(col, "is_calculated", False) and col.unique_name not in cols:
                    real = _real_column(col.unique_name, binding.table)
                    if real is not None:
                        binding.column_map.setdefault(sanitize_column(col.unique_name).upper(), real)
                        binding.unverified_calculated.append(col.unique_name)
            if binding.missing_columns:
                binding.note = f"{len(binding.missing_columns)} column(s) not in {binding.table.full_name}"
        else:
            missing.append(name)
            if mode == "stub":
                binding = TableBinding(name, alias, "stub", matched_by=None,
                                       stub_columns=_stub_columns(ds, alias, numeric_refs),
                                       note="table not found yet: empty table (created at deploy) until it exists")
            else:
                binding = TableBinding(name, alias, "skip", note="table not found: skipped (on_missing_source=skip)")
        plan.bindings.append(binding)

    _align_stub_key_types(sml_model, plan)
    if missing and mode == "fail":
        where = ", ".join(f"{c}.{s}" for c, s in source_schemas) or "the source schemas"
        raise TableMatchingError(f"No Unity Catalog table found in {where} for: {', '.join(missing)} "
                                 "(on_missing_source=fail).")
    return plan


def _align_stub_key_types(sml_model: Any, plan: TablePlan) -> None:
    """A stub's join-key column takes the type of the column it joins to, so the
    join compares like with like (we create the stub, so we choose its types).
    Against a real table that is its Unity Catalog type; between two stubs, the
    lookup (one) side's type."""
    by_dataset = {b.dataset: b for b in plan.bindings}

    def key_type(binding: TableBinding, column: str) -> Optional[str]:
        clean = sanitize_column(column).upper()
        if binding.action == "bind" and binding.table is not None:
            real = binding.column_map.get(clean)
            hit = binding.table.columns.get(real.upper()) if real else None
            return str(hit[1]).upper() if hit else None
        if binding.action == "stub":
            return next((t for n, t in binding.stub_columns if n.upper() == clean), None)
        return None

    def set_type(binding: TableBinding, column: str, spark: str) -> None:
        clean = sanitize_column(column).upper()
        binding.stub_columns = [(n, spark if n.upper() == clean else t) for n, t in binding.stub_columns]

    for rel in getattr(sml_model, "relationships", []) or []:
        many, one = by_dataset.get(rel.from_dataset), by_dataset.get(rel.to_dataset)
        if many is None or one is None:
            continue
        for m_col, o_col in zip(rel.from_columns, rel.to_columns):
            if many.action == "stub" and one.action in ("bind", "stub"):
                t = key_type(one, o_col)
                if t:
                    set_type(many, m_col, t)
            elif one.action == "stub" and many.action == "bind":
                t = key_type(many, m_col)
                if t:
                    set_type(one, o_col, t)


# --- measure SQL to real column names ---------------------------------------------------------

_REF = re.compile(r"(?<![A-Za-z0-9_`])([A-Za-z_][A-Za-z0-9_]*)\.`((?:[^`]|``)+)`")


def rewrite_measure_columns(sql: Optional[str], rewrite_map: Mapping[str, Mapping[str, str]]) -> Optional[str]:
    """ALIAS.`SANITIZED` -> ALIAS.`real name` for columns of bound tables.

    Only backticked column names after a known table alias change; the
    logic of the SQL is untouched. Unknown aliases / columns stay as they are,
    and text inside string literals (e.g. 'SALES.`X`') is never changed.
    """
    from semabridge.target_backends.databricks.planner.sqltext import sub_in_code

    if not sql or not rewrite_map:
        return sql

    def repl(m: "re.Match[str]") -> str:
        columns = rewrite_map.get(m.group(1).upper())
        if not columns:
            return m.group(0)
        real = columns.get(m.group(2).replace("``", "`").upper())
        if real is None:
            return m.group(0)
        return f"{m.group(1)}.{_quote(real)}"

    return sub_in_code(_REF, repl, sql)
