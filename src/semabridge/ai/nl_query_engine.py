"""Natural Language to Governed Semantic Query & SQL Engine.

Compiles plain English questions deterministically against a per-project
NLP lexicon built from ontology metadata, sqlglot-parsed metric filter
predicates, and deployed semantic view artifacts (dimensions & sample values).

Operates completely locally with zero Snowflake credit consumption.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.ai.nlp_lexicon import (
    _NUMERIC_TYPES,
    _TIME_SHIFT_TAGS,
    LexiconDimensionEntry,
    LexiconMetricEntry,
    ParsedMeasurePhrase,
    ParsedQuestion,
    ProjectLexicon,
    content_lemmas,
    lemmatize_phrase_tokens,
    lemmatize_token,
    parse_question,
    split_identifier_text,
    wordnet_related_lemmas,
)
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    FederatedEnterpriseOntology,
    infer_aggregation_from_sql,
)

logger = logging.getLogger("semabridge.ai.nl_query_engine")

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
    answerable: bool = True
    parsed_phrases: Dict[str, Any] = Field(default_factory=dict)
    # True when generated_sql reads physical tables directly (no deployed
    # semantic view): an offline draft, not a governed query.
    is_draft: bool = False


class NLQueryEngine:
    """Interprets natural language questions via generic lexicon & phrase resolution."""

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

    @classmethod
    def _sv_dim_ref(cls, concept: Any, attr: Any, lexicon: ProjectLexicon) -> str:
        """``TABLE_ALIAS.DIM_NAME`` of a dimension inside a deployed semantic view.

        Uses the alias recorded from the deployed view when known (the emitter
        may have de-duplicated it). Otherwise falls back to the same
        sanitisation the Snowflake emitter applies (``IdentifierSanitizer``:
        reserved words -> ``COL_``, e.g. ``DATE`` -> ``COL_DATE``).
        """
        c_cf = str(getattr(concept, "name", "") or "").casefold()
        a_cf = str(getattr(attr, "name", "") or "").casefold()
        for de in lexicon.dimensions:
            if de.sv_dim_alias and de.concept.name.casefold() == c_cf and de.attribute.name.casefold() == a_cf:
                return f"{cls._q_ident(de.sv_dim_alias[0])}.{cls._q_ident(de.sv_dim_alias[1])}"
        tbl = getattr(concept, "logical_name", None) or cls._to_logical_id(concept.name)
        col = getattr(attr, "logical_name", None) or cls._to_logical_id(attr.name)
        return f"{cls._q_ident(tbl)}.{cls._q_ident(col)}"

    @staticmethod
    def _unapplied_time_intent_warnings(
        parsed: ParsedQuestion,
        lexicon: ProjectLexicon,
        detected_metrics: List[DomainMetric],
        dim_to_pair: Dict[str, Tuple[DomainConcept, DomainAttribute]],
    ) -> List[str]:
        """Warn for each time/trend/growth phrase the generated query does not apply.

        A phrase counts as applied only when a selected metric itself expresses
        it (its name/label or time-intelligence tag, e.g. a "Revenue Growth" or
        "Sales YTD" metric) or, for "trend"/"over time", when the query groups
        by a date dimension. Relative periods ("last quarter", "this year") are
        never turned into filters here, so they are reported, not dropped.
        """
        if not parsed.time_intent_phrases:
            return []
        by_name = {me.metric.name: me for me in lexicon.metrics}
        m_lemmas: Set[str] = set()
        m_tags: Set[str] = set()
        for m in detected_metrics:
            me = by_name.get(m.name)
            if me:
                m_lemmas |= me.name_label_lemmas
                m_tags |= me.time_shift_tags
            else:
                m_lemmas |= set(content_lemmas(f"{m.name} {m.label or ''}"))
        groups_by_date = any(
            (a.data_type or "").split("(")[0].strip().upper() in {"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
            for _, a in dim_to_pair.values()
        )
        names = ", ".join(f"'{m.label or m.name}'" for m in detected_metrics)
        out: List[str] = []
        for phrase in parsed.time_intent_phrases:
            p_cf = phrase.casefold()
            if re.match(r"^(?:trends?|trending|over time)", p_cf):
                if groups_by_date:
                    continue
                out.append(
                    f"'{phrase}' was not applied: no date dimension is grouped, so {names} is a single total. "
                    "Ask e.g. '... by <date column>' to see it over time."
                )
                continue
            p_lemmas = set(content_lemmas(phrase))
            if p_lemmas and (p_lemmas <= (m_lemmas | m_tags)):
                continue
            if re.match(r"^(?:last|previous|prior|past|this|current|next)\s", p_cf):
                out.append(
                    f"'{phrase}' was not applied: relative periods are not turned into filters, so {names} "
                    "covers all periods. Ask with an explicit year/quarter (e.g. 'in 2024 Q3') instead."
                )
            else:
                out.append(
                    f"'{phrase}' was not applied: no selected metric computes it, so {names} "
                    "is shown as a plain total."
                )
        return out

    @staticmethod
    def _stem_words(text: str) -> Set[str]:
        """Lemmatize alphanumeric tokens via NLTK WordNet."""
        return {lemmatize_token(w) for w in re.findall(r"[a-z0-9]+", split_identifier_text(text).casefold()) if w}

    @staticmethod
    def _effective_aggregation(m: DomainMetric) -> str:
        """Aggregation actually computed by the metric.

        The compiled SQL wins when it is a single aggregate call (it is what
        executes; legacy ontologies may carry a stale default of SUM on a
        COUNT metric). Otherwise the declared aggregation, else NONE.
        """
        from_sql = infer_aggregation_from_sql(getattr(m, "sql_expression", None))
        if from_sql != "NONE":
            return from_sql
        return (getattr(m, "aggregation_type", None) or "NONE").strip().upper() or "NONE"

    @staticmethod
    def _is_sql_count_metric(m: DomainMetric) -> bool:
        """Return True when the metric's own aggregation or SQL defines a count."""
        m_text = f"{m.name} {m.label or ''}"
        if re.search(r"(%|_pct|\bpct\b|percent|ratio|share|\brate\b|\bmom|\byoy|\bqoq)", m_text, re.I):
            return False
        agg = NLQueryEngine._effective_aggregation(m)
        if agg in {"COUNT", "COUNT_DISTINCT", "DISTINCTCOUNT", "DISTINCT_COUNT"}:
            return True
        sx = (m.sql_expression or "").strip()
        if sx:
            if re.search(r"/\s*(?:NULLIF|COALESCE|IFF|\()?\s*(?:[A-Za-z0-9_.\"]*\s*)*\b(?:SUM|COUNT|AVG)\b", sx, re.I):
                return False
            if re.search(r"\b(?:COUNT|COUNT_IF|APPROX_COUNT_DISTINCT|DISTINCTCOUNT|COUNTROWS|COUNTA|COUNTX)\s*\(", sx, re.I):
                return True
        dax = (getattr(m, "dax_expression", None) or "").strip()
        dr = (getattr(m, "deploy_reason", None) or "").strip()
        if dax and ("non-additive" in dr.lower() or "non additive" in dr.lower()):
            if "/" not in dax and re.search(r"\b(?:COUNT|COUNTA|COUNTX|DISTINCTCOUNT|COUNTROWS)\s*\(", dax, re.I):
                return True
        return False

    @classmethod
    def _metric_invalid_reason(
        cls,
        m: DomainMetric,
        deploy_error: Optional[str] = None,
        using_semantic_view: bool = False,
        metrics_by_key: Optional[Dict[str, DomainMetric]] = None,
        physical_attr_keys: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Return a deploy/translation/path reason if metric lacks valid executable SQL."""
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
            expanded_sx = cls._expand_metric_expression(m, metrics_by_key) if metrics_by_key else sx
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
                            "which is only executable via a deployed SEMANTIC_VIEW"
                        )
        return None

    # ------------------------------------------------------------------
    # Generic Phrase Scoring Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_measure_phrase(
        phrase: ParsedMeasurePhrase,
        lexicon: ProjectLexicon,
        parsed: ParsedQuestion,
        all_concept_lemmas: Set[str],
    ) -> Tuple[List[Tuple[float, float, LexiconMetricEntry]], str]:
        """Score every metric in the project lexicon against one parsed measure phrase."""
        raw_explicit = list(dict.fromkeys(phrase.content_lemmas))

        # When a temporal year/quarter filter is present, strip verb/noun tokens that name a DATE/TIMESTAMP
        # column (e.g. 'created' matching 'Case Created On') so they act as date anchors, not missing metrics.
        date_attr_lemmas: Set[str] = set()
        if parsed.year_filters or parsed.quarter_filters:
            for de in lexicon.dimensions:
                if de.is_date:
                    date_attr_lemmas.update(de.lemmas - de.concept_lemmas)

        explicit_toks = [t for t in raw_explicit if t not in date_attr_lemmas] or raw_explicit
        # "number of <X>" is count syntax (like "count of"/"how many"), not a
        # content word that a metric must spell. Drop it so the noun after it is
        # matched; the phrase still sets COUNT intent and exact-label matching
        # still uses the raw text (a metric literally called "Number of X").
        if parsed.aggregation_intent in ("COUNT", "DISTINCT") and re.search(r"\bnumber\s+of\b", phrase.raw_text, re.I):
            explicit_toks = [t for t in explicit_toks if t != "number"] or explicit_toks
        combined_toks = list(dict.fromkeys(explicit_toks + phrase.inherited_subject_lemmas))
        if not combined_toks:
            return [], "not found"

        non_concept_toks = [t for t in explicit_toks if t not in all_concept_lemmas]
        phrase_set = set(combined_toks)
        total_w = sum(lexicon.weight(t) for t in combined_toks) or 1.0
        raw_cf = phrase.raw_text.strip().casefold()
        by_lemmas = {tok for bp in parsed.by_phrases for tok in content_lemmas(bp)}

        deriv_cache: Dict[str, frozenset[str]] = {t: wordnet_related_lemmas(t) for t in combined_toks}

        scored: List[Tuple[float, float, LexiconMetricEntry]] = []
        for me in lexicon.metrics:
            matched_w = 0.0
            matched_tokens: Set[str] = set()
            matched_non_concept = False
            matched_name_or_filter = False
            matched_filter_literal = False

            filter_lemmas: Set[str] = set()
            for vals in me.filter_literals.values():
                for v in vals:
                    filter_lemmas.update(lemmatize_phrase_tokens(v, use_pos=False))

            # The metric's aggregated column / description may answer the question
            # only when the question states the same aggregation ("sum of amount"
            # -> a SUM over AMOUNT, never an AVG or COUNT over it).
            intent = parsed.aggregation_intent
            agg_ok = bool(intent) and (
                me.aggregation == intent or (intent == "DISTINCT" and me.aggregation == "COUNT_DISTINCT")
            )
            for tok in combined_toks:
                w = lexicon.weight(tok)
                if tok in filter_lemmas and tok in me.qualifier_lemmas:
                    matched_w += w * 2.6
                    matched_tokens.add(tok)
                    matched_filter_literal = True
                    matched_name_or_filter = True
                    if tok in non_concept_toks:
                        matched_non_concept = True
                elif tok in me.qualifier_lemmas:
                    matched_w += w * 2.2
                    matched_tokens.add(tok)
                    matched_name_or_filter = True
                    if tok in non_concept_toks:
                        matched_non_concept = True
                elif tok in me.name_label_lemmas:
                    matched_w += w * 1.8
                    matched_tokens.add(tok)
                    matched_name_or_filter = True
                    if tok in non_concept_toks:
                        matched_non_concept = True
                elif tok in me.concept_lemmas:
                    matched_w += w * 0.85
                    matched_tokens.add(tok)
                elif agg_ok and (tok in me.measure_lemmas or tok in me.description_lemmas):
                    matched_w += w * (1.5 if tok in me.measure_lemmas else 1.0)
                    matched_tokens.add(tok)
                    matched_name_or_filter = True
                    if tok in non_concept_toks:
                        matched_non_concept = True
                else:
                    tok_fam = deriv_cache[tok]
                    if tok_fam & me.qualifier_lemmas:
                        matched_w += w * 1.6
                        matched_tokens.add(tok)
                        matched_name_or_filter = True
                        if tok in non_concept_toks:
                            matched_non_concept = True
                    elif tok_fam & me.name_label_lemmas:
                        matched_w += w * 1.3
                        matched_tokens.add(tok)
                        matched_name_or_filter = True
                        if tok in non_concept_toks:
                            matched_non_concept = True

            if matched_w <= 0.0:
                continue

            m_syns_cf = {s.casefold() for s in (getattr(me.metric, "synonyms", None) or [])}
            is_exact_phrase = bool(
                (raw_cf and raw_cf in {me.metric.name.casefold(), (me.metric.label or "").casefold()} | m_syns_cf)
                or (
                    len(explicit_toks) >= 1
                    and me.is_pct_or_ratio == parsed.asks_pct_ratio
                    and (
                        set(explicit_toks) == me.name_label_lemmas
                        or (bool(by_lemmas) and set(explicit_toks) | by_lemmas == me.name_label_lemmas)
                    )
                )
            )
            if non_concept_toks and not matched_non_concept and not is_exact_phrase:
                continue
            if not matched_name_or_filter and not is_exact_phrase:
                if not (parsed.aggregation_intent == "COUNT" and me.is_count and not me.has_filter_clause):
                    continue
                extra_specific = me.qualifier_lemmas - phrase_set - parsed.time_shift_tags
                if extra_specific:
                    continue

            coverage = len(matched_tokens) / max(len(combined_toks), 1)
            score = (matched_w / total_w) * (0.45 + 0.55 * coverage)

            q_shifts = parsed.time_shift_tags
            m_shifts = me.time_shift_tags
            if not is_exact_phrase:
                if m_shifts and not parsed.asks_time_intel:
                    score *= 0.25
                elif m_shifts and parsed.asks_time_intel:
                    if m_shifts & q_shifts:
                        score += 0.55
                    elif not q_shifts:
                        score += 0.20
                    else:
                        score *= 0.35

                if me.is_pct_or_ratio and not parsed.asks_pct_ratio and not (m_shifts & q_shifts):
                    score *= 0.30
                elif me.is_pct_or_ratio and parsed.asks_pct_ratio and not (m_shifts and not parsed.asks_time_intel):
                    score += 0.45

                if parsed.aggregation_intent == "COUNT":
                    if me.is_count:
                        score += 0.45
                    elif not parsed.asks_pct_ratio:
                        score *= 0.45
                elif parsed.aggregation_intent in ("AVG", "MIN", "MAX"):
                    agg_tok = parsed.aggregation_intent.lower()
                    if agg_tok in me.name_label_lemmas or NLQueryEngine._effective_aggregation(me.metric) == parsed.aggregation_intent:
                        score += 0.35

                extra_quals = me.qualifier_lemmas - phrase_set - q_shifts - by_lemmas
                if extra_quals:
                    score *= max(0.35, 1.0 - 0.12 * min(len(extra_quals), 4))
                if me.has_filter_clause and not matched_filter_literal and not (non_concept_toks and (set(non_concept_toks) & me.qualifier_lemmas)):
                    score *= 0.45

            if matched_filter_literal:
                score += 0.25

            # Tie-break metric variants whose own qualifier names the requested BY dimension (e.g. 'Cases % by Subject')
            if by_lemmas and (by_lemmas & me.name_label_lemmas):
                score += 0.35

            # Exact metric name/label/synonym matches always outrank partial matches
            if is_exact_phrase:
                score += 2.5

            scored.append((round(score, 4), round(coverage, 4), me))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not scored or scored[0][0] < 0.40:
            return [], "not found"

        if scored[0][1] >= 0.99 and len(combined_toks) >= 2:
            scored = [item for item in scored if item[1] >= 0.99 or item[0] >= scored[0][0] - 0.05]

        top_score = scored[0][0]
        top_exact = raw_cf and raw_cf in {scored[0][2].metric.name.casefold(), (scored[0][2].metric.label or "").casefold()}
        ambig_window = 0.04 if top_exact else 0.15
        if len(scored) > 1 and (top_score - scored[1][0]) <= ambig_window and abs(scored[0][1] - scored[1][1]) < 0.01:
            return scored, "ambiguous"
        return scored, "resolved"

    @staticmethod
    def _resolve_by_phrase(
        raw_by: str,
        lexicon: ProjectLexicon,
    ) -> Tuple[Optional[LexiconDimensionEntry], List[LexiconDimensionEntry], str]:
        """Resolve one BY-clause dimension request strictly to a non-numeric semantic-view dimension."""
        sbt = raw_by.strip()
        sbt_cf = sbt.casefold()
        sbt_nospace = re.sub(r"[^a-z0-9]+", "", sbt_cf)
        bt_lemmas = content_lemmas(sbt)
        bt_set = set(bt_lemmas)

        # Explicitly check numeric columns first so numeric attributes never group
        for _, col_name in lexicon.numeric_columns:
            col_cf = col_name.casefold()
            col_ns = re.sub(r"[^a-z0-9]+", "", col_cf)
            if sbt_cf == col_cf or (sbt_nospace and sbt_nospace == col_ns):
                return None, [], "numeric column cannot group"

        exact_hits: List[LexiconDimensionEntry] = []
        scored: List[Tuple[float, LexiconDimensionEntry]] = []

        for de in lexicon.dimensions:
            a_cf = de.attribute.name.casefold()
            a_ns = re.sub(r"[^a-z0-9]+", "", a_cf)
            c_cf = de.concept.name.casefold()
            syns_cf = {s.casefold() for s in (de.attribute.synonyms or [])}

            if sbt_cf == a_cf or sbt_nospace == a_ns or sbt_cf in syns_cf or sbt_cf == f"{c_cf} {a_cf}":
                exact_hits.append(de)
                continue

            score = 0.0
            if bt_set:
                attr_overlap = bt_set & de.lemmas
                concept_overlap = bt_set & de.concept_lemmas
                if attr_overlap:
                    score += sum(lexicon.weight(t) * 2.0 for t in attr_overlap)
                    # Syntactic head bonus: right-most noun in English compound BY phrase matching attribute lemma
                    if len(bt_lemmas) >= 2 and bt_lemmas[-1] in attr_overlap:
                        score += lexicon.weight(bt_lemmas[-1]) * 1.5
                    if bt_set.issubset(de.lemmas | de.concept_lemmas):
                        score += 1.5
                    is_key_attr = bool(getattr(de.attribute, "is_primary_key", False) or re.search(r"(seq|key|id)$", a_cf))
                    if is_key_attr and not re.search(r"(seq|key|id)\b", sbt_cf):
                        score *= 0.45
                    if concept_overlap:
                        score += 0.6 * len(concept_overlap)
                elif len(bt_lemmas) == 1 and bt_lemmas[0] in a_cf:
                    score += 0.8
            if score > 0.0:
                scored.append((round(score, 4), de))

        if len(exact_hits) == 1:
            return exact_hits[0], exact_hits, "resolved"
        if len(exact_hits) > 1:
            # Deduplicate only distinct physical concepts if same attr exists across multiple tables
            return exact_hits[0], exact_hits, "ambiguous"

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored or scored[0][0] < 0.75:
            return None, [], "not found"

        top_s = scored[0][0]
        tied = [d for s, d in scored if (top_s - s) <= 0.05]
        if len(tied) > 1:
            return tied[0], tied, "ambiguous"
        return scored[0][1], [scored[0][1]], "resolved"

    # ------------------------------------------------------------------
    # Main Entry Point
    # ------------------------------------------------------------------

    def resolve_query(
        self,
        prompt: str,
        dialect: str = "SNOWFLAKE",
        semantic_view: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
        deployment_error: Optional[str] = None,
        offline_draft: Optional[bool] = None,
    ) -> QueryResolutionPlan:
        """Parse natural language prompt against the auto-built project lexicon and compile SQL.

        ``offline_draft`` controls physical-table SQL when no deployed semantic
        view is known: ``False`` refuses (no SQL is produced), ``True`` allows an
        offline draft. ``None`` (not stated) allows it for backward
        compatibility. Whenever physical-table SQL is produced it is labelled
        as a draft (SQL header, warning and ``is_draft``); the ``LOCAL`` dialect
        is itself an explicit draft request.
        """
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        clean_prompt = prompt.strip()

        # 1. Build initial project lexicon (scoped by allowed_domains & active semantic_view when known)
        lexicon = ProjectLexicon.build(
            ontology=self.ontology,
            allowed_domains=allowed_domains,
            semantic_view=semantic_view,
            is_count_fn=self._is_sql_count_metric,
        )
        parsed = parse_question(clean_prompt, lexicon=lexicon)

        all_concept_lemmas: Set[str] = set()
        for c in lexicon.scoped_concepts:
            all_concept_lemmas.update(content_lemmas(f"{c.name} {getattr(c, 'label', None) or ''} {' '.join(c.synonyms or [])}"))
        for me in lexicon.metrics:
            all_concept_lemmas.update(me.concept_lemmas)

        # 2. Resolve each measure phrase independently
        detected_metrics: List[DomainMetric] = []
        seen_metric_names: Set[str] = set()
        measure_trace: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for m_phrase in parsed.measure_phrases:
            candidates, outcome = self._score_measure_phrase(
                phrase=m_phrase,
                lexicon=lexicon,
                parsed=parsed,
                all_concept_lemmas=all_concept_lemmas,
            )
            if outcome == "not found" or not candidates:
                measure_trace.append({"phrase": m_phrase.raw_text, "status": "not found", "selected": None})
                if len(parsed.measure_phrases) > 1 and m_phrase.content_lemmas:
                    warnings.append(f"Measure '{m_phrase.raw_text}' not found in domain.")
                continue

            if outcome == "ambiguous":
                opt_names = [item[2].display_name for item in candidates[:4] if (candidates[0][0] - item[0]) <= 0.15]
                measure_trace.append(
                    {"phrase": m_phrase.raw_text, "status": f"ambiguous ({', '.join(opt_names)})", "selected": opt_names[0]}
                )
                warnings.append(
                    f"Measure phrase '{m_phrase.raw_text}' is ambiguous ({', '.join(opt_names)}); selected '{opt_names[0]}'."
                )
            else:
                measure_trace.append(
                    {"phrase": m_phrase.raw_text, "status": "resolved", "selected": candidates[0][2].display_name}
                )

            chosen_m = candidates[0][2].metric
            if chosen_m.name not in seen_metric_names:
                seen_metric_names.add(chosen_m.name)
                detected_metrics.append(chosen_m)

        # 3. Determine primary domain and rebuild domain-scoped lexicon if multi-domain store lacked allowed_domains
        target_domain: Optional[str] = detected_metrics[0].domain if detected_metrics else None
        if target_domain and not allowed_domains:
            lexicon = ProjectLexicon.build(
                ontology=self.ontology,
                allowed_domains=[target_domain],
                target_domain=target_domain,
                semantic_view=semantic_view,
                is_count_fn=self._is_sql_count_metric,
            )

        target_dom_obj = next(
            (d for d in self.ontology.domains.values() if target_domain and d.domain.casefold() == target_domain.casefold()),
            None,
        )
        effective_deploy_error = deployment_error or (getattr(target_dom_obj, "deployment_error", None) if target_dom_obj else None)

        effective_semantic_view = semantic_view
        if dialect.upper() == "SNOWFLAKE" and not effective_semantic_view:
            if detected_metrics:
                effective_semantic_view = getattr(detected_metrics[0], "semantic_view_name", None)
            if not effective_semantic_view and target_dom_obj:
                effective_semantic_view = getattr(target_dom_obj, "semantic_view_name", None)

        # Re-scope lexicon cleanly if semantic_view was discovered from domain metadata
        if effective_semantic_view and not semantic_view:
            lexicon = ProjectLexicon.build(
                ontology=self.ontology,
                allowed_domains=allowed_domains or ([target_domain] if target_domain else None),
                target_domain=target_domain,
                semantic_view=effective_semantic_view,
                is_count_fn=self._is_sql_count_metric,
            )

        domain_metrics_by_key: Dict[str, DomainMetric] = {}
        for me in lexicon.metrics:
            vm = me.metric
            for k_opt in filter(None, [vm.name, vm.label, getattr(vm, "logical_name", None)]):
                domain_metrics_by_key[k_opt.casefold()] = vm
                domain_metrics_by_key[self._to_logical_id(k_opt).casefold()] = vm

        physical_attr_keys: Set[str] = set()
        for c_chk in lexicon.scoped_concepts:
            for a_chk in c_chk.attributes:
                for a_opt in filter(None, [a_chk.name, getattr(a_chk, "logical_name", None)]):
                    physical_attr_keys.add(a_opt.casefold())
                    physical_attr_keys.add(self._to_logical_id(a_opt).casefold())
                for m_ref in getattr(a_chk, "maps_to", None) or []:
                    col_part = str(m_ref).split(".")[-1].strip()
                    if col_part:
                        physical_attr_keys.add(col_part.casefold())
                        physical_attr_keys.add(self._to_logical_id(col_part).casefold())

        # 4. Validate executable SQL for each detected metric
        matched_before_sql_check = list(detected_metrics)
        answerable_metrics: List[DomainMetric] = []
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
                warnings.append(f"Metric '{m.label or m.name}' is not answerable: {bad_reason}.")
            else:
                answerable_metrics.append(m)
        detected_metrics = answerable_metrics

        # If no metric was found, compute close metric suggestions without hallucinating
        if not detected_metrics and not matched_before_sql_check:
            cand_labels = [me.display_name for me in lexicon.metrics]
            sugs = difflib.get_close_matches(clean_prompt, cand_labels, n=3, cutoff=0.25)
            if not sugs:
                broad_hits = self.search_index.search(
                    clean_prompt,
                    domain_filter=allowed_domains[0] if allowed_domains and len(allowed_domains) == 1 else None,
                    item_type_filter="METRIC",
                    limit=3,
                    min_score=0.05,
                )
                sugs = [h.name for h in broad_hits]
            sug_text = f" Did you mean: {', '.join(sugs)}?" if sugs else ""
            warnings.append(f"Metric not found matching '{clean_prompt}'.{sug_text}")

        # 5. Resolve BY dimensions strictly against semantic-view non-numeric dimensions
        dim_names: List[str] = []
        dim_to_pair: Dict[str, Tuple[DomainConcept, DomainAttribute]] = {}
        by_trace: List[Dict[str, Any]] = []
        all_dim_labels = [de.attribute.name for de in lexicon.dimensions]

        for bp in parsed.by_phrases:
            chosen_de, all_matches, outcome = self._resolve_by_phrase(bp, lexicon)
            if outcome == "numeric column cannot group":
                by_trace.append({"phrase": bp, "status": "not allowed (numeric column)", "selected": None})
                warnings.append(f"Column '{bp}' is numeric and cannot be used as a grouping dimension.")
                continue
            if outcome == "not found" or chosen_de is None:
                close_dims: List[str] = []
                # Check WordNet synonym/hypernym overlap across concepts & attributes (e.g. customer -> Accounts.Account Name)
                try:
                    from nltk.corpus import wordnet as _wn
                    bp_toks = content_lemmas(bp)
                    bp_syn_words: Set[str] = set(bp_toks)
                    for bt in bp_toks:
                        for syn in _wn.synsets(bt):
                            for lm in syn.lemmas():
                                bp_syn_words.add(lemmatize_token(lm.name()))
                            for hyp in syn.hypernyms():
                                for lm in hyp.lemmas():
                                    bp_syn_words.add(lemmatize_token(lm.name()))
                    for de in lexicon.dimensions:
                        if de.is_date:
                            continue
                        if (de.concept_lemmas & bp_syn_words) and ("name" in de.lemmas or len(de.lemmas) <= 2):
                            cand_d = f"{de.concept.name}.{de.attribute.name}"
                            if cand_d not in close_dims:
                                close_dims.append(cand_d)
                except Exception:
                    pass
                if not close_dims:
                    close_dims = difflib.get_close_matches(bp, all_dim_labels, n=2, cutoff=0.55)
                sug_part = f" Did you mean: {', '.join(close_dims[:2])}?" if close_dims else ""
                by_trace.append({"phrase": bp, "status": "not found", "selected": None})
                warnings.append(f"Dimension '{bp}' not found in domain.{sug_part}")
                continue
            if outcome == "ambiguous":
                opts = [f"{d.concept.name}.{d.attribute.name}" for d in all_matches[:4]]
                by_trace.append({"phrase": bp, "status": f"ambiguous ({', '.join(opts)})", "selected": chosen_de.attribute.name})
                warnings.append(
                    f"Dimension '{bp}' is ambiguous ({', '.join(opts)}); selected '{chosen_de.concept.name}.{chosen_de.attribute.name}'."
                )
            else:
                by_trace.append({"phrase": bp, "status": "resolved", "selected": chosen_de.attribute.name})

            if chosen_de.attribute.name not in dim_names:
                dim_names.append(chosen_de.attribute.name)
                dim_to_pair[chosen_de.attribute.name] = (chosen_de.concept, chosen_de.attribute)

        # Inline non-metric attribute check when prompt didn't use a BY clause (exclude WHERE/filter phrases)
        if not parsed.by_phrases and not parsed.filter_phrases:
            measure_only_text = " ".join(mp.raw_text for mp in parsed.measure_phrases)
            prompt_lemmas = set(content_lemmas(measure_only_text))
            for de in lexicon.dimensions:
                if de.is_date or not de.lemmas:
                    continue
                if de.lemmas.issubset(prompt_lemmas) and not any(
                    de.lemmas.issubset(me.name_label_lemmas) for me in lexicon.metrics if me.metric in detected_metrics
                ):
                    if de.attribute.name not in dim_names:
                        dim_names.append(de.attribute.name)
                        dim_to_pair[de.attribute.name] = (de.concept, de.attribute)

        # 6. Collect policy filters & explicit/sample-value filter phrases
        filters: List[str] = []
        applied_policies: List[str] = []
        derived_from_metrics: List[str] = []
        metrics_by_key: Dict[str, DomainMetric] = {k.casefold(): v for k, v in self.ontology.metrics.items()}
        for m in detected_metrics:
            for dep in getattr(m, "derives_from", None) or []:
                dep_m = metrics_by_key.get(dep.casefold())
                dep_name = dep_m.name if dep_m else dep
                if dep_name not in derived_from_metrics:
                    derived_from_metrics.append(dep_name)

        concept_by_name: Dict[str, DomainConcept] = {c.name.casefold(): c for c in lexicon.scoped_concepts}
        metric_owner_concepts = [
            concept_by_name[m.concept.casefold()]
            for m in detected_metrics
            if m.concept and m.concept.casefold() in concept_by_name
        ]
        dim_concepts = [pair[0] for pair in dim_to_pair.values()]
        policy_concepts: List[DomainConcept] = []
        for c_pol in metric_owner_concepts + dim_concepts:
            if c_pol not in policy_concepts:
                policy_concepts.append(c_pol)

        self._collect_policy_filters(
            detected_concepts=policy_concepts,
            detected_metrics=detected_metrics,
            metrics_by_key=metrics_by_key,
            filters=filters,
            applied_policies=applied_policies,
        )

        filter_trace: List[Dict[str, Any]] = []
        for fp in parsed.filter_phrases:
            if not fp.column_hint or not fp.value_text:
                filter_trace.append({"phrase": fp.raw_text, "status": "not found"})
                continue
            f_de, _, f_out = self._resolve_by_phrase(fp.column_hint, lexicon)
            if f_de is None or f_out == "not found":
                filter_trace.append({"phrase": fp.raw_text, "status": "not found"})
                warnings.append(f"Filter column '{fp.column_hint}' not found in domain.")
                continue
            escaped_val = fp.value_text.replace("'", "''")
            if effective_semantic_view:
                pred_sql = f"{self._sv_dim_ref(f_de.concept, f_de.attribute, lexicon)} = '{escaped_val}'"
            else:
                pred_sql = f"{self._q_ident(f_de.attribute.name)} = '{escaped_val}'"
            if pred_sql not in filters:
                filters.append(pred_sql)
            filter_trace.append({"phrase": fp.raw_text, "status": "resolved", "predicate": pred_sql})

        # 7. Resolve needed concepts, date filter, and joins
        needed_concepts: Set[str] = set()
        for c_obj, _ in dim_to_pair.values():
            needed_concepts.add(c_obj.name)
        for m in detected_metrics:
            if m.concept:
                needed_concepts.add(m.concept)

        primary_table: Optional[str] = None
        for m in detected_metrics:
            if m.concept and m.concept in needed_concepts:
                primary_table = m.concept
                break
        if not primary_table and needed_concepts:
            primary_table = sorted(needed_concepts)[0]

        if (parsed.year_filters or parsed.quarter_filters) and detected_metrics and primary_table:
            date_c, date_a = self._resolve_related_date_column(
                primary_table=primary_table,
                needed_concepts=needed_concepts,
                scoped_concepts=lexicon.scoped_concepts,
                lexicon_dimensions=lexicon.dimensions if lexicon.sv_enforced else None,
                target_domain=target_domain,
                has_metrics=bool(detected_metrics),
                prompt_lemmas=set(content_lemmas(clean_prompt)),
            )
            if date_c and date_a:
                if effective_semantic_view:
                    date_ref = self._sv_dim_ref(date_c, date_a, lexicon)
                else:
                    date_ref = self._q_ident(date_a.name)
                if parsed.year_filters:
                    filters.append(f"YEAR({date_ref}) = {parsed.year_filters[0]}")
                if parsed.quarter_filters:
                    filters.append(f"QUARTER({date_ref}) = {parsed.quarter_filters[0]}")
            else:
                warnings.append(
                    "No date dimension related to the metric's table was found; year/quarter filter omitted."
                )
        elif (parsed.year_filters or parsed.quarter_filters) and detected_metrics:
            # Never return an all-time total silently for a question that asked for a period.
            warnings.append(
                "The metric is not linked to a table with a date column; year/quarter filter omitted, "
                "so the result covers all periods."
            )

        # 7b. Relative-period / trend / growth wording that nothing in the plan
        # applies must never be dropped silently.
        if detected_metrics:
            warnings.extend(self._unapplied_time_intent_warnings(parsed, lexicon, detected_metrics, dim_to_pair))

        # 8. Refuse cleanly without fake 'FROM ANALYTICS' fallback when unanswerable
        is_distinct_dim_query = bool(
            parsed.aggregation_intent == "DISTINCT" and not detected_metrics and dim_names and not effective_semantic_view and primary_table
        )
        answerable = bool(detected_metrics) or is_distinct_dim_query

        # Physical-table SQL (no deployed semantic view) is only ever an offline
        # draft: refuse it unless a draft was asked for (or not ruled out).
        is_draft = answerable and not effective_semantic_view
        if is_draft and offline_draft is False and dialect.upper() != "LOCAL":
            warnings.append(
                "No deployed semantic view is known for this project, so no governed SQL can be generated. "
                "Deploy the semantic view, or explicitly request an offline draft against the physical tables."
            )
            answerable = False
            is_draft = False

        parsed_phrases = {
            "aggregation": parsed.aggregation_intent,
            "time": parsed.year_filters + [f"Q{q}" for q in parsed.quarter_filters] + sorted(parsed.time_shift_tags),
            "time_intent": list(parsed.time_intent_phrases),
            "measure": measure_trace,
            "by": by_trace,
            "filter": filter_trace,
        }

        if not answerable:
            refusal_lines = [
                "-- SemaBridge AI Query Refusal (No Executable Dummy Table Fallback)",
                f"-- Question: \"{clean_prompt}\"",
                f"-- Dialect: {dialect.upper()}",
            ]
            for w in warnings:
                refusal_lines.append(f"-- Reason: {w}")
            return QueryResolutionPlan(
                original_prompt=clean_prompt,
                detected_metrics=[],
                detected_dimensions=dim_names,
                filters=filters,
                applied_policies=applied_policies,
                derived_from_metrics=derived_from_metrics,
                resolved_tables=[],
                join_clauses=[],
                generated_sql="\n".join(refusal_lines),
                confidence_score=0.0,
                explanation="; ".join(warnings) if warnings else "Query cannot be answered from this project's ontology.",
                warnings=warnings,
                answerable=False,
                parsed_phrases=parsed_phrases,
            )

        # 9. Build relational joins & executable SQL
        resolved_tables_list = [primary_table] if primary_table else []
        join_clauses: List[str] = []

        if not effective_semantic_view and len(needed_concepts) > 1 and primary_table:
            in_query_tables: Set[str] = {primary_table.casefold()}
            remaining_concepts = {c for c in needed_concepts if c.casefold() not in in_query_tables}
            for rel in self.ontology.relationships:
                if not remaining_concepts:
                    break
                if getattr(rel, "is_active", True) is False:
                    continue
                from_c, to_c = rel.from_concept, rel.to_concept
                if from_c.casefold() in in_query_tables and to_c.casefold() in {c.casefold() for c in remaining_concepts}:
                    clean_join = self._quote_join_expression(rel.join_expression)
                    if clean_join:
                        tbl_str = f'"{to_c}"' if " " in to_c else to_c
                        join_clauses.append(f"LEFT JOIN {tbl_str} ON {clean_join}")
                        in_query_tables.add(to_c.casefold())
                        resolved_tables_list.append(to_c)
                        remaining_concepts = {c for c in remaining_concepts if c.casefold() != to_c.casefold()}
                elif to_c.casefold() in in_query_tables and from_c.casefold() in {c.casefold() for c in remaining_concepts}:
                    clean_join = self._quote_join_expression(rel.join_expression)
                    if clean_join:
                        tbl_str = f'"{from_c}"' if " " in from_c else from_c
                        join_clauses.append(f"LEFT JOIN {tbl_str} ON {clean_join}")
                        in_query_tables.add(from_c.casefold())
                        resolved_tables_list.append(from_c)
                        remaining_concepts = {c for c in remaining_concepts if c.casefold() != from_c.casefold()}

        limit_val = parsed.top_n or 100
        select_parts: List[str] = []
        group_by_parts: List[str] = []
        first_metric_order_col: Optional[str] = None

        if effective_semantic_view:
            sv_metrics: List[str] = []
            sv_dims: List[str] = []
            for m in detected_metrics:
                m_c_name = m.concept or primary_table or ""
                m_c_obj = concept_by_name.get(m_c_name.casefold())
                tbl_logical = self._q_ident(
                    (getattr(m_c_obj, "logical_name", None) if m_c_obj else None)
                    or self._to_logical_id(m_c_name)
                )
                met_logical = self._q_ident(getattr(m, "logical_name", None) or self._to_logical_id(m.name))
                sv_metrics.append(f"{tbl_logical}.{met_logical}")
                if not first_metric_order_col:
                    first_metric_order_col = met_logical

            for d in dim_names:
                c_obj, a_obj = dim_to_pair[d]
                sv_dims.append(self._sv_dim_ref(c_obj, a_obj, lexicon))

            sv_clauses: List[str] = [effective_semantic_view]
            if sv_metrics:
                sv_clauses.append(f"METRICS {', '.join(sv_metrics)}")
            if sv_dims:
                sv_clauses.append(f"DIMENSIONS {', '.join(sv_dims)}")
            if filters and (sv_metrics or sv_dims):
                sv_clauses.append(f"WHERE {' AND '.join(filters)}")

            select_parts = ["    *"]
            from_target: Optional[str] = "SEMANTIC_VIEW(" + " ".join(sv_clauses) + ")"
        else:
            if is_distinct_dim_query:
                primary_dim = dim_names[0]
                dim_col = self._q_ident(primary_dim)
                alias = f"UNIQUE_{re.sub(r'[^A-Za-z0-9_]', '_', primary_dim).upper()}"
                select_parts.append(f"    COUNT(DISTINCT {dim_col}) AS {alias}")
                for idx, dim in enumerate(dim_names[1:], 1):
                    select_parts.insert(idx - 1, f"    {self._q_ident(dim)}")
                    group_by_parts.append(str(idx))
            else:
                for idx, dim in enumerate(dim_names, 1):
                    select_parts.append(f"    {self._q_ident(dim)}")
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

            tbl_name = primary_table or ""
            from_target = f'"{tbl_name}"' if " " in tbl_name else tbl_name

        target_engine = (
            "Snowflake Cortex Analyst & Semantic View"
            if dialect.upper() == "SNOWFLAKE"
            else "Databricks AI/BI Genie & Unity Catalog Metric View"
            if dialect.upper() == "DATABRICKS"
            else "SemaBridge Local Engine"
        )
        if is_draft:
            target_engine = "SemaBridge Local Engine (offline draft on physical tables)"
            warnings.append(
                "OFFLINE DRAFT: this SQL reads the physical tables directly, not a deployed semantic view; "
                "it is not governed and must be reviewed before use."
            )
        sql_lines: List[str] = [
            "-- SemaBridge AI OFFLINE DRAFT (physical tables, no semantic view; not governed)"
            if is_draft
            else "-- SemaBridge AI Generated Governed Semantic Query",
            f"-- Target Engine: {target_engine}",
            f"-- Question: \"{clean_prompt}\"",
            f"-- Dialect: {dialect.upper()}",
        ]
        for m in detected_metrics:
            agg = self._effective_aggregation(m)
            sql_lines.append(f"-- Metric: {m.label or m.name} (Aggregation: {agg})")
        sql_lines.append("SELECT")
        sql_lines.append(",\n".join(select_parts))
        if from_target:
            sql_lines.append(f"FROM {from_target}")
        for j in join_clauses:
            sql_lines.append(j)
        if not effective_semantic_view and filters:
            sql_lines.append(f"WHERE {' AND '.join(filters)}")
        if not effective_semantic_view and group_by_parts:
            sql_lines.append(f"GROUP BY {', '.join(group_by_parts)}")
        order_col = first_metric_order_col or "1"
        sql_lines.append(f"ORDER BY {order_col} DESC")
        sql_lines.append(f"LIMIT {limit_val};")

        generated_sql = "\n".join(sql_lines)
        if dialect.upper() == "DATABRICKS":
            generated_sql = re.sub(r"\bIFF\(", "IF(", generated_sql, flags=re.IGNORECASE)

        confidence = min(0.95, 0.55 + (0.35 if dim_names else 0.15) - (0.10 if warnings else 0.0))
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
            confidence_score=round(max(confidence, 0.0), 2),
            explanation="; ".join(explanation_parts) if explanation_parts else "Resolved against semantic ontology.",
            warnings=warnings,
            answerable=True,
            parsed_phrases=parsed_phrases,
            is_draft=is_draft,
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
        """Append enforceable SQL filter expressions when their domain or target is active."""
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
        lexicon_dimensions: Optional[List[LexiconDimensionEntry]] = None,
        target_domain: Optional[str] = None,
        has_metrics: bool = True,
        prompt_lemmas: Optional[Set[str]] = None,
    ) -> Tuple[Optional[DomainConcept], Optional[DomainAttribute]]:
        """Find a date dimension attribute directly on or actively related to the metric's table."""
        by_name: Dict[str, DomainConcept] = {c.name.casefold(): c for c in scoped_concepts}
        sv_allowed_pairs: Optional[Set[Tuple[str, str]]] = None
        if lexicon_dimensions is not None:
            sv_allowed_pairs = {
                (de.concept.name.casefold(), de.attribute.name.casefold())
                for de in lexicon_dimensions
                if de.is_date
            }

        p_lems = prompt_lemmas or set()

        def pick_date_attr(c: DomainConcept) -> Optional[DomainAttribute]:
            date_attrs = [
                a for a in c.attributes
                if (a.data_type or "").split("(")[0].strip().upper() in self._DATE_TYPES
                and (sv_allowed_pairs is None or (c.name.casefold(), a.name.casefold()) in sv_allowed_pairs)
            ]
            if not date_attrs:
                return None
            if p_lems:
                verb_matched = [a for a in date_attrs if set(content_lemmas(a.name)) & p_lems]
                if verb_matched:
                    return verb_matched[0]
            exact = [a for a in date_attrs if a.name.casefold() == "date"]
            partial = [a for a in date_attrs if "date" in a.name.casefold()]
            return (exact or partial or date_attrs)[0]

        anchor_names = (
            {primary_table.casefold()}
            if has_metrics and primary_table
            else {n.casefold() for n in needed_concepts} | ({primary_table.casefold()} if primary_table else set())
        )

        # If the user specifically named a date verb/attribute (e.g. 'created' -> Case Created On), prefer direct match first
        if p_lems and primary_table:
            p_obj = by_name.get(primary_table.casefold())
            if p_obj:
                d_attrs = [
                    a for a in p_obj.attributes
                    if (a.data_type or "").split("(")[0].strip().upper() in self._DATE_TYPES
                    and (sv_allowed_pairs is None or (p_obj.name.casefold(), a.name.casefold()) in sv_allowed_pairs)
                    and (set(content_lemmas(a.name)) & p_lems)
                ]
                if d_attrs:
                    return p_obj, d_attrs[0]

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

        check_order = [primary_table] if has_metrics else [primary_table, *sorted(needed_concepts)]
        for cname in check_order:
            c_obj = by_name.get(cname.casefold())
            if c_obj:
                attr = pick_date_attr(c_obj)
                if attr:
                    return c_obj, attr

        return None, None

    @staticmethod
    def _quote_join_expression(expr: Optional[str]) -> Optional[str]:
        """Quote identifiers with spaces in join condition parts."""
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
