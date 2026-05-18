# Semabridge: AST Handlers for Time Intelligence Documentation

## 1. Overview

Power BI's Time Intelligence functions dynamically alter the evaluation context of a metric over a Date table. In a standard SQL data warehouse (Snowflake/Databricks), this concept does not exist intrinsically. To achieve deterministic translation without LLMs, we must expand the Abstract Syntax Tree (AST) to recognize Time Intelligence functions and render them directly into analytic SQL window functions or explicit conditional aggregates.

## 2. Architecture & Design

### Phase 1: Extending the AST Nodes

In `dax_ast_parser.py`, we need specific AST nodes to capture the semantic arguments of time intelligence functions. Currently, standard functions map to a generic `DaxFunctionCallNode`. We will specialize this.

```python
# src/semabridge/converter/dax_ast_parser.py
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional

class AstNodeType(Enum):
    # ... existing types ...
    TIME_INTELLIGENCE_TOTALYTD = auto()
    TIME_INTELLIGENCE_SPLY = auto() # SAMEPERIODLASTYEAR
    TIME_INTELLIGENCE_DATEADD = auto()

@dataclass
class TimeIntelligenceNode(AstNode):
    operation_type: AstNodeType
    expression: AstNode       # The measure being evaluated (e.g. SUM(Amount))
    date_column: AstNode      # The Date dimension column (e.g. 'Date'[Date])
    offset_value: Optional[AstNode] = None    # Used for DATEADD (e.g., -1)
    offset_interval: Optional[AstNode] = None # Used for DATEADD (e.g., YEAR)
```

### Phase 2: Updating the Recursive Descent Parser

In the `parse_function_call` method of the `DaxAstParser`, we add branching to identify Time Intelligence functions and build the specialized nodes.

```python
def _parse_time_intelligence(self, func_name: str, args: List[AstNode]) -> AstNode:
    """Matches the parsed arguments to specific Time Intelligence constraints."""
    func_upper = func_name.upper()

    if func_upper == "TOTALYTD":
        # Signature: TOTALYTD(<expression>, <dates>[, <filter>][, <year_end_date>])
        return TimeIntelligenceNode(
            operation_type=AstNodeType.TIME_INTELLIGENCE_TOTALYTD,
            expression=args[0],
            date_column=args[1]
        )

    elif func_upper == "SAMEPERIODLASTYEAR":
        # Signature: SAMEPERIODLASTYEAR(<dates>)
        # Note: Often found wrapped in a CALCULATE: CALCULATE([Measure], SAMEPERIODLASTYEAR('Date'[Date]))
        return TimeIntelligenceNode(
            operation_type=AstNodeType.TIME_INTELLIGENCE_SPLY,
            expression=AstNode(), # Contextual expression injected by CALCULATE handling
            date_column=args[0]
        )

    elif func_upper == "DATEADD":
        # Signature: DATEADD(<dates>, <number_of_intervals>, <interval>)
        return TimeIntelligenceNode(
            operation_type=AstNodeType.TIME_INTELLIGENCE_DATEADD,
            expression=None, # Injected by context
            date_column=args[0],
            offset_value=args[1],
            offset_interval=args[2]
        )
```

### Phase 3: The SQL Renderer (`DaxSqlRenderer`)

The `DaxSqlRenderer` uses the visitor pattern to traverse the AST. When it hits a `TimeIntelligenceNode`, it renders the logic utilizing standard SQL Window Functions (`OVER (PARTITION BY ...)`) or Conditional Aggregation (`SUM(CASE WHEN ... END)`), depending on whether the target is Snowflake or Databricks.

#### Implementation: `TOTALYTD`

DAX `TOTALYTD` aggregates the expression from the start of the current year up to the current date context.

```python
def _render_totalytd(self, node: TimeIntelligenceNode) -> str:
    expr_sql = self.render(node.expression)
    date_ref = self.render(node.date_column)

    # Conditional Aggregation Approach (Simplest for semantic views)
    # Assumes target view joins to the active Date context.
    return (
        f"SUM(CASE WHEN "
        f"EXTRACT(YEAR FROM {date_ref}) = EXTRACT(YEAR FROM CURRENT_DATE()) "
        f"AND {date_ref} <= CURRENT_DATE() "
        f"THEN ({expr_sql}) ELSE 0 END)"
    )
```

#### Implementation: `SAMEPERIODLASTYEAR` (SPLY)

DAX `SAMEPERIODLASTYEAR` steps the date filter context exactly one year back. Since semantic views often operate row-by-row on joined fact tables, the best native SQL approach is often an Analytic Window function.

```python
def _render_sply(self, node: TimeIntelligenceNode, context_expr_sql: str) -> str:
    date_ref = self.render(node.date_column)

    # Using SQL standard Window Function to look back strictly 1 year.
    # Supported by both Snowflake and Databricks.
    return (
        f"SUM({context_expr_sql}) OVER ("
        f"  ORDER BY {date_ref} "
        f"  RANGE BETWEEN INTERVAL '1 YEAR' PRECEDING AND INTERVAL '1 YEAR' PRECEDING"
        f")"
    )
```

## 3. Handling Contextual Overrides (`CALCULATE`)

In DAX, `SAMEPERIODLASTYEAR` does not return a scalar; it returns a _table of dates_ which alters the filter context. Users typically write:
`CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))`

To handle this cleanly in the AST, the `CALCULATE` rendering logic must peek at its filter arguments looking for context-altering Time Intelligence modifiers:

```python
def _render_calculate(self, node: FunctionCallNode) -> str:
    base_expr = node.arguments[0]
    base_expr_sql = self.render(base_expr)

    # Inspect filters for Time Intelligence table functions
    for filter_node in node.arguments[1:]:
        if isinstance(filter_node, TimeIntelligenceNode):
            if filter_node.operation_type == AstNodeType.TIME_INTELLIGENCE_SPLY:
               # Wrap the base expression using the SPLY rendering logic
               return self._render_sply(filter_node, context_expr_sql=base_expr_sql)

        elif isinstance(filter_node, FilterNode):
            # Standard conditional aggregation
            base_expr_sql = f"CASE WHEN {self.render(filter_node)} THEN ({base_expr_sql}) ELSE NULL END"

    return f"SUM({base_expr_sql})"
```

## 4. Required Schema Metadata

In order for this translation to work correctly, the `DaxAstParser` requires access to the schema context so it can identify which table aliases belong to the Date / Calendar dimension.

Ensure the translator instance has a mapping dictionary:

```python
self.calendar_dimension = "DIM_DATE" # The unified Date table name
self.date_column = "DATE_VAL"        # The key column
```

This guarantees that when rendering `{date_ref}`, the `DaxSqlRenderer` can safely output `dim_date."DATE_VAL"`.
