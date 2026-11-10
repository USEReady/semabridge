"""Embedded SemaBridge AI & Semantic Search Engine.

Provides zero-credit, local semantic and hybrid search across enterprise ontologies,
metrics, and business concepts without requiring Snowflake Cortex Search or external credits.
Combines BM25 token matching, synonym expansion, and dense vector similarity.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.ontology.models import FederatedEnterpriseOntology

logger = logging.getLogger("semabridge.ai.semantic_search")


class SearchHit(BaseModel):
    """Result item returned by semantic search."""
    item_id: str
    name: str
    label: Optional[str] = None
    item_type: str  # 'CONCEPT', 'METRIC', 'ATTRIBUTE', 'RELATIONSHIP'
    domain: Optional[str] = None
    description: Optional[str] = None
    score: float
    match_type: str  # 'EXACT', 'SYNONYM', 'FUZZY_BM25', 'SEMANTIC_VECTOR', 'HYBRID'
    matched_term: Optional[str] = None
    sql_expression: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    business_rules: List[str] = Field(default_factory=list)
    data_steward: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


def _stem_token(tok: str) -> str:
    """Lightweight generic plural-to-singular token normalizer."""
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 4 and tok.endswith(("ches", "shes", "xes", "zzes", "sses", "tuses", "nuses", "puses")):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def _tokenize(text: str) -> List[str]:
    """Tokenize and normalize text into clean lower-case alphanumeric tokens."""
    if not text:
        return []
    # Preserve '%' as 'percent' and split snake_case underscores cleanly
    cleaned = text.replace("%", " percent ").replace("_", " ")
    # Normalize standard BI time-intelligence phrases to canonical short tags before splitting
    cleaned = re.sub(r"\byear[- ]?to[- ]?date\b", " ytd ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bquarter[- ]?to[- ]?date\b", " qtd ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bmonth[- ]?to[- ]?date\b", " mtd ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bprevious\s+year\b", " py ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bprevious\s+month\b", " pm ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bprevious\s+quarter\b", " pq ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bsame\s+period\s+last\s+year\b", " sply ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bmoving\s+annual\s+total\b", " mat ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bmonth(?:[-\s]+over[-\s]+month)\b", " mom ", cleaned, flags=re.I)
    cleaned = re.sub(r"\byear(?:[-\s]+over[-\s]+year)\b", " yoy ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bquarter(?:[-\s]+over[-\s]+quarter)\b", " qoq ", cleaned, flags=re.I)
    # Normalize mixed-case YoY/MoM/QoQ before camelCase splitting so 'YoY' doesn't become 'Yo Y'
    cleaned = re.sub(r"\b(YoY|MoM|QoQ|YTD|QTD|MTD|SPLY|MAT)\b", lambda m: m.group(1).lower(), cleaned)
    # Split camelCase boundaries
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9 ]", " ", cleaned)
    raw_tokens = [t.strip().casefold() for t in cleaned.split() if t.strip()]
    tokens: List[str] = []
    for tok in raw_tokens:
        if tok in ("pct", "percentage"):
            tokens.append("percent")
        elif tok == "average":
            tokens.append("avg")
        else:
            tokens.append(_stem_token(tok))
    return tokens


def _compute_char_ngram_vector(text: str, dim: int = 128) -> List[float]:
    """Generate a fast, deterministic dense vector using character n-gram feature hashing.
    
    Zero-credit, local, and sub-millisecond performance without requiring PyTorch or external APIs.
    """
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * dim

    vec = [0.0] * dim
    # Unigrams and bigrams
    ngrams: List[str] = []
    for tok in tokens:
        ngrams.append(tok)
        for i in range(len(tok) - 2):
            ngrams.append(tok[i : i + 3])

    for i in range(len(tokens) - 1):
        ngrams.append(f"{tokens[i]}_{tokens[i+1]}")

    for ng in ngrams:
        # Deterministic hash bucket
        h = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
        vec[idx] += sign

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 1e-9:
        vec = [x / norm for x in vec]
    return vec


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Cosine similarity between two normalized vectors."""
    dot = sum(a * b for a, b in zip(v1, v2))
    return max(0.0, min(1.0, dot))


