from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional
import hashlib
import re


@dataclass(frozen=True)
class CalendarBinding:
    table: str
    date_column: str
    year_column: str
    period_column: str
    quarter_column: Optional[str] = None


class SemanticSchemaResolver:
    def __init__(self, model: Any = None, metrics: Optional[Iterable[Any]] = None) -> None:
        self.model = model
        self.metrics = list(metrics or [])
        self._datasets = list(getattr(model, "datasets", []) or [])
        self._relationships = list(getattr(model, "relationships", []) or [])
        self._metric_map = {
            str(getattr(metric, "unique_name", "") or "").casefold(): metric
            for metric in self._collect_metrics()
            if getattr(metric, "unique_name", None)
        }
        self._dataset_map = {
            str(getattr(dataset, "unique_name", "") or "").casefold(): dataset
            for dataset in self._datasets
            if getattr(dataset, "unique_name", None)
        }

    def _collect_metrics(self) -> list[Any]:
        collected: list[Any] = []
        seen: set[str] = set()
        for metric in self.metrics + list(getattr(self.model, "metrics", []) or []):
            name = str(getattr(metric, "unique_name", "") or "").strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            collected.append(metric)
        return collected

    def resolve_measure(self, name: str):
        if not name:
            return None
        return self._metric_map.get(str(name).strip().casefold())

    def resolve_table(self, name: str):
        if not name:
            return None
        key = str(name).strip().casefold()
        if key in self._dataset_map:
            return self._dataset_map[key]
        for dataset in self._datasets:
            aliases = [
                str(getattr(dataset, "label", "") or "").casefold(),
                str(getattr(dataset, "source_table", "") or "").casefold(),
                str(getattr(dataset, "unique_name", "") or "").casefold(),
            ]
            if key in aliases:
                return dataset
        return None

    def resolve_column(self, table: Optional[str], column: str):
        if not column:
            return None
        target = str(column).strip().casefold()
        if table:
            dataset = self.resolve_table(table)
            if not dataset:
                return None
            for col in getattr(dataset, "columns", []) or []:
                if str(getattr(col, "unique_name", "") or "").strip().casefold() == target:
                    return col
            return None
        owners = []
        for dataset in self._datasets:
            for col in getattr(dataset, "columns", []) or []:
                if str(getattr(col, "unique_name", "") or "").strip().casefold() == target:
                    owners.append((dataset, col))
        if len(owners) == 1:
            return owners[0][1]
        return None

    def resolve_calendar(self, expr: Optional[str] = None) -> Optional[CalendarBinding]:
        expr_lower = str(expr or "").casefold()
        candidate_scores: list[tuple[int, Any]] = []
        for dataset in self._datasets:
            score = 0
            cols = list(getattr(dataset, "columns", []) or [])
            names = {str(getattr(col, "unique_name", "") or "").casefold() for col in cols}
            if any(token in names for token in {"date", "datetime", "dateid", "createddate", "calendardate"}):
                score += 6
            if any(token in names for token in {"year", "month", "quarter", "period"}):
                score += 4
            if any(re.search(r"date|calendar|time", str(getattr(dataset, "unique_name", "") or ""), re.IGNORECASE) for _ in [0]):
                score += 2
            if expr_lower and str(getattr(dataset, "unique_name", "") or "").casefold() in expr_lower:
                score += 3
            candidate_scores.append((score, dataset))

        candidate_scores.sort(key=lambda item: (item[0], str(getattr(item[1], "unique_name", "") or "").casefold()), reverse=True)
        for score, dataset in candidate_scores:
            if score <= 0:
                continue
            cols = list(getattr(dataset, "columns", []) or [])
            by_name = {str(getattr(col, "unique_name", "") or "").casefold(): col for col in cols}
            date_col = self._find_column(by_name, ["date", "dateid", "datetime", "calendardate", "transactiondate", "createddate"])
            year_col = self._find_column(by_name, ["year"])
            period_col = self._find_column(by_name, ["month", "period", "date", "dateid"])
            quarter_col = self._find_column(by_name, ["quarter"])
            if date_col and year_col and period_col:
                return CalendarBinding(
                    table=str(getattr(dataset, "unique_name", "") or ""),
                    date_column=date_col,
                    year_column=year_col,
                    period_column=period_col,
                    quarter_column=quarter_col,
                )
        return None

    def resolve_dimension_reference(self, name: str) -> Optional[tuple[Any, Any]]:
        dataset = self.resolve_table(name)
        if not dataset:
            return None
        return dataset, getattr(dataset, "label", None) or getattr(dataset, "unique_name", None)

    def resolve_calendar_reference(self, expr: Optional[str] = None) -> Optional[CalendarBinding]:
        return self.resolve_calendar(expr)

    def resolve_partition_column(self, calendar_name: Optional[str] = None, column_name: Optional[str] = None) -> Optional[tuple[str, str]]:
        binding = self.resolve_calendar(calendar_name)
        if binding is None:
            return None
        if column_name:
            wanted = str(column_name).strip().casefold()
            if wanted in {binding.date_column.casefold(), binding.year_column.casefold(), binding.period_column.casefold(), (binding.quarter_column or "").casefold()}:
                return binding.table, column_name
        return binding.table, binding.year_column

    def stable_alias(self, name: str, scope: Optional[str] = None) -> str:
        base = re.sub(r"[^A-Za-z0-9_$]", "_", str(name or "").strip()).strip("_") or "ALIAS"
        base = base.upper()
        if not scope:
            return base
        digest = hashlib.sha1(f"{scope}:{name}".encode("utf-8")).hexdigest()[:4].upper()
        return f"{base}__{digest}"

    def plan_alias(self, name: str, lineage: Optional[str] = None, existing: Optional[set[str]] = None) -> str:
        existing = existing or set()
        candidate = self.stable_alias(name, scope=lineage)
        if candidate not in existing:
            return candidate
        idx = 2
        while True:
            next_candidate = f"{candidate}_{idx}"
            if next_candidate not in existing:
                return next_candidate
            idx += 1

    def resolve_relationship_path(self, table_a: str, table_b: str):
        start = self.resolve_table(table_a)
        goal = self.resolve_table(table_b)
        if not start or not goal:
            return None
        start_name = str(getattr(start, "unique_name", "") or "")
        goal_name = str(getattr(goal, "unique_name", "") or "")
        adjacency: dict[str, list[Any]] = {}
        for rel in self._relationships:
            if not getattr(rel, "is_active", True):
                continue
            adjacency.setdefault(str(getattr(rel, "from_dataset", "") or "").casefold(), []).append(rel)
            adjacency.setdefault(str(getattr(rel, "to_dataset", "") or "").casefold(), []).append(rel)
        queue: list[tuple[str, list[Any]]] = [(start_name.casefold(), [])]
        seen = {start_name.casefold()}
        while queue:
            current, path = queue.pop(0)
            if current == goal_name.casefold():
                return path
            for rel in adjacency.get(current, []):
                next_name = str(getattr(rel, "to_dataset", "") or "") if str(getattr(rel, "from_dataset", "") or "").casefold() == current else str(getattr(rel, "from_dataset", "") or "")
                next_key = next_name.casefold()
                if next_key in seen:
                    continue
                seen.add(next_key)
                queue.append((next_key, path + [rel]))
        return None

    @staticmethod
    def _find_column(columns: dict[str, Any], candidates: list[str]) -> Optional[str]:
        column_names = list(columns.keys())
        for candidate in candidates:
            candidate_lower = candidate.casefold()
            if candidate_lower in columns:
                return columns[candidate_lower].unique_name
            for column_name in column_names:
                if candidate_lower in column_name or column_name in candidate_lower:
                    return columns[column_name].unique_name
        return None

    def build_dependency_graph(self, metrics: Optional[Iterable[Any]] = None) -> dict[str, list[str]]:
        metrics_pool = list(metrics or [])
        graph: dict[str, list[str]] = {}
        from semabridge.compiler.parser import DaxParser
        parser = DaxParser()
        for metric in metrics_pool:
            name = str(getattr(metric, "unique_name", "") or "")
            expr = str(getattr(metric, "expression", "") or "")
            node = parser.parse(expr)
            refs: list[str] = []
            self._collect_measure_refs(node, refs)
            graph[name] = sorted({ref for ref in refs if ref.casefold() != name.casefold()}, key=str.casefold)
        return graph

    def _collect_measure_refs(self, node: Any, refs: list[str]) -> None:
        from semabridge.compiler.ast import BinaryOpNode, FunctionCallNode, MeasureReferenceNode, UnaryOpNode
        if node is None:
            return
        if isinstance(node, MeasureReferenceNode):
            refs.append(node.name)
            return
        if isinstance(node, BinaryOpNode):
            self._collect_measure_refs(node.left, refs)
            self._collect_measure_refs(node.right, refs)
            return
        if isinstance(node, UnaryOpNode):
            self._collect_measure_refs(node.operand, refs)
            return
        if isinstance(node, FunctionCallNode):
            for arg in node.args:
                self._collect_measure_refs(arg, refs)
