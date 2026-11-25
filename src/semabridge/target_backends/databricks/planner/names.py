"""Stage 6: names. One case-insensitive name space per view.

Names are made with the Databricks naming rule (Snowflake's: upper case,
% -> PCT, ...); the Power BI name stays as display name. Precedence:
names already deployed (reserved, by source object), then measures, then
fields nearest the source; a clashing field gets its table as prefix, then a
number. Every change is listed.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Set

from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import Rename, ViewPlan
from semabridge.target_rules.databricks import DATABRICKS_RULES


def _target(name: str, kind: str) -> str:
    return DATABRICKS_RULES.naming.target_name(name, entity_kind=kind)


def assign_names(view: ViewPlan, graph: ModelGraph, reserved: Mapping[str, str]) -> List[Rename]:
    renames: List[Rename] = []
    taken: Set[str] = set()
    by_id: Dict[str, str] = {}
    for obj_id, name in (reserved or {}).items():
        by_id[obj_id] = name
        taken.add(name.upper())

    def claim(obj_id: str, wanted: str, original: str, kind: str, prefix: str) -> str:
        if obj_id in by_id:
            final = by_id[obj_id]
            if final.upper() != wanted.upper():
                renames.append(Rename(view.name, kind, original, final, "kept the name already deployed"))
            return final
        final, why = wanted, ""
        if final.upper() in taken:
            final, why = f"{prefix}_{wanted}", f"{wanted} is already used in this view"
            n = 2
            while final.upper() in taken:
                final = f"{prefix}_{wanted}_{n}"
                n += 1
        taken.add(final.upper())
        if why:
            renames.append(Rename(view.name, kind, original, final, why))
        return final

    for m in view.measures:
        wanted = _target(m.source_name, "metric")
        m.name = claim(f"measure:{m.source_name}", wanted, m.source_name, "measure", "MEASURE")

    depth = {"source": 0}
    for j in view.joins:
        depth[j.name] = j.name.count(".") + 1
    ordered = sorted(view.fields, key=lambda f: (depth.get(f.path, 9), f.path, ))
    for f in ordered:
        wanted = _target(f.column, "dimension")
        prefix = graph.alias(f.dataset)
        f.name = claim(f"field:{f.dataset}.{f.column}", wanted, f"{f.dataset}[{f.column}]", "field", prefix)
    return renames
