"""AI Impact Check Service for Description Drift.

Evaluates how changing a metric or dimension's description alters question routing,
metric resolution, dimension grouping, query filters, and query refusal in the AI
natural language query engine.

Guarantees:
- ZERO warehouse writes.
- Read-only throughout; completely decoupled from sync/write actions.
- Stored baseline description text vs live introspected comment.
- Runs memory-isolated query resolution plans without side-effects.
- Samples certified queries, BI-truth cases, or question history; stops cleanly when none exist.
- Per-question evidence and clear risk summary.
"""

from __future__ import annotations

import copy
import difflib
import logging
import os
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.governance.drift.description_diff import classify_and_diff_description
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    FederatedEnterpriseOntology,
)

logger = logging.getLogger("semabridge.governance.ai_impact")

# In-memory cache keyed by (project_id, view_name, model_version, object_name, baseline_description, live_description)
_AI_IMPACT_CACHE: Dict[Tuple[str, str, int, str, str, str], AIImpactReport] = {}


def clear_ai_impact_cache() -> None:
    """Clear cached AI impact check results (for testing or baseline changes)."""
    _AI_IMPACT_CACHE.clear()


def get_max_questions() -> int:
    """Retrieve configured maximum questions for AI impact check."""
    raw = os.getenv("SEMABRIDGE_AI_IMPACT_MAX_QUESTIONS", "").strip()
    if raw.isdigit():
        val = int(raw)
        if val > 0:
            return val
    try:
        from semabridge.core.settings import get_settings
        settings = get_settings()
        ai_cfg = getattr(settings, "ai_impact", None) or getattr(settings, "drift", None)
        if ai_cfg and hasattr(ai_cfg, "max_questions"):
            return int(ai_cfg.max_questions)
    except Exception:
        pass
    return 5


class QuestionImpactResult(BaseModel):
    question: str
    status: str = "UNCHANGED"  # "CHANGED" | "UNCHANGED" | "NOT_COMPARABLE"
    baseline_object: Optional[str] = None
    live_object: Optional[str] = None
    object_choice_changed: bool = False
    baseline_sql: Optional[str] = None
    live_sql: Optional[str] = None
    sql_changed: bool = False
    sql_diff: Optional[str] = None
    baseline_groupings: List[str] = Field(default_factory=list)
    live_groupings: List[str] = Field(default_factory=list)
    baseline_filters: List[str] = Field(default_factory=list)
    live_filters: List[str] = Field(default_factory=list)
    grouping_or_filter_changed: bool = False
    baseline_refused: bool = False
    live_refused: bool = False
    refusal_changed: bool = False
    baseline_refusal_reason: Optional[str] = None
    live_refusal_reason: Optional[str] = None
    difference_details: List[str] = Field(default_factory=list)
    summary: str = ""


class AIImpactReport(BaseModel):
    project_id: Optional[str] = None
    object_name: str
    kind: str = "METRIC"   # "METRIC" | "DIMENSION"
    table: Optional[str] = None
    view_name: Optional[str] = None
    baseline_description: Optional[str] = None
    live_description: Optional[str] = None
    classification: str = "TEXT_REWORDED"  # "TEXT_ADDED" | "TEXT_REMOVED" | "TEXT_REWORDED"
    status: str = "completed"  # "completed" | "no_covering_questions" | "partial" | "failed"
    question_source: Optional[str] = None  # "certified_queries" | "bi_truth_cases" | "query_history" | None
    questions_tested: int = 0
    questions_compared: int = 0
    questions_changed: int = 0
    questions_unchanged: int = 0
    questions_not_comparable: int = 0
    sample_is_weak: bool = False
    max_questions_cap: int = 5
    cap_applied: bool = False
    questions: List[QuestionImpactResult] = Field(default_factory=list)
    any_object_changed: bool = False
    any_sql_changed: bool = False
    any_grouping_or_filter_changed: bool = False
    any_refusal_changed: bool = False
    overall_impact: str = "NO_DETECTED_IMPACT"  # "HIGH_IMPACT" | "MEDIUM_IMPACT" | "NO_DETECTED_IMPACT"
    risk_summary: str = ""
    sampling_disclaimer: str = (
        "Evidence from sampled questions, not a guarantee that no other questions are affected. "
        "Zero warehouse writes performed."
    )
    partial_failure_reason: Optional[str] = None


