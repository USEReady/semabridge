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
from semabridge.target_backends.databricks.planner.model import FieldPlan, JoinPlan, SourceId


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def build_fields(source: str, joins: List[JoinPlan], where: Dict[str, str], graph: ModelGraph,
                 bindings: Bindings) -> Tuple[List[FieldPlan], List[str]]:
    """Fields with naming-v2 base names (allocated later in names.py), quoted
    expressions over the exact physical column, and the Power BI label kept."""
    from semabridge.target_backends.databricks.planner.names import label_metadata
    from semabridge.target_rules.databricks.naming import normalize

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
            status = bindings.column_status(dataset, name)
            if status != "ok":
                why = "several real columns could match; set an explicit mapping" if status == "ambiguous_binding"                     else "not in the Unity Catalog table"
                notes.append(f"{dataset}[{name}] not a field ({status}): {why}")
                continue
            real = bindings.real_column(dataset, name)
            label = str(getattr(col, "label", "") or name)
            original = str(getattr(col, "renamed_from", "") or name)
            sid = SourceId("field", dataset, name)
            base, _reasons = normalize(original, "field", sid.key())
            display, synonyms, label_notes = label_metadata(label, list(getattr(col, "synonyms", None) or []))
            notes += [f"{dataset}[{name}]: {n}" for n in label_notes]
            fields.append(FieldPlan(
                name=base,
                expr=f"{where[dataset]}.{_quote(real)}",
                display_name=display,
                dataset=dataset,
                column=name,
                path=path,
                synonyms=synonyms,
                readiness=bindings.readiness(dataset),
                source_verification=verification,
                source_id=sid,
                match_method=bindings.match_method(dataset, name),
                source_format=getattr(col, "format_string", None),
            ))
    return fields, notes
