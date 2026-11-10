"""Semantic Model & Domain Ontology CI/CD Linter.

Automates semantic quality gates for GitHub Actions, GitLab CI, and pre-commit hooks:
- Enforces data stewardship and documentation completeness
- Enforces metric naming conventions
- Detects circular joins and invalid graph relationships
- Validates cross-domain metric collisions
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from semabridge.ontology.models import FederatedEnterpriseOntology

logger = logging.getLogger("semabridge.governance.linter")


class LintIssue(BaseModel):
    rule_id: str
    severity: str  # 'ERROR', 'WARNING', 'INFO'
    object_type: str  # 'METRIC', 'CONCEPT', 'RELATIONSHIP'
    object_name: str
    domain: Optional[str] = None
    message: str
    remediation: str


class LintReport(BaseModel):
    passed: bool
    total_issues: int
    error_count: int
    warning_count: int
    issues: List[LintIssue] = Field(default_factory=list)
    summary: str


class SemanticLinter:
    """Evaluates semantic models and domain ontologies against enterprise governance standards."""

    def __init__(
        self,
        require_steward: bool = True,
        require_description: bool = True,
        enforce_snake_case_metrics: bool = True,
    ):
        self.require_steward = require_steward
        self.require_description = require_description
        self.enforce_snake_case_metrics = enforce_snake_case_metrics

    def lint_ontology(self, ontology: FederatedEnterpriseOntology) -> LintReport:
        """Run all governance rules over a federated enterprise ontology."""
        issues: List[LintIssue] = []

        # 1. Rule: Metric Collisions
        for col in ontology.collisions:
            issues.append(
                LintIssue(
                    rule_id="RULE_METRIC_COLLISION",
                    severity=col.severity,
                    object_type=col.item_type.upper(),
                    object_name=col.item_name,
                    domain=", ".join(col.conflicting_domains),
                    message=col.message,
                    remediation="Align metric definitions across teams or designate a single domain owner.",
                )
            )

        # 2. Rule: Unresolved References
        for unres in ontology.unresolved_references:
            issues.append(
                LintIssue(
                    rule_id="RULE_UNRESOLVED_REFERENCE",
                    severity="ERROR",
                    object_type="RELATIONSHIP",
                    object_name="Unknown",
                    message=unres,
                    remediation="Ensure concepts referenced in joins are defined in an ingested domain YAML.",
                )
            )

        # 3. Rule: Metric Data Stewards & Descriptions
        for m in ontology.metrics.values():
            if self.require_steward and not m.data_steward:
                issues.append(
                    LintIssue(
                        rule_id="RULE_MISSING_STEWARD",
                        severity="ERROR",
                        object_type="METRIC",
                        object_name=m.name,
                        domain=m.domain,
                        message=f"Metric '{m.name}' has no data steward assigned.",
                        remediation="Add 'data_steward: \"user@enterprise.com\"' to the metric definition.",
                    )
                )

            if self.require_description and (not m.description or len(m.description.strip()) < 5):
                issues.append(
                    LintIssue(
                        rule_id="RULE_MISSING_DESCRIPTION",
                        severity="WARNING",
                        object_type="METRIC",
                        object_name=m.name,
                        domain=m.domain,
                        message=f"Metric '{m.name}' lacks a comprehensive business description.",
                        remediation="Provide an unambiguous description explaining the business logic.",
                    )
                )

            if not m.sql_expression or not m.sql_expression.strip():
                issues.append(
                    LintIssue(
                        rule_id="RULE_EMPTY_EXPRESSION",
                        severity="ERROR",
                        object_type="METRIC",
                        object_name=m.name,
                        domain=m.domain,
                        message=f"Metric '{m.name}' has an empty or blank SQL expression.",
                        remediation="Define a valid calculation expression (e.g. SUM(amount)).",
                    )
                )

            if self.enforce_snake_case_metrics:
                # Check for strict snake_case: ^[a-z0-9_]+$
                if not re.match(r"^[a-z0-9_]+$", m.name):
                    issues.append(
                        LintIssue(
                            rule_id="RULE_NAMING_CONVENTION",
                            severity="WARNING",
                            object_type="METRIC",
                            object_name=m.name,
                            domain=m.domain,
                            message=f"Metric name '{m.name}' violates snake_case convention (must match ^[a-z0-9_]+$).",
                            remediation="Use snake_case with lowercase alphanumeric characters and underscores (e.g. 'gross_margin').",
                        )
                    )

        # 4. Rule: Concept and Attribute Data Stewards & Descriptions
        for c in ontology.concepts.values():
            if self.require_steward and not c.data_steward:
                issues.append(
                    LintIssue(
                        rule_id="RULE_MISSING_STEWARD",
                        severity="ERROR",
                        object_type="CONCEPT",
                        object_name=c.name,
                        domain=c.domain,
                        message=f"Concept '{c.name}' has no data steward assigned.",
                        remediation="Add 'data_steward: \"user@enterprise.com\"' to the concept definition.",
                    )
                )

            if self.require_description and (not c.description or len(c.description.strip()) < 5):
                issues.append(
                    LintIssue(
                        rule_id="RULE_MISSING_DESCRIPTION",
                        severity="WARNING",
                        object_type="CONCEPT",
                        object_name=c.name,
                        domain=c.domain,
                        message=f"Concept '{c.name}' lacks a comprehensive business description.",
                        remediation="Provide an unambiguous description explaining the concept.",
                    )
                )

            for attr in c.attributes:
                if self.require_description and (not attr.description or len(attr.description.strip()) < 5):
                    issues.append(
                        LintIssue(
                            rule_id="RULE_MISSING_DESCRIPTION",
                            severity="WARNING",
                            object_type="ATTRIBUTE",
                            object_name=f"{c.name}.{attr.name}",
                            domain=c.domain,
                            message=f"Attribute '{c.name}.{attr.name}' lacks a comprehensive description.",
                            remediation="Provide an unambiguous description for the attribute.",
                        )
                    )

        # 5. Rule: Circular Joins in Relationships (report all cycles)
        graph: Dict[str, List[str]] = {}
        for rel in ontology.relationships:
            if rel.from_concept not in graph:
                graph[rel.from_concept] = []
            graph[rel.from_concept].append(rel.to_concept)

        visited: Set[str] = set()
        cycles_reported: Set[Any] = set()

        def find_cycles(node: str, path: List[str]):
            visited.add(node)
            path.append(node)
            for neighbor in graph.get(node, []):
                if neighbor in path:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    cycle_key = tuple(sorted(set(cycle)))
                    if cycle_key not in cycles_reported:
                        cycles_reported.add(cycle_key)
                        issues.append(
                            LintIssue(
                                rule_id="RULE_CIRCULAR_JOIN",
                                severity="ERROR",
                                object_type="RELATIONSHIP",
                                object_name=neighbor,
                                message=f"Circular relationship cycle detected: {' -> '.join(cycle)}.",
                                remediation="Break the circular dependency in the relationship graph.",
                            )
                        )
                elif neighbor not in visited:
                    find_cycles(neighbor, path)
            path.pop()

        for node in list(graph.keys()):
            if node not in visited:
                find_cycles(node, [])

        error_count = sum(1 for i in issues if i.severity == "ERROR")
        warning_count = sum(1 for i in issues if i.severity == "WARNING")
        passed = (error_count == 0)

        summary = (
            f"CI/CD Governance Gate: {'PASSED' if passed else 'FAILED'} · "
            f"{error_count} Errors, {warning_count} Warnings across {len(ontology.metrics)} metrics."
        )

        return LintReport(
            passed=passed,
            total_issues=len(issues),
            error_count=error_count,
            warning_count=warning_count,
            issues=issues,
            summary=summary,
        )
