"""Stage 0: what each measure counts, read from its DAX tree.

For every aggregate the planner records the table it counts over (its grain):
``SUM(T[c])`` -> T, ``COUNTROWS(T)`` -> T, ``SUMX(T, ...)`` -> T (values pulled
in with RELATED are one row each, many-to-one). Measure references are
followed, each referenced measure staying a separate component. Tables used
only in filters are condition tables.

Only known shapes are accepted (allow-list). Anything else gives an issue and
the measure is left out; nothing is guessed. A bounded cross-check then
compares the tree with the converted SQL: every table the SQL names must be in
the tree, and the SQL's COUNT(*)s must equal the tree's row counts.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from semabridge.target_backends.databricks.planner import dax_view as dv
from semabridge.target_backends.databricks.planner import sqltext
from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import Aggregate, MeasureSemantics

COLUMN_AGGREGATES = {
    "SUM", "AVERAGE", "AVG", "MIN", "MAX", "COUNT", "COUNTA", "COUNTBLANK",
    "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK",
}
ITERATORS = {"SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX"}
SCALAR_FUNCTIONS = {
    "DIVIDE", "IF", "SWITCH", "BLANK", "ISBLANK", "ABS", "ROUND", "INT", "COALESCE",
    "TRUE", "FALSE", "NOT", "AND", "OR", "ROUNDUP", "ROUNDDOWN", "TRUNC", "SIGN",
    "MROUND", "CEILING", "FLOOR", "POWER", "SQRT", "EXP", "LN", "LOG", "LOG10", "MOD",
}
# Row-level functions (one value per row): allowed inside iterator and FILTER
# expressions, where they cannot change which rows are counted.
ROW_FUNCTIONS = {
    "VALUE", "LEFT", "RIGHT", "MID", "LEN", "UPPER", "LOWER", "TRIM", "CONCATENATE", "SUBSTITUTE",
    "YEAR", "MONTH", "DAY", "QUARTER", "WEEKDAY", "DATE", "CONTAINSSTRING", "STARTSWITH", "IN",
}
FILTER_REMOVAL = {"ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "ALLEXCEPT", "ALLSELECTED"}
TIME_FILTERS = {
    "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD", "PREVIOUSYEAR", "PREVIOUSQUARTER",
    "PREVIOUSMONTH", "PREVIOUSDAY", "NEXTYEAR", "NEXTQUARTER", "NEXTMONTH", "NEXTDAY",
    "DATESYTD", "DATESQTD", "DATESMTD", "DATESINPERIOD", "DATESBETWEEN",
}
TIME_TOTALS = {"TOTALYTD", "TOTALQTD", "TOTALMTD"}

UNRECOGNISED = "unrecognised_pattern"
MISMATCH = "analysis_mismatch"
CYCLE = "dependency_cycle"


class _Issue(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _Acc:
    """What one measure's walk collects."""

    def __init__(self) -> None:
        self.aggregates: List[Aggregate] = []
        self.conditions: Set[str] = set()
        self.refs: List[str] = []
        self.calculate = False
        self.removal = False
        self.time = False


