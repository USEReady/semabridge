"""Semantic model validator for missing metric dependencies and join paths."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from semabridge.sml.models import SMLMetric, SMLModel
from semabridge.utils.semantic_graph import SemanticGraph


_TABLE_COLUMN_PATTERNS = (
    re.compile(r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)\s*\[\s*(?P<column>[^\]]+?)\s*\]"),
    re.compile(r"\[\s*(?P<table>[^\]]+?)\s*\]\.\[\s*(?P<column>[^\]]+?)\s*\]"),
)


@dataclass(frozen=True)
class SemanticValidationFinding:
    """Single validation finding for a semantic model metric."""

    code: str
    metric_name: str
    message: str
    suggested_fix: str
    table_name: str | None = None
    column_name: str | None = None
    measure_name: str | None = None


@dataclass
class MetricValidationResult:
    """Validation result for one metric."""

    metric_name: str
    findings: list[SemanticValidationFinding] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.findings


@dataclass
class SemanticModelValidationReport:
    """Validation report across all metrics in a model."""

    metric_results: list[MetricValidationResult] = field(default_factory=list)

    @property
    def findings(self) -> list[SemanticValidationFinding]:
        return [finding for result in self.metric_results for finding in result.findings]

    @property
    def is_valid(self) -> bool:
        return not self.findings


class SemanticModelValidator:
    """Validate metric dependencies against datasets, columns, and relationships."""

    def validate(self, model: SMLModel) -> SemanticModelValidationReport:
        graph = SemanticGraph(model)
        results: list[MetricValidationResult] = []

        for metric in model.metrics:
            results.append(self.validate_metric(model, metric, graph))

        return SemanticModelValidationReport(metric_results=results)

    def validate_metric(
        self,
        model: SMLModel,
        metric: SMLMetric,
        graph: SemanticGraph | None = None,
    ) -> MetricValidationResult:
        findings: list[SemanticValidationFinding] = []
        metric_name = metric.unique_name
        dataset = model.get_dataset(metric.dataset)

        if graph is None:
            try:
                graph = SemanticGraph(model)
            except ValueError:
                graph = None

        reachable_tables = self._reachable_tables(metric.dataset, graph) if graph and dataset else set()

        if dataset is None:
            findings.append(
                SemanticValidationFinding(
                    code="MISSING_DATASET",
                    metric_name=metric_name,
                    message=f"Metric '{metric_name}' is anchored to missing dataset '{metric.dataset}'.",
                    suggested_fix=f"Add dataset '{metric.dataset}' to the model or repoint the metric to an existing dataset.",
                    table_name=metric.dataset,
                )
            )

        if dataset is not None and metric.source_column:
            if dataset.get_column(metric.source_column) is None:
                findings.append(
                    SemanticValidationFinding(
                        code="MISSING_COLUMN",
                        metric_name=metric_name,
                        message=(
                            f"Metric '{metric_name}' source column '{metric.source_column}' is missing from dataset '{metric.dataset}'."
                        ),
                        suggested_fix=(
                            f"Add column '{metric.source_column}' to dataset '{metric.dataset}' or update the metric source_column."
                        ),
                        table_name=metric.dataset,
                        column_name=metric.source_column,
                    )
                )

        for dependency_name in metric.depends_on_measures:
            if model.get_metric(dependency_name) is None:
                findings.append(
                    SemanticValidationFinding(
                        code="MISSING_MEASURE",
                        metric_name=metric_name,
                        message=(
                            f"Metric '{metric_name}' depends on missing measure '{dependency_name}'."
                        ),
                        suggested_fix=(
                            f"Add measure '{dependency_name}' to the model or remove it from depends_on_measures on '{metric_name}'."
                        ),
                        measure_name=dependency_name,
                    )
                )

        for table_name, column_name in self._extract_table_column_references(metric.expression):
            table = model.get_dataset(table_name)

            if table is None:
                findings.append(
                    SemanticValidationFinding(
                        code="MISSING_TABLE",
                        metric_name=metric_name,
                        message=f"Metric '{metric_name}' references missing table '{table_name}'.",
                        suggested_fix=f"Add table '{table_name}' to the model or remove the reference from the metric expression.",
                        table_name=table_name,
                        column_name=column_name,
                    )
                )
                continue

            if table.get_column(column_name) is None:
                findings.append(
                    SemanticValidationFinding(
                        code="MISSING_COLUMN",
                        metric_name=metric_name,
                        message=(
                            f"Metric '{metric_name}' references missing column '{column_name}' on table '{table_name}'."
                        ),
                        suggested_fix=(
                            f"Add column '{column_name}' to table '{table_name}' or update the metric expression."
                        ),
                        table_name=table_name,
                        column_name=column_name,
                    )
                )
                continue

            if dataset is not None and graph is not None:
                current_table = table.unique_name.upper()
                source_table = metric.dataset.upper()
                if current_table != source_table and current_table not in reachable_tables:
                    findings.append(
                        SemanticValidationFinding(
                            code="MISSING_RELATIONSHIP",
                            metric_name=metric_name,
                            message=(
                                f"Metric '{metric_name}' references table '{table_name}', but '{metric.dataset}' cannot reach it through existing relationships."
                            ),
                            suggested_fix=(
                                f"Add a relationship from '{metric.dataset}' to '{table_name}' or move the metric to a dataset that can reach '{table_name}'."
                            ),
                            table_name=table_name,
                            column_name=column_name,
                        )
                    )

        return MetricValidationResult(metric_name=metric_name, findings=findings)

    def _reachable_tables(self, dataset_name: str, graph: SemanticGraph | None) -> set[str]:
        if graph is None:
            return set()

        try:
            reachable = graph.get_reachable_from(dataset_name)
        except ValueError:
            return set()

        tables = {dataset_name.upper()}
        tables.update(table.upper() for table in reachable)
        return tables

    def _extract_table_column_references(self, expression: str) -> list[tuple[str, str]]:
        if not expression:
            return []

        references: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for pattern in _TABLE_COLUMN_PATTERNS:
            for match in pattern.finditer(expression):
                table_name = (match.group("table") or "").strip()
                column_name = (match.group("column") or "").strip()
                if not table_name or not column_name:
                    continue

                key = (table_name.upper(), column_name.upper())
                if key in seen:
                    continue

                seen.add(key)
                references.append((table_name, column_name))

        return references