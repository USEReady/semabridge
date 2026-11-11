"""Semantic Model & Domain Ontology CI/CD Linter.

Automates semantic quality gates for CI/CD pipelines and pre-commit checks:
- Enforces data stewardship and documentation completeness on metrics & concepts
- Enforces naming conventions and separator-normalized collision detection
- Detects circular joins and self-referencing hierarchies across relationships
- Lints every domain independently so duplicate metrics in secondary domains are never skipped
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.ontology.models import DomainOntology, FederatedEnterpriseOntology

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


def _norm_metric_name(name: str) -> str:
    """Normalize whitespace, underscores, and hyphens while preserving symbols such as '%'."""
    return re.sub(r"[\s_\-]+", "", str(name or "").strip().casefold())


def _norm_formula_text(expr: Optional[str]) -> str:
    """Normalize SQL formula whitespace and casing for collision comparison."""
    txt = re.sub(r"\s+", " ", str(expr or "").strip()).casefold()
    return re.sub(r"\s*([(),+\-*/=<>])\s*", r"\1", txt)


class SemanticLinter:
    """Evaluates semantic models and domain ontologies against enterprise governance standards."""

    def __init__(
        self,
        require_steward: bool = True,
        require_description: bool = True,
        enforce_snake_case_metrics: bool = True,
        steward_severity: Optional[str] = None,
    ):
        self.require_steward = require_steward
        self.require_description = require_description
        self.enforce_snake_case_metrics = enforce_snake_case_metrics
        if steward_severity is not None and str(steward_severity).strip():
            sev = str(steward_severity).strip().upper()
            self.steward_severity = sev if sev in ("ERROR", "WARNING", "OFF") else ("ERROR" if require_steward else "OFF")
        else:
            self.steward_severity = "ERROR" if require_steward else "OFF"

    def lint_ontology(
        self,
        ontology: FederatedEnterpriseOntology,
        allowed_domains: Optional[List[str]] = None,
    ) -> LintReport:
        """Run all governance rules over every scoped domain and relationship in the ontology."""
        issues: List[LintIssue] = []
        allowed_cf = {d.casefold() for d in (allowed_domains or []) if d}

        def _domain_allowed(dom_name: Optional[str], dom_id: Optional[str] = None) -> bool:
            if not allowed_cf:
                return True
            names = {n.casefold() for n in (dom_name, dom_id) if n}
            return bool(names & allowed_cf)

        # 1. Rule: Existing Metric Collisions + Separator-Normalized Collisions (E7, E8)
        reported_collision_keys: Set[str] = set()
        for col in ontology.collisions:
            if allowed_cf and not any(d.casefold() in allowed_cf for d in (col.conflicting_domains or [])):
                continue
            reported_collision_keys.add(_norm_metric_name(col.item_name))
            issues.append(
                LintIssue(
                    rule_id="RULE_METRIC_COLLISION",
                    severity=col.severity,
                    object_type=col.item_type.upper(),
                    object_name=col.item_name,
                    domain=", ".join(col.conflicting_domains),
                    message=col.message,
                    remediation="Align definitions across domains or designate a single owner.",
                )
            )

        domain_list: List[DomainOntology] = (
            [d for d in ontology.domains.values() if _domain_allowed(d.domain, getattr(d, "id", None))]
            if ontology.domains
            else []
        )

        # Group all metric definitions across distinct-project domains by separator-normalized identifier
        if domain_list:
            dom_proj_key: Dict[str, str] = {}
            for dom in domain_list:
                sp = str(getattr(dom, "source_project_id", None) or "").strip()
                dom_proj_key[dom.domain] = sp.casefold() if sp else f"dom:{dom.domain.casefold()}"

            by_norm: Dict[str, List[Tuple[str, Any]]] = {}
            for dom in domain_list:
                for m in dom.metrics:
                    nk = _norm_metric_name(m.name)
                    if nk:
                        by_norm.setdefault(nk, []).append((dom.domain, m))

            for nk, entries in sorted(by_norm.items()):
                if len(entries) < 2 or nk in reported_collision_keys:
                    continue
                # Collapse identical metric names across same-project duplicate domains, while preserving distinct spellings (E8)
                bucketed: Dict[Tuple[str, str], Tuple[str, Any]] = {}
                for d_name, m_obj in entries:
                    bkey = (dom_proj_key.get(d_name, d_name.casefold()), m_obj.name.casefold())
                    bucketed.setdefault(bkey, (d_name, m_obj))
                eff_entries = list(bucketed.values())
                if len(eff_entries) < 2:
                    continue

                distinct_names = {m.name for _, m in eff_entries}
                distinct_domains: List[str] = []
                for d_name, _ in eff_entries:
                    if d_name not in distinct_domains:
                        distinct_domains.append(d_name)
                norm_exprs = {
                    _norm_formula_text(m.sql_expression)
                    for _, m in eff_entries
                    if (m.sql_expression or "").strip()
                }
                has_diff_formula = len(norm_exprs) > 1
                # Flag normalized name variations (e.g. 'Dup Metric' vs 'DUP_METRIC') or cross-domain conflicts
                if len(distinct_names) > 1 or len(distinct_domains) > 1:
                    rep_name = eff_entries[0][1].name
                    diff_note = ""
                    if has_diff_formula:
                        diff_parts = [f"{d}: {(m.sql_expression or '').strip()!r}" for d, m in eff_entries]
                        diff_note = f" Conflicting formulas: {'; '.join(diff_parts)}."
                    issues.append(
                        LintIssue(
                            rule_id="RULE_METRIC_COLLISION",
                            severity="ERROR" if has_diff_formula else "WARNING",
                            object_type="METRIC",
                            object_name=rep_name,
                            domain=", ".join(distinct_domains),
                            message=(
                                f"Normalized metric name collision across {sorted(distinct_names)} "
                                f"in domain(s) {', '.join(distinct_domains)}.{diff_note}"
                            ),
                            remediation="Standardize to a single canonical name or disambiguate formulas.",
                        )
                    )

        # 2. Rule: Unresolved References (E13, E14)
        for unres in ontology.unresolved_references:
            obj_match = re.search(r"'([^']+)'", str(unres))
            obj_name = obj_match.group(1) if obj_match else "Relationship"
            issues.append(
                LintIssue(
                    rule_id="RULE_UNRESOLVED_REFERENCE",
                    severity="ERROR",
                    object_type="RELATIONSHIP",
                    object_name=obj_name,
                    message=unres,
                    remediation="Ensure concepts referenced in joins are defined in an ingested domain YAML.",
                )
            )

        # 3. Rule: Concepts & Metrics across EVERY domain (E1-E6, E9)
        if domain_list:
            all_concepts = [(dom.domain, c) for dom in domain_list for c in dom.concepts]
            all_metrics = [(dom.domain, m) for dom in domain_list for m in dom.metrics]
        elif allowed_cf:
            all_concepts = []
            all_metrics = []
        else:
            all_concepts = [(c.domain, c) for c in ontology.concepts.values()]
            all_metrics = [(m.domain, m) for m in ontology.metrics.values()]

        for dom_name, c in all_concepts:
            if self.steward_severity != "OFF" and not getattr(c, "data_steward", None):
                issues.append(
                    LintIssue(
                        rule_id="RULE_MISSING_STEWARD",
                        severity=self.steward_severity,
                        object_type="CONCEPT",
                        object_name=c.name,
                        domain=dom_name or c.domain,
                        message=f"Concept '{c.name}' has no data steward assigned.",
                        remediation="Add 'data_steward: \"user@enterprise.com\"' to the concept definition.",
                    )
                )

        for dom_name, m in all_metrics:
            eff_domain = dom_name or m.domain
            if self.steward_severity != "OFF" and not m.data_steward:
                issues.append(
                    LintIssue(
                        rule_id="RULE_MISSING_STEWARD",
                        severity=self.steward_severity,
                        object_type="METRIC",
                        object_name=m.name,
                        domain=eff_domain,
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
                        domain=eff_domain,
                        message=f"Metric '{m.name}' lacks a comprehensive business description.",
                        remediation="Provide an unambiguous description explaining the business logic.",
                    )
                )

            has_sql = bool(m.sql_expression and m.sql_expression.strip())
            has_binding = bool(getattr(m, "maps_to", None))
            deploy_reason = getattr(m, "deploy_reason", None)
            if not has_sql and not has_binding:
                if deploy_reason:
                    issues.append(
                        LintIssue(
                            rule_id="RULE_EMPTY_EXPRESSION",
                            severity="INFO",
                            object_type="METRIC",
                            object_name=m.name,
                            domain=eff_domain,
                            message=f"Metric '{m.name}' has no compiled SQL due to deployment constraint: {deploy_reason}",
                            remediation="Query through a deployed SEMANTIC_VIEW or supply an explicit SQL override.",
                        )
                    )
                else:
                    issues.append(
                        LintIssue(
                            rule_id="RULE_EMPTY_EXPRESSION",
                            severity="ERROR",
                            object_type="METRIC",
                            object_name=m.name,
                            domain=eff_domain,
                            message=f"Metric '{m.name}' has an empty or blank SQL expression.",
                            remediation="Define a valid calculation expression or bind it with maps_to.",
                        )
                    )

            if self.enforce_snake_case_metrics:
                if " " in m.name or not re.match(r"^[A-Za-z0-9_]+$", m.name):
                    issues.append(
                        LintIssue(
                            rule_id="RULE_NAMING_CONVENTION",
                            severity="WARNING",
                            object_type="METRIC",
                            object_name=m.name,
                            domain=eff_domain,
                            message=f"Metric name '{m.name}' contains spaces or non-standard characters.",
                            remediation="Use snake_case or standard alphanumeric identifiers.",
                        )
                    )

        # 4. Rule: Circular Joins & Self-Referencing Hierarchies per domain (E10, E11, E12)
        domain_rel_groups: List[Tuple[Optional[str], List[Any]]] = (
            [(dom.domain, list(dom.relationships or [])) for dom in sorted(domain_list, key=lambda d: d.domain.casefold())]
            if domain_list
            else ([] if allowed_cf else [(None, list(ontology.relationships or []))])
        )

        for dom_name, dom_rels in domain_rel_groups:
            graph: Dict[str, List[str]] = {}
            self_loops: List[str] = []
            seen_self: Set[str] = set()
            for rel in dom_rels:
                # Inactive relationships are already deactivated in BI/semantic models to prevent cycles
                if getattr(rel, "is_active", True) is False:
                    continue
                fc = rel.from_concept
                tc = rel.to_concept
                if fc == tc:
                    if fc not in seen_self:
                        seen_self.add(fc)
                        self_loops.append(fc)
                    continue
                adj = graph.setdefault(fc, [])
                if tc not in adj:
                    adj.append(tc)

            for sl in sorted(self_loops):
                issues.append(
                    LintIssue(
                        rule_id="RULE_CIRCULAR_JOIN",
                        severity="INFO",
                        object_type="RELATIONSHIP",
                        object_name=sl,
                        domain=dom_name,
                        message=f"Self-referencing hierarchy relationship detected on concept '{sl}'.",
                        remediation="Verify whether parent-child hierarchy self-join is intentional.",
                    )
                )

            # Report all distinct directed multi-node active cycles within this domain
            visited: Set[str] = set()
            reported_cycles: Set[Tuple[str, ...]] = set()
            path: List[str] = []
            on_stack: Set[str] = set()

            def _dfs(node: str) -> None:
                visited.add(node)
                on_stack.add(node)
                path.append(node)
                for nb in graph.get(node, []):
                    if nb in on_stack:
                        idx = path.index(nb)
                        cyc_nodes = path[idx:]
                        canon = tuple(sorted(cyc_nodes))
                        if canon not in reported_cycles:
                            reported_cycles.add(canon)
                            cycle_str = " -> ".join(cyc_nodes + [nb])
                            issues.append(
                                LintIssue(
                                    rule_id="RULE_CIRCULAR_JOIN",
                                    severity="ERROR",
                                    object_type="RELATIONSHIP",
                                    object_name=cyc_nodes[0],
                                    domain=dom_name,
                                    message=f"Circular relationship cycle detected: {cycle_str}.",
                                    remediation="Break the circular dependency in the relationship graph.",
                                )
                            )
                    elif nb not in visited:
                        _dfs(nb)
                path.pop()
                on_stack.remove(node)

            for node in sorted(graph.keys()):
                if node not in visited:
                    _dfs(node)

        error_count = sum(1 for i in issues if i.severity == "ERROR")
        warning_count = sum(1 for i in issues if i.severity == "WARNING")
        passed = error_count == 0

        summary = (
            f"CI/CD Governance Gate: {'PASSED' if passed else 'FAILED'} · "
            f"{error_count} Errors, {warning_count} Warnings across {len(all_metrics)} metrics."
        )

        return LintReport(
            passed=passed,
            total_issues=len(issues),
            error_count=error_count,
            warning_count=warning_count,
            issues=issues,
            summary=summary,
        )