class SemanticsReader:
    def __init__(self, graph: ModelGraph) -> None:
        self.graph = graph
        self._cache: Dict[str, MeasureSemantics] = {}
        self._stack: List[str] = []

    # --- public --------------------------------------------------------------------------

    def read(self, name: str) -> MeasureSemantics:
        if name in self._cache:
            return self._cache[name]
        if name in self._stack:
            loop = " -> ".join(self._stack[self._stack.index(name):] + [name])
            return MeasureSemantics(name=name, issue=(CYCLE, loop))
        metric = self.graph.measures[name]
        self._stack.append(name)
        try:
            sem = self._read(metric)
        finally:
            self._stack.pop()
        self._cache[name] = sem
        return sem

    # --- walk ----------------------------------------------------------------------------

    def _read(self, metric) -> MeasureSemantics:
        name = metric.unique_name
        tree = dv.parse(getattr(metric, "expression", "") or "")
        if tree is None:
            return MeasureSemantics(name=name, issue=(UNRECOGNISED, "the DAX could not be parsed"))
        home = self.graph.table(getattr(metric, "dataset", ""))
        acc = _Acc()
        try:
            root = tree
            while isinstance(root, dv.VarNode):
                root = root.body
            constant = _numeric_literal(root)
            if constant is None:
                self._scalar(tree, acc, home, set())
        except _Issue as exc:
            return MeasureSemantics(name=name, measure_refs=tuple(acc.refs), issue=(exc.code, exc.detail))
        sem = MeasureSemantics(
            name=name,
            aggregates=acc.aggregates,
            condition_tables=frozenset(acc.conditions),
            measure_refs=tuple(dict.fromkeys(acc.refs)),
            uses_calculate=acc.calculate,
            filter_removal=acc.removal,
            time_intelligence=acc.time,
            constant=constant,
        )
        deps_ok = all(self.read(r).issue is None for r in sem.measure_refs if r in self.graph.measures)
        if constant is None and not sem.aggregates and deps_ok and not self._constant_only(sem):
            sem.issue = (UNRECOGNISED, "the measure has no aggregate")
        return sem

    def _constant_only(self, sem: MeasureSemantics) -> bool:
        """No aggregate of its own, built only from constant measures."""
        return bool(sem.measure_refs) and all(self.read(r).constant is not None or self._constant_only(self.read(r))
                                              for r in sem.measure_refs if r in self.graph.measures)

    def _merge(self, ref: str, acc: _Acc) -> None:
        acc.refs.append(ref)
        sub = self.read(ref)
        if sub.issue is not None:
            return  # Stage 1 leaves the parent out with the dependency as root cause
        for a in sub.aggregates:
            acc.aggregates.append(Aggregate(a.function, a.grain, a.value_tables, a.condition_tables,
                                            a.row_count, a.via_measure or ref))
        acc.conditions |= set(sub.condition_tables)
        acc.calculate |= sub.uses_calculate
        acc.removal |= sub.filter_removal
        acc.time |= sub.time_intelligence

    # name resolution ---------------------------------------------------------------------

    def _column(self, table: Optional[str], column: str, home: Optional[str]) -> Tuple[str, str]:
        """(table, column) for a column reference; raises when unresolvable."""
        if table:
            t = self.graph.table(dv.strip_quotes(table))
            if t and self.graph.column(t, column) is not None:
                return t, column
            raise _Issue(UNRECOGNISED, f"unknown column {table}[{column}]")
        if home and self.graph.column(home, column) is not None:
            return home, column
        owners = self.graph.tables_with_column(column)
        if len(owners) == 1:
            return owners[0], column
        raise _Issue(UNRECOGNISED, f"column [{column}] is ambiguous or unknown")

    def _ref_kind(self, node, home: Optional[str]) -> Tuple[str, str, str]:
        """('measure', name, '') or ('column', table, column) for a [x] / T[x] node."""
        if isinstance(node, dv.MeasureRefNode):
            m = self.graph.measure(node.name)
            if m:
                return "measure", m, ""
            t, c = self._column(None, node.name, home)
            return "column", t, c
        if isinstance(node, dv.ColumnRefNode):
            t = self.graph.table(dv.strip_quotes(node.table)) if node.table else None
            if t and self.graph.column(t, node.column) is not None:
                return "column", t, node.column
            m = self.graph.measure(node.column)
            if m:
                return "measure", m, ""
            t, c = self._column(node.table, node.column, home)
            return "column", t, c
        raise _Issue(UNRECOGNISED, f"expected a column, got {type(node).__name__}")

    def _table_ref(self, node) -> str:
        if isinstance(node, dv.FunctionCallNode) and node.func.upper() == "KEEPFILTERS" and len(node.args) == 1:
            node = node.args[0]
        if isinstance(node, dv.IdentifierNode):
            t = self.graph.table(dv.strip_quotes(node.name))
            if t:
                return t
        raise _Issue(UNRECOGNISED, f"expected a table, got {_short(node)}")

    # contexts ----------------------------------------------------------------------------

    def _scalar(self, node, acc: _Acc, home: Optional[str], vars_: Set[str]) -> None:
        if isinstance(node, dv.LiteralNode):
            return
        if isinstance(node, (dv.MeasureRefNode, dv.ColumnRefNode)):
            kind, a, _ = self._ref_kind(node, home)
            if kind == "measure":
                self._merge(a, acc)
                return
            raise _Issue(UNRECOGNISED, f"a bare column ({a}) outside an aggregate")
        if isinstance(node, dv.IdentifierNode):
            if node.name in vars_:
                return
            raise _Issue(UNRECOGNISED, f"a table ({node.name}) used as a value")
        if isinstance(node, (dv.BinaryOpNode, dv.UnaryOpNode)):
            for ch in dv.children(node):
                self._scalar(ch, acc, home, vars_)
            return
        if isinstance(node, dv.VarNode):
            names = set(vars_)
            for var, expr in node.variables:
                self._scalar(expr, acc, home, names)
                names.add(var)
            self._scalar(node.body, acc, home, names)
            return
        if not isinstance(node, dv.FunctionCallNode):
            raise _Issue(UNRECOGNISED, f"{type(node).__name__}")
        fn = node.func.upper()
        if fn in ("MIN", "MAX") and len(node.args) == 2:  # scalar MIN(a, b) / MAX(a, b)
            for a in node.args:
                self._scalar(a, acc, home, vars_)
        elif fn in COLUMN_AGGREGATES:
            acc.aggregates.append(self._column_aggregate(fn, node, home))
        elif fn == "COUNTROWS":
            acc.aggregates.append(self._countrows(node, home, acc))
        elif fn in ITERATORS:
            acc.aggregates.append(self._iterator(fn, node, home, acc))
        elif fn == "CALCULATE":
            if not node.args:
                raise _Issue(UNRECOGNISED, "CALCULATE without an expression")
            acc.calculate = True
            self._scalar(node.args[0], acc, home, vars_)
            for f in node.args[1:]:
                self._filter_arg(f, acc, home)
        elif fn in TIME_TOTALS:
            if len(node.args) < 2:
                raise _Issue(UNRECOGNISED, f"{fn} with too few arguments")
            acc.time = True
            self._scalar(node.args[0], acc, home, vars_)
            kind, t, _ = self._ref_kind(node.args[1], home)
            if kind != "column":
                raise _Issue(UNRECOGNISED, f"{fn} over a measure as its date column")
            acc.conditions.add(t)
            for f in node.args[2:]:
                if not isinstance(f, dv.LiteralNode):
                    self._filter_arg(f, acc, home)
        elif fn in SCALAR_FUNCTIONS:
            for a in node.args:
                self._scalar(a, acc, home, vars_)
        else:
            raise _Issue(UNRECOGNISED, f"function {fn}")

    def _column_aggregate(self, fn: str, node, home: Optional[str]) -> Aggregate:
        if len(node.args) != 1:
            raise _Issue(UNRECOGNISED, f"{fn} with {len(node.args)} arguments")
        kind, t, c = self._ref_kind(node.args[0], home)
        if kind != "column":
            raise _Issue(UNRECOGNISED, f"{fn} over a measure")
        return Aggregate(fn, t, frozenset({t}), frozenset())

    def _countrows(self, node, home: Optional[str], acc: _Acc) -> Aggregate:
        if len(node.args) != 1:
            raise _Issue(UNRECOGNISED, "COUNTROWS needs one table")
        arg = node.args[0]
        if isinstance(arg, dv.FunctionCallNode) and arg.func.upper() == "KEEPFILTERS" and len(arg.args) == 1:
            arg = arg.args[0]
        if isinstance(arg, dv.IdentifierNode):
            t = self._table_ref(arg)
            return Aggregate("COUNTROWS", t, frozenset({t}), frozenset(), row_count=True)
        if isinstance(arg, dv.FunctionCallNode) and arg.func.upper() == "FILTER" and len(arg.args) == 2:
            t = self._table_ref(arg.args[0])
            conds: Set[str] = set()
            self._row(arg.args[1], t, conds, conds, home, condition=True)
            return Aggregate("COUNTROWS", t, frozenset({t}), frozenset(conds - {t}))
        if isinstance(arg, dv.FunctionCallNode) and arg.func.upper() in ("VALUES", "DISTINCT") and len(arg.args) == 1:
            kind, t, _ = self._ref_kind(arg.args[0], home)
            if kind == "column":
                return Aggregate("DISTINCTCOUNT", t, frozenset({t}), frozenset())
        if isinstance(arg, dv.FunctionCallNode) and arg.func.upper() in FILTER_REMOVAL:
            acc.removal = True
            t = self._table_ref(arg.args[0]) if arg.args and isinstance(arg.args[0], dv.IdentifierNode) else None
            if t:
                return Aggregate("COUNTROWS", t, frozenset({t}), frozenset(), row_count=True)
        raise _Issue(UNRECOGNISED, f"COUNTROWS over {_short(arg)}")

    def _iterator(self, fn: str, node, home: Optional[str], acc: _Acc) -> Aggregate:
        if len(node.args) != 2:
            raise _Issue(UNRECOGNISED, f"{fn} needs a table and an expression")
        table_arg, expr = node.args
        conds: Set[str] = set()
        if isinstance(table_arg, dv.FunctionCallNode) and table_arg.func.upper() == "FILTER" and len(table_arg.args) == 2:
            t = self._table_ref(table_arg.args[0])
            self._row(table_arg.args[1], t, conds, conds, home, condition=True)
        else:
            t = self._table_ref(table_arg)
        values: Set[str] = {t}
        self._row(expr, t, values, conds, home, condition=False)
        return Aggregate(fn, t, frozenset(values), frozenset(conds - values))

    def _row(self, node, table: str, values: Set[str], conds: Set[str], home: Optional[str], condition: bool) -> None:
        """Row-level expression over `table` (inside an iterator or FILTER)."""
        target = conds if condition else values
        if isinstance(node, dv.LiteralNode):
            return
        if isinstance(node, (dv.MeasureRefNode, dv.ColumnRefNode)):
            kind, t, _ = self._ref_kind(node, table)
            if kind == "measure":
                raise _Issue(UNRECOGNISED, "a measure inside a row expression (context transition)")
            if t != table:
                raise _Issue(UNRECOGNISED, f"{t} column used in a {table} row expression without RELATED")
            target.add(t)
            return
        if isinstance(node, (dv.BinaryOpNode, dv.UnaryOpNode)):
            for ch in dv.children(node):
                self._row(ch, table, values, conds, home, condition)
            return
        if isinstance(node, dv.FunctionCallNode):
            fn = node.func.upper()
            if fn == "RELATED" and len(node.args) == 1:
                kind, t, _ = self._ref_kind(node.args[0], None)
                if kind != "column":
                    raise _Issue(UNRECOGNISED, "RELATED over a measure")
                target.add(t)
                return
            if fn in ("IF", "SWITCH") and node.args:
                self._row(node.args[0], table, values, conds, home, True)
                for a in node.args[1:]:
                    self._row(a, table, values, conds, home, condition)
                return
            if fn in SCALAR_FUNCTIONS or fn in ROW_FUNCTIONS:
                for a in node.args:
                    self._row(a, table, values, conds, home, condition)
                return
            raise _Issue(UNRECOGNISED, f"function {fn} in a row expression")
        raise _Issue(UNRECOGNISED, f"{type(node).__name__} in a row expression")

    def _filter_arg(self, node, acc: _Acc, home: Optional[str]) -> None:
        """A CALCULATE filter argument: records condition tables and flags."""
        if isinstance(node, dv.FunctionCallNode):
            fn = node.func.upper()
            if fn == "KEEPFILTERS" and len(node.args) == 1:
                self._filter_arg(node.args[0], acc, home)
                return
            if fn in FILTER_REMOVAL:
                acc.removal = True
                for a in node.args:
                    if isinstance(a, dv.IdentifierNode):
                        acc.conditions.add(self._table_ref(a))
                    else:
                        acc.conditions.add(self._ref_kind(a, home)[1])
                return
            if fn in TIME_FILTERS:
                acc.time = True
                for a in node.args:
                    if isinstance(a, (dv.ColumnRefNode, dv.MeasureRefNode)):
                        kind, t, _ = self._ref_kind(a, home)
                        if kind == "column":
                            acc.conditions.add(t)
                return
            if fn == "FILTER" and len(node.args) == 2:
                first = node.args[0]
                if isinstance(first, dv.FunctionCallNode) and first.func.upper() == "ALL" and len(first.args) == 1 \
                        and isinstance(first.args[0], (dv.ColumnRefNode, dv.MeasureRefNode)):
                    # FILTER(ALL(T[c]), <predicate on T[c]>) is the long form of a column filter.
                    kind, t, c = self._ref_kind(first.args[0], home)
                    if kind != "column" or not self._predicate_only_on(node.args[1], t, c, home):
                        raise _Issue(UNRECOGNISED, "FILTER(ALL(column), ...) over other columns")
                    acc.conditions.add(t)
                    return
                t = self._table_ref(first)
                conds: Set[str] = set()
                self._row(node.args[1], t, conds, conds, home, condition=True)
                acc.conditions |= conds | {t}
                return
            if fn == "USERELATIONSHIP":
                raise _Issue(UNRECOGNISED, "USERELATIONSHIP (inactive relationship) is not supported")
        if isinstance(node, dv.BinaryOpNode):
            self._filter_predicate(node, acc, home)
            return
        raise _Issue(UNRECOGNISED, f"CALCULATE filter {_short(node)}")

    def _filter_predicate(self, node, acc: _Acc, home: Optional[str]) -> None:
        if isinstance(node, dv.LiteralNode):
            return
        if isinstance(node, (dv.ColumnRefNode, dv.MeasureRefNode)):
            kind, t, _ = self._ref_kind(node, home)
            if kind == "measure":
                raise _Issue(UNRECOGNISED, "a measure inside a filter condition")
            acc.conditions.add(t)
            return
        if isinstance(node, (dv.BinaryOpNode, dv.UnaryOpNode)):
            for ch in dv.children(node):
                self._filter_predicate(ch, acc, home)
            return
        if isinstance(node, dv.FunctionCallNode) and node.func.upper() in ("NOT", "AND", "OR", "TRUE", "FALSE", "BLANK"):
            for a in node.args:
                self._filter_predicate(a, acc, home)
            return
        raise _Issue(UNRECOGNISED, f"filter condition {_short(node)}")

    def _predicate_only_on(self, node, table: str, column: str, home: Optional[str]) -> bool:
        for n in dv.walk(node):
            if isinstance(n, (dv.ColumnRefNode, dv.MeasureRefNode)):
                kind, t, c = self._ref_kind(n, table)
                if kind != "column" or t != table or c.lower() != column.lower():
                    return False
            elif isinstance(n, dv.FunctionCallNode) and n.func.upper() not in ("NOT", "AND", "OR", "BLANK"):
                return False
        return True