def _collect_sample_questions(
    object_name: str,
    kind: str = "METRIC",
    table: Optional[str] = None,
    project_id: Optional[str] = None,
    max_cap: int = 5,
) -> Tuple[List[str], Optional[str], int, List[str]]:
    """Gather candidate questions in strict order of preference:

    1. Certified queries that use this object (AIVerifiedQueryRow)
    2. BI-truth cases covering it (BITruthCaseRow / _load_suite)
    3. Recent questions from history that resolved to it (AIQueryExecutionRow)

    Never invents synthetic questions. If no real questions cover this object, returns ([], None, 0, failed_sources).
    """
    obj_clean = str(object_name or "").strip()
    from semabridge.governance.blast_radius import _ident_in_expr
    from semabridge.governance.drift.project_scope import matches_project

    def _proj_match(pid: Optional[str]) -> bool:
        if not project_id:
            return True
        if not pid or not str(pid).strip():
            return False
        return matches_project(project_id, pid)

    failed_sources: List[str] = []

    # 1. Certified queries (AIVerifiedQueryRow)
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.ai_models import AIVerifiedQueryRow
        with db_manager.get_session() as s:
            rows = s.query(AIVerifiedQueryRow).all()
            q_cert: List[str] = []
            seen_cert: Set[str] = set()
            for r in rows:
                if not _proj_match(r.project_id):
                    continue
                sql_txt = str(r.sql or "")
                name_txt = str(r.name or "")
                prompt_txt = str(r.question or "").strip()
                target_txt = str(r.target or "")

                hit = (
                    _ident_in_expr(sql_txt, obj_clean, table)
                    or _ident_in_expr(name_txt, obj_clean)
                    or _ident_in_expr(target_txt, obj_clean)
                    or _ident_in_expr(prompt_txt, obj_clean)
                )
                if hit and prompt_txt and prompt_txt.casefold() not in seen_cert:
                    seen_cert.add(prompt_txt.casefold())
                    q_cert.append(prompt_txt)
            if q_cert:
                return q_cert[:max_cap], "certified_queries", len(q_cert), failed_sources
    except Exception as ex:
        logger.warning("Certified queries lookup failed: %s", ex)
        failed_sources.append("certified_queries")

    # 2. BI-truth cases (BITruthCaseRow / _load_suite())
    try:
        from semabridge.ai.bi_truth_service import _load_suite
        suite = _load_suite()
        q_truth: List[str] = []
        seen_truth: Set[str] = set()
        for c in (suite.get("cases") or []):
            if not _proj_match(c.get("project_id")):
                continue
            c_m = str(c.get("metric_name") or "").strip()
            c_q = str(c.get("question") or "").strip()
            c_sql = str(c.get("sql") or c.get("query") or "")
            hit = (
                _ident_in_expr(c_m, obj_clean)
                or _ident_in_expr(c_q, obj_clean)
                or _ident_in_expr(c_sql, obj_clean, table)
            )
            if hit and c_q and c_q.casefold() not in seen_truth:
                seen_truth.add(c_q.casefold())
                q_truth.append(c_q)
        if q_truth:
            return q_truth[:max_cap], "bi_truth_cases", len(q_truth), failed_sources
    except Exception as ex:
        logger.warning("BI-truth cases lookup failed: %s", ex)
        failed_sources.append("bi_truth_cases")

    # 3. Recent questions from history (AIQueryExecutionRow)
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.ai_models import AIQueryExecutionRow
        with db_manager.get_session() as s:
            rows = s.query(AIQueryExecutionRow).order_by(AIQueryExecutionRow.executed_at.desc()).limit(100).all()
            q_hist: List[str] = []
            seen_hist: Set[str] = set()
            for r in rows:
                if not _proj_match(r.project_id):
                    continue
                sql_txt = str(r.sql or "")
                prompt_txt = str(r.question or "").strip()
                hit = _ident_in_expr(sql_txt, obj_clean, table) or _ident_in_expr(prompt_txt, obj_clean)
                if hit and prompt_txt and prompt_txt.casefold() not in seen_hist:
                    seen_hist.add(prompt_txt.casefold())
                    q_hist.append(prompt_txt)
            if q_hist:
                return q_hist[:max_cap], "query_history", len(q_hist), failed_sources
    except Exception as ex:
        logger.warning("Query history lookup failed: %s", ex)
        failed_sources.append("query_history")

    # When there are no questions: say so plainly and stop. Never invent questions to fill the gap.
    return [], None, 0, failed_sources


