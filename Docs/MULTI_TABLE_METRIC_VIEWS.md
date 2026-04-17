# Multi-Table Metric View YAML Support

## Overview

Semabridge now supports **native Databricks metric view `joins` property** for multi-table snowflake schemas. This enables single metric views to span multiple related tables without requiring explicit SQL joins in measure expressions.

## Feature Highlights

✅ **Direct Joins**: Connect fact tables to dimension tables  
✅ **Nested Joins**: Support snowflake schema patterns (fact → dimension → sub-dimension)  
✅ **Automatic Join Detection**: Leverages existing semantic model relationships  
✅ **Backward Compatible**: Disabled by default, opt-in via configuration  
✅ **Full YAML Spec Support**: Follows Azure Databricks metric view v1.1 specification  

## Requirements

To use multi-table metric view joins:

1. **Enable Two Flags** in `Config/behavior.yaml`:
   ```yaml
   databricks:
     enable_cross_table_joins: true        # Required: enables cross-table join support
     enable_metric_view_joins: true        # New: enables metric view 'joins' property
     measure_view_type: "metric_view"      # Required: use native metric views
   ```

2. **Define Relationships** in your semantic model:
   - Relationships define the join paths between datasets
   - Semabridge automatically builds join hierarchies from relationships
   - Cardinality is respected (many-to-one patterns preferred)

## Example: TPC-H Orders → Customer → Nation

This example (from Azure Databricks docs) demonstrates a snowflake schema:

### Semantic Model Relationships

```python
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLMetric, SMLRelationship, Cardinality
)

# Create model with three tables and two relationships
model = SMLModel(
    unique_name="tpch_model",
    datasets=[
        SMLDataset(
            unique_name="orders",
            source_table="orders",
            columns=[
                SMLColumn(unique_name="o_orderkey"),
                SMLColumn(unique_name="o_custkey"),
                SMLColumn(unique_name="o_totalprice"),
            ]
        ),
        SMLDataset(
            unique_name="customer",
            source_table="customer",
            columns=[
                SMLColumn(unique_name="c_custkey"),
                SMLColumn(unique_name="c_nationkey"),
                SMLColumn(unique_name="c_name"),
            ]
        ),
        SMLDataset(
            unique_name="nation",
            source_table="nation",
            columns=[
                SMLColumn(unique_name="n_nationkey"),
                SMLColumn(unique_name="n_name"),
            ]
        ),
    ],
    metrics=[
        SMLMetric(
            unique_name="order_count",
            dataset="orders",
            expression="COUNT(DISTINCT o_orderkey)",
        ),
        SMLMetric(
            unique_name="total_revenue",
            dataset="orders",
            expression="SUM(o_totalprice)",
            source_column="o_totalprice",
        ),
    ],
    relationships=[
        # Direct join: orders → customer
        SMLRelationship(
            unique_name="orders_to_customer",
            from_dataset="orders",
            from_columns=["o_custkey"],
            to_dataset="customer",
            to_columns=["c_custkey"],
            cardinality=Cardinality.MANY_TO_ONE,
        ),
        # Nested join: customer → nation
        SMLRelationship(
            unique_name="customer_to_nation",
            from_dataset="customer",
            from_columns=["c_nationkey"],
            to_dataset="nation",
            to_columns=["n_nationkey"],
            cardinality=Cardinality.MANY_TO_ONE,
        ),
    ]
)
```

### Generated Metric View YAML

When `enable_metric_view_joins=true`, semabridge generates:

```yaml
version: 1.1
comment: "Semabridge: tpch_model - orders"
source: SELECT * FROM samples.tpch.orders

joins:
  - name: customer
    source: samples.tpch.customer
    'on': o_custkey = c_custkey
    joins:
      - name: nation
        source: samples.tpch.nation
        'on': c_nationkey = n_nationkey

dimensions:
  - name: o_orderkey
    expr: `o_orderkey`
  - name: c_name
    expr: `c_name`
  - name: n_name
    expr: `n_name`

measures:
  - name: order_count
    expr: count(distinct o_orderkey)
  - name: total_revenue
    expr: sum(o_totalprice)
```

### Configuration Example

Create or update `Config/behavior.yaml`:

```yaml
databricks:
  # Deploy to Databricks
  create_metadata_table: true
  create_measure_views: true
  measure_view_type: "metric_view"      # Use native metric views
  measure_view_mode: "combined"          # One view per dataset
  
  # Enable multi-table joins
  enable_cross_table_joins: true         # Prerequisite
  enable_metric_view_joins: true         # NEW: Enable joins in metric view YAML
  
  # Optional: explicit table mappings
  source_table_mapping:
    orders: "samples.tpch.orders"
    customer: "samples.tpch.customer"
    nation: "samples.tpch.nation"
```

## How It Works

### 1. Relationship Detection
Semabridge scans your semantic model relationships to find outbound connections from each dataset.

