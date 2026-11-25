"""The model as the planner sees it: tables, columns, measures and the join graph.

Only active many-to-one relationships (one-to-one counts as many-to-one) are
joinable. Inactive and many-to-many relationships are not; two-way ones are
joined in their many-to-one direction and noted, because the reverse
filtering cannot be represented. Placement and join building use this same
graph, so they can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from semabridge.utils.naming import sanitize_column, to_alias


@dataclass(frozen=True)
class Edge:
    many: str                         # dataset on the many side
    one: str                          # dataset on the one side
    many_cols: Tuple[str, ...]
    one_cols: Tuple[str, ...]
    two_way: bool = False
    name: str = ""


@dataclass
class TableInfo:
    name: str
    alias: str                        # the converter's SQL alias, upper case
    columns: Dict[str, Any] = field(default_factory=dict)   # lower name -> SMLColumn
    is_fact: bool = False
    hidden: bool = False


def _card(rel: Any) -> str:
    raw = getattr(rel, "cardinality", "many-to-one")
    return str(getattr(raw, "value", raw) or "many-to-one").lower()


class ModelGraph:
    def __init__(self, sml_model: Any) -> None:
        self.tables: Dict[str, TableInfo] = {}
        self._by_lower: Dict[str, str] = {}
        for ds in getattr(sml_model, "datasets", []) or []:
            info = TableInfo(
                name=ds.unique_name,
                alias=to_alias(ds.unique_name).upper(),
                is_fact=bool(getattr(ds, "is_fact", False)),
                hidden=bool(getattr(ds, "is_hidden", False)),
            )
            for col in getattr(ds, "columns", []) or []:
                info.columns[str(col.unique_name).lower()] = col
            self.tables[ds.unique_name] = info
            self._by_lower[ds.unique_name.lower()] = ds.unique_name

        self.by_alias: Dict[str, List[str]] = {}
        for t, info in self.tables.items():
            self.by_alias.setdefault(info.alias, []).append(t)
        # converter aliases shared by two tables: their SQL references can't be told apart
        self.ambiguous_aliases = {a for a, ts in self.by_alias.items() if len(ts) > 1}

        self.measures: Dict[str, Any] = {}
        self._measure_lower: Dict[str, str] = {}
        for m in getattr(sml_model, "metrics", []) or []:
            self.measures[m.unique_name] = m
            self._measure_lower[m.unique_name.lower()] = m.unique_name

        self.edges: List[Edge] = []
        self.skipped_relationships: List[str] = []
        self.two_way_notes: List[str] = []
        for rel in getattr(sml_model, "relationships", []) or []:
            label = f"{rel.from_dataset}[{', '.join(rel.from_columns)}] -> {rel.to_dataset}[{', '.join(rel.to_columns)}]"
            card = _card(rel)
            if not getattr(rel, "is_active", True):
                self.skipped_relationships.append(f"{label}: inactive (not joined)")
                continue
            if card == "many-to-many":
                self.skipped_relationships.append(f"{label}: many-to-many (not joined)")
                continue
            if rel.from_dataset not in self.tables or rel.to_dataset not in self.tables:
                self.skipped_relationships.append(f"{label}: table not in model")
                continue
            cross = str(getattr(getattr(rel, "cross_filter", ""), "value", getattr(rel, "cross_filter", "")) or "").lower()
            if card == "one-to-many":
                edge = Edge(rel.to_dataset, rel.from_dataset, tuple(rel.to_columns), tuple(rel.from_columns),
                            cross == "both", getattr(rel, "unique_name", ""))
            else:  # many-to-one, one-to-one
                edge = Edge(rel.from_dataset, rel.to_dataset, tuple(rel.from_columns), tuple(rel.to_columns),
                            cross == "both", getattr(rel, "unique_name", ""))
            self.edges.append(edge)
            if edge.two_way:
                self.two_way_notes.append(
                    f"{edge.many} -> {edge.one} filters both ways in Power BI; only {edge.one} filtering "
                    f"{edge.many} is kept (filters on {edge.many} don't reach {edge.one})."
                )
        self._out: Dict[str, List[Edge]] = {}
        for e in self.edges:
            self._out.setdefault(e.many, []).append(e)

    # --- lookups -------------------------------------------------------------------------

    def table(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return self._by_lower.get(name.strip().lower())

    def measure(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return self._measure_lower.get(name.strip().lower())

    def column(self, table: str, column: str) -> Optional[Any]:
        info = self.tables.get(table)
        return info.columns.get(column.strip().lower()) if info else None

    def tables_with_column(self, column: str) -> List[str]:
        low = column.strip().lower()
        return [t for t, info in self.tables.items() if low in info.columns]

    def alias(self, table: str) -> str:
        return self.tables[table].alias

    def table_for_alias(self, alias: str) -> Optional[str]:
        ts = self.by_alias.get(alias.upper(), [])
        return ts[0] if len(ts) == 1 else None

    def out_edges(self, table: str) -> List[Edge]:
        return list(self._out.get(table, []))

    # --- paths ---------------------------------------------------------------------------

    def paths_from(self, source: str, limit: int = 2) -> Dict[str, List[Tuple[Edge, ...]]]:
        """Every table reachable from `source` through many-to-one edges, with up to
        `limit` distinct paths each (2 is enough to know a path is not unique).
        Cycles stop the walk; a table on a cycle shows as ambiguous via two paths."""
        found: Dict[str, List[Tuple[Edge, ...]]] = {}

        def visit(table: str, path: Tuple[Edge, ...], seen: FrozenSet[str]) -> None:
            for e in sorted(self.out_edges(table), key=lambda x: (x.one, x.many_cols)):
                if e.one == source or e.one in seen:
                    found.setdefault(e.one, [])
                    if len(found[e.one]) < limit:
                        found[e.one].append(path + (e,) + (e,))  # marks a cycle as a second path
                    continue
                lst = found.setdefault(e.one, [])
                if len(lst) < limit:
                    lst.append(path + (e,))
                    visit(e.one, path + (e,), seen | {e.one})

        visit(source, (), frozenset({source}))
        found.pop(source, None)
        return found

    def unique_path(self, source: str, target: str) -> Optional[Tuple[Edge, ...]]:
        if source == target:
            return ()
        paths = self.paths_from(source).get(target, [])
        return paths[0] if len(paths) == 1 else None

    def direct_edges(self, many: str, one: str) -> List[Edge]:
        return [e for e in self.out_edges(many) if e.one == one]


def column_sql_name(col: Any) -> str:
    """The cleaned column name the converter writes in measure SQL."""
    return sanitize_column(getattr(col, "unique_name", "") or "")
