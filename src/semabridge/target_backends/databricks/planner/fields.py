"""Stage 4: the fields (what people group and filter by) of one view.

Visible columns of the source and its many-to-one joins become fields; never
columns of one-to-many tables (Databricks forbids it). Hidden columns stay
out but remain usable in joins and measures. A calculated column is kept
only when the table really has a column of that name, marked unverified.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from semabridge.target_backends.databricks.planner.bindings import BIND, Bindings
from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import FieldPlan, JoinPlan


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def build_fields(source: str, joins: List[JoinPlan], graph: ModelGraph, bindings: Bindings
                 ) -> Tuple[List[FieldPlan], List[str]]:
    notes: List[str] = []
    places: List[Tuple[str, str]] = [(source, "source")]
    places += [(j.dataset, j.name) for j in joins if j.cardinality == "many_to_one"]
    fields: List[FieldPlan] = []
    for dataset, path in places:
        info = graph.tables[dataset]
        for col in info.columns.values():
            name = str(col.unique_name)
            if getattr(col, "is_hidden", False) or name.startswith(("RowNumber", "_")):
                continue
            verification = "verified"
            if getattr(col, "is_calculated", False):
                present = bindings.physically_present(dataset, name)
                if not present:
                    continue  # no physical column to read (translating calculated columns comes later)
                verification = "unverified"
            if bindings.missing(dataset, name):
                notes.append(f"{dataset}[{name}] is not in the Unity Catalog table: not a field")
                continue
            real = bindings.real_column(dataset, name)
            fields.append(FieldPlan(
                name=name,
                expr=f"{path}.{_quote(real)}",
                display_name=str(getattr(col, "label", "") or name),
                dataset=dataset,
                column=name,
                path=path,
                synonyms=list(getattr(col, "synonyms", None) or []),
                readiness=bindings.readiness(dataset),
                source_verification=verification,
            ))
    return fields, notes
