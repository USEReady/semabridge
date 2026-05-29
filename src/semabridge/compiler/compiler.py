from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from semabridge.compiler.ast import SqlNode, SqlRawNode
from semabridge.compiler.generator import SqlGenerator
from semabridge.compiler.parser import DaxParser
from semabridge.compiler.dependency_resolver import DependencyPlan, MetricDependencyResolver
from semabridge.compiler.metric_classifier import MetricClassifier
from semabridge.compiler.relational_planner import RelationalPlanner
from semabridge.compiler.schema_resolver import SemanticSchemaResolver
from semabridge.compiler.sql_validator import SQLValidator, ValidationResult, validate_ast, validate_sql_expression
from semabridge.compiler.transformer import DaxToSqlTransformer, TransformContext
from semabridge.compiler.registry import FunctionRegistry, default_registry


@dataclass
class CompilerResult:
    sql: Optional[str]
    dax_ast: Any = None
    sql_ast: Optional[SqlNode] = None
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(True))
    diagnostics: list[str] = field(default_factory=list)
    is_success: bool = False


class DAXCompiler:
    def __init__(self, registry: FunctionRegistry | None = None) -> None:
        self.registry = registry or default_registry
        self.parser = DaxParser()
        self.validator = SQLValidator()
        self.classifier = MetricClassifier()
        self.planner = RelationalPlanner()
        self.last_dependency_plan: DependencyPlan | None = None

    def plan_metric_dependencies(
        self,
        metrics: Iterable[Any],
        *,
        model: Any = None,
    ) -> DependencyPlan:
        metrics_list = list(metrics or [])
        resolver = MetricDependencyResolver(model=model, metrics=metrics_list, compiler=self)
        plan = resolver.build_dependency_plan(metrics_list)
        self.last_dependency_plan = plan
        return plan

    def compile_expression(
        self,
        dax: str,
        *,
        model: Any = None,
        metrics: Optional[Iterable[Any]] = None,
        metric_name: Optional[str] = None,
        table_alias: Optional[str] = None,
        dataset_name: Optional[str] = None,
    ) -> CompilerResult:
        resolver = SemanticSchemaResolver(model=model, metrics=metrics)
        dax_ast = self.parser.parse(dax)
        if dax_ast is None:
            return CompilerResult(sql=None, dax_ast=None, sql_ast=None, validation=ValidationResult(False, ["parse failed"]), diagnostics=["parse failed"], is_success=False)

        transformer = DaxToSqlTransformer(resolver, self.registry)
        context = TransformContext(
            metric_name=metric_name,
            table_alias=table_alias,
            dataset_name=dataset_name,
            metric_pool=list(metrics or []),
        )
        sql_ast = transformer.transform(dax_ast, context)
        if sql_ast is None:
            return CompilerResult(sql=None, dax_ast=dax_ast, sql_ast=None, validation=ValidationResult(False, ["transformation failed"]), diagnostics=["transformation failed"], is_success=False)

        try:
            planned_sql_ast = self.planner.plan(sql_ast)
        except Exception as exc:
            validation = ValidationResult(False, [str(exc)])
            return CompilerResult(sql=None, dax_ast=dax_ast, sql_ast=sql_ast, validation=validation, diagnostics=[str(exc)], is_success=False)

        try:
            transformer.validate_ast(planned_sql_ast)
        except Exception as exc:
            validation = ValidationResult(False, [str(exc)])
            return CompilerResult(sql=None, dax_ast=dax_ast, sql_ast=planned_sql_ast, validation=validation, diagnostics=[str(exc)], is_success=False)

        ast_validation = validate_ast(planned_sql_ast)
        if not ast_validation.is_valid:
            return CompilerResult(
                sql=None,
                dax_ast=dax_ast,
                sql_ast=planned_sql_ast,
                validation=ast_validation,
                diagnostics=ast_validation.errors + ast_validation.warnings,
                is_success=False,
            )

        generator = SqlGenerator()
        sql = generator.generate(planned_sql_ast)
        validation = validate_sql_expression(sql)
        return CompilerResult(
            sql=sql if validation.is_valid else None,
            dax_ast=dax_ast,
            sql_ast=planned_sql_ast,
            validation=validation,
            diagnostics=validation.errors + validation.warnings,
            is_success=validation.is_valid,
        )

    def compile_metrics(
        self,
        metrics: Iterable[Any],
        *,
        model: Any = None,
        table_alias: Optional[str] = None,
        dataset_name: Optional[str] = None,
    ) -> dict[str, CompilerResult]:
        metrics_list = list(metrics or [])
        resolver = SemanticSchemaResolver(model=model, metrics=metrics_list)
        dependency_resolver = MetricDependencyResolver(model=model, metrics=metrics_list, compiler=self)
        plan = self.plan_metric_dependencies(metrics_list, model=model)
        graph = plan.graph
        ordered = plan.ordered
        metric_by_name = {str(getattr(metric, "unique_name", "") or "").casefold(): metric for metric in metrics_list if getattr(metric, "unique_name", None)}
        results: dict[str, CompilerResult] = {}
        compiled_sql: dict[str, str] = {}
        ordered_set = {name.casefold() for name in ordered}

        def _failure_result(message: str) -> CompilerResult:
            validation = ValidationResult(False, [message])
            return CompilerResult(sql=None, validation=validation, diagnostics=[message], is_success=False)

        for metric_name, deps in plan.missing_dependencies.items():
            if metric_name in results:
                continue
            results[metric_name] = _failure_result(f"missing dependency: {', '.join(deps)}")

        for cycle in plan.cycles:
            for metric_name in cycle[:-1]:
                if metric_name in results:
                    continue
                results[metric_name] = _failure_result(f"circular dependency detected: {' -> '.join(cycle)}")

        for name in ordered:
            metric = metric_by_name.get(name.casefold())
            if not metric:
                continue
            classification = self.classifier.classify(metric)
            if not classification.deploy:
                result = CompilerResult(sql=None, validation=ValidationResult(False, classification.reasons or [classification.category]), diagnostics=classification.reasons or [classification.category], is_success=False)
            else:
                result = dependency_resolver.expand_metric(name)
            if result.is_success and result.sql:
                compiled_sql[str(getattr(metric, "unique_name", "") or "")] = result.sql
                if isinstance(metric, dict):
                    metric["sql_expression"] = result.sql
                else:
                    try:
                        metric.sql_expression = result.sql
                    except Exception:
                        pass
            results[str(getattr(metric, "unique_name", "") or "")] = result

        for metric in metrics_list:
            metric_name = str(getattr(metric, "unique_name", "") or "")
            if metric_name.casefold() not in ordered_set and metric_name not in results:
                if metric_name.casefold() in plan.missing_dependencies or any(metric_name.casefold() in {node.casefold() for node in cycle} for cycle in plan.cycles):
                    continue
                result = dependency_resolver.expand_metric(metric_name)
                if result.is_success and result.sql:
                    compiled_sql[metric_name] = result.sql
                    if isinstance(metric, dict):
                        metric["sql_expression"] = result.sql
                    else:
                        try:
                            metric.sql_expression = result.sql
                        except Exception:
                            pass
                results[metric_name] = result
        return results

    def _topological_order(self, graph: dict[str, list[str]]) -> list[str]:
        visited: dict[str, int] = {}
        order: list[str] = []

        def dfs(node: str) -> None:
            state = visited.get(node.casefold(), 0)
            if state == 1:
                return
            if state == 2:
                return
            visited[node.casefold()] = 1
            for dep in graph.get(node, []):
                dfs(dep)
            visited[node.casefold()] = 2
            order.append(node)

        for node in graph:
            if visited.get(node.casefold(), 0) == 0:
                dfs(node)
        return order
