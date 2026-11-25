"""Stage 6: names (Databricks naming policy revision 2).

Each name comes from the independent Databricks normaliser (lowercase
snake_case; target_rules/databricks/naming.py), then a per-namespace allocator:

  1. reserved (already deployed) names, by structured source identity
  1a. firm requests (explicit user edits): honoured, or a BLOCKING conflict
      when the name is taken (deployed name, another firm request, a join
      alias / `source`) or not legal -- never silently changed
  1b. soft requests (unmarked historical saved names): honoured only when free
      AND not another object's natural name; otherwise the object is named
      normally and the change is reported (never silently discarded)
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


class NameRequest:
    """A saved name for one source object: firm (explicit user edit) or soft."""

    def __init__(self, source_path: str, name: str, firm: bool) -> None:
        self.source_path, self.name, self.firm = source_path, name, firm


class Allocator:
    """One case-insensitive namespace."""

    def __init__(self, reserved_words: Sequence[str] = ()) -> None:
        self.words: Set[str] = {w.lower() for w in reserved_words}
        self.taken: Set[str] = set(self.words)

    def free(self, name: str) -> bool:
        return name.lower() not in self.taken

    def take(self, name: str) -> None:
        self.taken.add(name.lower())

    def _holder(self, name: str, items: List[_Item], me: _Item) -> str:
        for o in items:
            if o is not me and o.final is not None and o.final.lower() == name.lower():
                return f"{o.kind} {o.label}"
        return "a join alias or the reserved word `source`" if name.lower() in self.words else "another object"

    def allocate(self, items: List[_Item], reserved: Mapping[SourceId, str],
                 requests: Optional[Mapping[SourceId, NameRequest]] = None) -> List[Dict]:
        """Allocate every item; returns one event per requested name."""
        items = sorted(items, key=lambda it: it.order)
        requests = requests or {}
        events: List[Dict] = []
        for it in items:                                   # 1. reserved
            name = reserved.get(it.sid)
            if name and NAMING.is_legal_identifier(name) and self.free(name):
                it.final, it.generated, it.why = name, NAMING.is_generated_name(name), "kept the name already deployed"
                self.take(name)
        for it in items:                                   # 1a. firm requests
            req = requests.get(it.sid)
            if req is None or not req.firm:
                continue
            ev = {"item": it, "request": req, "holder": ""}
            events.append(ev)
            if it.final is not None and it.final.lower() != req.name.lower():
                ev.update(status="conflict", holder="the deployed name",
                          reason=f"the deployed name {it.final} is kept (deployed names are protected)")
            elif it.final is not None:
                ev.update(status="kept", reason="")
            elif not NAMING.is_legal_identifier(req.name):
                ev.update(status="conflict", reason=f"{req.name!r} is not a legal Databricks name")
            elif not self.free(req.name):
                holder = self._holder(req.name, items, it)
                ev.update(status="conflict", holder=holder,
                          reason=f"{req.name} is already used by {holder} in this view")
            else:
                it.final, it.generated, it.why = req.name, NAMING.is_generated_name(req.name), "your saved name"
                self.take(req.name)
                ev.update(status="kept", reason="")
        for it in items:                                   # 1b. soft requests
            req = requests.get(it.sid)
            if req is None or req.firm:
                continue
            ev = {"item": it, "request": req, "holder": ""}
            events.append(ev)
            if it.final is not None:
                same = it.final.lower() == req.name.lower()
                ev.update(status="kept" if same else "changed",
                          reason="" if same else "the deployed name is kept")
                continue
            if req.name.lower() == it.base.lower():
                ev.update(status="pending", reason="")       # its own natural name: step 2 decides
                continue
            owner = next((o for o in items if o is not it and o.final is None
                          and o.base.lower() == req.name.lower()), None)
            if not NAMING.is_legal_identifier(req.name):
                ev.update(status="changed", reason=f"{req.name!r} is not a legal Databricks name")
            elif not self.free(req.name):
                holder = self._holder(req.name, items, it)
                ev.update(status="changed", holder=holder, reason=f"{req.name} is already used by {holder}")
            elif owner is not None:
                # a soft request never displaces another object's natural name
                ev.update(status="changed", holder=f"{owner.kind} {owner.label}",
                          reason=f"{req.name} is the natural name of {owner.kind} {owner.label}")
            else:
                it.final, it.generated, it.why = req.name, NAMING.is_generated_name(req.name), "saved name kept"
                self.take(req.name)
                ev.update(status="kept", reason="")
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
        for ev in events:                                  # outcome of requests left to steps 2-3
            it, req = ev["item"], ev["request"]
            ev["final"] = it.final
            if ev["status"] == "pending":
                ok = it.final.lower() == req.name.lower()
                ev["status"] = "kept" if ok else "changed"
                ev["reason"] = "" if ok else f"{req.name} is already used in this view"
        return events


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
                 join_aliases: Sequence[str], measure_order: Mapping[str, int],
                 requests: Optional[Mapping[SourceId, NameRequest]] = None,
                 events_out: Optional[List[Dict]] = None) -> List[Rename]:
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
    events = alloc.allocate(items, reserved, requests)
    if events_out is not None:
        for ev in events:
            ev["view"] = view.name
        events_out.extend(events)

    renames: List[Rename] = []
    for it in items:
        it.obj.name = it.final
        it.obj.name_generated = it.generated
        if it.final != it.base and it.why not in ("your saved name", "saved name kept"):
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
