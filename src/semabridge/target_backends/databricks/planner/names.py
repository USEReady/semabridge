"""Stage 6: names (Databricks naming policy revision 2).

Each name comes from the independent Databricks normaliser (lowercase
snake_case; target_rules/databricks/naming.py), then a per-namespace allocator:

  1. reserved (already deployed) names, by structured source identity
  2. natural names for every object, in a stable order: measures in model
     order, then fields by join distance, ties broken by source identity
  3. only then secondary candidates: fields <table>_<name>, then numbers;
     measures <name>_2, _3 (+ a "possible duplicate" note when the SQL is
     identical) -- a prefixed name never takes another object's natural name
  4. every final name re-checked (grammar for generated names, legality for
     preserved ones, length, uniqueness)

Join aliases and the internal word `source` are reserved in the field and
measure namespace (no field/join overlap). Business labels stay in
display_name / synonyms, within the YAML 1.1 limits.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import (
    FieldPlan,
    MeasurePlan,
    Rename,
    SourceId,
    ViewPlan,
)
from semabridge.target_rules.databricks import DATABRICKS_RULES
from semabridge.target_rules.databricks.naming import MAX_LEN

NAMING = DATABRICKS_RULES.naming   # the Databricks rulebook's naming object (policy revision 2)

MAX_SYNONYMS = 10
MAX_LABEL = 255


class _Item:
    def __init__(self, obj, sid: SourceId, base: str, label: str, kind: str,
                 secondary: Callable[[int], str], order: Tuple) -> None:
        self.obj, self.sid, self.base, self.label, self.kind = obj, sid, base, label, kind
        self.secondary, self.order = secondary, order
        self.final: Optional[str] = None
        self.generated = True
        self.why = ""


class Allocator:
    """One case-insensitive namespace."""

    def __init__(self, reserved_words: Sequence[str] = ()) -> None:
        self.taken: Set[str] = {w.lower() for w in reserved_words}

    def free(self, name: str) -> bool:
        return name.lower() not in self.taken

    def take(self, name: str) -> None:
        self.taken.add(name.lower())

    def allocate(self, items: List[_Item], reserved: Mapping[SourceId, str]) -> None:
        items = sorted(items, key=lambda it: it.order)
        for it in items:                                   # 1. reserved
            name = reserved.get(it.sid)
            if name and NAMING.is_legal_identifier(name) and self.free(name):
                it.final, it.generated, it.why = name, NAMING.is_generated_name(name), "kept the name already deployed"
                self.take(name)
        for it in items:                                   # 2. natural names first
            if it.final is None and self.free(it.base):
                it.final = it.base
                self.take(it.base)
        for it in items:                                   # 3. secondary candidates
            if it.final is not None:
                continue
            n = 1
            while True:
                cand = NAMING.fit_length(it.secondary(n), (it.sid.key(), n))
                if self.free(cand):
                    break
                n += 1
            it.final = cand
            it.why = f"{it.base} is already used in this view"
            self.take(cand)


def _labels(label: str, synonyms: Sequence[str]) -> Tuple[str, List[str], List[str]]:
    """display_name, synonyms (own only, deduplicated, each <= 255, at most 10), notes."""
    notes: List[str] = []
    display = label
    if len(display) > MAX_LABEL:
        display = display[:MAX_LABEL]
        notes.append(f"display name shortened to {MAX_LABEL} characters (full label kept in the plan)")
    out: List[str] = []
    seen: Set[str] = set()
    for s in [label] + [str(x) for x in synonyms or [] if x]:
        if len(s) > MAX_LABEL:
            notes.append(f"synonym longer than {MAX_LABEL} characters omitted: {s[:40]}...")
            continue
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    if len(out) > MAX_SYNONYMS:
        notes.append(f"{len(out) - MAX_SYNONYMS} synonym(s) omitted (limit {MAX_SYNONYMS})")
        out = out[:MAX_SYNONYMS]
    return display, out, notes


def assign_names(view: ViewPlan, graph: ModelGraph, reserved: Mapping[SourceId, str],
                 join_aliases: Sequence[str], measure_order: Mapping[str, int]) -> List[Rename]:
    alloc = Allocator(["source", *join_aliases])
    items: List[_Item] = []
    sql_by_name: Dict[str, List[MeasurePlan]] = {}
    for m in view.measures:
        sql_by_name.setdefault(m.expr, []).append(m)
        base = m.name  # measures.py set the normalised base name
        items.append(_Item(m, m.source_id, base, m.source_name, "measure",
                           (lambda n, b=base: f"{b}_{n + 1}"),
                           (0, measure_order.get(m.source_name, 10 ** 6), m.source_id.key())))
    depth = {"source": 0}
    for j in view.joins:
        depth[j.name] = j.name.count(".") + 1
    for f in view.fields:
        base = f.name  # fields.py set the normalised base name
        table_base = NAMING.normalize(f.dataset, "table", ("table", f.dataset))[0]
        items.append(_Item(f, f.source_id, base, f.column, "field",
                           (lambda n, b=base, t=table_base: f"{t}_{b}" if n == 1 else f"{t}_{b}_{n}"),
                           (1, depth.get(f.path, 9), f.path, f.source_id.key())))
    alloc.allocate(items, reserved)

    renames: List[Rename] = []
    for it in items:
        it.obj.name = it.final
        it.obj.name_generated = it.generated
        if it.final != it.base:
            renames.append(Rename(view.name, it.kind, it.label, it.final, it.why, it.sid))
    for expr, ms in sql_by_name.items():
        if len(ms) > 1:
            names = ", ".join(sorted(m.name for m in ms))
            for m in ms:
                m.notes.append(f"possible duplicate: same SQL as {names} (kept as separate measures)")
    return renames




def label_metadata(label: str, synonyms: Sequence[str]) -> Tuple[str, List[str], List[str]]:
    return _labels(label, synonyms)


def view_names(sources: Sequence[str], reserved: Mapping[str, str]) -> Tuple[Dict[str, str], List[Rename]]:
    """{source table: view name}: <table>_metrics, allocated in the destination
    schema's relation namespace (existing objects are checked at deploy, 3.8)."""
    alloc = Allocator()
    items: List[_Item] = []
    for s in sorted(sources):
        base_table = NAMING.normalize(s, "view", ("view", s))[0]
        base = NAMING.fit_length(f"{base_table}_metrics", ("view", s))
        sid = SourceId("table", s, s)
        items.append(_Item(None, sid, base, s, "view",
                           (lambda n, b=base_table: f"{b}_{n + 1}_metrics"), (0, s)))
    alloc.allocate(items, {SourceId("table", s, s): v for s, v in (reserved or {}).items()})
    out = {it.sid.table: it.final for it in items}
    renames = [Rename(it.final, "view", it.label, it.final, it.why, it.sid) for it in items if it.final != it.base]
    return out, renames


def join_aliases(tables: Sequence[str]) -> Dict[str, str]:
    """{table: join alias}, unique within one view; `source` reserved."""
    alloc = Allocator(["source"])
    items = []
    for t in tables:
        base = NAMING.normalize(t, "join", ("join", t))[0]
        items.append(_Item(None, SourceId("table", t, t), base, t, "join",
                           (lambda n, b=base: f"{b}_{n + 1}"), (0, t)))
    alloc.allocate(items, {})
    return {it.sid.table: it.final for it in items}


def check_name(name: str, generated: bool) -> Optional[str]:
    if generated and not NAMING.is_generated_name(name):
        return f"generated name {name!r} breaks the naming rule"
    if not NAMING.is_legal_identifier(name) or len(name) > MAX_LEN:
        return f"name {name!r} is not a legal Databricks identifier"
    return None
