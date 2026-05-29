from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Optional

from semabridge.compiler.metric_classifier import MetricClassifier

if TYPE_CHECKING:
    from semabridge.compiler.compiler import CompilerResult, DAXCompiler


@dataclass
class DependencyResolutionResult:
    metric_name: str
    result: CompilerResult
    cached: bool = False


@dataclass
class DependencyPlan:
    graph: dict[str, list[str]] = field(default_factory=dict)
    ordered: list[str] = field(default_factory=list)
    groups: list[list[str]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    missing_dependencies: dict[str, list[str]] = field(default_factory=dict)


class MetricDependencyResolver:
    def __init__(self, model: Any = None, metrics: Optional[Iterable[Any]] = None, compiler: Optional[DAXCompiler] = None) -> None:
        self.model = model
        self.metrics = list(metrics or getattr(model, "metrics", []) or [])
        if compiler is None:
            from semabridge.compiler.compiler import DAXCompiler

            compiler = DAXCompiler()
        self.compiler = compiler
        self.classifier = MetricClassifier()
        self._cache: dict[str, DependencyResolutionResult] = {}
        self._visiting: set[str] = set()
        self._metric_map = {
            str(getattr(metric, "unique_name", "") or "").casefold(): metric
            for metric in self.metrics
            if getattr(metric, "unique_name", None)
        }

    def _metric_names(self, metrics: Optional[Iterable[Any]] = None) -> set[str]:
        pool = list(metrics or self.metrics or [])
        return {
            str(getattr(metric, "unique_name", "") or "").strip().casefold()
            for metric in pool
            if getattr(metric, "unique_name", None)
        }

    def build_dependency_graph(self, metrics: Optional[Iterable[Any]] = None) -> dict[str, list[str]]:
        metrics_list = list(metrics or self.metrics or [])
        metric_names = self._metric_names(metrics_list)
        graph: dict[str, list[str]] = {}
        from semabridge.compiler.parser import DaxParser

        parser = DaxParser()
        for metric in metrics_list:
            name = str(getattr(metric, "unique_name", "") or "").strip()
            if not name:
                continue
            expr = str(getattr(metric, "expression", "") or "")
            node = parser.parse(expr)
            refs: list[str] = []
            self._collect_measure_refs(node, refs)
            unique_refs = sorted(
                {
                    ref.strip()
                    for ref in refs
                    if ref and ref.strip().casefold() in metric_names and ref.strip().casefold() != name.casefold()
                },
                key=str.casefold,
            )
            graph[name] = unique_refs

        logger = None
        try:
            from semabridge.utils.logger import get_logger

            logger = get_logger(__name__)
        except Exception:
            logger = None

        if logger:
            for metric, deps in graph.items():
                if deps:
                    logger.info("[DEPENDENCY_GRAPH] %s -> %s", metric, " -> ".join(deps))
                else:
                    logger.info("[DEPENDENCY_GRAPH] %s -> <none>", metric)
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

    def build_dependency_plan(self, metrics: Optional[Iterable[Any]] = None) -> DependencyPlan:
        metrics_list = list(metrics or self.metrics or [])
        graph = self.build_dependency_graph(metrics_list)
        metric_order = {
            str(getattr(metric, "unique_name", "") or "").strip().casefold(): idx
            for idx, metric in enumerate(metrics_list)
            if getattr(metric, "unique_name", None)
        }

        metric_names = {name.casefold() for name in graph}
        missing_dependencies: dict[str, list[str]] = {}
        from semabridge.compiler.parser import DaxParser

        parser = DaxParser()
        for metric in metrics_list:
            metric_name = str(getattr(metric, "unique_name", "") or "").strip()
            if not metric_name:
                continue
            expr = str(getattr(metric, "expression", "") or "")
            node = parser.parse(expr)
            refs: list[str] = []
            self._collect_measure_refs(node, refs)
            missing = sorted(
                {
                    ref.strip()
                    for ref in refs
                    if ref and ref.strip().casefold() not in metric_names and ref.strip().casefold() != metric_name.casefold()
                },
                key=str.casefold,
            )
            if missing:
                missing_dependencies[metric_name] = missing

        if missing_dependencies:
            changed = True
            while changed:
                changed = False
                for metric_name, deps in graph.items():
                    inherited: set[str] = set()
                    for dep in deps:
                        inherited.update(missing_dependencies.get(dep, []))
                    if not inherited:
                        continue
                    current = set(missing_dependencies.get(metric_name, []))
                    updated = current | inherited
                    if updated != current:
                        missing_dependencies[metric_name] = sorted(updated, key=str.casefold)
                        changed = True

        indegree: dict[str, int] = {name: 0 for name in graph}
        dependents: dict[str, set[str]] = {name: set() for name in graph}
        for metric_name, deps in graph.items():
            for dep in deps:
                if dep not in indegree:
                    continue
                indegree[metric_name] += 1
                dependents.setdefault(dep, set()).add(metric_name)

        def sort_key(name: str) -> tuple[int, str]:
            return (metric_order.get(name.casefold(), 10**9), name.casefold())

        queue = deque(sorted([name for name, degree in indegree.items() if degree == 0], key=sort_key))
        ordered: list[str] = []
        groups: list[list[str]] = []
        remaining_indegree = dict(indegree)

        while queue:
            current_level = sorted(list(queue), key=sort_key)
            queue.clear()
            groups.append(current_level)
            for name in current_level:
                ordered.append(name)
                for dependent in sorted(dependents.get(name, set()), key=sort_key):
                    remaining_indegree[dependent] -= 1
                    if remaining_indegree[dependent] == 0:
                        queue.append(dependent)

        cyclic_nodes = [name for name, degree in remaining_indegree.items() if degree > 0]
        cycles: list[list[str]] = []
        if cyclic_nodes:
            logger = None
            try:
                from semabridge.utils.logger import get_logger

                logger = get_logger(__name__)
            except Exception:
                logger = None

            if logger:
                logger.warning("[CYCLE_DETECTED] Metric cycle candidates: %s", ", ".join(sorted(cyclic_nodes, key=str.casefold)))

            adjacency = {name: [dep for dep in graph.get(name, []) if dep in cyclic_nodes] for name in cyclic_nodes}
            visited: set[str] = set()
            stack: list[str] = []

            def dfs(node: str) -> None:
                if node in stack:
                    cycle_start = stack.index(node)
                    cycle = stack[cycle_start:] + [node]
                    if cycle not in cycles:
                        cycles.append(cycle)
                    return
                if node in visited:
                    return
                visited.add(node)
                stack.append(node)
                for dep in adjacency.get(node, []):
                    dfs(dep)
                stack.pop()

            for node in sorted(cyclic_nodes, key=sort_key):
                dfs(node)

        if missing_dependencies:
            logger = None
            try:
                from semabridge.utils.logger import get_logger

                logger = get_logger(__name__)
            except Exception:
                logger = None
            if logger:
                for metric_name, deps in missing_dependencies.items():
                    logger.warning("[MISSING_DEPENDENCY] %s -> %s", metric_name, ", ".join(deps))

        return DependencyPlan(
            graph=graph,
            ordered=ordered,
            groups=groups,
            cycles=cycles,
            missing_dependencies=missing_dependencies,
        )

    def expand_metric(self, metric_name: str) -> CompilerResult:
        key = str(metric_name or "").strip().casefold()
        if not key:
            from semabridge.compiler.compiler import CompilerResult

            return CompilerResult(sql=None, validation=self.compiler.validator.validate(""), diagnostics=["missing metric name"], is_success=False)
        if key in self._cache:
            return self._cache[key].result
        if key in self._visiting:
            from semabridge.compiler.compiler import CompilerResult

            return CompilerResult(sql=None, validation=self.compiler.validator.validate(""), diagnostics=[f"circular dependency detected for {metric_name}"], is_success=False)

        metric = self._metric_map.get(key)
        if metric is None:
            from semabridge.compiler.compiler import CompilerResult

            return CompilerResult(sql=None, validation=self.compiler.validator.validate(""), diagnostics=[f"unknown metric: {metric_name}"], is_success=False)

        classification = self.classifier.classify(metric)
        if not classification.deploy:
            from semabridge.compiler.compiler import CompilerResult

            result = CompilerResult(sql=None, validation=self.compiler.validator.validate(""), diagnostics=classification.reasons or [classification.category], is_success=False)
            self._cache[key] = DependencyResolutionResult(metric_name=metric_name, result=result)
            return result

        self._visiting.add(key)
        try:
            try:
                result = self.compiler.compile_expression(
                    str(getattr(metric, "expression", "") or ""),
                    model=self.model,
                    metrics=self.metrics,
                    metric_name=getattr(metric, "unique_name", None),
                    table_alias=str(getattr(metric, "dataset", "") or "FACT"),
                    dataset_name=str(getattr(metric, "dataset", "") or "Fact"),
                )
            except Exception as exc:
                from semabridge.compiler.compiler import CompilerResult

                result = CompilerResult(
                    sql=None,
                    validation=self.compiler.validator.validate(""),
                    diagnostics=[str(exc)],
                    is_success=False,
                )
            self._cache[key] = DependencyResolutionResult(metric_name=metric_name, result=result)
            return result
        finally:
            self._visiting.discard(key)