class SemanticSearchIndex:
    """In-memory hybrid semantic search engine for enterprise models and ontologies."""

    def __init__(self, vector_dim: int = 128):
        self.vector_dim = vector_dim
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.doc_vectors: Dict[str, List[float]] = {}
        # Inverted index for BM25
        self.inverted_index: Dict[str, Set[str]] = {}
        self.doc_lengths: Dict[str, int] = {}
        self.avg_doc_len: float = 1.0

    def index_ontology(self, ontology: FederatedEnterpriseOntology) -> int:
        """Index all concepts, metrics, and attributes from a federated enterprise ontology."""
        self.docs.clear()
        self.doc_vectors.clear()
        self.inverted_index.clear()
        self.doc_lengths.clear()

        # 1. Index Concepts
        for concept in ontology.concepts.values():
            doc_id = f"concept:{concept.name.casefold()}"
            corpus_text = f"{concept.name} {concept.label or ''} {concept.description or ''} {' '.join(concept.synonyms)} {' '.join(concept.business_rules)} {' '.join(concept.tags)}"
            for attr in concept.attributes:
                corpus_text += f" {attr.name} {attr.description or ''} {' '.join(attr.synonyms)}"

            self._add_document(
                doc_id=doc_id,
                name=concept.name,
                item_type="CONCEPT",
                domain=concept.domain,
                description=concept.description,
                corpus_text=corpus_text,
                synonyms=concept.synonyms,
                business_rules=concept.business_rules,
                data_steward=concept.data_steward,
                tags=concept.tags,
            )

        # 2. Index Metrics
        # Note: sql_expression is intentionally omitted from corpus_text to prevent SQL keywords
        # (e.g. 'CASE WHEN', 'SUM', 'COUNT') or table names inside formulas from creating false positive BM25 hits.
        for metric in ontology.metrics.values():
            doc_id = f"metric:{metric.name.casefold()}"
            corpus_text = f"{metric.name} {metric.label or ''} {metric.concept or ''} {metric.description or ''} {' '.join(metric.synonyms)} {' '.join(metric.business_rules)} {' '.join(metric.tags)}"
            display_label = metric.label or (metric.name.replace("_", " ").title() if "_" in metric.name else metric.name)
            self._add_document(
                doc_id=doc_id,
                name=metric.name,
                label=display_label,
                item_type="METRIC",
                domain=metric.domain,
                description=metric.description,
                corpus_text=corpus_text,
                sql_expression=metric.sql_expression,
                synonyms=metric.synonyms,
                business_rules=metric.business_rules,
                data_steward=metric.data_steward,
                tags=metric.tags,
            )

        # 3. Index Attributes (Columns)
        # Enables dimension discovery so queries like "show case count by agent" resolve "agent" as an attribute.
        for concept in ontology.concepts.values():
            for attr in concept.attributes:
                doc_id = f"attribute:{concept.name.casefold()}:{attr.name.casefold()}"
                corpus_text = f"{attr.name} {attr.description or ''} {' '.join(attr.synonyms)} {concept.name}"
                self._add_document(
                    doc_id=doc_id,
                    name=attr.name,
                    label=attr.name,
                    item_type="ATTRIBUTE",
                    domain=concept.domain,
                    description=attr.description,
                    corpus_text=corpus_text,
                    synonyms=attr.synonyms,
                    tags=[concept.name],
                )

        # Compute average document length
        total_len = sum(self.doc_lengths.values())
        self.avg_doc_len = (total_len / len(self.docs)) if self.docs else 1.0
        logger.info("Indexed %d documents into SemaBridge Semantic Index", len(self.docs))
        return len(self.docs)

    def _add_document(
        self,
        doc_id: str,
        name: str,
        item_type: str,
        domain: Optional[str],
        description: Optional[str],
        corpus_text: str,
        label: Optional[str] = None,
        sql_expression: Optional[str] = None,
        synonyms: Optional[List[str]] = None,
        business_rules: Optional[List[str]] = None,
        data_steward: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        syns = synonyms or []
        rules = business_rules or []
        tgs = tags or []

        tokens = _tokenize(corpus_text)
        self.docs[doc_id] = {
            "item_id": doc_id,
            "name": name,
            "label": label or name,
            "item_type": item_type,
            "domain": domain,
            "description": description,
            "sql_expression": sql_expression,
            "synonyms": syns,
            "business_rules": rules,
            "data_steward": data_steward,
            "tags": tgs,
            "tokens": tokens,
        }
        self.doc_lengths[doc_id] = len(tokens)
        self.doc_vectors[doc_id] = _compute_char_ngram_vector(corpus_text, self.vector_dim)

        for tok in set(tokens):
            if tok not in self.inverted_index:
                self.inverted_index[tok] = set()
            self.inverted_index[tok].add(doc_id)

    def search(
        self,
        query: str,
        domain_filter: Optional[str] = None,
        item_type_filter: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.15,
    ) -> List[SearchHit]:
        """Perform hybrid search (Exact + BM25 + Dense Semantic Vector) over the index."""
        if not query or not query.strip():
            return []

        query_clean = query.strip()
        query_tokens = _tokenize(query_clean)
        raw_alpha = [w.casefold() for w in re.findall(r"[A-Za-z0-9]+", query_clean)]
        q_bigrams = {
            f"{query_tokens[i]}{query_tokens[i + 1]}" for i in range(len(query_tokens) - 1)
        } | {
            f"{raw_alpha[i]}{raw_alpha[i + 1]}" for i in range(len(raw_alpha) - 1)
        }
        query_vec = _compute_char_ngram_vector(query_clean, self.vector_dim)

        candidates: Set[str] = set()
        for tok in query_tokens:
            if tok in self.inverted_index:
                candidates.update(self.inverted_index[tok])
        for idx_tok, doc_ids in self.inverted_index.items():
            if any(bg in idx_tok for bg in q_bigrams if len(bg) >= 5):
                candidates.update(doc_ids)

        # Ensure domain_filter doesn't starve sparse retrieval due to hits in unrelated domains
        domain_cand_count = (
            sum(
                1 for cid in candidates
                if self.docs[cid]["domain"] and self.docs[cid]["domain"].casefold() == domain_filter.casefold()
            )
            if domain_filter
            else len(candidates)
        )
        if domain_cand_count < limit * 2:
            candidates.update(self.docs.keys())

        # BM25 Parameters
        k1 = 1.5
        b = 0.75
        n_docs = len(self.docs)

        scored_results: List[Tuple[float, str, str, str]] = []  # (score, doc_id, match_type, matched_term)

        for doc_id in candidates:
            doc = self.docs[doc_id]
            if domain_filter and doc["domain"] and doc["domain"].casefold() != domain_filter.casefold():
                continue
            if item_type_filter and doc["item_type"].casefold() != item_type_filter.casefold():
                continue

            # 1. Exact / Substring Name and Label Match
            doc_name_lower = doc["name"].casefold()
            doc_label_lower = (doc.get("label") or "").casefold()
            query_lower = query_clean.casefold()
            doc_name_clean = doc_name_lower.replace("_", " ")
            doc_label_clean = doc_label_lower.replace("_", " ")
            query_clean_spaced = query_lower.replace("_", " ")
            match_type = "FUZZY_BM25"
            matched_term = None
            exact_bonus = 0.0

            name_toks = _tokenize(doc["name"])
            label_toks = _tokenize(doc.get("label") or "")
            q_toks_set = set(query_tokens)
            name_toks_set = set(name_toks) | set(label_toks)
            doc_nospace = re.sub(r"[^a-z0-9]+", "", doc_name_lower)
            doc_label_nospace = re.sub(r"[^a-z0-9]+", "", doc_label_lower)
            bigram_match = any(len(bg) >= 5 and (bg in doc_nospace or bg in doc_label_nospace) for bg in q_bigrams)

            if (
                doc_name_lower == query_lower
                or doc_name_clean == query_clean_spaced
                or doc_label_lower == query_lower
                or doc_label_clean == query_clean_spaced
            ):
                exact_bonus = 1.0
                match_type = "EXACT"
                matched_term = doc["name"]
            elif (
                (doc_name_clean and re.search(rf"(?<!\w){re.escape(doc_name_clean)}(?!\w)", query_clean_spaced))
                or (doc_label_clean and re.search(rf"(?<!\w){re.escape(doc_label_clean)}(?!\w)", query_clean_spaced))
                or (len(query_clean_spaced) > 3 and (query_clean_spaced in doc_name_clean or query_clean_spaced in doc_label_clean))
                or (name_toks_set and name_toks_set.issubset(q_toks_set))
            ):
                cov = len(name_toks_set) / max(1, len(q_toks_set))
                exact_bonus = 0.35 + 0.45 * min(1.0, cov)
                match_type = "EXACT"
                matched_term = doc["name"]
            elif bigram_match:
                cov = min(1.0, 2.0 / max(1, len(name_toks_set)))
                exact_bonus = 0.25 + 0.35 * cov
                match_type = "EXACT"
                matched_term = doc["name"]
            else:
                for s in doc["synonyms"]:
                    s_lower = s.casefold()
                    s_clean = s_lower.replace("_", " ")
                    s_toks = set(_tokenize(s))
                    if (
                        s_clean == query_clean_spaced
                        or (s_clean and re.search(rf"(?<!\w){re.escape(s_clean)}(?!\w)", query_clean_spaced))
                        or (s_toks and s_toks.issubset(q_toks_set))
                    ):
                        cov = len(s_toks) / max(1, len(q_toks_set))
                        exact_bonus = 0.35 + 0.45 * min(1.0, cov)
                        match_type = "SYNONYM"
                        matched_term = s
                        break

            # 2. BM25 Score
            doc_tokens = doc["tokens"]
            doc_len = self.doc_lengths.get(doc_id, 1)
            bm25_score = 0.0

            for q_tok in query_tokens:
                if q_tok in self.inverted_index:
                    df = len(self.inverted_index[q_tok])
                    idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
                    tf = doc_tokens.count(q_tok)
                    tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (doc_len / self.avg_doc_len)))
                    bm25_score += idf * tf_norm

            # Normalize BM25 approximately into [0, 1] range
            norm_bm25 = min(1.0, bm25_score / (bm25_score + 10.0)) if bm25_score > 0 else 0.0

            # 3. Dense Vector Cosine Similarity
            vec_sim = _cosine_similarity(query_vec, self.doc_vectors[doc_id])

            # 4. Hybrid Reciprocal Rank Fusion
            final_score = (exact_bonus * 0.4) + (norm_bm25 * 0.3) + (vec_sim * 0.3)

            if final_score >= min_score:
                if exact_bonus > 0:
                    effective_type = match_type
                elif vec_sim > 0.6 and norm_bm25 > 0.3:
                    effective_type = "HYBRID"
                elif vec_sim > 0.5:
                    effective_type = "SEMANTIC_VECTOR"
                else:
                    effective_type = "FUZZY_BM25"

                scored_results.append((final_score, doc_id, effective_type, matched_term or query_clean))

        # Sort descending by score
        scored_results.sort(key=lambda x: x[0], reverse=True)

        hits: List[SearchHit] = []
        for score, doc_id, m_type, m_term in scored_results[:limit]:
            d = self.docs[doc_id]
            hits.append(
                SearchHit(
                    item_id=d["item_id"],
                    name=d["name"],
                    label=d.get("label"),
                    item_type=d["item_type"],
                    domain=d["domain"],
                    description=d["description"],
                    score=round(score, 4),
                    match_type=m_type,
                    matched_term=m_term,
                    sql_expression=d.get("sql_expression"),
                    synonyms=d.get("synonyms", []),
                    business_rules=d.get("business_rules", []),
                    data_steward=d.get("data_steward"),
                    tags=d.get("tags", []),
                )
            )

        return hits
