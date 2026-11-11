"""Natural Language to Semantic Query & SQL Engine.

Interprets analytical questions in plain English, resolves them against governed
domain ontologies, determines optimal join paths, and generates executable SQL.
Operates completely locally with zero Snowflake credit consumption.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.ai.semantic_search import SearchHit, SemanticSearchIndex, _stem_token, _tokenize as _ss_tokenize
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainRelationship,
    FederatedEnterpriseOntology,
)

logger = logging.getLogger("semabridge.ai.nl_query_engine")

_NUMERIC_TYPES = frozenset({
    "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT",
    "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "NUMBER", "REAL",
})

_DAX_INVALID_RE = re.compile(
    r"\b(CALCULATE|CALCULATETABLE|COUNTROWS|DIVIDE|SUMX|AVERAGEX|MINX|MAXX|"
    r"SELECTEDVALUE|ALLEXCEPT|ALLSELECTED|DATEADD|SAMEPERIODLASTYEAR|"
    r"DATESYTD|TOTALYTD|RELATED|USERELATIONSHIP|HASONEVALUE)\s*\("
    r"|\bVAR\s+\w+\s*=|\bRETURN\b|'[^']+'\[[^\]]+\]|#\s*Measures\b",
    re.I,
)


class QueryResolutionPlan(BaseModel):
    """Execution plan generated from a natural language query."""
    original_prompt: str
    detected_metrics: List[str]
    detected_dimensions: List[str]
    filters: List[str] = Field(default_factory=list)
    applied_policies: List[str] = Field(default_factory=list)
    derived_from_metrics: List[str] = Field(default_factory=list)
    resolved_tables: List[str]
    join_clauses: List[str] = Field(default_factory=list)
    generated_sql: str
    confidence_score: float
    explanation: str
    warnings: List[str] = Field(default_factory=list)


class NLQueryEngine:
    """Interprets natural language questions and compiles them into governed SQL."""

    def __init__(self, search_index: SemanticSearchIndex, ontology: FederatedEnterpriseOntology):
        self.search_index = search_index
        self.ontology = ontology

    @staticmethod
    def _is_numeric_attr(attr: DomainAttribute) -> bool:
        base = (attr.data_type or "").split("(")[0].strip().upper()
        return base in _NUMERIC_TYPES

    @staticmethod
    def _q_ident(ident: str) -> str:
        s = str(ident or "").strip()
        return f'"{s}"' if re.search(r"[^A-Za-z0-9_]", s) else s

    @staticmethod
    def _to_logical_id(raw: str) -> str:
        from semabridge.utils.identifiers import IdentifierSanitizer
        return IdentifierSanitizer().to_alias(str(raw or "").strip() or "COL")

    # Stem tokens generically so singular and plural forms match across prompts, metrics, and dimensions
    @staticmethod
    def _stem_words(text: str) -> Set[str]:
        """Extract stemmed alphanumeric tokens so singular and plural words match generically."""
        return {_stem_token(w) for w in re.findall(r"[a-z0-9]+", str(text or "").casefold())}

    # Identify COUNT metrics strictly from SQL/aggregation or non-additive definitions (never from 'count' in the name)
    @staticmethod
    def _is_sql_count_metric(m: DomainMetric) -> bool:
        """Return True when the metric's own aggregation/SQL defines a count (not inferred from name)."""
        m_text = f"{m.name} {m.label or ''}"
        if re.search(r"(%|_pct|\bpct\b|percent|ratio|share|\brate\b|\bmom|\byoy|\bqoq)", m_text, re.I):
            return False
        agg = (getattr(m, "aggregation_type", None) or "").strip().upper()
        if agg in {"COUNT", "COUNT_DISTINCT", "DISTINCTCOUNT", "DISTINCT_COUNT"}:
            return True
        sx = (m.sql_expression or "").strip()
        if sx:
            # Exclude ratio/rate formulas that divide aggregates (e.g. COUNT(a) / NULLIF(COUNT(b), 0))
            if re.search(r"/\s*(?:NULLIF|COALESCE|IFF|\()?\s*(?:[A-Za-z0-9_.\"]*\s*)*\b(?:SUM|COUNT|AVG)\b", sx, re.I):
                return False
            if re.search(r"\b(?:COUNT|COUNT_IF|APPROX_COUNT_DISTINCT|DISTINCTCOUNT|COUNTROWS|COUNTA|COUNTX)\s*\(", sx, re.I):
                return True
        # Non-additive count metrics without compiled SQL may still expose a count definition via DAX
        dax = (getattr(m, "dax_expression", None) or "").strip()
        dr = (getattr(m, "deploy_reason", None) or "").strip()
        if dax and ("non-additive" in dr.lower() or "non additive" in dr.lower()):
            if "/" not in dax and re.search(r"\b(?:COUNT|COUNTA|COUNTX|DISTINCTCOUNT|COUNTROWS)\s*\(", dax, re.I):
                return True
        return False

    # Block uncompiled/DAX metrics everywhere, and block window/non-additive/cross-metric refs on raw tables
    @classmethod
    def _metric_invalid_reason(
        cls,
        m: DomainMetric,
        deploy_error: Optional[str] = None,
        using_semantic_view: bool = False,
        metrics_by_key: Optional[Dict[str, DomainMetric]] = None,
        physical_attr_keys: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Return a deploy/translation/path reason if metric lacks valid executable SQL on the active path."""
        if getattr(m, "deploy_reason", None):
            return m.deploy_reason
        sx = (m.sql_expression or "").strip()
        if not sx:
            return deploy_error or "no compiled SQL expression in the ontology"
        if re.match(r"^(?:CAST\s*\(\s*NULL\b|NULL\b)", sx, re.I):
            return deploy_error or "compiled as a NULL placeholder during deployment"
        if _DAX_INVALID_RE.search(sx):
            return deploy_error or "expression contains untranslated DAX or unresolved placeholders"

        if not using_semantic_view:
            agg = (getattr(m, "aggregation_type", None) or "").strip().upper()
            expanded_sx = (
                cls._expand_metric_expression(m, metrics_by_key)
                if metrics_by_key
                else sx
            )
            if agg in {"NON_ADDITIVE", "NONADDITIVE", "SNAPSHOT"} or re.search(
                r"\b(?:NON\s+ADDITIVE|PARTITION\s+BY\s+EXCLUDING)\b", expanded_sx, re.I
            ):
                return "non-additive/snapshot metrics can only be queried through a deployed SEMANTIC_VIEW"
            if re.search(r"\bOVER\s*\(", expanded_sx, re.I):
                return "window metrics can only be queried through a deployed SEMANTIC_VIEW"
            if re.search(r"\[[^\]]+\]", expanded_sx):
                return "expression contains unexpanded [Metric] references that cannot run on raw tables"
            attr_keys = physical_attr_keys or set()
            m_keys = metrics_by_key or {}
            for q_tbl, u_tbl, q_col, u_col in re.findall(
                r'(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))',
                expanded_sx,
            ):
                col_ref = (q_col or u_col or "").strip()
                if not col_ref:
                    continue
                col_cf = col_ref.casefold()
                col_id_cf = cls._to_logical_id(col_ref).casefold()
                if col_cf not in attr_keys and col_id_cf not in attr_keys:
                    if col_cf in m_keys or col_id_cf in m_keys:
                        tbl_ref = (q_tbl or u_tbl or "").strip()
                        return (
                            f"expression references semantic metric '{tbl_ref}.{col_ref}', "
                            f"which is only executable via a deployed SEMANTIC_VIEW"
                        )
        return None

    def resolve_query(
        self,
        prompt: str,
        dialect: str = "SNOWFLAKE",
        semantic_view: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
        deployment_error: Optional[str] = None,
    ) -> QueryResolutionPlan:
        """Parse natural language prompt, find matching metrics/dimensions, and compile SQL."""
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        clean_prompt = prompt.strip()

        # Strip trailing grouping/time clauses (e.g. 'by <dim>', 'each <dim>', 'in 2024') when searching for METRICS
        # so dimension tokens do not cause derived metrics containing that dimension name to outrank base metrics.
        metric_search_query = re.sub(
            r"\b(?:by|for\s+each|each|per|grouped\s+by)\s+[a-zA-Z0-9_\s,]+?(?:\s+(?:in|for|where|with|during|from)|\.|;|\?|$)",
            " ",
            clean_prompt,
            flags=re.I,
        )
        metric_search_query = re.sub(r"\bin\s+(?:20[12][0-9]|Q[1-4])\b", " ", metric_search_query, flags=re.I).strip(" ?,.;:")
        # Only fall back to full prompt when stripping removed everything (e.g. prompt was just 'by product')
        if not metric_search_query:
            metric_search_query = clean_prompt

        # 1. Search for matching metrics (using stripped metric_search_query so 'by <dim>' doesn't skew metric rank)
        domain_filter = allowed_domains[0] if allowed_domains and len(allowed_domains) == 1 else None
        metric_hits = self.search_index.search(
            metric_search_query,
            domain_filter=domain_filter,
            item_type_filter="METRIC",
            limit=12,
            min_score=0.10,
        )

        # 2. Search for matching attributes (columns) and concepts (tables)
        attr_hits = self.search_index.search(
            clean_prompt,
            domain_filter=domain_filter,
            item_type_filter="ATTRIBUTE",
            limit=8,
            min_score=0.15,
        )
        concept_hits = self.search_index.search(
            clean_prompt,
            domain_filter=domain_filter,
            item_type_filter="CONCEPT",
            limit=8,
            min_score=0.15,
        )

        # Build quick lookups by name, label, and synonyms
        metric_lookup: Dict[str, DomainMetric] = {}
        for m in self.ontology.metrics.values():
            if allowed_domains and m.domain and m.domain not in allowed_domains:
                continue
            metric_lookup[m.name.casefold()] = m
            if "_" in m.name:
                metric_lookup[m.name.replace("_", " ").casefold()] = m
            if m.label:
                metric_lookup[m.label.casefold()] = m
            for syn in m.synonyms:
                metric_lookup[syn.casefold()] = m

        concept_lookup: Dict[str, DomainConcept] = {}
        for c in self.ontology.concepts.values():
            if allowed_domains and c.domain and c.domain not in allowed_domains:
                continue
            concept_lookup[c.name.casefold()] = c
            if getattr(c, "label", None):
                concept_lookup[c.label.casefold()] = c
            for syn in c.synonyms:
                concept_lookup[syn.casefold()] = c

        # Filter hits to allowed_domains before scoring/ranking so external domain hits do not skew top_score
        if allowed_domains:
            metric_hits = [h for h in metric_hits if not h.domain or h.domain in allowed_domains]
            attr_hits = [h for h in attr_hits if not h.domain or h.domain in allowed_domains]
            concept_hits = [h for h in concept_hits if not h.domain or h.domain in allowed_domains]

        # Generic intent classifiers (no domain-specific vocabulary)
        asks_for_rate_or_pct = bool(
            re.search(
                r"(%|percent|percentage|pct|ratio|share|proportion|rate)",
                clean_prompt,
                re.I,
            )
        )
        asks_for_time_intel = bool(
            re.search(
                r"\b(ytd|qtd|mtd|py|pm|pq|sply|mat|mom|yoy|qoq|year[- ]?to[- ]?date|quarter[- ]?to[- ]?date|month[- ]?to[- ]?date|previous\s+(?:year|month|quarter)|same\s+period\s+last\s+year|moving\s+annual\s+total|month[-\s]+over[-\s]+month|year[-\s]+over[-\s]+year|quarter[-\s]+over[-\s]+quarter|change|growth|variance)\b",
                clean_prompt,
                re.I,
            )
        )
        asks_for_count = bool(re.search(r"\b(how many|number of|count of)\b", clean_prompt, re.I))
        # Check multi-metric intent on metric_search_query so 'by X and Y' dimension conjunctions don't multiply metrics
        has_multi_intent = bool(re.search(r"\b(and|vs|versus|compare|comparison|compared|both|all|with)\b|,", metric_search_query, re.I))

        q_norm_tokens = set(_ss_tokenize(clean_prompt))
        _TIME_SHIFTS = {"ytd", "qtd", "mtd", "py", "pm", "pq", "sply", "mat", "mom", "yoy", "qoq"}
        q_requested_shifts = q_norm_tokens & _TIME_SHIFTS

        # Re-rank metric hits cleanly based on structural intent:
        # - Penalize derived percentage/ratio/time-shift metrics when the prompt did not ask for them.
        # - Boost count, percentage, or requested time-intelligence metrics when explicitly asked.
        reranked_hits: List[tuple[float, Any, DomainMetric]] = []
        for hit in metric_hits:
            m = metric_lookup.get(hit.name.casefold()) or self.ontology.metrics.get(hit.name)
            if not m:
                continue
            if allowed_domains and m.domain and m.domain not in allowed_domains:
                continue
            adj_score = hit.score
            m_text = f"{m.name} {m.label or ''}".casefold()
            m_norm_toks = set(_ss_tokenize(f"{m.name} {m.label or ''}"))
            m_shifts = m_norm_toks & _TIME_SHIFTS
            is_pct_metric = bool(re.search(r"(%|_pct|\bpct\b|pctof|percent|ratio|share|\brate\b|\bmom|\byoy|\bqoq)", m_text, re.I))
            is_time_metric = bool(m_shifts) or bool(re.search(r"(\bcm\b|_cm\b)", m_text, re.I))
            is_count_metric = self._is_sql_count_metric(m)
            if is_time_metric and not asks_for_time_intel:
                adj_score *= 0.45
            elif is_time_metric and asks_for_time_intel:
                # Only boost if this metric matches the specifically requested shift (e.g. py vs pm),
                # or if query used general change/growth/variance words without naming a specific shift.
                if (m_shifts & q_requested_shifts) or (not q_requested_shifts):
                    adj_score += 0.25
                else:
                    adj_score *= 0.55
            if is_pct_metric and not asks_for_rate_or_pct and not (m_shifts & q_requested_shifts):
                adj_score *= 0.40
            elif is_pct_metric and asks_for_rate_or_pct and not (is_time_metric and not asks_for_time_intel):
                adj_score += 0.25
            if asks_for_count and is_count_metric:
                adj_score += 0.25
            reranked_hits.append((adj_score, hit, m))

        # Reject modifier-only / cross-subject false positives (e.g. 'average salary' matching 'Avg Age' on 'avg',
        # or 'new hires SPLY' pulling 'Empcount Sply' solely on 'sply'):
        # require at least one non-stopword/non-modifier subject token (or 3-char prefix) overlap when below 0.45.
        _STOP_OR_MOD = {
            "what", "is", "are", "the", "of", "in", "for", "a", "an", "show", "get", "give",
            "me", "how", "many", "did", "we", "have", "our", "does", "each", "was", "were",
            "avg", "average", "sum", "count", "total", "min", "max", "pct", "percent", "percentage", "rate",
            "ytd", "qtd", "mtd", "py", "pm", "pq", "sply", "mat", "mom", "yoy", "qoq",
            "year", "month", "quarter", "date", "previous", "same", "period", "last", "this",
            "compared", "compare", "vs", "versus", "change", "growth", "variance", "moving", "annual", "to",
        }
        q_subject_toks = [
            _stem_token(w) for w in re.findall(r"[a-z0-9]+", metric_search_query.casefold())
            if w not in _STOP_OR_MOD and _stem_token(w) not in _STOP_OR_MOD and len(w) >= 2
        ]
        if q_subject_toks:
            q_subj_set = set(q_subject_toks)
            filtered_reranked = []
            for adj_score, hit, m in reranked_hits:
                m_corpus = f"{m.name} {m.label or ''} {' '.join(m.synonyms or [])}"
                m_toks = self._stem_words(m_corpus)
                shares_subject = any(
                    qt in mt or mt in qt or (len(qt) >= 3 and len(mt) >= 3 and qt[:3] == mt[:3])
                    for qt in q_subject_toks for mt in m_toks
                )
                # Concept boost removed per false match analysis
                if shares_subject or hit.score >= 0.45:
                    filtered_reranked.append((adj_score, hit, m))
            reranked_hits = filtered_reranked

        reranked_hits.sort(key=lambda x: x[0], reverse=True)

        detected_metrics: List[DomainMetric] = []
        seen_m = set()
        ambiguous_alternatives: List[str] = []

        prompt_tokens = self._stem_words(clean_prompt)
        if reranked_hits:
            top_adj_score, top_hit, top_m = reranked_hits[0]
            top_hit_overlap = bool(self._stem_words(f"{top_hit.name} {top_m.label or ''}") & prompt_tokens)
            if top_adj_score >= 0.20:
                if has_multi_intent:
                    # Explicit comparison/multi-metric intent: retain up to 3 top matching metrics
                    for adj_score, hit, m in reranked_hits:
                        if adj_score < top_adj_score - 0.20 and adj_score < 0.35:
                            continue
                        if m.name not in seen_m:
                            seen_m.add(m.name)
                            detected_metrics.append(m)
                            if len(detected_metrics) >= 3:
                                break
                else:
                    # Singular intent: select ONLY the top candidate; record close runners-up as ambiguity warnings
                    seen_m.add(top_m.name)
                    detected_metrics.append(top_m)
                    for adj_score, hit, m in reranked_hits[1:]:
                        if m.name in seen_m:
                            continue
                        if top_m.domain and m.domain and m.domain.casefold() != top_m.domain.casefold():
                            continue
                        runner_overlap = bool(self._stem_words(f"{hit.name} {m.label or ''}") & prompt_tokens)
                        if runner_overlap and (top_adj_score - adj_score <= 0.10):
                            ambiguous_alternatives.append(m.label or m.name)

        # Count-intent entity recovery:
        # Handles 'how many <entity>' / 'number of <entity>' using SQL/non-additive COUNT metrics,
        # only when no count metric or subject-matching metric has been selected yet.
        has_selected_count = any(self._is_sql_count_metric(m) for m in detected_metrics)
        has_direct_subject_match = any(
            bool(self._stem_words(f"{m.name} {m.label or ''} {' '.join(m.synonyms or [])}") & set(q_subject_toks))
            for m in detected_metrics
        )
        if asks_for_count and not has_selected_count and (not detected_metrics or not has_direct_subject_match):
            count_match = re.search(
                r"\b(?:how many|number of)\s+(?:are there\s+)?([a-zA-Z0-9_]+)\b",
                clean_prompt,
                re.I,
            )
            if count_match:
                target_entity = count_match.group(1).casefold()
                target_stem = _stem_token(target_entity)
                name_matched_cnt: Optional[DomainMetric] = None
                concept_matched_cnt: Optional[DomainMetric] = None
                for m in self.ontology.metrics.values():
                    if allowed_domains and m.domain and m.domain not in allowed_domains:
                        continue
                    if not self._is_sql_count_metric(m):
                        continue
                    m_stems = self._stem_words(f"{m.name} {m.label or ''} {' '.join(m.synonyms or [])}")
                    if target_stem in m_stems or any(len(ms) >= 3 and target_stem.startswith(ms) for ms in m_stems):
                        name_matched_cnt = m
                        break
                    c_stem = _stem_token((m.concept or "").casefold())
                    if c_stem == target_stem and not concept_matched_cnt:
                        sx_upper = (m.sql_expression or "").upper()
                        dax_upper = (getattr(m, "dax_expression", None) or "").upper()
                        # Skip conditional event-subset counts when falling back purely on concept name
                        has_event_filter = (
                            "CASE WHEN" in sx_upper
                            or bool(re.search(r"\bFILTER\s*\(\s*(?!ALL\b)", dax_upper))
                        )
                        if not has_event_filter:
                            concept_matched_cnt = m
                recovered_cnt = name_matched_cnt or concept_matched_cnt
                if recovered_cnt:
                    if not asks_for_rate_or_pct:
                        detected_metrics = [recovered_cnt]
                        seen_m = {recovered_cnt.name}
                        ambiguous_alternatives.clear()
                    elif recovered_cnt.name not in seen_m:
                        detected_metrics.insert(0, recovered_cnt)
                        seen_m.add(recovered_cnt.name)

        detected_concepts: List[DomainConcept] = []
        seen_c = set()
        for hit in concept_hits:
            c = concept_lookup.get(hit.name.casefold()) or self.ontology.concepts.get(hit.name)
            if c and c.name not in seen_c:
                if allowed_domains and c.domain and c.domain not in allowed_domains:
                    continue
                seen_c.add(c.name)
                detected_concepts.append(c)

        # Intent keyword detection:
        # Extracts modifiers that change aggregation or result limits (unique/distinct, average, top N)
        is_unique = bool(re.search(r"\b(unique|distinct|how many distinct|number of unique)\b", clean_prompt, re.I))
        is_average = bool(re.search(r"\b(average|avg|mean)\b", clean_prompt, re.I))
        top_n_match = re.search(r"\btop\s+(\d+)\b", clean_prompt, re.I)
        limit_val = int(top_n_match.group(1)) if top_n_match else 100

        # Unmatched metric handling & suggestions:
        # If no metric matched the question, do NOT hallucinate random concepts or report high confidence.
        # Instead, search for the closest metric candidates and append helpful suggestions to warnings.
        metric_suggestions: List[str] = []
        if not detected_metrics:
            broad_metric_hits = self.search_index.search(
                clean_prompt,
                domain_filter=domain_filter,
                item_type_filter="METRIC",
                limit=3,
                min_score=0.0,
            )
            for h in broad_metric_hits:
                if allowed_domains and h.domain and h.domain not in allowed_domains:
                    continue
                m_obj = metric_lookup.get(h.name.casefold()) or self.ontology.metrics.get(h.name)
                sug_name = m_obj.label if (m_obj and m_obj.label) else h.name
                if sug_name not in metric_suggestions:
                    metric_suggestions.append(sug_name)

        # Identify primary domain and deployment metadata BEFORE validating metric SQL and resolving dimensions
        target_domain = None
        if detected_metrics:
            target_domain = detected_metrics[0].domain
        elif detected_concepts:
            target_domain = detected_concepts[0].domain
        elif attr_hits:
            target_domain = attr_hits[0].domain

        target_dom_obj = next(
            (d for d in self.ontology.domains.values() if target_domain and d.domain.casefold() == target_domain.casefold()),
            None,
        )
        effective_deploy_error = deployment_error or (getattr(target_dom_obj, "deployment_error", None) if target_dom_obj else None)

        # Determine active SEMANTIC_VIEW target and collect domain metrics/physical columns to validate raw-table execution
        effective_semantic_view = semantic_view
        if dialect.upper() == "SNOWFLAKE" and not effective_semantic_view:
            if detected_metrics:
                effective_semantic_view = getattr(detected_metrics[0], "semantic_view_name", None)
            if not effective_semantic_view and target_dom_obj:
                effective_semantic_view = getattr(target_dom_obj, "semantic_view_name", None)

        domain_metrics_by_key: Dict[str, DomainMetric] = {}
        for km, vm in self.ontology.metrics.items():
            if target_domain and vm.domain and vm.domain.casefold() != target_domain.casefold():
                continue
            for k_opt in filter(None, [km, vm.name, vm.label, getattr(vm, "logical_name", None)]):
                domain_metrics_by_key[k_opt.casefold()] = vm
                domain_metrics_by_key[self._to_logical_id(k_opt).casefold()] = vm

        concepts_for_check: List[DomainConcept] = (
            list(target_dom_obj.concepts)
            if target_dom_obj
            else [
                c for c in self.ontology.concepts.values()
                if not target_domain or not c.domain or c.domain.casefold() == target_domain.casefold()
            ]
        )
        physical_attr_keys: Set[str] = set()
        for c_chk in concepts_for_check:
            for a_chk in c_chk.attributes:
                for a_opt in filter(None, [a_chk.name, getattr(a_chk, "logical_name", None)]):
                    physical_attr_keys.add(a_opt.casefold())
                    physical_attr_keys.add(self._to_logical_id(a_opt).casefold())
                for m_ref in getattr(a_chk, "maps_to", None) or []:
                    col_part = str(m_ref).split(".")[-1].strip()
                    if col_part:
                        physical_attr_keys.add(col_part.casefold())
                        physical_attr_keys.add(self._to_logical_id(col_part).casefold())

        # Reject metrics without valid SQL (DAX, NULL placeholder, unresolved refs) or raw-table-incompatible logic
        unanswerable_warnings: List[str] = []
        answerable_metrics: List[DomainMetric] = []
        matched_before_sql_check = list(detected_metrics)
        for m in detected_metrics:
            m_sv = effective_semantic_view or (
                getattr(m, "semantic_view_name", None) if dialect.upper() == "SNOWFLAKE" else None
            )
            bad_reason = self._metric_invalid_reason(
                m,
                effective_deploy_error,
                using_semantic_view=bool(m_sv),
                metrics_by_key=domain_metrics_by_key,
                physical_attr_keys=physical_attr_keys,
            )
            if bad_reason:
                unanswerable_warnings.append(
                    f"Metric '{m.label or m.name}' is not answerable: {bad_reason}."
                )
            else:
                answerable_metrics.append(m)
        detected_metrics = answerable_metrics
        unanswerable_concepts: List[str] = [
            m.concept for m in matched_before_sql_check
            if m not in detected_metrics and m.concept
        ]

        # Extract potential time filters and build warnings after checking metric SQL validity
        filters: List[str] = []
        applied_policies: List[str] = []
        derived_from_metrics: List[str] = []
        warnings: List[str] = list(unanswerable_warnings)
        if not detected_metrics and not matched_before_sql_check:
            sug_text = f" Did you mean: {', '.join(metric_suggestions)}?" if metric_suggestions else ""
            warnings.append(f"No metric found matching '{clean_prompt}'.{sug_text}")
        elif detected_metrics and ambiguous_alternatives:
            valid_alts = [
                a for a in ambiguous_alternatives
                if not any(u for u in unanswerable_warnings if f"'{a}'" in u)
            ]
            if valid_alts:
                top_disp = detected_metrics[0].label or detected_metrics[0].name
                warnings.append(
                    f"Multiple metrics match your query ({top_disp}, {', '.join(valid_alts)}). "
                    f"Selected '{top_disp}'; specify another or ask to compare them if needed."
                )

        # Resolve upstream derived metrics and collect enforceable SQL policies & filter expressions
        metrics_by_key: Dict[str, DomainMetric] = {
            k.casefold(): v for k, v in self.ontology.metrics.items()
        }
        for m in detected_metrics:
            for dep in getattr(m, "derives_from", None) or []:
                dep_m = metrics_by_key.get(dep.casefold())
                dep_name = dep_m.name if dep_m else dep
                if dep_name not in derived_from_metrics:
                    derived_from_metrics.append(dep_name)

        scoped_policy_concepts = [
            c for c in detected_concepts
            if not target_domain or not c.domain or c.domain.casefold() == target_domain.casefold()
        ]
        self._collect_policy_filters(
            detected_concepts=scoped_policy_concepts,
            detected_metrics=detected_metrics,
            metrics_by_key=metrics_by_key,
            filters=filters,
            applied_policies=applied_policies,
        )

        # Use only deployment-record semantic_view (passed from router or stored on ontology item);
        # never derive a view name from the domain display name.
        if dialect.upper() == "SNOWFLAKE" and not semantic_view and detected_metrics:
            sv_candidate = getattr(detected_metrics[0], "semantic_view_name", None)
            if not sv_candidate and target_dom_obj:
                sv_candidate = getattr(target_dom_obj, "semantic_view_name", None)
            if sv_candidate:
                semantic_view = sv_candidate

        # Detect dimensions from prompt:
        # Only select attributes whose full name or component tokens are explicitly mentioned in the user prompt,
        # scoped to target_domain when known, avoiding dumping irrelevant indexed attributes or foreign keys.
        by_match = re.search(r"\b(?:by|each|per)\s+([a-zA-Z0-9_\s,]+?)(?:\s+(?:in|for|where|with|during|from)|\.|;|\?|$)", clean_prompt, re.I)
        by_target = by_match.group(1).strip() if by_match else None
        # In 'top N <entity> by <metric>' (e.g. 'Top 3 stores by sales'), if post-'by' names a metric, group by <entity> instead
        top_n_by_match = re.search(
            r"\btop\s+\d+\s+([a-zA-Z0-9_\s]+?)\s+by\s+([a-zA-Z0-9_\s]+?)(?:\s+(?:in|for|where|with|during|from)|\.|;|\?|$)",
            clean_prompt,
            re.I,
        )
        if top_n_by_match and by_target:
            rank_phrase = top_n_by_match.group(2).strip().casefold()
            if any(rank_phrase in (m.name or "").casefold() or rank_phrase in (m.label or "").casefold() for m in detected_metrics):
                by_target = top_n_by_match.group(1).strip()
        bt_lower = by_target.casefold() if by_target else ""

        # Gather target domain's full concept list from self.ontology.domains so shared names
        # (e.g. Product, Customer, Date across multiple models) are never shadowed by first-wins federation.
        domain_concepts: List[DomainConcept] = []
        if target_domain:
            dom_entry = next(
                (d for d in self.ontology.domains.values() if d.domain.casefold() == target_domain.casefold()),
                None,
            )
            if dom_entry:
                domain_concepts = list(dom_entry.concepts)
        if not domain_concepts and allowed_domains:
            allowed_cf = {ad.casefold() for ad in allowed_domains}
            for dom_entry in self.ontology.domains.values():
                if dom_entry.domain.casefold() in allowed_cf or (dom_entry.id and dom_entry.id.casefold() in allowed_cf):
                    domain_concepts.extend(dom_entry.concepts)

        scoped_concepts: List[DomainConcept] = list(domain_concepts)
        if not scoped_concepts and (target_domain or allowed_domains):
            scoped_concepts = [
                c for c in self.ontology.concepts.values()
                if (not target_domain or not c.domain or c.domain.casefold() == target_domain.casefold())
                and (not allowed_domains or not c.domain or c.domain in allowed_domains)
            ]

        non_numeric_attr_names: Set[str] = {
            a.name.casefold()
            for c in scoped_concepts
            for a in c.attributes
            if not self._is_numeric_attr(a)
        }

        # Strip multi-word time-intelligence phrases when scanning for dimension attributes so
        # e.g. 'year-to-date' or 'month-to-date' do not falsely trigger 'Year' or 'Date' dimensions.
        prompt_for_dims = re.sub(
            r"\b(?:\w+[- ]?to[- ]?date|\w+[-\s]+over[-\s]+\w+|previous\s+\w+|same\s+period\s+last\s+\w+|moving\s+annual\s+total|last\s+\d+\s+\w+)\b",
            " ",
            clean_prompt,
            flags=re.I,
        )
        prompt_words = self._stem_words(prompt_for_dims)
        dim_names: List[str] = []
        for h in attr_hits:
            if target_domain and h.domain and h.domain.casefold() != target_domain.casefold():
                continue
            if non_numeric_attr_names and h.name.casefold() not in non_numeric_attr_names:
                continue
            h_lower = h.name.casefold()
            h_words = self._stem_words(h_lower)
            if h_words and (h_lower in prompt_for_dims.casefold() or h_words.issubset(prompt_words)):
                h_clean = re.sub(r"[^a-z0-9]+", " ", h_lower).strip()
                part_of_metric = any(
                    (
                        h_clean and (
                            h_clean in re.sub(r"[^a-z0-9]+", " ", (m.name or "").casefold())
                            or re.sub(r"[^a-z0-9]+", " ", (m.name or "").casefold()) in h_clean
                            or h_clean in re.sub(r"[^a-z0-9]+", " ", (m.label or "").casefold())
                            or re.sub(r"[^a-z0-9]+", " ", (m.label or "").casefold()) in h_clean
                        )
                    )
                    for m in detected_metrics
                )
                if h.name not in dim_names and (not part_of_metric or (bt_lower and bt_lower in h_lower)):
                    dim_names.append(h.name)

        def _non_numeric_attrs(concept: DomainConcept) -> List[DomainAttribute]:
            return [a for a in concept.attributes if not self._is_numeric_attr(a)]

        preferred_dim_pair: Dict[str, Tuple[DomainConcept, DomainAttribute]] = {}
        if by_target:
            bt_asks_key = bool(re.search(r"(seq|key|id)\b", bt_lower))
            whole_exact = any(
                bt_lower == a.name.casefold() or bt_n == a.name.casefold().replace(" ", "")
                for bt_n in [bt_lower.replace(" ", "")]
                for c in scoped_concepts for a in _non_numeric_attrs(c)
            )
            sub_targets = (
                [by_target]
                if whole_exact or not re.search(r",|\band\b", by_target, re.I)
                else [s.strip() for s in re.split(r",|\band\b", by_target, flags=re.I) if s.strip()]
            )

            all_candidate_dim_names: List[str] = [
                a.name for c in scoped_concepts for a in _non_numeric_attrs(c)
            ]

            for sub_bt in sub_targets:
                sbt_lower = sub_bt.casefold()
                if any(sbt_lower == d.casefold() for d in dim_names):
                    continue
                matched_dim = None
                matched_pair: Optional[Tuple[DomainConcept, DomainAttribute]] = None
                bt_words = sbt_lower.split()
                bt_nospace = sbt_lower.replace(" ", "")
                bt_stem = " ".join(_stem_token(w) for w in bt_words) if bt_words else _stem_token(sbt_lower)

                # Pass 0: compound noun phrase where first word hints the concept and last word names the non-numeric attribute
                if len(bt_words) >= 2:
                    head_w, tail_w = bt_words[0], bt_words[-1]
                    for c in scoped_concepts:
                        if head_w in c.name.casefold():
                            for a in _non_numeric_attrs(c):
                                if tail_w == a.name.casefold():
                                    matched_dim = a.name
                                    matched_pair = (c, a)
                                    break
                        if matched_dim:
                            break

                bt_candidates = [sbt_lower]
                if bt_stem != sbt_lower and bt_stem not in bt_candidates:
                    bt_candidates.append(bt_stem)
                if bt_nospace != sbt_lower and bt_nospace not in bt_candidates:
                    bt_candidates.append(bt_nospace)
                if len(bt_words) >= 2:
                    if bt_words[-1] not in bt_candidates:
                        bt_candidates.append(bt_words[-1])
                    if bt_words[0] not in bt_candidates:
                        bt_candidates.append(bt_words[0])

                if not matched_dim:
                    for bt_cand in bt_candidates:
                        # Pass 1: exact or spaceless non-numeric attribute match
                        for c in scoped_concepts:
                            for a in _non_numeric_attrs(c):
                                a_low = a.name.casefold()
                                if bt_cand == a_low or bt_cand == a_low.replace(" ", ""):
                                    matched_dim = a.name
                                    matched_pair = (c, a)
                                    break
                            if matched_dim:
                                break
                        if matched_dim:
                            break
                        # Pass 2: non-key non-numeric substring match
                        for c in scoped_concepts:
                            for a in _non_numeric_attrs(c):
                                a_low = a.name.casefold()
                                is_surrogate = bool(re.search(r"(seq|key|id)$", a_low)) and not bt_asks_key
                                if not is_surrogate and bt_cand in a_low:
                                    matched_dim = a.name
                                    matched_pair = (c, a)
                                    break
                            if matched_dim:
                                break
                        if matched_dim:
                            break
                # Pass 3: any non-numeric attribute substring match (never invent concept name as fake dim)
                if not matched_dim:
                    for c in scoped_concepts:
                        for a in _non_numeric_attrs(c):
                            if sbt_lower in a.name.casefold() or bt_stem in a.name.casefold():
                                matched_dim = a.name
                                matched_pair = (c, a)
                                break
                        if matched_dim:
                            break
                if matched_dim:
                    dim_names = [
                        d for d in dim_names
                        if not (d.casefold() != matched_dim.casefold() and len(bt_words) >= 2 and (d.casefold() in bt_words or sbt_lower in d.casefold()))
                    ]
                    if matched_dim not in dim_names:
                        dim_names.append(matched_dim)
                    if matched_pair:
                        preferred_dim_pair[matched_dim] = matched_pair
                else:
                    # Never invent a dimension (no X.title()); warn not found and suggest closest non-numeric attribute
                    attr_sugs: List[str] = []
                    for h in self.search_index.search(
                        sub_bt,
                        domain_filter=target_domain or domain_filter,
                        item_type_filter="ATTRIBUTE",
                        limit=5,
                        min_score=0.0,
                    ):
                        if non_numeric_attr_names and h.name.casefold() not in non_numeric_attr_names:
                            continue
                        if h.name not in attr_sugs:
                            attr_sugs.append(h.name)
                    if not attr_sugs and all_candidate_dim_names:
                        close = difflib.get_close_matches(sub_bt, all_candidate_dim_names, n=2, cutoff=0.3)
                        attr_sugs.extend(close)
                    sug_part = f" Did you mean: {', '.join(attr_sugs[:2])}?" if attr_sugs else ""
                    warnings.append(f"Dimension '{sub_bt}' not found in domain.{sug_part}")

        # If dim_names is empty and no explicit 'by X' failed, check concept_hits for non-numeric attributes
        if not dim_names and not by_target and detected_concepts:
            clean_toks = [w.strip("?,.:;'\"").casefold() for w in prompt_for_dims.split() if len(w.strip("?,.:;'\"")) >= 3]
            for c in detected_concepts:
                if target_domain and c.domain and c.domain.casefold() != target_domain.casefold():
                    continue
                for a in _non_numeric_attrs(c):
                    a_low = a.name.casefold()
                    part_of_metric = any(
                        a_low in m.name.casefold() or a_low in (m.label or "").casefold()
                        for m in detected_metrics
                    )
                    if not part_of_metric and any(re.search(rf"\b{re.escape(tok)}\b", a_low) for tok in clean_toks):
                        dim_names.append(a.name)
                        preferred_dim_pair[a.name] = (c, a)
                        break
                if dim_names:
                    break

        # Determine minimal set of concepts and attribute/concept objects strictly within scoped_concepts
        # (no all-concepts cross-domain fallback).
        needed_concepts: Set[str] = set()
        dim_to_concept: Dict[str, str] = {}
        dim_to_pair: Dict[str, Tuple[DomainConcept, DomainAttribute]] = {}
        concept_by_name: Dict[str, DomainConcept] = {c.name.casefold(): c for c in scoped_concepts}
        for d in dim_names:
            if d in preferred_dim_pair:
                pc, pa = preferred_dim_pair[d]
                needed_concepts.add(pc.name)
                dim_to_concept[d] = pc.name
                dim_to_pair[d] = (pc, pa)
        for c in scoped_concepts:
            for a in _non_numeric_attrs(c):
                for d in dim_names:
                    if d.casefold() == a.name.casefold() and d not in dim_to_pair:
                        needed_concepts.add(c.name)
                        dim_to_concept[d] = c.name
                        dim_to_pair[d] = (c, a)

        # Keep only dimensions that resolved to an actual non-numeric attribute in the domain
        dim_names = [d for d in dim_names if d in dim_to_pair]

        for m in detected_metrics:
            if m.concept:
                needed_concepts.add(m.concept)

        if not needed_concepts and detected_concepts:
            for c in detected_concepts:
                if not target_domain or not c.domain or c.domain.casefold() == target_domain.casefold():
                    needed_concepts.add(c.name)

        # Also enforce any concept-level policy/filter on detected metric's owning concept
        metric_owner_concepts = [
            concept_by_name[m.concept.casefold()]
            for m in detected_metrics
            if m.concept and m.concept.casefold() in concept_by_name
            and concept_by_name[m.concept.casefold()] not in scoped_policy_concepts
        ]
        if metric_owner_concepts:
            self._collect_policy_filters(
                detected_concepts=metric_owner_concepts,
                detected_metrics=detected_metrics,
                metrics_by_key=metrics_by_key,
                filters=filters,
                applied_policies=applied_policies,
            )

        # Pick primary table:
        # 1. Prefer concept associated with detected metrics
        # 2. Prefer top detected concept from semantic search
        # 3. Fallback to first needed concept
        primary_table = None
        for m in detected_metrics:
            if m.concept and m.concept in needed_concepts:
                primary_table = m.concept
                break
        if not primary_table and detected_concepts:
            for c in detected_concepts:
                if c.name in needed_concepts:
                    primary_table = c.name
                    break
        if not primary_table and needed_concepts:
            primary_table = sorted(needed_concepts)[0]
        if not primary_table:
            primary_table = "ANALYTICS"

        # Year/quarter filters using a date dimension related to the metric's table (inside SEMANTIC_VIEW WHERE);
        # if none exists in the domain's related concepts, warn and omit (never assume 'date').
        year_match = re.search(r"\b(20[12][0-9])\b", clean_prompt)
        quarter_match = re.search(r"\bQ([1-4])\b", clean_prompt, re.IGNORECASE)
        if year_match or quarter_match:
            date_c, date_a = self._resolve_related_date_column(
                primary_table=primary_table,
                needed_concepts=needed_concepts,
                scoped_concepts=scoped_concepts,
                target_domain=target_domain,
                has_metrics=bool(detected_metrics),
            )
            if date_c and date_a:
                if semantic_view:
                    dt_tbl = self._q_ident(getattr(date_c, "logical_name", None) or self._to_logical_id(date_c.name))
                    dt_col = self._q_ident(getattr(date_a, "logical_name", None) or self._to_logical_id(date_a.name))
                    date_ref = f"{dt_tbl}.{dt_col}"
                else:
                    date_ref = self._q_ident(date_a.name)
                if year_match:
                    filters.append(f"YEAR({date_ref}) = {year_match.group(1)}")
                if quarter_match:
                    filters.append(f"QUARTER({date_ref}) = {quarter_match.group(1)}")
            else:
                warnings.append(
                    "No date dimension related to the metric's table was found; year/quarter filter omitted."
                )

        resolved_tables_list = [primary_table]
        join_clauses: List[str] = []

        # Relational Join Generation (Acyclic, Active-Only):
        # 1. Skipped when targeting a Snowflake Semantic View (the view handles internal joins natively).
        # 2. Only joins tables that belong to needed_concepts.
        # 3. Skips inactive relationships (is_active=False) to avoid cycle joins.
        # 4. Quotes identifiers with spaces to prevent SQL syntax errors.
        if not semantic_view and len(needed_concepts) > 1:
            in_query_tables: Set[str] = {primary_table.casefold()}
            remaining_concepts = {c for c in needed_concepts if c.casefold() not in in_query_tables}

            for rel in self.ontology.relationships:
                if not remaining_concepts:
                    break
                if getattr(rel, "is_active", True) is False:
                    continue

                from_c = rel.from_concept
                to_c = rel.to_concept

                # Join to_c if from_c is already in query and to_c is needed
                if from_c.casefold() in in_query_tables and to_c.casefold() in {c.casefold() for c in remaining_concepts}:
                    clean_join = self._quote_join_expression(rel.join_expression)
                    if clean_join:
                        tbl_str = f'"{to_c}"' if " " in to_c else to_c
                        join_clauses.append(f"LEFT JOIN {tbl_str} ON {clean_join}")
                        in_query_tables.add(to_c.casefold())
                        resolved_tables_list.append(to_c)
                        remaining_concepts = {c for c in remaining_concepts if c.casefold() != to_c.casefold()}

                # Join from_c if to_c is already in query and from_c is needed
                elif to_c.casefold() in in_query_tables and from_c.casefold() in {c.casefold() for c in remaining_concepts}:
                    clean_join = self._quote_join_expression(rel.join_expression)
                    if clean_join:
                        tbl_str = f'"{from_c}"' if " " in from_c else from_c
                        join_clauses.append(f"LEFT JOIN {tbl_str} ON {clean_join}")
                        in_query_tables.add(from_c.casefold())
                        resolved_tables_list.append(from_c)
                        remaining_concepts = {c for c in remaining_concepts if c.casefold() != from_c.casefold()}

        # 3. Assemble Governed SQL
        select_parts: List[str] = []
        group_by_parts: List[str] = []
        first_metric_order_col: Optional[str] = None

        if semantic_view:
            sv_metrics: List[str] = []
            sv_dims: List[str] = []

            for m in detected_metrics:
                m_c_name = m.concept or primary_table
                m_c_obj = concept_by_name.get(m_c_name.casefold())
                tbl_logical = self._q_ident(
                    (getattr(m_c_obj, "logical_name", None) if m_c_obj else None)
                    or self._to_logical_id(m_c_name)
                )
                met_logical = self._q_ident(
                    getattr(m, "logical_name", None) or self._to_logical_id(m.name)
                )
                sv_metrics.append(f"{tbl_logical}.{met_logical}")
                if not first_metric_order_col:
                    first_metric_order_col = met_logical

            for d in dim_names:
                c_obj, a_obj = dim_to_pair[d]
                tbl_logical = self._q_ident(
                    getattr(c_obj, "logical_name", None) or self._to_logical_id(c_obj.name)
                )
                col_logical = self._q_ident(
                    getattr(a_obj, "logical_name", None) or self._to_logical_id(a_obj.name)
                )
                sv_dims.append(f"{tbl_logical}.{col_logical}")

            sv_clauses: List[str] = [semantic_view]
            if sv_metrics:
                sv_clauses.append(f"METRICS {', '.join(sv_metrics)}")
            if sv_dims:
                sv_clauses.append(f"DIMENSIONS {', '.join(sv_dims)}")
            if filters and (sv_metrics or sv_dims):
                sv_clauses.append(f"WHERE {' AND '.join(filters)}")

            if sv_metrics or sv_dims:
                select_parts = ["    *"]
                from_target = "SEMANTIC_VIEW(" + " ".join(sv_clauses) + ")"
            else:
                select_parts = ["    NULL AS UNANSWERABLE"]
                from_target = None
        else:
            if is_unique and not detected_metrics and dim_names:
                primary_dim = dim_names[0]
                dim_col = self._q_ident(primary_dim)
                alias = f"UNIQUE_{re.sub(r'[^A-Za-z0-9_]', '_', primary_dim).upper()}"
                select_parts.append(f"    COUNT(DISTINCT {dim_col}) AS {alias}")
                for idx, dim in enumerate(dim_names[1:], 1):
                    d_col = self._q_ident(dim)
                    select_parts.insert(idx - 1, f"    {d_col}")
                    group_by_parts.append(str(idx))
            else:
                for idx, dim in enumerate(dim_names, 1):
                    dim_col = self._q_ident(dim)
                    select_parts.append(f"    {dim_col}")
                    if detected_metrics:
                        group_by_parts.append(str(idx))

            for m in detected_metrics:
                expr = self._expand_metric_expression(m, metrics_by_key)
                if not expr:
                    continue
                alias = self._q_ident(m.name)
                select_parts.append(f"    {expr} AS {alias}")
                if not first_metric_order_col:
                    first_metric_order_col = alias

            if not select_parts:
                select_parts.append("    *")
            from_target = f'"{primary_table}"' if " " in primary_table else primary_table

        target_engine = "Snowflake Cortex Analyst & Semantic View" if dialect.upper() == "SNOWFLAKE" else "Databricks AI/BI Genie & Unity Catalog Metric View" if dialect.upper() == "DATABRICKS" else "SemaBridge Local Engine"
        sql_lines: List[str] = []
        sql_lines.append(f"-- SemaBridge AI Generated Governed Semantic Query")
        sql_lines.append(f"-- Target Engine: {target_engine}")
        sql_lines.append(f"-- Question: \"{clean_prompt}\"")
        sql_lines.append(f"-- Dialect: {dialect.upper()}")
        for m in detected_metrics:
            agg = (m.aggregation_type or "SUM").upper()
            sql_lines.append(f"-- Metric: {m.label or m.name} (Aggregation: {agg})")
        sql_lines.append("SELECT")
        sql_lines.append(",\n".join(select_parts))
        if from_target:
            sql_lines.append(f"FROM {from_target}")

        for j in join_clauses:
            sql_lines.append(f"{j}")

        if not semantic_view and filters:
            sql_lines.append(f"WHERE {' AND '.join(filters)}")
        elif semantic_view and not from_target:
            sql_lines.append("WHERE FALSE")

        if not semantic_view and group_by_parts:
            sql_lines.append(f"GROUP BY {', '.join(group_by_parts)}")

        if from_target:
            order_col = first_metric_order_col or "1"
            sql_lines.append(f"ORDER BY {order_col} DESC")
            sql_lines.append(f"LIMIT {limit_val};")
        else:
            sql_lines[-1] = sql_lines[-1] + ";"

        generated_sql = "\n".join(sql_lines)
        if dialect.upper() == "DATABRICKS":
            # Adapt any Snowflake-specific IFF to Databricks standard IF
            generated_sql = re.sub(r"\bIFF\(", "IF(", generated_sql, flags=re.IGNORECASE)

        # 4. Compute Confidence Score & Explanation
        if not detected_metrics:
            confidence = 0.25 if dim_names else 0.1
        else:
            metric_score = 0.5
            concept_score = 0.4 if dim_names else 0.1
            confidence = min(0.95, metric_score + concept_score)

        explanation_parts = []
        if detected_metrics:
            explanation_parts.append(f"Calculated metrics: {', '.join(m.name for m in detected_metrics)}")
        if derived_from_metrics:
            explanation_parts.append(f"Derived from upstream metrics: {', '.join(derived_from_metrics)}")
        if dim_names:
            explanation_parts.append(f"Grouped by dimensions: {', '.join(dim_names)}")
        if applied_policies:
            explanation_parts.append(f"Enforced policies: {', '.join(applied_policies)}")
        if filters:
            explanation_parts.append(f"Applied filters: {', '.join(filters)}")
        for w in warnings:
            explanation_parts.append(f"Warning: {w}")
        explanation = "; ".join(explanation_parts) if explanation_parts else "Resolved against semantic ontology."

        return QueryResolutionPlan(
            original_prompt=clean_prompt,
            detected_metrics=[m.label or m.name for m in detected_metrics],
            detected_dimensions=dim_names,
            filters=filters,
            applied_policies=applied_policies,
            derived_from_metrics=derived_from_metrics,
            resolved_tables=resolved_tables_list,
            join_clauses=join_clauses,
            generated_sql=generated_sql,
            confidence_score=round(confidence, 2),
            explanation=explanation,
            warnings=warnings,
        )

    @staticmethod
    def _expand_metric_expression(
        metric: DomainMetric,
        metrics_by_key: Dict[str, DomainMetric],
        _seen: Optional[Set[str]] = None,
    ) -> str:
        """Expand bracketed composite metric references (``[Metric]``) into executable SQL."""
        expr = (metric.sql_expression or "").strip()
        if not expr:
            return ""
        deps: List[str] = list(getattr(metric, "derives_from", None) or [])
        # Also include inline [Metric] references so composite SQL expands even if derives_from is omitted
        for inline_ref in re.findall(r"\[([^\]]+)\]", expr):
            if inline_ref not in deps:
                deps.append(inline_ref)
        if not deps:
            return expr
        seen = set(_seen or set())
        seen.add(metric.name.casefold())

        expanded = expr
        for dep_name in deps:
            dep_key = dep_name.casefold()
            if dep_key in seen:
                continue
            dep_m = metrics_by_key.get(dep_key)
            if not dep_m or not (dep_m.sql_expression or "").strip():
                continue
            dep_sql = NLQueryEngine._expand_metric_expression(dep_m, metrics_by_key, seen)
            if not dep_sql:
                continue
            # Replace ONLY explicit bracketed [Metric] references, never bare words
            for token in filter(None, [dep_m.name, dep_m.label, dep_name]):
                expanded = re.sub(
                    r"\[" + re.escape(token) + r"\]",
                    f"({dep_sql})",
                    expanded,
                    flags=re.IGNORECASE,
                )
        return expanded

    def _collect_policy_filters(
        self,
        detected_concepts: List[DomainConcept],
        detected_metrics: List[DomainMetric],
        metrics_by_key: Dict[str, DomainMetric],
        filters: List[str],
        applied_policies: List[str],
    ) -> None:
        """Append enforceable SQL filter expressions only when their domain or target is active in the query."""
        seen_filters = {f.strip().casefold() for f in filters if f.strip()}
        seen_policies = {p.casefold() for p in applied_policies}

        def add_filter(sql_pred: Optional[str], policy_label: Optional[str] = None) -> None:
            pred = (sql_pred or "").strip()
            if not pred:
                return
            if pred.casefold() not in seen_filters:
                seen_filters.add(pred.casefold())
                filters.append(pred)
            if policy_label and policy_label.casefold() not in seen_policies:
                seen_policies.add(policy_label.casefold())
                applied_policies.append(policy_label)

        def extract_inline_sql_rules(rules: List[str], owner_name: str) -> None:
            for rule in rules or []:
                m = re.match(r"^(?:SQL|FILTER)\s*:\s*(.+)$", str(rule or "").strip(), flags=re.IGNORECASE)
                if m:
                    add_filter(m.group(1), f"{owner_name} rule")

        active_names: Set[str] = set()
        active_domains: Set[str] = set()

        for c in detected_concepts:
            active_names.add(c.name.casefold())
            if c.domain:
                active_domains.add(c.domain.casefold())
            if getattr(c, "filter_expression", None):
                add_filter(c.filter_expression, f"{c.name} filter")
            extract_inline_sql_rules(c.business_rules, c.name)

        # Include detected metrics plus any upstream derived metrics
        all_involved_metrics: List[DomainMetric] = list(detected_metrics)
        for m in detected_metrics:
            for dep in getattr(m, "derives_from", None) or []:
                dep_m = metrics_by_key.get(dep.casefold())
                if dep_m and dep_m not in all_involved_metrics:
                    all_involved_metrics.append(dep_m)

        for m in all_involved_metrics:
            active_names.add(m.name.casefold())
            if m.domain:
                active_domains.add(m.domain.casefold())
            if getattr(m, "filter_expression", None):
                add_filter(m.filter_expression, f"{m.name} filter")
            extract_inline_sql_rules(m.business_rules, m.name)

        # Check domain-level and federated policies strictly against active domains/targets
        all_policies = list(getattr(self.ontology, "policies", {}).values())
        for dom in self.ontology.domains.values():
            for pol in getattr(dom, "policies", None) or []:
                if pol not in all_policies:
                    all_policies.append(pol)

        for pol in all_policies:
            applies = [a.strip().casefold() for a in (getattr(pol, "applies_to", None) or []) if a.strip()]
            pol_dom = (getattr(pol, "domain", None) or "").strip().casefold()
            if not applies or "*" in applies:
                if pol_dom and pol_dom in active_domains:
                    add_filter(pol.filter_expression, pol.name)
            elif any(a in active_names for a in applies) and (not pol_dom or pol_dom in active_domains):
                add_filter(pol.filter_expression, pol.name)

    _DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}

    def _resolve_related_date_column(
        self,
        primary_table: str,
        needed_concepts: Set[str],
        scoped_concepts: List[DomainConcept],
        target_domain: Optional[str] = None,
        has_metrics: bool = True,
    ) -> Tuple[Optional[DomainConcept], Optional[DomainAttribute]]:
        """Find a date dimension attribute directly on or actively related to the metric's table.

        Returns ``(concept, attribute)`` or ``(None, None)``; never scans outside the scoped
        domain and never falls back to an assumed 'date' column.
        """
        by_name: Dict[str, DomainConcept] = {c.name.casefold(): c for c in scoped_concepts}

        def pick_date_attr(c: DomainConcept) -> Optional[DomainAttribute]:
            date_attrs = [
                a for a in c.attributes
                if (a.data_type or "").split("(")[0].strip().upper() in self._DATE_TYPES
            ]
            if not date_attrs:
                return None
            exact = [a for a in date_attrs if a.name.casefold() == "date"]
            partial = [a for a in date_attrs if "date" in a.name.casefold()]
            return (exact or partial or date_attrs)[0]

        # When metrics exist, anchor strictly on the metric's table (primary_table) so dimension tables
        # shared by another fact table (e.g. Products -> Cases) don't inject an unrelated fact's date.
        anchor_names = (
            {primary_table.casefold()}
            if has_metrics and primary_table and primary_table != "ANALYTICS"
            else {n.casefold() for n in needed_concepts} | ({primary_table.casefold()} if primary_table else set())
        )

        # 1. Check active relationships within the domain linking anchor_names to a Date concept
        dom_obj = next(
            (d for d in self.ontology.domains.values() if target_domain and d.domain.casefold() == target_domain.casefold()),
            None,
        )
        rels = list(dom_obj.relationships) if dom_obj else list(self.ontology.relationships)
        for rel in rels:
            if getattr(rel, "is_active", True) is False:
                continue
            fc = rel.from_concept.casefold()
            tc = rel.to_concept.casefold()
            linked_name = tc if fc in anchor_names else (fc if tc in anchor_names else None)
            if linked_name and linked_name in by_name:
                attr = pick_date_attr(by_name[linked_name])
                if attr:
                    return by_name[linked_name], attr

        # 2. Check date attribute directly on primary_table / anchor_names
        check_order = [primary_table] if (has_metrics and primary_table != "ANALYTICS") else [primary_table, *sorted(needed_concepts)]
        for cname in check_order:
            c_obj = by_name.get(cname.casefold())
            if c_obj:
                attr = pick_date_attr(c_obj)
                if attr:
                    return c_obj, attr

        return None, None

    @staticmethod
    def _quote_join_expression(expr: Optional[str]) -> Optional[str]:
        """Quote identifiers with spaces in join condition parts (e.g. Accounts."Account Owner" = Owners."Sales owner").
        
        Prevents Snowflake/Databricks syntax errors when join predicates reference columns or tables with whitespace.
        """
        if not expr:
            return None

        def quote_col_part(p: str) -> str:
            p = p.strip()
            if "." in p:
                t, c = p.split(".", 1)
                t_str = f'"{t}"' if (" " in t and not t.startswith('"')) else t
                c_str = f'"{c}"' if (" " in c and not c.startswith('"')) else c
                return f"{t_str}.{c_str}"
            return f'"{p}"' if (" " in p and not p.startswith('"')) else p

        if " AND " in expr:
            sub = [NLQueryEngine._quote_join_expression(s.strip()) for s in expr.split(" AND ")]
            return " AND ".join(s for s in sub if s)

        if "=" in expr:
            left, right = expr.split("=", 1)
            return f"{quote_col_part(left)} = {quote_col_part(right)}"
        return expr