def cross_check(sem: MeasureSemantics, sql: Optional[str], graph: ModelGraph) -> Optional[Tuple[str, str]]:
    """(MISMATCH, detail) when the tree and the converted SQL disagree, else None."""
    if sem.issue is not None or not sql:
        return None
    tree_aliases = {graph.alias(t) for t in sem.tables if t in graph.tables}
    extra = sqltext.aliases(sql) - tree_aliases
    if extra:
        return MISMATCH, f"the SQL reads {sorted(extra)}, which the DAX does not"
    stars = sqltext.count_star(sql)
    if stars and not count_grains(sem):
        return MISMATCH, f"the SQL has {stars} COUNT(*), but the DAX counts no rows"
    return None


ROW_COUNTERS = {"COUNTROWS", "COUNTX", "COUNTAX"}


def count_grains(sem: MeasureSemantics) -> FrozenSet[str]:
    """Tables whose rows the measure counts: the converter may write any of these
    as COUNT(*) (COUNTROWS(T), COUNTAX(T, TRUE())), so COUNT(*) belongs to them."""
    return frozenset(a.grain for a in sem.aggregates if a.row_count or a.function in ROW_COUNTERS)


def _numeric_literal(node) -> Optional[str]:
    if isinstance(node, dv.LiteralNode) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.raw or str(node.value)
    if isinstance(node, dv.UnaryOpNode) and node.op == "-" and isinstance(node.operand, dv.LiteralNode):
        inner = _numeric_literal(node.operand)
        return f"-{inner}" if inner is not None else None
    return None


def _short(node) -> str:
    if isinstance(node, dv.FunctionCallNode):
        return f"{node.func}(...)"
    if isinstance(node, dv.IdentifierNode):
        return node.name
    if isinstance(node, dv.ColumnRefNode):
        return f"{node.table}[{node.column}]"
    if isinstance(node, dv.MeasureRefNode):
        return f"[{node.name}]"
    return type(node).__name__