### 2. Join Tree Building
For each dataset, joins are organized hierarchically:
- **Primary dataset**: Orders (the main fact table)
- **Direct joins**: Customer (joined directly to orders)
- **Nested joins**: Nation (joined through customer)

### 3. YAML Generation
The join tree is converted to native Databricks metric view YAML:
- Join names are derived from dataset names (customer, nation)
- Join conditions are built from relationship column mappings
- Nested joins are indented appropriately for YAML structure

### 4. Snowflake Schema Support
Arbitrary nesting is supported, enabling complex star/snowflake schemas:

```
Orders (fact)
  ├── Customer (dim)
  │   ├── Nation (subdim)
  │   └── Postal Code (subdim)
  ├── Product (dim)
  │   └── Category (subdim)
  └── Date (dim)
```

## Key Behaviors

### Circular Relationship Handling
If your model has circular relationships (A → B → A), semabridge detects and prevents infinite loops:
- Once a dataset is visited, it won't be joined again
- The visited set prevents circular traversal
- Log warnings indicate which circular paths were skipped

### Maximum Nesting Depth
To prevent overly complex join hierarchies:
- Default maximum depth: **5 levels**
- Configurable via `JoinTreeBuilder(model).build_join_tree(dataset, max_depth=N)`
- Exceeding max_depth logs a debug message and stops recursion

### Backward Compatibility
The feature is **disabled by default**:
- Old `Config/behavior.yaml` files work unchanged
- `enable_metric_view_joins: false` in all configurations
- Zero impact on existing deployments
- Opt-in per project with explicit configuration

## Testing

Comprehensive test coverage is provided:

```bash
# Run join functionality tests
pytest Tests/test_metric_view_joins.py -v

# Run YAML generation tests
pytest Tests/test_metric_view_yaml_joins.py -v

# Run all tests
pytest Tests/ -k "join" -v
```

Test scenarios include:
- Single and nested joins
- Circular relationship detection
- Join condition generation
- YAML structure validation
- Feature flag behavior

## Troubleshooting

### Joins not appearing in YAML
**Cause**: Feature flag disabled.  
**Solution**: Set `enable_metric_view_joins: true` in `Config/behavior.yaml`.

### Relationships not detected
**Cause**: Relationships not defined in semantic model.  
**Solution**: Define `SMLRelationship` objects in your model.

### Circular relationship warnings
**Cause**: Your model has circular paths (A → B → A).  
**Solution**: Check relationship definitions; semabridge handles this safely by preventing re-entry.

### Complex join hierarchy
**Cause**: Very deep nesting (6+ levels).  
**Solution**: Consider flattening relationships or increasing `max_depth` in builder call.

## API Reference

### JoinTreeBuilder

```python
from semabridge.utils.join_builder import JoinTreeBuilder

builder = JoinTreeBuilder(sml_model)

# Build join tree from primary dataset
joins = builder.build_join_tree(
    primary_dataset="orders",
    max_depth=5  # optional
)

# Returns list[SMLJoin] with nested structure
for join in joins:
    print(f"Join {join.name}: {join.source} ON {join.on}")
    for nested in join.joins:
        print(f"  Nested: {nested.name}")
```

### SMLJoin Model

```python
from semabridge.sml.models import SMLJoin

join = SMLJoin(
    name="customer",              # Join alias
    source="db.schema.customer",  # Fully-qualified table
    on="o_custkey = c_custkey",   # Join condition
    joins=[]                       # Nested joins
)
```

### Configuration Flag

```yaml
databricks:
  enable_metric_view_joins: bool
    # Enable native Databricks metric view 'joins' property
    # for multi-table snowflake schemas.
    # Requires enable_cross_table_joins=true.
    # Default: false
```

## Examples

See `Examples/` for complete working examples:
- `tpch_multitable_metric_view.yaml` — TPC-H orders/customer/nation example
- `snowflake_schema_example.yaml` — Multi-level snowflake schema demo

## Performance Considerations

Join performance depends on:
- **Relationship complexity**: More relationships = more traversal
- **Nesting depth**: Deeper hierarchies = more recursive calls
- **Data cardinality**: Large tables may benefit from schema optimization
- **Join selectivity**: Proper key relationships improve query performance

Semabridge handles reasonable schemas (5-10 levels, 10-20 relationships) efficiently. For exceptionally complex models, consider breaking into multiple metric views.

## Future Enhancements

Potential follow-up improvements:
- Dimension-level join specification (e.g., outer joins vs. inner joins)
- Multi-column join condition formatting
- Join filtering and materialization hints
- Performance analysis and join optimization recommendations

## References

- [Azure Databricks Metric View YAML Specification](https://docs.databricks.com/analytics/unity-catalog/metric-views/create-metric-view)
- [Semabridge SML Models](../src/semabridge/sml/models.py)
- [Join Tree Builder](../src/semabridge/utils/join_builder.py)
