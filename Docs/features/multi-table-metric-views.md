# Multi-Table Metric Views

Semabridge can generate Databricks metric views that span multiple tables when
relationships are defined in the semantic model.

## Required Flags
Set in Config/behavior.yaml:

```
databricks:
  enable_cross_table_joins: true
  enable_metric_view_joins: true
  measure_view_type: "metric_view"
```

## How It Works
- Relationship detection builds a join tree.
- Joins are emitted in the Databricks metric view YAML.
- Nested joins are supported for snowflake schemas.

## Implementation References
- Join detection: src/semabridge/connectors/relationship_detector.py
- Join assembly: src/semabridge/connectors/relationships_clause_builder.py
- YAML generation: src/semabridge/connectors/ddl_builder.py
