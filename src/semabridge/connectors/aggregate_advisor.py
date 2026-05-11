"""
Aggregate Advisor — Mandate 6: Aggregate Awareness for Cortex Analyst.

Analyzes the SML model to recommend materialized aggregate tables that
would improve Cortex Analyst query performance.  The advisor examines:

1. **Metric aggregation patterns** — Identifies SUM/COUNT/AVG metrics
   that are frequently queried with the same dimension groupings.
2. **Dimension cardinality** — High-cardinality dimensions benefit less
   from pre-aggregation; low-cardinality ones (Month, Region, Category)
   are prime candidates.
3. **Fact table row counts** — Large tables (>1M rows) are the main
   beneficiaries of aggregate tables.

Output:
    A list of ``AggregateRecommendation`` objects with DDL for creating
    ``CREATE TABLE ... AS SELECT`` materialized aggregates, plus the
    corresponding metric overrides for the Cortex YAML.

Usage::

    from semabridge.connectors.aggregate_advisor import AggregateAdvisor

    advisor = AggregateAdvisor(min_row_threshold=100_000)
    recommendations = advisor.analyze(sml_model)
    for rec in recommendations:
        print(rec.ddl)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AggregateRecommendation:
    """A recommended materialized aggregate table."""

    table_name: str
    """Name for the aggregate table (e.g. ``AGG_SALES_BY_REGION_MONTH``)."""

    source_dataset: str
    """Source fact table unique_name."""

    group_by_columns: list[str]
    """Columns to GROUP BY in the aggregate."""

    metrics: list[dict[str, str]]
    """List of ``{"name": ..., "expression": ..., "aggregation": ...}``."""

    ddl: str
    """Complete ``CREATE TABLE ... AS SELECT`` DDL statement."""

    estimated_row_reduction: float = 0.0
    """Estimated percentage reduction in rows vs. source table."""

    reason: str = ""
    """Human-readable explanation of why this aggregate was recommended."""


class AggregateAdvisor:
    """Analyzes SML models and recommends materialized aggregates.

    Parameters
    ----------
    min_row_threshold : int
        Minimum source table row count to consider for aggregation.
        Tables smaller than this are skipped.
    max_group_by : int
        Maximum number of GROUP BY columns in a single aggregate.
    auto_materialize : bool
        If True, generate DDL that can be executed directly.
        If False, generate recommendations only.
    database : str
        Target Snowflake database for DDL generation.
    schema_name : str
        Target Snowflake schema for DDL generation.
    """

    def __init__(
        self,
        min_row_threshold: int = 100_000,
        max_group_by: int = 5,
        auto_materialize: bool = False,
        database: str = "",
        schema_name: str = "",
    ) -> None:
        self.min_row_threshold = min_row_threshold
        self.max_group_by = max_group_by
        self.auto_materialize = auto_materialize
        self.database = database
        self.schema_name = schema_name

    def analyze(self, sml_model: Any) -> list[AggregateRecommendation]:
        """Analyze an SML model and return aggregate recommendations.

        Parameters
        ----------
        sml_model
            An ``SMLModel`` instance.

        Returns
        -------
        list[AggregateRecommendation]
        """
        recommendations: list[AggregateRecommendation] = []

        # Build dataset lookup
        ds_lookup = {ds.unique_name: ds for ds in sml_model.datasets}

        # Find fact tables (is_fact=True or has metrics referencing them)
        fact_datasets = set()
        for ds in sml_model.datasets:
            if getattr(ds, "is_fact", False):
                fact_datasets.add(ds.unique_name)
        for metric in sml_model.metrics:
            if metric.dataset:
                fact_datasets.add(metric.dataset)

        # Group metrics by their source dataset
        metrics_by_dataset: dict[str, list] = {}
        for metric in sml_model.metrics:
            if not metric.dataset:
                continue
            metrics_by_dataset.setdefault(metric.dataset, []).append(metric)

        # Find dimension columns via relationships
        dim_cols_by_dataset: dict[str, list[str]] = {}
        for rel in sml_model.relationships:
            if rel.is_active and rel.from_dataset in fact_datasets:
                dim_cols_by_dataset.setdefault(
                    rel.from_dataset, []
                ).extend(rel.from_columns)

        # Generate recommendations for each fact table
        for ds_name in fact_datasets:
            ds = ds_lookup.get(ds_name)
            if not ds:
                continue

            row_count = getattr(ds, "row_count", None) or 0
            if row_count < self.min_row_threshold and row_count > 0:
                logger.debug(
                    f"Skipping '{ds_name}': {row_count} rows < "
                    f"threshold {self.min_row_threshold}"
                )
                continue

            dataset_metrics = metrics_by_dataset.get(ds_name, [])
            if not dataset_metrics:
                continue

            # Get dimension columns for grouping
            group_cols = dim_cols_by_dataset.get(ds_name, [])
            if not group_cols:
                # Fall back to non-measure, non-key low-ish cardinality columns
                group_cols = self._infer_group_columns(ds)

            if not group_cols:
                continue

            # Limit group by columns
            group_cols = group_cols[: self.max_group_by]

            rec = self._build_recommendation(
                dataset=ds,
                metrics=dataset_metrics,
                group_cols=group_cols,
                row_count=row_count,
            )
            if rec:
                recommendations.append(rec)

        logger.info(
            f"Aggregate advisor: {len(recommendations)} recommendation(s) "
            f"for {len(fact_datasets)} fact table(s)"
        )
        return recommendations

    def _infer_group_columns(self, dataset: Any) -> list[str]:
        """Infer grouping columns from a dataset's column types.

        Prefers DATE, STRING columns that are not keys and not hidden.
        """
        candidates = []
        for col in dataset.columns:
            if col.is_key or col.is_hidden:
                continue
            dtype = str(getattr(col, "data_type", "")).upper()
            if dtype in ("DATE", "DATETIME", "STRING", "VARCHAR", "INTEGER"):
                candidates.append(col.unique_name)
        return candidates[: self.max_group_by]

    def _build_recommendation(
        self,
        dataset: Any,
        metrics: list,
        group_cols: list[str],
        row_count: int,
    ) -> Optional[AggregateRecommendation]:
        """Build a single aggregate recommendation with DDL."""
        # Build aggregate table name
        col_suffix = "_".join(c.upper()[:8] for c in group_cols[:3])
        agg_table_name = f"AGG_{dataset.unique_name.upper()}_{col_suffix}"

        # Build SELECT clause
        select_parts = [f'"{c.upper()}"' for c in group_cols]
        metric_defs = []
        for m in metrics:
            agg = getattr(m, "aggregation", "SUM").value if hasattr(
                getattr(m, "aggregation", None), "value"
            ) else "SUM"
            source_col = getattr(m, "source_column", None)
            if source_col:
                select_parts.append(
                    f'{agg}("{source_col.upper()}") AS "{m.unique_name.upper()}"'
                )
                metric_defs.append({
                    "name": m.unique_name,
                    "expression": f'{agg}("{source_col.upper()}")',
                    "aggregation": agg,
                })

        if not metric_defs:
            return None

        group_by = ", ".join(f'"{c.upper()}"' for c in group_cols)
        source_table = dataset.source_table or dataset.unique_name

        # Build qualified name
        if self.database and self.schema_name:
            qualified = f'{self.database}.{self.schema_name}."{agg_table_name}"'
            source_qualified = f'{self.database}.{self.schema_name}."{source_table.upper()}"'
        else:
            qualified = f'"{agg_table_name}"'
            source_qualified = f'"{source_table.upper()}"'

        newline = "\n"
        select_joined = f",{newline}  ".join(select_parts)
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {qualified} AS\n"
            f"SELECT\n  {select_joined}\n"
            f"FROM {source_qualified}\n"
            f"GROUP BY {group_by};"
        )

        # Rough row reduction estimate
        reduction = min(0.99, 1.0 - (len(group_cols) * 100 / max(row_count, 1)))

        return AggregateRecommendation(
            table_name=agg_table_name,
            source_dataset=dataset.unique_name,
            group_by_columns=group_cols,
            metrics=metric_defs,
            ddl=ddl,
            estimated_row_reduction=max(0, reduction),
            reason=(
                f"Fact table '{dataset.unique_name}' "
                f"({'~' + str(row_count) + ' rows' if row_count else 'unknown size'}) "
                f"with {len(metric_defs)} aggregatable metric(s) "
                f"grouped by {len(group_cols)} dimension column(s)"
            ),
        )
