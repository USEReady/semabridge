"""The 3.4 table plan as the planner reads it.

Without a plan (no Databricks connection, e.g. an offline dry run) every
table is "unbound": the planner still plans, using the model's cleaned
column names, and marks everything unbound.

Physical names are always the bound table's real names or the stub's allocated
names (naming v2); never re-normalised here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from semabridge.utils.naming import sanitize_column

BIND, STUB, SKIP, UNBOUND = "bind", "stub", "skip", "unbound"
OK, MISSING, AMBIGUOUS = "ok", "column_missing_in_table", "ambiguous_binding"


class Bindings:
    def __init__(self, table_plan: Optional[Any]) -> None:
        self.available = table_plan is not None
        self._by_dataset: Dict[str, Any] = {}
        for b in getattr(table_plan, "bindings", []) or []:
            self._by_dataset[b.dataset] = b
        self.ambiguous_aliases = set(getattr(table_plan, "ambiguous_aliases", lambda: [])()) if table_plan else set()

    def binding(self, dataset: str) -> Optional[Any]:
        return self._by_dataset.get(dataset)

    def action(self, dataset: str) -> str:
        b = self._by_dataset.get(dataset)
        return b.action if b is not None else UNBOUND

    def uc_table(self, dataset: str) -> Optional[str]:
        b = self._by_dataset.get(dataset)
        table = getattr(b, "table", None) if b is not None else None
        return getattr(table, "full_name", None) if table is not None else None

    def readiness(self, dataset: str) -> str:
        return {BIND: "real", STUB: "structure_only", SKIP: "skipped"}.get(self.action(dataset), "unbound")

    def real_column(self, dataset: str, column_name: str) -> str:
        """The physical name of a model column: the real Unity Catalog name (bound),
        the allocated stub column (stub), else the converter's cleaned name."""
        clean = sanitize_column(column_name)
        b = self._by_dataset.get(dataset)
        if b is not None and b.action in (BIND, STUB):
            return b.column_map.get(clean.upper(), clean)
        return clean

    def match_method(self, dataset: str, column_name: str) -> Optional[str]:
        b = self._by_dataset.get(dataset)
        return (getattr(b, "column_match", {}) or {}).get(column_name) if b is not None else None

    def column_status(self, dataset: str, column_name: str) -> str:
        """ok | column_missing_in_table | ambiguous_binding for a model column."""
        b = self._by_dataset.get(dataset)
        if b is None or b.action != BIND:
            return OK
        if column_name in (getattr(b, "ambiguous_columns", {}) or {}):
            return AMBIGUOUS
        if sanitize_column(column_name).upper() in b.column_map:
            return OK
        return MISSING

    def reference_status(self, dataset: str, sql_column: str) -> str:
        """Status of a column as written in (rewritten) measure SQL for this table."""
        b = self._by_dataset.get(dataset)
        if b is None or b.action not in (BIND, STUB):
            return OK
        name = sql_column.replace("``", "`")
        if name.upper() in b.column_map:   # a converter reference that 3.4 bound (not yet rewritten)
            return OK
        if b.action == STUB:
            return OK if any(n == name for n, _t in b.stub_columns) else MISSING
        # An unbound converter reference must never fall through to a real column of a
        # similar name (e.g. SALES_AMOUNT vs real sales_amount when SalesAmount also exists).
        ambiguous_keys = {sanitize_column(c).upper() for c in (getattr(b, "ambiguous_columns", {}) or {})}
        if name.upper() in ambiguous_keys:
            return AMBIGUOUS
        missing_keys = {sanitize_column(c).upper() for c in (getattr(b, "missing_columns", []) or [])}
        if name.upper() in missing_keys:
            return MISSING
        bound_reals = {real.lower() for real in b.column_map.values()}
        return OK if name.lower() in bound_reals else MISSING

    def physically_present(self, dataset: str, column_name: str) -> Optional[bool]:
        """True/False for a bound table; None when it can't be known (stub/unbound)."""
        b = self._by_dataset.get(dataset)
        if b is None or b.action != BIND:
            return None
        return sanitize_column(column_name).upper() in b.column_map

    def missing(self, dataset: str, column_name: str) -> bool:
        return self.column_status(dataset, column_name) != OK

    def column_type(self, dataset: str, column_name: str, model_column=None) -> Optional[str]:
        """The column's type: Unity Catalog's for a bound table, the stub's for a
        stub, else the Power BI model's (None if unknown)."""
        clean = sanitize_column(column_name).upper()
        b = self._by_dataset.get(dataset)
        if b is not None and b.action == BIND and getattr(b, "table", None) is not None:
            real = b.column_map.get(clean)
            hit = b.table.columns.get(real.upper()) if real else None
            if hit:
                return str(hit[1])
        if b is not None and b.action == STUB:
            stub = b.column_map.get(clean)
            hit = next((t for n, t in b.stub_columns if n == stub), None)
            if hit:
                return hit
        raw = getattr(model_column, "data_type", None)
        return str(getattr(raw, "value", raw)) if raw else None

    def ambiguous_columns(self, dataset: str) -> List[str]:
        b = self._by_dataset.get(dataset)
        return sorted((getattr(b, "ambiguous_columns", {}) or {}).keys()) if b is not None else []
