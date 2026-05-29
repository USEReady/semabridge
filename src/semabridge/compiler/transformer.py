from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Optional

from semabridge.compiler.ast import (
    AggregateNode,
    BinaryOpNode,
    CalculateNode,
    CaseNode,
    ColumnReferenceNode,
    DivideNode,
    DaxNode,
    FunctionCallNode,
    IfNode,
    IdentifierNode,
    LiteralNode,
    MeasureReferenceNode,
    SqlAggregateNode,
    SqlBinaryOpNode,
    SqlColumnReferenceNode,
    SqlFunctionNode,
    SqlIdentifierNode,
    SqlLiteralNode,
    SqlNode,
    SqlRawNode,
    SqlUnaryOpNode,
    TimeIntelligenceNode,
    WindowFunctionNode,
)
from semabridge.compiler.registry import FunctionRegistry, default_registry
from semabridge.compiler.schema_resolver import CalendarBinding, SemanticSchemaResolver


@dataclass
class TransformContext:
    metric_name: Optional[str] = None
    table_alias: Optional[str] = None
    dataset_name: Optional[str] = None
    metric_pool: list[Any] = field(default_factory=list)
    visiting: set[str] = field(default_factory=set)
    measure_sql_cache: dict[str, SqlNode] = field(default_factory=dict)


