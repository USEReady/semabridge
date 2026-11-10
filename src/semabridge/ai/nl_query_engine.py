"""Natural Language to Semantic Query & SQL Engine.

Interprets analytical questions in plain English, resolves them against governed
domain ontologies, determines optimal join paths, and generates executable SQL.
Operates completely locally with zero Snowflake credit consumption.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from semabridge.ai.semantic_search import SearchHit, SemanticSearchIndex
from semabridge.ontology.models import (
    DomainConcept,
    DomainMetric,
    DomainRelationship,
    FederatedEnterpriseOntology,
)

logger = logging.getLogger("semabridge.ai.nl_query_engine")


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

    def resolve_query(
        self,
        prompt: str,
        dialect: str = "SNOWFLAKE",
        semantic_view: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
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
                r"\b(ytd|qtd|mtd|py|pm|pq|sply|mat|mom|yoy|qoq|year[- ]?to[- ]?date|quarter[- ]?to[- ]?date|month[- ]?to[- ]?date|previous\s+(?:year|month|quarter)|same\s+period\s+last\s+year|moving\s+annual\s+total|month[- ]over[- ]month|year[- ]over[- ]year|quarter[- ]over[- ]quarter|change|growth|variance)\b",
                clean_prompt,
                re.I,
            )
        )
        asks_for_count = bool(re.search(r"\b(how many|number of|count of|most)\b", clean_prompt, re.I))
        # Check multi-metric intent on metric_search_query so 'by X and Y' dimension conjunctions don't multiply metrics
        has_multi_intent = bool(re.search(r"\b(and|vs|versus|compare|comparison|compared|both|all|with)\b|,", metric_search_query, re.I))

        from semabridge.ai.semantic_search import _tokenize as _ss_tokenize
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
            is_count_metric = ("count" in m_text and not is_pct_metric)
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
            w for w in re.findall(r"[a-z0-9]+", metric_search_query.casefold())
            if w not in _STOP_OR_MOD and len(w) >= 2
        ]
        if q_subject_toks:
            filtered_reranked = []
            for adj_score, hit, m in reranked_hits:
                m_corpus = f"{m.name} {m.label or ''} {' '.join(m.synonyms or [])}".casefold()
                m_toks = re.findall(r"[a-z0-9]+", m_corpus)
                shares_subject = any(
                    qt in mt or mt in qt or (len(qt) >= 3 and len(mt) >= 3 and qt[:3] == mt[:3])
                    for qt in q_subject_toks for mt in m_toks
                )
                if shares_subject or hit.score >= 0.45:
                    filtered_reranked.append((adj_score, hit, m))
            reranked_hits = filtered_reranked

        reranked_hits.sort(key=lambda x: x[0], reverse=True)

        detected_metrics: List[DomainMetric] = []
        seen_m = set()
        ambiguous_alternatives: List[str] = []

        prompt_tokens = {w.strip("?,.:;'\"").casefold() for w in clean_prompt.split()}
        if reranked_hits:
            top_adj_score, top_hit, top_m = reranked_hits[0]
            top_hit_overlap = bool(set(re.findall(r"[a-z0-9]+", top_hit.name.casefold())) & prompt_tokens)
            if top_adj_score >= 0.20 or (top_hit_overlap and top_adj_score >= 0.15):
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
                        runner_overlap = bool(set(re.findall(r"[a-z0-9]+", hit.name.casefold())) & prompt_tokens)
                        if runner_overlap and (top_adj_score - adj_score <= 0.10):
                            ambiguous_alternatives.append(m.label or m.name)

        # Count-intent entity recovery:
        # Handles 'how many <entity>', 'which <dim> has the most <entity>', 'number of <entity>'
        # whenever no count metric has been selected yet (even if a percentage metric matched earlier).
        has_selected_count = any(
            "count" in f"{m.name} {m.label or ''}".casefold()
            and not re.search(r"(%|_pct|\bpct\b|percent|ratio)", f"{m.name} {m.label or ''}", re.I)
            for m in detected_metrics
        )
        if asks_for_count and not has_selected_count:
            count_match = re.search(
                r"\b(?:how many|number of|count of|most)\s+(?:are there\s+)?([a-zA-Z0-9_]+)\b",
                clean_prompt,
                re.I,
            )
            if count_match:
                target_entity = count_match.group(1).casefold()
                target_stem = target_entity[:-3] + "y" if len(target_entity) > 4 and target_entity.endswith("ies") else target_entity.rstrip("s")
                for m in self.ontology.metrics.values():
                    if allowed_domains and m.domain and m.domain not in allowed_domains:
                        continue
                    m_concept = (m.concept or "").casefold()
                    m_name = m.name.casefold()
                    m_label = (m.label or "").casefold()
                    m_text = f"{m_name} {m_label}"
                    is_pct = bool(re.search(r"(%|_pct|\bpct\b|percent|ratio)", m_text, re.I))
                    is_cnt = ("count" in m_name or "count" in m_label) and not is_pct
                    entity_matches = (
                        m_concept in (target_entity, target_stem)
                        or m_concept.rstrip("s") == target_stem
                        or target_stem in m_name
                        or target_stem in m_label
                    )
                    if is_cnt and entity_matches:
                        # Prefer the entity's true count metric over an unrequested percentage/non-count hit
                        if not asks_for_rate_or_pct:
                            detected_metrics = [m]
                            seen_m = {m.name}
                            ambiguous_alternatives.clear()
                        elif m.name not in seen_m:
                            detected_metrics.insert(0, m)
                            seen_m.add(m.name)
                        break

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

        # Extract potential time filters from prompt (e.g. 2024, 2023, Q1, Q2)
        filters: List[str] = []
        applied_policies: List[str] = []
        derived_from_metrics: List[str] = []
        warnings: List[str] = []
        if not detected_metrics:
            sug_text = f" Did you mean: {', '.join(metric_suggestions)}?" if metric_suggestions else ""
            warnings.append(f"No metric found matching '{clean_prompt}'.{sug_text}")
        elif ambiguous_alternatives:
            top_disp = detected_metrics[0].label or detected_metrics[0].name
            warnings.append(
                f"Multiple metrics match your query ({top_disp}, {', '.join(ambiguous_alternatives)}). "
                f"Selected '{top_disp}'; specify another or ask to compare them if needed."
            )

        year_match = re.search(r"\b(20[12][0-9])\b", clean_prompt)
        quarter_match = re.search(r"\bQ([1-4])\b", clean_prompt, re.IGNORECASE)
        if year_match or quarter_match:
            date_col = self._resolve_date_column(detected_concepts)
            if date_col is None:
                date_col = "date"
                warnings.append(
                    "No date attribute found in the ontology; time filters assume a column named 'date'."
                )
            if year_match:
                filters.append(f"YEAR({date_col}) = {year_match.group(1)}")
            if quarter_match:
                # QUARTER() returns an integer 1-4, so compare against a number, not 'Q1'.
                filters.append(f"QUARTER({date_col}) = {quarter_match.group(1)}")

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

        # Identify primary domain early to prevent cross-domain concept & attribute bleeding:
        # e.g., if a metric from AI Sample is detected, only resolve dimensions from AI Sample (not Adventure Works ProductKey).
        target_domain = None
        if detected_metrics:
            target_domain = detected_metrics[0].domain
        elif detected_concepts:
            target_domain = detected_concepts[0].domain
        elif attr_hits:
            target_domain = attr_hits[0].domain

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

        # Auto-resolve Snowflake Semantic View when targeting a BI-backed domain without explicit project_id,
        # preventing fallback to physical tables (e.g. FROM Opportunities) when a governed Semantic View exists.
        if dialect.upper() == "SNOWFLAKE" and not semantic_view and detected_metrics:
            sv_candidate = getattr(detected_metrics[0], "semantic_view_name", None)
            if not sv_candidate and target_domain:
                dom_obj = next(
                    (d for d in self.ontology.domains.values() if d.domain.casefold() == target_domain.casefold()),
                    None,
                )
                if dom_obj and getattr(dom_obj, "source_project_id", None):
                    try:
                        from semabridge.utils.name_translator import get_target_deployment_name
                        sv_candidate = get_target_deployment_name(dom_obj.domain, "snowflake")
                    except Exception:
                        sv_candidate = None
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

        # Strip multi-word time-intelligence phrases when scanning for dimension attributes so
        # e.g. 'year-to-date' or 'month-to-date' do not falsely trigger 'Year' or 'Date' dimensions.
        prompt_for_dims = re.sub(
            r"\b(?:\w+[- ]?to[- ]?date|previous\s+\w+|same\s+period\s+last\s+\w+|moving\s+annual\s+total|last\s+\d+\s+\w+)\b",
            " ",
            clean_prompt,
            flags=re.I,
        )
        prompt_words = {w.strip("?,.:;'\"").casefold() for w in prompt_for_dims.split()}
        dim_names: List[str] = []
        for h in attr_hits:
            if target_domain and h.domain and h.domain.casefold() != target_domain.casefold():
                continue
            h_lower = h.name.casefold()
            h_words = set(re.findall(r"\w+", h_lower))
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

        if by_target:
            bt_asks_key = bool(re.search(r"(seq|key|id)\b", bt_lower))
            scoped_concepts = domain_concepts or [
                c for c in self.ontology.concepts.values()
                if (not target_domain or not c.domain or c.domain.casefold() == target_domain.casefold())
                and (not allowed_domains or not c.domain or c.domain in allowed_domains)
            ]
            if not scoped_concepts:
                scoped_concepts = list(self.ontology.concepts.values())

            # Support multi-dimension lists ('by gender and ethnicity', 'by product, discount band')
            whole_exact = any(
                bt_lower == a.name.casefold() or bt_n == a.name.casefold().replace(" ", "")
                for bt_n in [bt_lower.replace(" ", "")]
                for c in scoped_concepts for a in c.attributes
            )
            sub_targets = (
                [by_target]
                if whole_exact or not re.search(r",|\band\b", by_target, re.I)
                else [s.strip() for s in re.split(r",|\band\b", by_target, flags=re.I) if s.strip()]
            )

            for sub_bt in sub_targets:
                sbt_lower = sub_bt.casefold()
                if any(sbt_lower == d.casefold() for d in dim_names):
                    continue
                matched_dim = None
                bt_words = sbt_lower.split()
                bt_nospace = sbt_lower.replace(" ", "")
                bt_stem = sbt_lower[:-3] + "y" if len(sbt_lower) > 4 and sbt_lower.endswith("ies") else (sbt_lower[:-1] if len(sbt_lower) > 3 and sbt_lower.endswith("s") else sbt_lower)

                # Pass 0: compound noun phrase where first word hints the concept and last word names the attribute
                if len(bt_words) >= 2:
                    head_w, tail_w = bt_words[0], bt_words[-1]
                    for c in scoped_concepts:
                        if head_w in c.name.casefold():
                            for a in c.attributes:
                                if tail_w == a.name.casefold():
                                    matched_dim = a.name
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
                        # Pass 1: exact or spaceless attribute name match
                        for c in scoped_concepts:
                            for a in c.attributes:
                                a_low = a.name.casefold()
                                if bt_cand == a_low or bt_cand == a_low.replace(" ", ""):
                                    matched_dim = a.name
                                    break
                            if matched_dim:
                                break
                        if matched_dim:
                            break
                        # Pass 2: non-key substring match
                        for c in scoped_concepts:
                            for a in c.attributes:
                                a_low = a.name.casefold()
                                is_surrogate = bool(re.search(r"(seq|key|id)$", a_low)) and not bt_asks_key
                                if not is_surrogate and bt_cand in a_low:
                                    matched_dim = a.name
                                    break
                            if matched_dim:
                                break
                        if matched_dim:
                            break
                # Pass 3: any attribute substring or concept match
                if not matched_dim:
                    for c in scoped_concepts:
                        for a in c.attributes:
                            if sbt_lower in a.name.casefold() or bt_stem in a.name.casefold():
                                matched_dim = a.name
                                break
                        if matched_dim:
                            break
                if not matched_dim:
                    for c in scoped_concepts:
                        if sbt_lower == c.name.casefold() or bt_stem == c.name.casefold() or sbt_lower in c.name.casefold():
                            matched_dim = c.name
                            break
                if matched_dim:
                    dim_names = [
                        d for d in dim_names
                        if not (d.casefold() != matched_dim.casefold() and len(bt_words) >= 2 and (d.casefold() in bt_words or sbt_lower in d.casefold()))
                    ]
                    if matched_dim not in dim_names:
                        dim_names.append(matched_dim)
                elif len(sub_targets) == 1 and not dim_names:
                    dim_names.append(sub_bt.title())
                elif len(sub_targets) > 1 and sub_bt.title() not in dim_names:
                    dim_names.append(sub_bt.title())

        # If dim_names is empty but concept_hits matched a concept with attributes (e.g. Owners -> Sales owner)
        if not dim_names and detected_concepts:
            clean_toks = [w.strip("?,.:;'\"").casefold() for w in prompt_for_dims.split() if len(w.strip("?,.:;'\"")) >= 3]
            for c in detected_concepts:
                if target_domain and c.domain and c.domain.casefold() != target_domain.casefold():
                    continue
                for a in c.attributes:
                    a_low = a.name.casefold()
                    part_of_metric = any(
                        a_low in m.name.casefold() or a_low in (m.label or "").casefold()
                        for m in detected_metrics
                    )
                    if not part_of_metric and any(re.search(rf"\b{re.escape(tok)}\b", a_low) for tok in clean_toks):
                        dim_names.append(a.name)
                        break
                if dim_names:
                    break

        # Determine minimal set of concepts actually referenced in the query.
        # Scoped to domain_concepts / target_domain when known to prevent unrelated tables or phantom joins.
        needed_concepts: Set[str] = set()
        dim_to_concept: Dict[str, str] = {}
        concept_pool = domain_concepts or list(self.ontology.concepts.values())
        for c in concept_pool:
            if not domain_concepts and target_domain and c.domain and c.domain.casefold() != target_domain.casefold():
                continue
            for a in c.attributes:
                for d in dim_names:
                    if d.casefold() == a.name.casefold():
                        needed_concepts.add(c.name)
                        dim_to_concept[d] = c.name

        # Fallback if domain-scoping filtered everything out
        if not needed_concepts and dim_names:
            for c in self.ontology.concepts.values():
                for a in c.attributes:
                    for d in dim_names:
                        if d.casefold() == a.name.casefold():
                            needed_concepts.add(c.name)
                            dim_to_concept[d] = c.name

        for m in detected_metrics:
            if m.concept:
                needed_concepts.add(m.concept)

        if not needed_concepts and detected_concepts:
            for c in detected_concepts:
                if not target_domain or not c.domain or c.domain.casefold() == target_domain.casefold():
                    needed_concepts.add(c.name)

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

        # Fix 4: If 'unique' or 'distinct' requested and no metric detected, select COUNT(DISTINCT dim)
        if is_unique and not detected_metrics and dim_names:
            primary_dim = dim_names[0]
            dim_col = f'"{primary_dim}"' if re.search(r"[^A-Za-z0-9_]", primary_dim) else primary_dim
            alias = f"UNIQUE_{re.sub(r'[^A-Za-z0-9_]', '_', primary_dim).upper()}"
            select_parts.append(f"    COUNT(DISTINCT {dim_col}) AS {alias}")
            # If there are additional dimensions (e.g. by campaign), group by them
            for idx, dim in enumerate(dim_names[1:], 1):
                d_col = f'"{dim}"' if re.search(r"[^A-Za-z0-9_]", dim) else dim
                select_parts.insert(idx - 1, f"    {d_col}")
                group_by_parts.append(str(idx))
        else:
            for idx, dim in enumerate(dim_names, 1):
                dim_col = f'"{dim}"' if re.search(r"[^A-Za-z0-9_]", dim) else dim
                select_parts.append(f"    {dim_col}")
                if detected_metrics:
                    group_by_parts.append(str(idx))

        metric_names = []
        all_metric_identifiers: List[str] = []
        for m in detected_metrics:
            metric_names.append(m.name)
            all_metric_identifiers.append(m.name)
            if m.label and m.label != m.name and m.label not in all_metric_identifiers:
                all_metric_identifiers.append(m.label)
            spaced = m.name.replace("_", " ").title()
            if spaced not in all_metric_identifiers:
                all_metric_identifiers.append(spaced)
            if "PCT" in m.name or m.name.endswith("_PCT"):
                pct_alias = m.name.replace("_PCT", " %").replace("_", " ").title()
                if pct_alias not in all_metric_identifiers:
                    all_metric_identifiers.append(pct_alias)

            if semantic_view:
                metric_col = f'"{m.name}"' if re.search(r"[^A-Za-z0-9_]", m.name) else m.name
                select_parts.append(f"    {metric_col}")
            else:
                is_dax = bool(re.search(r"\b(CALCULATE|COUNTROWS|DIVIDE|VAR\s+\w+\s*=|RETURN)\b|'[^']+'\[[^\]]+\]", m.sql_expression or "", re.I))
                if m.sql_expression and not is_dax:
                    expr = self._expand_metric_expression(m, metrics_by_key)
                else:
                    agg = (m.aggregation_type or "SUM").upper()
                    expr = f"{agg}({m.name})" if agg != "NONE" else m.name
                alias = f'"{m.name}"' if re.search(r"[^A-Za-z0-9_]", m.name) else m.name
                select_parts.append(f"    {expr} AS {alias}")

        if not select_parts:
            select_parts.append("    *")

        if semantic_view:
            if detected_metrics:
                m_list = ", ".join(f'"{m.name}"' if re.search(r"[^A-Za-z0-9_]", m.name) else m.name for m in detected_metrics)
                d_list = ", ".join(f'"{d}"' if re.search(r"[^A-Za-z0-9_]", d) else d for d in dim_names)
                from_target = f"SEMANTIC_VIEW({semantic_view} METRICS {m_list}" + (f" DIMENSIONS {d_list}" if d_list else "") + ")"
            elif dim_names:
                d_list = ", ".join(f'"{d}"' if re.search(r"[^A-Za-z0-9_]", d) else d for d in dim_names)
                from_target = f"SEMANTIC_VIEW({semantic_view} DIMENSIONS {d_list})"
            else:
                from_target = f"SEMANTIC_VIEW({semantic_view})"
        else:
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
        sql_lines.append(f"FROM {from_target}")

        for j in join_clauses:
            sql_lines.append(f"{j}")

        if filters:
            sql_lines.append(f"WHERE {' AND '.join(filters)}")

        if group_by_parts:
            sql_lines.append(f"GROUP BY {', '.join(group_by_parts)}")

        sql_lines.append("ORDER BY 1 DESC")
        sql_lines.append(f"LIMIT {limit_val};")

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
        """Expand composite metric references (``derives_from``) into executable SQL."""
        expr = (metric.sql_expression or "").strip()
        if not expr:
            return f"SUM({metric.name})"
        deps = list(getattr(metric, "derives_from", None) or [])
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
            # Replace bracketed [dep_name] / [dep_label] first, then bare word boundary matches
            for token in filter(None, [dep_m.name, dep_m.label, dep_name]):
                expanded = re.sub(
                    r"\[" + re.escape(token) + r"\]",
                    f"({dep_sql})",
                    expanded,
                    flags=re.IGNORECASE,
                )
            expanded = re.sub(
                r"\b" + re.escape(dep_m.name) + r"\b",
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
        """Append enforceable SQL filter expressions from concepts, metrics, and domain policies."""
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

        # Check domain-level and federated policies
        all_policies = list(getattr(self.ontology, "policies", {}).values())
        for dom in self.ontology.domains.values():
            for pol in getattr(dom, "policies", None) or []:
                if pol not in all_policies:
                    all_policies.append(pol)

        for pol in all_policies:
            applies = [a.strip().casefold() for a in (getattr(pol, "applies_to", None) or []) if a.strip()]
            pol_dom = (getattr(pol, "domain", None) or "").strip().casefold()
            if not applies or "*" in applies:
                if not pol_dom or pol_dom in active_domains or not active_domains:
                    add_filter(pol.filter_expression, pol.name)
            elif any(a in active_names for a in applies):
                add_filter(pol.filter_expression, pol.name)

    _DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}

    def _resolve_date_column(self, detected_concepts: List[DomainConcept]) -> Optional[str]:
        """Find the date attribute to filter on, from the ontology rather than a hard-coded name.

        Prefers date-typed attributes on the concepts the question resolved to. If none of
        those has one, uses the ontology-wide date attribute only when it is unambiguous
        (exactly one distinct name). Returns None when it cannot be determined.
        """

        def date_attrs(concepts: List[DomainConcept]) -> List[str]:
            found: List[str] = []
            for c in concepts:
                for a in c.attributes:
                    base = (a.data_type or "").split("(")[0].strip().upper()
                    if base in self._DATE_TYPES and a.name not in found:
                        found.append(a.name)
            return found

        def pick(names: List[str]) -> str:
            exact = [n for n in names if n.casefold() == "date"]
            partial = [n for n in names if "date" in n.casefold()]
            return (exact or partial or names)[0]

        local = date_attrs(detected_concepts)
        if local:
            return pick(local)

        global_names = date_attrs(list(self.ontology.concepts.values()))
        if len(global_names) == 1:
            return global_names[0]
        return None

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
