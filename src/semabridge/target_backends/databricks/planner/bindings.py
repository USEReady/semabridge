"""The 3.4 table plan as the planner reads it.

Without a plan (no Databricks connection, e.g. an offline dry run) every
table is "unbound": the planner still plans, using the model's cleaned
column names, and marks everything unbound.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from semabridge.utils.naming import sanitize_column

BIND, STUB, SKIP, UNBOUND = "bind", "stub", "skip", "unbound"


class Bindings:
    def __init__(self, table_plan: Optional[Any]) -> None:
        self.available = table_plan is not None
        self._by_dataset: Dict[str, Any] = {}
        for b in getattr(table_plan, "bindings", []) or []:
            self._by_dataset[b.dataset] = b

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
        """The Unity Catalog name of a model column (cleaned name when not bound)."""
        clean = sanitize_column(column_name)
        b = self._by_dataset.get(dataset)
        if b is not None and b.action == BIND:
            return b.column_map.get(clean.upper(), clean)
        return clean

    def physically_present(self, dataset: str, column_name: str) -> Optional[bool]:
        """True/False for a bound table; None when it can't be known (stub/unbound)."""
        b = self._by_dataset.get(dataset)
        if b is None or b.action != BIND:
            return None
        return sanitize_column(column_name).upper() in b.column_map

    def missing(self, dataset: str, column_name: str) -> bool:
        b = self._by_dataset.get(dataset)
        if b is None or b.action != BIND:
            return False
        clean = sanitize_column(column_name).upper()
        return any(sanitize_column(m).upper() == clean for m in b.missing_columns)

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
            hit = next((t for n, t in b.stub_columns if n.upper() == clean), None)
            if hit:
                return hit
        raw = getattr(model_column, "data_type", None)
        return str(getattr(raw, "value", raw)) if raw else None
