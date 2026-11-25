"""Stage 3: the joins of one view.

From the source, every table reachable through active many-to-one
relationships is joined (nested where needed), with the reason it is there.
A table reachable in more than one way is not joined (only it, and whatever
uses it, is affected). Composed (B) measures add their tables as direct
one-to-many joins, with nothing beneath them.

Every join's key types are checked (keytypes.py): text against a number is
compared as text, date-time against date is kept with a warning (as Power BI
does it), and keys that cannot be compared block that table only.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from semabridge.target_backends.databricks.planner import keytypes
from semabridge.target_backends.databricks.planner.anchors import Decision
from semabridge.target_backends.databricks.planner.bindings import SKIP, Bindings
from semabridge.target_backends.databricks.planner.graph import Edge, ModelGraph
from semabridge.target_backends.databricks.planner.model import JoinPlan
from semabridge.target_rules.databricks.capabilities import DATABRICKS_CAPABILITIES

AMBIGUOUS = "ambiguous_path"
KEY_TYPES = "join_key_type_mismatch"
KEY_UNBOUND = "join_key_unbound"


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


class _Keys:
    """The checked join condition for one edge."""

    def __init__(self, edge: Edge, parent_is_one: bool, parent_path: str, child_path: str,
                 graph: ModelGraph, bindings: Bindings, parent_local: str = "source",
                 child_local: Optional[str] = None) -> None:
        self.pairs: List[Tuple[str, str]] = []
        # Per key: which side (if any) is compared as text -- the structured form the
        # writer renders with LOCAL aliases (nested joins name their parent by its join
        # name, YAML 1.1), while on_expr keeps the full expression paths.
        self.casts: List[Optional[str]] = []
        local_terms: List[str] = []
        child_local = child_local or child_path
        self.types: List[Tuple[str, str]] = []
        self.warnings: List[str] = []
        self.blocked: Optional[str] = None
        self.code = KEY_TYPES
        self.relationship = (edge.many, tuple(edge.many_cols), edge.one, tuple(edge.one_cols))
        terms: List[str] = []
        for many_col, one_col in zip(edge.many_cols, edge.one_cols):
            sides = [(edge.many, many_col), (edge.one, one_col)]
            (p_ds, p_col), (c_ds, c_col) = (sides[1], sides[0]) if parent_is_one else (sides[0], sides[1])
            for ds, col in ((p_ds, p_col), (c_ds, c_col)):
                status = bindings.column_status(ds, col)
                if status != "ok" and not self.blocked:
                    self.blocked = f"{ds}[{col}]: {status}"
                    self.code = KEY_UNBOUND
            p_real, c_real = bindings.real_column(p_ds, p_col), bindings.real_column(c_ds, c_col)
            p_type = bindings.column_type(p_ds, p_col, graph.column(p_ds, p_col)) or "unknown"
            c_type = bindings.column_type(c_ds, c_col, graph.column(c_ds, c_col)) or "unknown"
            self.pairs.append((p_real, c_real))
            self.types.append((p_type, c_type))
            left, right = f"{parent_path}.{_quote(p_real)}", f"{child_path}.{_quote(c_real)}"
            verdict, cast_side = keytypes.decide(p_type, c_type)
            l_left, l_right = f"{parent_local}.{_quote(p_real)}", f"{child_local}.{_quote(c_real)}"
            label = f"{p_ds}[{p_col}] ({p_type}) = {c_ds}[{c_col}] ({c_type})"
            if verdict == keytypes.BLOCK and not self.blocked:
                self.blocked = f"{label}: these types can't be compared on Databricks"
            elif verdict == keytypes.CAST:
                if cast_side == "left":
                    left = f"CAST({left} AS STRING)"
                    l_left = f"CAST({l_left} AS STRING)"
                else:
                    right = f"CAST({right} AS STRING)"
                    l_right = f"CAST({l_right} AS STRING)"
                self.warnings.append(f"{label}: compared as text; values that differ only in format "
                                     "(e.g. leading zeros) won't match (checked in 3.9)")
            elif verdict == keytypes.WARN:
                self.warnings.append(f"{label}: date-time against date only matches times at midnight, "
                                     "as in Power BI")
            terms.append(f"{left} = {right}")
            local_terms.append(f"{l_left} = {l_right}")
            self.casts.append(cast_side if verdict == keytypes.CAST else None)
        self.on_expr = " AND ".join(terms)
        self.on_local = " AND ".join(local_terms)


def build_joins(source: str, decisions: List[Decision], graph: ModelGraph, bindings: Bindings
                ) -> Tuple[List[JoinPlan], Dict[str, str], List[str], Dict[str, str]]:
    """(joins, {table: quoted expression path}, notes, {blocked table: reason code}).

    Join aliases are naming-v2 names, unique within the view (`source` reserved);
    expression paths quote every component: `store`.`district`. `source` itself
    is the metric view's own keyword and stays bare."""
    from semabridge.target_backends.databricks.planner.names import join_aliases

    notes: List[str] = []
    paths = graph.paths_from(source)
    children = {c for d in decisions if d.placement == "B" for c, _e in d.children}
    alias = join_aliases(sorted(set(paths) | children))
    plain: Dict[str, str] = {source: "source"}
    blocked: Dict[str, str] = {t: AMBIGUOUS for t, ps in paths.items() if len(ps) > 1}
    for t in sorted(blocked):
        notes.append(f"{t} is reachable from {source} in more than one way: not joined; "
                     "fields and measures that need it are left out")
    used: Set[str] = set()
    for d in decisions:
        if d.semantics is not None:
            used |= set(d.semantics.tables)
    needed: Set[str] = set()
    for t in used:
        if t in paths and len(paths[t]) == 1:
            needed |= {e.one for e in paths[t][0]}

    joins: List[JoinPlan] = []
    where: Dict[str, str] = {source: "source"}
    nested_ok = DATABRICKS_CAPABILITIES.nested_joins
    for target in sorted(paths, key=lambda t: (len(paths[t][0]), t)):
        if target in blocked:
            continue
        path = paths[target][0]
        upstream_blocked = next((e.one for e in path[:-1] if e.one in blocked), None)
        if upstream_blocked:
            blocked[target] = blocked[upstream_blocked]
            continue
        if any(bindings.action(e.one) == SKIP for e in path):
            if bindings.action(target) == SKIP:
                notes.append(f"{target} is skipped (no Unity Catalog table): not joined")
            continue
        if len(path) > 1 and not nested_ok:
            continue
        edge = path[-1]
        parent = where.get(edge.many)
        if parent is None:
            continue
        name = alias[target]
        full = name if parent == "source" else f"{plain[edge.many]}.{name}"
        qpath = _quote(name) if parent == "source" else f"{parent}.{_quote(name)}"
        parent_local = "source" if parent == "source" else _quote(plain[edge.many].split(".")[-1])
        keys = _Keys(edge, parent_is_one=False, parent_path=parent, child_path=qpath, graph=graph, bindings=bindings,
                     parent_local=parent_local, child_local=_quote(name))
        if keys.blocked:
            blocked[target] = keys.code
            notes.append(f"{target} not joined: {keys.blocked}; fields and measures that need it are left out")
            continue
        joins.append(JoinPlan(
            name=full, dataset=target, uc_table=bindings.uc_table(target), parent=plain[edge.many],
            on=keys.pairs, reason="measure_dependency" if target in needed else "business_dimension",
            readiness=bindings.readiness(target), on_expr=keys.on_expr, key_types=keys.types,
            warnings=keys.warnings, relationship=keys.relationship,
            alias=name, parent_alias=parent_local.strip("`").replace("``", "`") if parent_local != "source" else "source",
            on_local=keys.on_local, key_casts=list(keys.casts),
        ))
        notes += [f"join {full}: {w}" for w in keys.warnings]
        where[target] = qpath
        plain[target] = full

    # Composed measures: their other tables, joined one-to-many straight under the source.
    for d in decisions:
        if d.placement != "B":
            continue
        for child, edge in d.children:
            if child in where or child in blocked:
                continue  # already joined (another composed measure) or not joinable
            name = alias[child]
            keys = _Keys(edge, parent_is_one=True, parent_path="source", child_path=_quote(name), graph=graph,
                         bindings=bindings, parent_local="source", child_local=_quote(name))
            if keys.blocked:
                blocked[child] = keys.code
                notes.append(f"{child} not joined: {keys.blocked}; composed measures that need it are left out")
                continue
            joins.append(JoinPlan(
                name=name, dataset=child, uc_table=bindings.uc_table(child), parent="source",
                on=keys.pairs, cardinality="one_to_many", reason="measure_dependency",
                readiness=bindings.readiness(child), on_expr=keys.on_expr, key_types=keys.types,
                warnings=keys.warnings, relationship=keys.relationship,
                alias=name, parent_alias="source", on_local=keys.on_local, key_casts=list(keys.casts),
            ))
            notes += [f"join {name}: {w}" for w in keys.warnings]
            where[child] = _quote(name)
            plain[child] = name
    for e in graph.edges:
        if e.two_way and (e.many in where and e.one in where):
            notes.append(f"{e.many} -> {e.one} filters both ways in Power BI; filters on {e.many} don't reach {e.one} here")
    return joins, where, notes, blocked