class DaxToSqlTransformer:
    def __init__(self, resolver: SemanticSchemaResolver, registry: FunctionRegistry | None = None) -> None:
        self.resolver = resolver
        self.registry = registry or default_registry

    def transform(self, node: DaxNode | None, context: Optional[TransformContext] = None) -> SqlNode | None:
        if node is None:
            return None
        ctx = context or TransformContext()
        if isinstance(node, LiteralNode):
            return SqlLiteralNode(value=node.value, metadata=dict(node.metadata))
        if isinstance(node, IdentifierNode):
            return SqlIdentifierNode(name=node.name, metadata=dict(node.metadata))
        if isinstance(node, ColumnReferenceNode):
            return SqlColumnReferenceNode(table=self._resolve_table_alias(node.table, ctx), column=node.column, metadata=dict(node.metadata))
        if isinstance(node, MeasureReferenceNode):
            return self._transform_measure_reference(node, ctx)
        if isinstance(node, BinaryOpNode):
            return SqlBinaryOpNode(op=node.op, left=self.transform(node.left, ctx), right=self.transform(node.right, ctx), metadata=dict(node.metadata))
        if isinstance(node, CalculateNode):
            return self._transform_calculate(node, ctx)
        if isinstance(node, DivideNode):
            return self._transform_divide(node, ctx)
        if isinstance(node, IfNode):
            return self._transform_if(node, ctx)
        if isinstance(node, TimeIntelligenceNode):
            return self._transform_time_intel(node, ctx)
        if isinstance(node, AggregateNode):
            return self._transform_aggregate(node, ctx)
        if isinstance(node, FunctionCallNode):
            custom = self.registry.get_transformer(node.func)
            if custom:
                transformed = custom(node=node, context=ctx, transformer=self)
                if transformed is not None:
                    return transformed
            upper = node.func.upper()
            if upper == "IF":
                return self._transform_if(IfNode(func=upper, args=node.args), ctx)
            if upper == "DIVIDE":
                return self._transform_divide(DivideNode(func=upper, args=node.args), ctx)
            if upper == "CALCULATE":
                return self._transform_calculate(CalculateNode(func=upper, args=node.args), ctx)
            if upper in {"TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD", "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"}:
                return self._transform_time_intel(TimeIntelligenceNode(func=upper, args=node.args), ctx)
            if upper == "ISBLANK":
                if not node.args:
                    raise ValueError("ISBLANK requires an argument")
                value = self.transform(node.args[0], ctx)
                return SqlBinaryOpNode(op="IS", left=value, right=SqlLiteralNode(value=None))
            if upper == "CONCATENATE":
                if len(node.args) < 2:
                    raise ValueError("CONCATENATE requires two arguments")
                return SqlFunctionNode(name="CONCAT", args=[self.transform(node.args[0], ctx), self.transform(node.args[1], ctx)])
            if upper == "COUNTBLANK":
                if not node.args:
                    raise ValueError("COUNTBLANK requires an argument")
                value = self.transform(node.args[0], ctx)
                predicate = SqlBinaryOpNode(op="IS", left=value, right=SqlLiteralNode(value=None))
                return SqlFunctionNode(name="COUNT_IF", args=[predicate])
            if upper == "COUNTA":
                if not node.args:
                    raise ValueError("COUNTA requires an argument")
                return SqlAggregateNode(function="COUNT", expression=self.transform(node.args[0], ctx))
            if upper == "FILTER":
                if len(node.args) < 2:
                    raise ValueError("FILTER requires a table and a predicate")
                return self.transform(node.args[1], ctx)
            if upper == "ALL":
                return SqlLiteralNode(value=True)
            if upper in {"RELATED", "CALCULATETABLE", "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "EARLIER", "SWITCH", "VAR", "RETURN"}:
                raise ValueError(f"Unsupported DAX function requires planning or rewrite: {node.func}")
            return self._transform_generic_function(node, ctx)
        if isinstance(node, UnaryOpNode):
            return SqlUnaryOpNode(op=node.op, operand=self.transform(node.operand, ctx), metadata=dict(node.metadata))
        raise TypeError(f"Unsupported DAX node: {type(node).__name__}")

    def _resolve_table_alias(self, table: str, ctx: TransformContext) -> str:
        dataset = self.resolver.resolve_table(table) if table else None
        return str(getattr(dataset, "unique_name", table) or table or ctx.table_alias or "FACT")

    def _transform_measure_reference(self, node: MeasureReferenceNode, ctx: TransformContext) -> SqlNode:
        metric = self.resolver.resolve_measure(node.name)
        if metric is None:
            raise ValueError(f"Unresolved metric reference: {node.name}")
        key = str(getattr(metric, "unique_name", node.name)).casefold()
        if key in ctx.visiting:
            raise ValueError(f"Circular measure dependency detected for {node.name}")
        if key in ctx.measure_sql_cache:
            return ctx.measure_sql_cache[key]

        expression = str(getattr(metric, "expression", "") or "").strip()
        if not expression:
            sql_expression = str(getattr(metric, "sql_expression", "") or "").strip()
            if not sql_expression:
                raise ValueError(f"Metric has no expression: {node.name}")

            aggregate_match = re.match(r"^\s*(SUM|AVG|AVERAGE|MIN|MAX|COUNT)\s*\((.+)\)\s*$", sql_expression, re.IGNORECASE | re.DOTALL)
            distinct_match = re.match(r"^\s*COUNT\s*\(\s*DISTINCT\s+(.+)\)\s*$", sql_expression, re.IGNORECASE | re.DOTALL)
            if distinct_match:
                resolved = SqlAggregateNode(function="COUNT_DISTINCT", expression=SqlRawNode(sql=distinct_match.group(1).strip()))
            elif aggregate_match:
                function_name = aggregate_match.group(1).upper()
                if function_name == "AVERAGE":
                    function_name = "AVG"
                resolved = SqlAggregateNode(function=function_name, expression=SqlRawNode(sql=aggregate_match.group(2).strip()))
            else:
                raise ValueError(f"Metric '{node.name}' lacks DAX expression and cannot be safely recompiled")

            ctx.measure_sql_cache[key] = resolved
            return resolved

        from semabridge.compiler.parser import DaxParser
        transformed = self.expand_metric_dependencies_recursive(metric, ctx, parser=DaxParser())
        if transformed is None:
            raise ValueError(f"Unable to expand measure: {node.name}")
        ctx.measure_sql_cache[key] = transformed
        return transformed

    def expand_metric_dependencies_recursive(self, metric: Any, ctx: TransformContext, parser: Any | None = None) -> SqlNode | None:
        name = str(getattr(metric, "unique_name", "") or "").strip()
        if not name:
            return None
        key = name.casefold()
        if key in ctx.measure_sql_cache:
            return ctx.measure_sql_cache[key]

        expression = str(getattr(metric, "expression", "") or "").strip()
        sql_expression = str(getattr(metric, "sql_expression", "") or "").strip()
        if not expression:
            if sql_expression:
                aggregate_match = re.match(r"^\s*(SUM|AVG|AVERAGE|MIN|MAX|COUNT)\s*\((.+)\)\s*$", sql_expression, re.IGNORECASE | re.DOTALL)
                distinct_match = re.match(r"^\s*COUNT\s*\(\s*DISTINCT\s+(.+)\)\s*$", sql_expression, re.IGNORECASE | re.DOTALL)
                if distinct_match:
                    resolved = SqlAggregateNode(function="COUNT_DISTINCT", expression=SqlRawNode(sql=distinct_match.group(1).strip()))
                elif aggregate_match:
                    function_name = aggregate_match.group(1).upper()
                    if function_name == "AVERAGE":
                        function_name = "AVG"
                    resolved = SqlAggregateNode(function=function_name, expression=SqlRawNode(sql=aggregate_match.group(2).strip()))
                else:
                    return None
                ctx.measure_sql_cache[key] = resolved
                return resolved
            return None

        from semabridge.compiler.parser import DaxParser
        dax_parser = parser or DaxParser()
        dax_node = dax_parser.parse(expression)
        if dax_node is None:
            return None
        if key in ctx.visiting:
            raise ValueError(f"Circular measure dependency detected for {name}")
        ctx.visiting.add(key)
        try:
            transformed = self.transform(dax_node, ctx)
            if transformed is not None:
                ctx.measure_sql_cache[key] = transformed
            return transformed
        finally:
            ctx.visiting.discard(key)

    def validate_ast(self, node: SqlNode | None) -> None:
        if node is None:
            raise ValueError("SQL AST is empty")
        if isinstance(node, WindowFunctionNode):
            if node.expression is None:
                raise ValueError("window function expression is required")
            if not node.order_by:
                raise ValueError("window function order by is required")
        if isinstance(node, SqlAggregateNode) and node.filter_condition is not None:
            self.validate_ast(node.filter_condition)
        if isinstance(node, CaseNode):
            for cond, expr in node.whens:
                self.validate_ast(cond)
                self.validate_ast(expr)
            if node.else_expr is not None:
                self.validate_ast(node.else_expr)

    def _transform_aggregate(self, node: AggregateNode, ctx: TransformContext) -> SqlNode:
        func = node.func.upper()
        if not node.args:
            raise ValueError(f"{func} requires an argument")
        expr = self.transform(node.args[0], ctx)
        if func == "DISTINCTCOUNT":
            return SqlAggregateNode(function="COUNT_DISTINCT", expression=expr)
        if func == "COUNTA":
            return SqlAggregateNode(function="COUNT", expression=expr)
        if func == "COUNTROWS":
            return SqlFunctionNode(name="COUNT", args=[SqlRawNode(sql="*")])
        return SqlAggregateNode(function=func if func != "AVG" else "AVG", expression=expr)

    def _transform_if(self, node: IfNode, ctx: TransformContext) -> SqlNode:
        if len(node.args) < 2:
            raise ValueError("IF requires at least two arguments")
        cond = self.transform(node.args[0], ctx)
        true_expr = self.transform(node.args[1], ctx)
        false_expr = self.transform(node.args[2], ctx) if len(node.args) > 2 else SqlLiteralNode(value=None)
        return CaseNode(whens=[(cond, true_expr)], else_expr=false_expr)

    def _transform_divide(self, node: DivideNode, ctx: TransformContext) -> SqlNode:
        if len(node.args) < 2:
            raise ValueError("DIVIDE requires two arguments")
        num = self.transform(node.args[0], ctx)
        den = self.transform(node.args[1], ctx)
        alt = self.transform(node.args[2], ctx) if len(node.args) > 2 else SqlLiteralNode(value=0)
        cond = SqlBinaryOpNode(op="=", left=den, right=SqlLiteralNode(value=0))
        null_cond = SqlBinaryOpNode(op="IS", left=den, right=SqlLiteralNode(value=None))
        combined = SqlBinaryOpNode(op="OR", left=cond, right=null_cond)
        division = SqlBinaryOpNode(op="/", left=num, right=den)
        return CaseNode(whens=[(combined, alt)], else_expr=division)

    def _transform_calculate(self, node: CalculateNode, ctx: TransformContext) -> SqlNode:
        if not node.args:
            raise ValueError("CALCULATE requires at least one argument")
        base = self.transform(node.args[0], ctx)
        predicates = [self.transform(arg, ctx) for arg in node.args[1:]]
        predicates = [pred for pred in predicates if pred is not None]
        if not predicates:
            return base
        filter_expr = predicates[0]
        for extra in predicates[1:]:
            filter_expr = SqlBinaryOpNode(op="AND", left=filter_expr, right=extra)
        if isinstance(base, SqlAggregateNode):
            return SqlAggregateNode(
                function=base.function,
                expression=base.expression,
                distinct=base.distinct,
                filter_condition=filter_expr,
                metadata=dict(base.metadata),
            )
        return CaseNode(whens=[(filter_expr, base)], else_expr=SqlLiteralNode(value=None))

    def _transform_time_intel(self, node: TimeIntelligenceNode, ctx: TransformContext) -> SqlNode:
        calendar = self.resolver.resolve_calendar(ctx.metric_name) or self.resolver.resolve_calendar()
        if calendar is None:
            raise ValueError(f"No calendar binding found for {node.func}")
        base = self.transform(node.args[0], ctx) if node.args else None
        if base is None:
            raise ValueError(f"{node.func} requires a base expression")
        order_col = SqlColumnReferenceNode(table=calendar.table, column=calendar.date_column)
        partition_cols = [SqlColumnReferenceNode(table=calendar.table, column=calendar.year_column)]
        if node.func.upper() == "TOTALMTD":
            partition_cols.append(SqlColumnReferenceNode(table=calendar.table, column=calendar.period_column))
        elif node.func.upper() == "TOTALQTD" and calendar.quarter_column:
            partition_cols.append(SqlColumnReferenceNode(table=calendar.table, column=calendar.quarter_column))
        elif node.func.upper() in {"SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD", "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"}:
            return WindowFunctionNode(
                function="LAG",
                expression=base,
                partition_by=partition_cols,
                order_by=[order_col],
                frame_clause=None,
                offset=1,
            )
        return WindowFunctionNode(
            function=self._window_function_name(base),
            expression=self._window_expression(base),
            partition_by=partition_cols,
            order_by=[order_col],
            frame_clause="ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
        )

    def _window_function_name(self, base: SqlNode) -> str:
        if isinstance(base, SqlAggregateNode):
            return base.function
        return "SUM"

    def _window_expression(self, base: SqlNode) -> SqlNode:
        if isinstance(base, SqlAggregateNode):
            return base.expression or SqlLiteralNode(value=1)
        return base

    def _transform_generic_function(self, node: FunctionCallNode, ctx: TransformContext) -> SqlNode:
        args = [self.transform(arg, ctx) for arg in node.args]
        args = [arg for arg in args if arg is not None]
        return SqlFunctionNode(name=node.func, args=args)