def run_ai_impact_check(
    object_name: Optional[str] = None,
    metric_name: Optional[str] = None,
    kind: str = "METRIC",
    table: Optional[str] = None,
    project_id: Optional[str] = None,
    view_name: Optional[str] = None,
    baseline_description: Optional[str] = None,
    live_description: Optional[str] = None,
    ontology: Optional[FederatedEnterpriseOntology] = None,
    custom_questions: Optional[List[str]] = None,
    max_questions: Optional[int] = None,
    bypass_cache: bool = False,
) -> AIImpactReport:
    """Compare question routing and resolution under baseline vs live description without warehouse writes."""
    obj_clean = str(object_name or metric_name or "").strip()
    if not obj_clean:
        raise ValueError("Object name must be provided for AI impact check.")

    knd_upper = str(kind or "METRIC").strip().upper()
    if knd_upper in ("DIMENSION", "ATTRIBUTE", "COL"):
        knd_upper = "DIMENSION"
    else:
        knd_upper = "METRIC"

    base_raw = (baseline_description or "").strip()
    live_raw = (live_description or "").strip()

    # Determine default question cap from configuration/env/default
    max_cap = max_questions or get_max_questions()

    # Determine baseline model version for cache key
    model_version = 0
    if project_id and view_name:
        try:
            from semabridge.governance.drift.baseline_store import get_active_baseline
            active_base = get_active_baseline(project_id, view_name)
            if active_base and isinstance(active_base, dict):
                model_version = active_base.get("version", 0)
        except Exception:
            model_version = 0

    # Caching check keyed by (project_id, view_name, model_version, object_name, baseline_description, live_description)
    cache_key = (
        project_id or "",
        view_name or "",
        model_version,
        obj_clean.casefold(),
        base_raw,
        live_raw,
    )
    if not bypass_cache and custom_questions is None and cache_key in _AI_IMPACT_CACHE:
        return copy.deepcopy(_AI_IMPACT_CACHE[cache_key])

    # Classify description change (Addition vs Removal vs Rewording)
    if not base_raw and live_raw:
        classification = "TEXT_ADDED"
    elif base_raw and not live_raw:
        classification = "TEXT_REMOVED"
    else:
        diff_info = classify_and_diff_description(base_raw, live_raw)
        classification = diff_info.get("classification") or "TEXT_REWORDED"

    # 1. Resolve active ontology - fail cleanly if not loadable (no dummy stubs)
    fed = ontology
    if fed is None:
        try:
            from semabridge.ontology.store import DomainOntologyStore
            fed = DomainOntologyStore().get_federated_ontology()
        except Exception:
            fed = None

    if fed is None:
        failure_msg = "Ontology could not be loaded; cannot run AI impact comparison."
        logger.warning(failure_msg)
        return AIImpactReport(
            project_id=project_id,
            object_name=obj_clean,
            kind=knd_upper,
            table=table,
            view_name=view_name,
            baseline_description=baseline_description,
            live_description=live_description,
            classification=classification,
            status="failed",
            question_source=None,
            questions_tested=0,
            questions_changed=0,
            questions_unchanged=0,
            max_questions_cap=max_cap,
            cap_applied=False,
            questions=[],
            overall_impact="NO_DETECTED_IMPACT",
            risk_summary=failure_msg,
            sampling_disclaimer="",
            partial_failure_reason=failure_msg,
        )

    # 2. Collect sample questions
    source_name: Optional[str] = None
    total_found: int = 0
    cap_applied = False
    failed_sources: List[str] = []

    if custom_questions is not None:
        questions = list(custom_questions)
        source_name = "custom_test_suite"
        total_found = len(questions)
        if len(questions) > max_cap:
            questions = questions[:max_cap]
            cap_applied = True
    else:
        questions, source_name, total_found, failed_sources = _collect_sample_questions(
            object_name=obj_clean,
            kind=knd_upper,
            table=table,
            project_id=project_id,
            max_cap=max_cap,
        )
        if total_found > max_cap:
            cap_applied = True

    source_label = (
        "certified queries" if source_name == "certified_queries"
        else ("BI-truth cases" if source_name == "bi_truth_cases"
              else ("recent question history" if source_name == "query_history" else "test suite"))
    )

    # 3. When there are NO questions: Say so plainly and stop
    if not questions:
        if failed_sources:
            failure_reason = f"Question source query failure: {', '.join(failed_sources)}"
            risk_summary = f"Cannot run AI impact check: question source(s) failed ({', '.join(failed_sources)})."
            return AIImpactReport(
                project_id=project_id,
                object_name=obj_clean,
                kind=knd_upper,
                table=table,
                view_name=view_name,
                baseline_description=baseline_description,
                live_description=live_description,
                classification=classification,
                status="failed",
                question_source=None,
                questions_tested=0,
                questions_changed=0,
                questions_unchanged=0,
                max_questions_cap=max_cap,
                cap_applied=False,
                questions=[],
                overall_impact="NO_DETECTED_IMPACT",
                risk_summary=risk_summary,
                sampling_disclaimer="",
                partial_failure_reason=failure_reason,
            )

        risk_summary = f"No certified queries, BI-truth cases, or query history cover '{obj_clean}'. No synthetic questions were generated."
        disclaimer = f"No questions available from certified queries, BI-truth cases, or question history for '{obj_clean}'. Zero warehouse writes performed."
        report = AIImpactReport(
            project_id=project_id,
            object_name=obj_clean,
            kind=knd_upper,
            table=table,
            view_name=view_name,
            baseline_description=baseline_description,
            live_description=live_description,
            classification=classification,
            status="no_covering_questions",
            question_source=None,
            questions_tested=0,
            questions_changed=0,
            questions_unchanged=0,
            max_questions_cap=max_cap,
            cap_applied=False,
            questions=[],
            overall_impact="NO_DETECTED_IMPACT",
            risk_summary=risk_summary,
            sampling_disclaimer=disclaimer,
        )
        if custom_questions is None:
            _AI_IMPACT_CACHE[cache_key] = report
        return report

    # 4. Create two memory-isolated ontology models with targeted overrides
    def _clone_with_desc(desc: Optional[str]) -> FederatedEnterpriseOntology:
        cloned = copy.copy(fed)
        cloned.domains = dict(fed.domains) if getattr(fed, "domains", None) else {}
        if hasattr(fed, "metrics") and fed.metrics:
            cloned.metrics = dict(fed.metrics)
        if hasattr(fed, "concepts") and fed.concepts:
            cloned.concepts = dict(fed.concepts)

        target_attr_or_metric = "ATTRIBUTE" if knd_upper == "DIMENSION" else "METRIC"
        found = False

        if target_attr_or_metric == "ATTRIBUTE":
            for d_k, dom in list(cloned.domains.items()):
                dom_copied = False
                new_concepts = []
                for c in getattr(dom, "concepts", []) or []:
                    if not table or c.name.casefold() == table.casefold() or (getattr(c, "logical_name", None) and c.logical_name.casefold() == table.casefold()):
                        c_copy = copy.copy(c)
                        c_copy.attributes = [copy.copy(a) for a in getattr(c, "attributes", []) or []]
                        for a in c_copy.attributes:
                            if a.name.casefold() == obj_clean.casefold() or (getattr(a, "logical_name", None) and a.logical_name.casefold() == obj_clean.casefold()):
                                a.description = desc
                                found = True
                        new_concepts.append(c_copy)
                        dom_copied = True
                    else:
                        new_concepts.append(c)
                if dom_copied:
                    dom_copy = copy.copy(dom)
                    dom_copy.concepts = new_concepts
                    cloned.domains[d_k] = dom_copy
        else:
            for d_k, dom in list(cloned.domains.items()):
                dom_copied = False
                new_metrics = []
                for m in getattr(dom, "metrics", []) or []:
                    if m.name.casefold() == obj_clean.casefold():
                        m_copy = copy.copy(m)
                        m_copy.description = desc
                        new_metrics.append(m_copy)
                        found = True
                        dom_copied = True
                    else:
                        new_metrics.append(m)
                if dom_copied:
                    dom_copy = copy.copy(dom)
                    dom_copy.metrics = new_metrics
                    cloned.domains[d_k] = dom_copy

            if not found and hasattr(cloned, "metrics") and cloned.metrics:
                for mk, m in list(cloned.metrics.items()):
                    if m.name.casefold() == obj_clean.casefold():
                        m_copy = copy.copy(m)
                        m_copy.description = desc
                        cloned.metrics[mk] = m_copy
                        found = True

        return cloned

    ont_base = _clone_with_desc(baseline_description)
    ont_live = _clone_with_desc(live_description)

    # Reuse existing search index from DomainOntologyStore or index once if empty,
    # then clone and override only the target object's document in sub-millisecond time.
    from semabridge.ontology.store import DomainOntologyStore
    store = DomainOntologyStore()
    shared_index = store.search_index
    if not shared_index.docs:
        shared_index.index_ontology(fed)

    target_item_type = "ATTRIBUTE" if knd_upper == "DIMENSION" else "METRIC"
    index_base = shared_index.clone()
    index_base.override_description(obj_clean, target_item_type, baseline_description, table=table)

    index_live = shared_index.clone()
    index_live.override_description(obj_clean, target_item_type, live_description, table=table)

    engine_base = NLQueryEngine(search_index=index_base, ontology=ont_base)
    engine_live = NLQueryEngine(search_index=index_live, ontology=ont_live)

    results: List[QuestionImpactResult] = []
    any_object_changed = False
    any_sql_changed = False
    any_grouping_changed = False
    any_refusal_changed = False
    partial_failure_reason: Optional[str] = None
    run_status = "completed"

    # 5. Run each question twice
    for q in questions:
        try:
            plan_base = engine_base.resolve_query(q, semantic_view=view_name)
            plan_live = engine_live.resolve_query(q, semantic_view=view_name)
        except Exception as ex:
            logger.warning("Query resolution failed mid-run on '%s': %s", q, ex)
            partial_failure_reason = f"Execution interrupted on question '{q}': {ex}"
            run_status = "partial" if results else "failed"
            break

        base_m = plan_base.detected_metrics[0] if plan_base.detected_metrics else None
        live_m = plan_live.detected_metrics[0] if plan_live.detected_metrics else None
        base_d = plan_base.detected_dimensions[0] if plan_base.detected_dimensions else None
        live_d = plan_live.detected_dimensions[0] if plan_live.detected_dimensions else None

        if knd_upper == "DIMENSION":
            base_obj = base_d
            live_obj = live_d
            obj_changed = (base_d != live_d)
        else:
            base_obj = base_m
            live_obj = live_m
            obj_changed = (base_m != live_m)

        base_sql = (plan_base.generated_sql or plan_base.sql or "").strip()
        live_sql = (plan_live.generated_sql or plan_live.sql or "").strip()
        sql_changed = (base_sql != live_sql)
        sql_diff_str: Optional[str] = None
        if sql_changed and base_sql and live_sql:
            diff_lines = list(difflib.unified_diff(
                base_sql.splitlines(),
                live_sql.splitlines(),
                fromfile="baseline_sql",
                tofile="live_sql",
                lineterm="",
            ))
            sql_diff_str = "\n".join(diff_lines)

        base_dims = sorted(plan_base.detected_dimensions)
        live_dims = sorted(plan_live.detected_dimensions)
        base_filts = sorted(plan_base.filters)
        live_filts = sorted(plan_live.filters)
        g_changed = (base_dims != live_dims or base_filts != live_filts)

        base_refused = bool(plan_base.refused or not getattr(plan_base, "answerable", True) or not base_sql)
        live_refused = bool(plan_live.refused or not getattr(plan_live, "answerable", True) or not live_sql)
        r_changed = (bool(plan_base.refused) != bool(plan_live.refused))

        is_double_refusal = base_refused and live_refused
        is_unresolvable = (not base_sql and not live_sql and not base_obj and not live_obj)
        is_not_comparable = is_double_refusal or is_unresolvable

        diff_details: List[str] = []

        if is_not_comparable:
            q_status = "NOT_COMPARABLE"
            if plan_base.reason_text or plan_live.reason_text:
                reason = plan_base.reason_text or plan_live.reason_text
                q_summary = f"Not comparable: question refused or unanswerable on both baseline and live ({reason})."
            elif not base_sql and not live_sql:
                q_summary = "Not comparable: no compiled SQL expression generated on either baseline or live."
            else:
                q_summary = "Not comparable: question could not be resolved on either baseline or live."
            diff_details.append(q_summary)
        else:
            is_changed = obj_changed or sql_changed or g_changed or r_changed
            if obj_changed:
                any_object_changed = True
            if sql_changed:
                any_sql_changed = True
            if g_changed:
                any_grouping_changed = True
            if r_changed:
                any_refusal_changed = True

            if obj_changed:
                diff_details.append(f"Chosen object shifted from '{base_obj or 'None'}' to '{live_obj or 'None'}'")
            if sql_changed:
                diff_details.append("Generated SQL changed")
            if g_changed:
                diff_details.append("Grouping or filtering changed")
            if r_changed:
                if live_refused and not base_refused:
                    diff_details.append(f"Query refused under live comment ({plan_live.reason_text or 'Refused'})")
                else:
                    diff_details.append("Refusal disappeared under live comment")

            q_status = "CHANGED" if is_changed else "UNCHANGED"
            q_summary = "; ".join(diff_details) if diff_details else f"Unchanged: resolves to '{live_obj or base_obj or obj_clean}' with identical SQL."

        results.append(
            QuestionImpactResult(
                question=q,
                status=q_status,
                baseline_object=base_obj,
                live_object=live_obj,
                object_choice_changed=obj_changed if not is_not_comparable else False,
                baseline_sql=base_sql,
                live_sql=live_sql,
                sql_changed=sql_changed if not is_not_comparable else False,
                sql_diff=sql_diff_str if not is_not_comparable else None,
                baseline_groupings=base_dims,
                live_groupings=live_dims,
                baseline_filters=base_filts,
                live_filters=live_filts,
                grouping_or_filter_changed=g_changed if not is_not_comparable else False,
                baseline_refused=base_refused,
                live_refused=live_refused,
                refusal_changed=r_changed if not is_not_comparable else False,
                baseline_refusal_reason=plan_base.reason_text if base_refused else None,
                live_refusal_reason=plan_live.reason_text if live_refused else None,
                difference_details=diff_details,
                summary=q_summary,
            )
        )

    # 6. Overall Impact & Risk Formulation
    changed_count = sum(1 for r in results if r.status == "CHANGED")
    unchanged_count = sum(1 for r in results if r.status == "UNCHANGED")
    not_comparable_count = sum(1 for r in results if r.status == "NOT_COMPARABLE")
    compared_count = changed_count + unchanged_count
    sample_is_weak = (not_comparable_count > compared_count) or (compared_count == 0 and len(results) > 0)

    if any_object_changed or any_refusal_changed:
        overall_impact = "HIGH_IMPACT"
    elif any_sql_changed or any_grouping_changed:
        overall_impact = "MEDIUM_IMPACT"
    else:
        overall_impact = "NO_DETECTED_IMPACT"

    if classification == "TEXT_REMOVED":
        base_risk = (
            f"Removal risk: Guidance and constraints for '{obj_clean}' that the question engine previously relied on "
            "have been removed, which may cause inaccurate object routing or missing filters."
        )
    elif classification == "TEXT_ADDED":
        base_risk = (
            f"Addition risk: The question engine now has description guidance for '{obj_clean}' that it did not have before, "
            "which may pull questions toward this object that previously went elsewhere."
        )
    else:
        base_risk = (
            f"Rewording risk: Updated phrasing for '{obj_clean}' alters semantic similarity ranking and may change "
            "query routing or grouping decisions."
        )

    if sample_is_weak:
        risk_summary = (
            f"Sample is weak: {not_comparable_count} of {len(results)} question(s) were not comparable "
            f"(both baseline and live refused or could not resolve). {base_risk}"
        )
        disclaimer = (
            f"This check is based on sampled evidence from {source_label} ({len(results)} question(s) tested), "
            f"but only {compared_count} could be compared ({not_comparable_count} not comparable due to refusal/unresolvable status). "
            f"A result of {overall_impact.replace('_', ' ')} based on a weak sample is not conclusive. Zero warehouse writes performed."
        )
    else:
        risk_summary = base_risk
        disclaimer = (
            f"This check is based on sampled evidence from {source_label} ({compared_count} question(s) compared, "
            f"{not_comparable_count} not comparable), so an unchanged result is not a guarantee that no other questions are affected. "
            "Zero warehouse writes performed."
        )

    if failed_sources and not partial_failure_reason:
        partial_failure_reason = f"Some question sources failed to query: {', '.join(failed_sources)}"

    report = AIImpactReport(
        project_id=project_id,
        object_name=obj_clean,
        kind=knd_upper,
        table=table,
        view_name=view_name,
        baseline_description=baseline_description,
        live_description=live_description,
        classification=classification,
        status=run_status,
        question_source=source_name,
        questions_tested=len(results),
        questions_compared=compared_count,
        questions_changed=changed_count,
        questions_unchanged=unchanged_count,
        questions_not_comparable=not_comparable_count,
        sample_is_weak=sample_is_weak,
        max_questions_cap=max_cap,
        cap_applied=cap_applied,
        questions=results,
        any_object_changed=any_object_changed,
        any_sql_changed=any_sql_changed,
        any_grouping_or_filter_changed=any_grouping_changed,
        any_refusal_changed=any_refusal_changed,
        overall_impact=overall_impact,
        risk_summary=risk_summary,
        sampling_disclaimer=disclaimer,
        partial_failure_reason=partial_failure_reason,
    )

    if run_status == "completed" and custom_questions is None:
        _AI_IMPACT_CACHE[cache_key] = report

    return report
