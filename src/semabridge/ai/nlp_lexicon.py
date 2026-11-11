"""Per-Project NLP Lexicon Builder, NLTK Lemmatizer, and Question Phrase Parser.

Builds a deterministic, project-scoped semantic lexicon automatically from:
- Ontology metrics (names, labels, synonyms, concept bindings, and filter
  literals parsed directly from SQL/filter expressions via sqlglot)
- Ontology concepts & non-numeric attributes (names, synonyms, sample values)
- Deployed semantic view artifacts (`semantic_view.sql` & `cortex_analyst.yaml`)
  for strict semantic-view dimension allowlists and sample value discovery

Uses NLTK WordNet lemmatization + POS tagging + NLTK English stopwords
(no hand-written domain word lists, no 3-letter prefix matching).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml

try:
    import sqlglot
    from sqlglot import exp as sql_exp
except Exception:  # pragma: no cover
    sqlglot = None  # type: ignore
    sql_exp = None  # type: ignore

logger = logging.getLogger("semabridge.ai.nlp_lexicon")

_NUMERIC_TYPES = frozenset({
    "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT",
    "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "NUMBER", "REAL",
})

_TIME_SHIFT_TAGS = frozenset({"ytd", "qtd", "mtd", "py", "pm", "pq", "sply", "mat", "mom", "yoy", "qoq"})

# Purely syntactic SQL/question aggregation & comparison tokens (not domain vocabulary)
_SYNTAX_STOPWORDS = frozenset({
    "show", "give", "get", "find", "list", "calculate", "compute", "tell", "many", "much",
    "top", "bottom", "limit", "unique", "distinct", "avg", "average", "mean", "sum",
    "total", "count", "min", "minimum", "max", "maximum", "pct", "percent", "percentage",
    "ratio", "rate", "share", "proportion", "vs", "versus", "compare", "compared", "both",
})

_CONJUNCTION_JOINERS = frozenset({"and", "vs", "versus"})

_nltk_ready = False


def _ensure_nltk() -> bool:
    """Verify required NLTK corpora exist locally (downloads quietly once if missing)."""
    global _nltk_ready
    if _nltk_ready:
        return True
    try:
        import nltk
        for resource_path, download_id in (
            ("corpora/wordnet", "wordnet"),
            ("corpora/omw-1.4", "omw-1.4"),
            ("corpora/stopwords", "stopwords"),
            ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
        ):
            try:
                nltk.data.find(resource_path)
            except LookupError:
                nltk.download(download_id, quiet=True)
        _nltk_ready = True
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("NLTK corpus initialization unavailable: %s", exc)
        return False


@lru_cache(maxsize=1)
def get_english_stopwords() -> frozenset[str]:
    """Return NLTK's standard English stopwords merged with pure query-syntax operators."""
    words: Set[str] = set(_SYNTAX_STOPWORDS)
    if _ensure_nltk():
        try:
            from nltk.corpus import stopwords
            words.update(stopwords.words("english"))
        except Exception as exc:  # pragma: no cover
            logger.debug("Could not load NLTK stopwords: %s", exc)
    return frozenset(words)


@lru_cache(maxsize=1)
def _get_lemmatizer():
    _ensure_nltk()
    from nltk.stem import WordNetLemmatizer
    return WordNetLemmatizer()


def _treebank_to_wn_pos(tb_tag: str) -> str:
    from nltk.corpus import wordnet
    if tb_tag.startswith("V"):
        return wordnet.VERB
    if tb_tag.startswith("J"):
        return wordnet.ADJ
    if tb_tag.startswith("R"):
        return wordnet.ADV
    return wordnet.NOUN


@lru_cache(maxsize=16384)
def lemmatize_token(token: str, wn_pos: Optional[str] = None) -> str:
    """Lemmatize a single lowercase alphanumeric token using NLTK WordNet."""
    raw = str(token or "").strip().casefold()
    if not raw or raw.isdigit():
        return raw
    try:
        wnl = _get_lemmatizer()
        if wn_pos:
            cand = wnl.lemmatize(raw, wn_pos)
            if cand != raw:
                return cand
        # Prefer noun inflection when it normalizes cleanly; otherwise check verb/adj bases
        noun_form = wnl.lemmatize(raw, "n")
        if noun_form != raw:
            return noun_form
        verb_form = wnl.lemmatize(raw, "v")
        if verb_form != raw:
            return verb_form
        adj_form = wnl.lemmatize(raw, "a")
        if adj_form != raw:
            return adj_form
        return raw
    except Exception:  # pragma: no cover
        return raw


@lru_cache(maxsize=8192)
def wordnet_related_lemmas(lemma: str) -> frozenset[str]:
    """Return direct WordNet derivational family lemmas for soft semantic fallback."""
    if not lemma or len(lemma) < 3 or not _ensure_nltk():
        return frozenset({lemma}) if lemma else frozenset()
    try:
        from nltk.corpus import wordnet
        family: Set[str] = {lemma}
        for syn in wordnet.synsets(lemma):
            for lem in syn.lemmas():
                for drv in lem.derivationally_related_forms():
                    drv_name = drv.name().casefold()
                    if "_" not in drv_name and len(drv_name) >= 3:
                        family.add(lemmatize_token(drv_name))
        return frozenset(family)
    except Exception:  # pragma: no cover
        return frozenset({lemma})


def split_identifier_text(text: str) -> str:
    """Split camelCase/snake_case and normalize standard symbolic operators without word lists."""
    if not text:
        return ""
    cleaned = str(text).replace("%", " percent ").replace("_", " ")
    # Normalize multi-word time-intelligence shorthands consistently across prompts & metric names
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
    cleaned = re.sub(r"\b(YoY|MoM|QoQ|YTD|QTD|MTD|SPLY|MAT)\b", lambda m: m.group(1).lower(), cleaned)
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    return cleaned


def lemmatize_phrase_tokens(text: str, use_pos: bool = True) -> List[str]:
    """Tokenize and lemmatize all alphanumeric words in a phrase using NLTK."""
    cleaned = split_identifier_text(text)
    raw_words = re.findall(r"[A-Za-z0-9]+", cleaned)
    if not raw_words:
        return []
    pos_tags: Optional[List[Tuple[str, str]]] = None
    if use_pos and len(raw_words) > 1 and _ensure_nltk():
        try:
            from nltk import pos_tag
            pos_tags = pos_tag(raw_words)
        except Exception:  # pragma: no cover
            pos_tags = None

    lemmas: List[str] = []
    for idx, tok in enumerate(raw_words):
        wn_pos = _treebank_to_wn_pos(pos_tags[idx][1]) if pos_tags else None
        lemmas.append(lemmatize_token(tok, wn_pos=wn_pos))
    return lemmas


def content_lemmas(text: str, use_pos: bool = True) -> List[str]:
    """Return lemmatized tokens omitting NLTK English stopwords and syntax modifiers."""
    sw = get_english_stopwords()
    if use_pos:
        pos_lemmas = lemmatize_phrase_tokens(text, use_pos=True)
        out = [lem for lem in pos_lemmas if lem not in sw and len(lem) >= 2]
        if out:
            return out
    out_plain: List[str] = []
    for raw_tok in re.findall(r"[A-Za-z0-9]+", split_identifier_text(text)):
        lem = lemmatize_token(raw_tok.casefold())
        if lem in sw or len(lem) < 2:
            continue
        out_plain.append(lem)
    return out_plain


# ---------------------------------------------------------------------------
# Metric SQL Filter Literal Extractor (sqlglot AST)
# ---------------------------------------------------------------------------

def extract_sql_filter_literals(
    sql_expression: Optional[str],
    filter_expression: Optional[str] = None,
) -> Dict[str, List[str]]:
    """Parse column = 'Literal' / column IN (...) predicates from metric SQL using sqlglot."""
    found: Dict[str, Set[str]] = {}

    def _add(col_name: str, lit_val: str) -> None:
        c = str(col_name or "").strip().strip('"').upper() or "PREDICATE"
        v = str(lit_val or "").strip()
        if not v or len(v) > 80:
            return
        found.setdefault(c, set()).add(v)

    for raw_expr in (sql_expression, filter_expression):
        expr_text = (raw_expr or "").strip()
        if not expr_text:
            continue
        parsed_ok = False
        if sqlglot is not None and sql_exp is not None:
            for read_dialect in ("snowflake", None):
                try:
                    ast_node = sqlglot.parse_one(expr_text, read=read_dialect)
                    parsed_ok = True
                    for node in ast_node.walk():
                        if isinstance(node, sql_exp.EQ):
                            left, right = node.this, node.expression
                            col_node, lit_node = None, None
                            if isinstance(left, sql_exp.Column) and isinstance(right, sql_exp.Literal):
                                col_node, lit_node = left, right
                            elif isinstance(right, sql_exp.Column) and isinstance(left, sql_exp.Literal):
                                col_node, lit_node = right, left
                            if col_node is not None and lit_node is not None and lit_node.is_string:
                                _add(col_node.name, lit_node.name)
                        elif isinstance(node, sql_exp.In) and isinstance(node.this, sql_exp.Column):
                            for item in node.expressions or []:
                                if isinstance(item, sql_exp.Literal) and item.is_string:
                                    _add(node.this.name, item.name)
                    break
                except Exception:
                    continue

        if not parsed_ok:
            for col, val in re.findall(
                r"""(?:[A-Za-z_][A-Za-z0-9_]*\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?\s*=\s*'([^']+)'""",
                expr_text,
            ):
                _add(col, val)

    return {col: sorted(vals) for col, vals in found.items()}


# ---------------------------------------------------------------------------
# Deployed Semantic View & Cortex YAML Artifact Reader
# ---------------------------------------------------------------------------

@dataclass
class DeployLexiconMetadata:
    """Dimensions, synonyms, and sample values loaded from deployed view/Cortex artifacts."""
    has_deployed_sv: bool = False
    sv_dimensions_by_table: Dict[str, Set[str]] = field(default_factory=dict)  # UPPER table -> {UPPER col}
    sv_all_dimension_keys: Set[Tuple[str, str]] = field(default_factory=set)
    sv_dim_aliases: Dict[Tuple[str, str], Tuple[str, str]] = field(default_factory=dict)
    dim_sample_values: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)
    dim_synonyms: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)
    metric_synonyms: Dict[str, List[str]] = field(default_factory=dict)


def _norm_ident(val: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(val or "").strip().strip('"').upper())


def _extract_balanced_block(text: str, keyword: str) -> str:
    m = re.search(rf"\b{keyword}\s*\(", text, re.I)
    if not m:
        return ""
    i = m.end()
    n = len(text)
    depth = 1
    in_sq = False
    while i < n and depth > 0:
        ch = text[i]
        if in_sq:
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_sq = False
        else:
            if ch == "'":
                in_sq = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[m.end() : i]
        i += 1
    return ""


def _resolve_deploy_dirs(
    ontology: Any,
    allowed_domains: Optional[List[str]],
    semantic_view: Optional[str],
) -> List[Path]:
    rev_root = Path("output") / "reverse"
    if not rev_root.is_dir():
        return []
    candidates: List[Path] = []
    seen: Set[Path] = set()

    def _add(p: Path) -> None:
        if p.is_dir() and p not in seen:
            seen.add(p)
            candidates.append(p)

    # Match domains matching allowed_domains or semantic_view
    dom_objs = list(getattr(ontology, "domains", {}).values())
    sv_target = _norm_ident(semantic_view.split(".")[-1]) if semantic_view else ""
    for d in dom_objs:
        if allowed_domains and d.domain not in allowed_domains and getattr(d, "id", None) not in allowed_domains:
            continue
        d_sv = _norm_ident((getattr(d, "semantic_view_name", None) or "").split(".")[-1])
        if sv_target and d_sv and d_sv != sv_target:
            continue
        for raw_name in filter(None, [getattr(d, "source_project_id", None), d.domain, d.domain.replace(" ", "_"), getattr(d, "id", None)]):
            _add(rev_root / str(raw_name))
    return candidates


def load_deploy_lexicon_metadata(
    ontology: Any,
    allowed_domains: Optional[List[str]] = None,
    semantic_view: Optional[str] = None,
) -> DeployLexiconMetadata:
    """Read deployed `semantic_view.sql` and `cortex_analyst.yaml` metadata when available."""
    meta = DeployLexiconMetadata()
    dirs = _resolve_deploy_dirs(ontology, allowed_domains, semantic_view)
    if not dirs:
        return meta

    for d_path in dirs:
        sv_sql_path = d_path / "semantic_view.sql"
        if sv_sql_path.is_file():
            try:
                ddl = sv_sql_path.read_text(encoding="utf-8")
                dims_block = _extract_balanced_block(ddl, "DIMENSIONS")
                dim_entry_re = re.compile(
                    r'(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))'
                    r'\s+AS\s+'
                    r'(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))'
                    r'(?:\s+WITH\s+SYNONYMS\s*=\s*\(([^)]*)\))?',
                    re.IGNORECASE,
                )
                for raw_line in dims_block.splitlines():
                    stripped_line = raw_line.strip()
                    if not stripped_line or re.match(r"^PRIVATE\b", stripped_line, re.I):
                        continue
                    m = dim_entry_re.search(stripped_line)
                    if not m:
                        continue
                    t_alias = (m.group(1) or m.group(2) or "").strip()
                    c_alias = (m.group(3) or m.group(4) or "").strip()
                    t_phys = (m.group(5) or m.group(6) or "").strip()
                    c_phys = (m.group(7) or m.group(8) or "").strip()
                    syns_raw = m.group(9) or ""
                    syns = [s.strip().strip("'").replace("''", "'") for s in re.findall(r"'(?:[^']|'')*'", syns_raw) if s.strip()]

                    for t_raw, c_raw in ((t_alias, c_alias), (t_phys, c_phys)):
                        t_norm = _norm_ident(t_raw)
                        c_norm = _norm_ident(c_raw)
                        if t_norm and c_norm:
                            meta.has_deployed_sv = True
                            meta.sv_dimensions_by_table.setdefault(t_norm, set()).add(c_norm)
                            meta.sv_all_dimension_keys.add((t_norm, c_norm))
                            meta.sv_dim_aliases.setdefault((t_norm, c_norm), (t_alias, c_alias))
                            if syns:
                                prev_y = meta.dim_synonyms.setdefault((t_norm, c_norm), [])
                                for sy in syns:
                                    if sy not in prev_y:
                                        prev_y.append(sy)
            except Exception as exc:
                logger.debug("Could not parse semantic_view.sql in %s: %s", d_path, exc)

        ca_path = d_path / "cortex_analyst.yaml"
        if ca_path.is_file():
            try:
                parsed = yaml.safe_load(ca_path.read_text(encoding="utf-8")) or {}
                sm = parsed.get("semantic_model") if isinstance(parsed.get("semantic_model"), dict) else parsed
                for tbl in sm.get("tables", []) or []:
                    t_name = _norm_ident(tbl.get("name", ""))
                    if not t_name:
                        continue
                    for dim in tbl.get("dimensions", []) or []:
                        d_name = _norm_ident(dim.get("name", ""))
                        d_expr = _norm_ident(dim.get("expr", ""))
                        if not meta.sv_dimensions_by_table:
                            meta.has_deployed_sv = True
                        samples = [
                            str(v).strip()
                            for v in (dim.get("sample_values") or [])
                            if str(v).strip() and len(str(v).strip()) <= 80 and not str(v).strip().startswith("data:")
                        ]
                        syns = [str(v).strip() for v in (dim.get("synonyms") or []) if str(v).strip()]
                        for key_c in filter(None, {d_name, d_expr}):
                            meta.sv_dimensions_by_table.setdefault(t_name, set()).add(key_c)
                            meta.sv_all_dimension_keys.add((t_name, key_c))
                            if samples:
                                prev_s = meta.dim_sample_values.setdefault((t_name, key_c), [])
                                for sv in samples:
                                    if sv not in prev_s:
                                        prev_s.append(sv)
                            if syns:
                                prev_y = meta.dim_synonyms.setdefault((t_name, key_c), [])
                                for sy in syns:
                                    if sy not in prev_y:
                                        prev_y.append(sy)
                    for meas in tbl.get("measures", []) or []:
                        m_name = _norm_ident(meas.get("name", ""))
                        m_syns = [str(v).strip() for v in (meas.get("synonyms") or []) if str(v).strip()]
                        if m_name and m_syns:
                            prev_my = meta.metric_synonyms.setdefault(m_name, [])
                            for sy in m_syns:
                                if sy not in prev_my:
                                    prev_my.append(sy)
            except Exception as exc:
                logger.debug("Could not parse cortex_analyst.yaml in %s: %s", d_path, exc)

    return meta


# ---------------------------------------------------------------------------
# Project Lexicon & Question Phrase Structures
# ---------------------------------------------------------------------------

@dataclass
class LexiconMetricEntry:
    metric: Any
    display_name: str
    concept_lemmas: Set[str]
    name_label_lemmas: Set[str]
    qualifier_lemmas: Set[str]  # Non-concept distinguishing tokens + SQL filter literals
    filter_literals: Dict[str, List[str]]
    all_lemmas: Set[str]
    is_count: bool
    is_pct_or_ratio: bool
    time_shift_tags: Set[str]
    has_filter_clause: bool


@dataclass
class LexiconDimensionEntry:
    concept: Any
    attribute: Any
    lemmas: Set[str]
    concept_lemmas: Set[str]
    sample_values: List[str]
    is_date: bool
    sv_dim_alias: Optional[Tuple[str, str]] = None


@dataclass
class ParsedFilterPhrase:
    raw_text: str
    column_hint: Optional[str] = None
    value_text: Optional[str] = None


@dataclass
class ParsedMeasurePhrase:
    raw_text: str
    content_lemmas: List[str]
    inherited_subject_lemmas: List[str] = field(default_factory=list)


@dataclass
class ParsedQuestion:
    original_prompt: str
    aggregation_intent: Optional[str]  # COUNT | AVG | SUM | MIN | MAX | DISTINCT | None
    asks_pct_ratio: bool
    asks_time_intel: bool
    time_shift_tags: Set[str]
    year_filters: List[str]
    quarter_filters: List[str]
    top_n: Optional[int]
    by_phrases: List[str]
    filter_phrases: List[ParsedFilterPhrase]
    measure_phrases: List[ParsedMeasurePhrase]


class ProjectLexicon:
    """Auto-built project lexicon indexing metrics, SQL filter literals, dimensions, and sample values."""

    def __init__(
        self,
        metrics: List[LexiconMetricEntry],
        dimensions: List[LexiconDimensionEntry],
        numeric_columns: List[Tuple[str, str]],
        scoped_concepts: List[Any],
        sv_enforced: bool,
    ):
        self.metrics = metrics
        self.dimensions = dimensions
        self.numeric_columns = numeric_columns
        self.scoped_concepts = scoped_concepts
        self.sv_enforced = sv_enforced
        self.token_idf: Dict[str, float] = self._compute_idf()
        self.sample_value_index: Dict[str, List[Tuple[LexiconDimensionEntry, str]]] = self._build_sample_index()

    def _compute_idf(self) -> Dict[str, float]:
        doc_count = max(len(self.metrics) + len(self.dimensions), 1)
        df: Dict[str, int] = {}
        for me in self.metrics:
            for tok in me.all_lemmas | me.concept_lemmas:
                df[tok] = df.get(tok, 0) + 1
        for de in self.dimensions:
            for tok in de.lemmas:
                df[tok] = df.get(tok, 0) + 1
        return {tok: math.log((doc_count + 1.0) / (cnt + 1.0)) + 1.0 for tok, cnt in df.items()}

    def _build_sample_index(self) -> Dict[str, List[Tuple[LexiconDimensionEntry, str]]]:
        idx: Dict[str, List[Tuple[LexiconDimensionEntry, str]]] = {}
        for de in self.dimensions:
            if de.is_date:
                continue
            for val in de.sample_values:
                val_clean = val.strip()
                if not val_clean or len(val_clean) < 2 or val_clean.isdigit():
                    continue
                idx.setdefault(val_clean.casefold(), []).append((de, val_clean))
        return idx

    def weight(self, lemma: str) -> float:
        return self.token_idf.get(lemma, 1.5)

    @classmethod
    def build(
        cls,
        ontology: Any,
        allowed_domains: Optional[List[str]] = None,
        target_domain: Optional[str] = None,
        semantic_view: Optional[str] = None,
        is_count_fn: Optional[Any] = None,
    ) -> "ProjectLexicon":
        allowed_cf = {d.casefold() for d in (allowed_domains or []) if d}
        target_cf = target_domain.casefold() if target_domain else None

        deploy_meta = load_deploy_lexicon_metadata(
            ontology,
            allowed_domains=allowed_domains,
            semantic_view=semantic_view,
        )

        # Gather scoped concepts strictly
        scoped_concepts: List[Any] = []
        seen_concepts: Set[str] = set()
        for dom in getattr(ontology, "domains", {}).values():
            dom_names = {dom.domain.casefold()}
            if getattr(dom, "id", None):
                dom_names.add(dom.id.casefold())
            if allowed_cf and not (dom_names & allowed_cf):
                continue
            if target_cf and target_cf not in dom_names:
                continue
            for c in getattr(dom, "concepts", []) or []:
                ck = c.name.casefold()
                if ck not in seen_concepts:
                    seen_concepts.add(ck)
                    scoped_concepts.append(c)

        if not scoped_concepts:
            for c in getattr(ontology, "concepts", {}).values():
                if allowed_cf and c.domain and c.domain.casefold() not in allowed_cf:
                    continue
                if target_cf and c.domain and c.domain.casefold() != target_cf:
                    continue
                ck = c.name.casefold()
                if ck not in seen_concepts:
                    seen_concepts.add(ck)
                    scoped_concepts.append(c)

        sv_enforced = bool(semantic_view and deploy_meta.has_deployed_sv and deploy_meta.sv_all_dimension_keys)
        date_types = {"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}

        dimensions: List[LexiconDimensionEntry] = []
        numeric_columns: List[Tuple[str, str]] = []
        for c in scoped_concepts:
            c_norm = _norm_ident(getattr(c, "logical_name", None) or c.name)
            c_lemmas = set(content_lemmas(f"{c.name} {getattr(c, 'label', None) or ''} {' '.join(c.synonyms or [])}"))
            for a in getattr(c, "attributes", []) or []:
                base_type = (a.data_type or "").split("(")[0].strip().upper()
                if base_type in _NUMERIC_TYPES:
                    numeric_columns.append((c.name, a.name))
                    continue

                a_norm = _norm_ident(getattr(a, "logical_name", None) or a.name)
                c_raw_norm = _norm_ident(c.name)
                a_raw_norm = _norm_ident(a.name)
                cand_keys = [
                    (c_norm, a_norm),
                    (c_raw_norm, a_raw_norm),
                    (c_norm, f"COL{a_raw_norm}"),
                    (c_norm, f"{c_norm}{a_raw_norm}"),
                ]
                matched_sv_key = next((k for k in cand_keys if k in deploy_meta.sv_all_dimension_keys), None)
                if sv_enforced and matched_sv_key is None:
                    continue

                sv_alias_pair = (
                    deploy_meta.sv_dim_aliases.get(matched_sv_key) if matched_sv_key else None
                )
                extra_syns = (
                    deploy_meta.dim_synonyms.get(matched_sv_key, []) if matched_sv_key else []
                )
                extra_samples = (
                    deploy_meta.dim_sample_values.get((c_norm, a_norm), [])
                    or deploy_meta.dim_sample_values.get((c_raw_norm, a_raw_norm), [])
                    or (deploy_meta.dim_sample_values.get(matched_sv_key, []) if matched_sv_key else [])
                )
                all_syns = list(dict.fromkeys([*(a.synonyms or []), *extra_syns]))
                all_samples = list(dict.fromkeys([*(getattr(a, "sample_values", None) or []), *extra_samples]))
                d_lemmas = set(content_lemmas(f"{a.name} {' '.join(all_syns)}"))
                dimensions.append(
                    LexiconDimensionEntry(
                        concept=c,
                        attribute=a,
                        lemmas=d_lemmas,
                        concept_lemmas=c_lemmas,
                        sample_values=all_samples,
                        is_date=base_type in date_types,
                        sv_dim_alias=sv_alias_pair,
                    )
                )

        # Build metric lexicon entries including SQL-parsed filter literals
        metrics_raw: List[Any] = []
        seen_m: Set[str] = set()
        for dom in getattr(ontology, "domains", {}).values():
            dom_names = {dom.domain.casefold()}
            if getattr(dom, "id", None):
                dom_names.add(dom.id.casefold())
            if allowed_cf and not (dom_names & allowed_cf):
                continue
            if target_cf and target_cf not in dom_names:
                continue
            for m in getattr(dom, "metrics", []) or []:
                mk = m.name.casefold()
                if mk not in seen_m:
                    seen_m.add(mk)
                    metrics_raw.append(m)

        if not metrics_raw:
            for m in getattr(ontology, "metrics", {}).values():
                if allowed_cf and m.domain and m.domain.casefold() not in allowed_cf:
                    continue
                if target_cf and m.domain and m.domain.casefold() != target_cf:
                    continue
                mk = m.name.casefold()
                if mk not in seen_m:
                    seen_m.add(mk)
                    metrics_raw.append(m)

        metric_entries: List[LexiconMetricEntry] = []
        for m in metrics_raw:
            extra_m_syns = deploy_meta.metric_synonyms.get(_norm_ident(m.name), [])
            syns = list(dict.fromkeys([*(m.synonyms or []), *extra_m_syns]))
            disp = m.label or (m.name.replace("_", " ").title() if "_" in m.name else m.name)
            c_lemmas = set(content_lemmas(m.concept or ""))
            name_label_lemmas = set(content_lemmas(f"{m.name} {m.label or ''} {' '.join(syns)}"))

            filter_lits = extract_sql_filter_literals(
                getattr(m, "sql_expression", None),
                getattr(m, "filter_expression", None),
            )
            filter_val_lemmas: Set[str] = set()
            for vals in filter_lits.values():
                for v in vals:
                    # Preserve explicit SQL predicate values cleanly via full lemmatization (never drop via stopword filter)
                    filter_val_lemmas.update(lemmatize_phrase_tokens(v, use_pos=False))

            qualifier_lemmas = (name_label_lemmas - c_lemmas) | filter_val_lemmas
            m_text = f"{m.name} {m.label or ''}".casefold()
            m_raw_toks = {t.casefold() for t in re.findall(r"[A-Za-z0-9]+", split_identifier_text(m_text))}
            time_tags = m_raw_toks & _TIME_SHIFT_TAGS
            if re.search(r"(\bcm\b|_cm\b)", m_text, re.I):
                time_tags = set(time_tags) | {"cm"}

            is_pct = bool(
                re.search(r"(%|_pct|\bpct\b|pctof|percent|ratio|share|\brate\b|\bmom|\byoy|\bqoq)", m_text, re.I)
            )
            is_cnt = bool(is_count_fn(m)) if is_count_fn else False
            has_filter = bool(filter_lits) or bool(getattr(m, "filter_expression", None))

            all_l = name_label_lemmas | c_lemmas | filter_val_lemmas
            metric_entries.append(
                LexiconMetricEntry(
                    metric=m,
                    display_name=disp,
                    concept_lemmas=c_lemmas,
                    name_label_lemmas=name_label_lemmas,
                    qualifier_lemmas=qualifier_lemmas,
                    filter_literals=filter_lits,
                    all_lemmas=all_l,
                    is_count=is_cnt,
                    is_pct_or_ratio=is_pct,
                    time_shift_tags=time_tags,
                    has_filter_clause=has_filter,
                )
            )

        return cls(
            metrics=metric_entries,
            dimensions=dimensions,
            numeric_columns=numeric_columns,
            scoped_concepts=scoped_concepts,
            sv_enforced=sv_enforced,
        )


# ---------------------------------------------------------------------------
# Deterministic Question Phrase Parser (Requirement 3)
# ---------------------------------------------------------------------------

def parse_question(prompt: str, lexicon: Optional[ProjectLexicon] = None) -> ParsedQuestion:
    """Parse a natural language question into structured measure, by, filter, time, and aggregation phrases."""
    clean = (prompt or "").strip()

    # 1. Aggregation & rank modifiers
    top_n_match = re.search(r"\btop\s+(\d+)\b", clean, re.I)
    top_n = int(top_n_match.group(1)) if top_n_match else None

    agg_intent: Optional[str] = None
    if re.search(r"\b(unique|distinct|how\s+many\s+distinct|number\s+of\s+unique)\b", clean, re.I):
        agg_intent = "DISTINCT"
    elif re.search(r"\b(how\s+many|number\s+of|count\s+of)\b", clean, re.I):
        agg_intent = "COUNT"
    elif re.search(r"\b(average|avg|mean)\b", clean, re.I):
        agg_intent = "AVG"
    elif re.search(r"\b(minimum|lowest|min)\b", clean, re.I):
        agg_intent = "MIN"
    elif re.search(r"\b(maximum|highest|max)\b", clean, re.I):
        agg_intent = "MAX"
    elif re.search(r"\b(total|sum)\b", clean, re.I):
        agg_intent = "SUM"

    asks_pct_ratio = bool(re.search(r"(%|percent|percentage|pct|ratio|share|proportion|rate)", clean, re.I))
    asks_time_intel = bool(
        re.search(
            r"\b(ytd|qtd|mtd|py|pm|pq|sply|mat|mom|yoy|qoq|year[- ]?to[- ]?date|"
            r"quarter[- ]?to[- ]?date|month[- ]?to[- ]?date|previous\s+(?:year|month|quarter)|"
            r"same\s+period\s+last\s+year|moving\s+annual\s+total|"
            r"month[-\s]+over[-\s]+month|year[-\s]+over[-\s]+year|quarter[-\s]+over[-\s]+quarter|"
            r"variance|growth|change|last\s+\d+\s+\w+)\b",
            clean,
            re.I,
        )
    )
    norm_raw_toks = {t.casefold() for t in re.findall(r"[A-Za-z0-9]+", split_identifier_text(clean))}
    q_time_tags = norm_raw_toks & _TIME_SHIFT_TAGS

    year_filters = re.findall(r"\b(20[12][0-9])\b", clean)
    quarter_filters = re.findall(r"\bQ([1-4])\b", clean, re.I)

    # 2. Extract WHERE / FILTER phrases before parsing BY & MEASURE clauses
    filter_phrases: List[ParsedFilterPhrase] = []
    work_prompt = clean

    where_match = re.search(
        r"\bwhere\s+(.+?)(?:\s+(?:grouped\s+by|by)\b|\s*$|[?;.])",
        work_prompt,
        flags=re.I,
    )
    if where_match:
        raw_where = where_match.group(1).strip()
        for part in re.split(r"\s+\band\b\s+", raw_where, flags=re.I):
            eq_m = re.match(r"^\s*([A-Za-z0-9_ ]+?)\s*(?:=|is|equals?)\s*['\"]?([^'\"]+?)['\"]?\s*$", part, re.I)
            if eq_m:
                filter_phrases.append(
                    ParsedFilterPhrase(
                        raw_text=part.strip(),
                        column_hint=eq_m.group(1).strip(),
                        value_text=eq_m.group(2).strip(),
                    )
                )
        work_prompt = (work_prompt[: where_match.start()] + " " + work_prompt[where_match.end() :]).strip()

    # Check explicit sample values present in prompt when lexicon provides deployed sample values
    if lexicon and lexicon.sample_value_index:
        prompt_cf = f" {work_prompt.casefold()} "
        for val_cf, matches in lexicon.sample_value_index.items():
            if len(val_cf) < 3:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(val_cf)}(?![a-z0-9])", prompt_cf):
                # Do NOT consume as row filter if the same token matches a metric SQL filter literal (e.g. 'Won'/'Open')
                if any(val_cf in {v.casefold() for vals in m.filter_literals.values() for v in vals} for m in lexicon.metrics):
                    continue
                if len(matches) == 1:
                    de, canonical_val = matches[0]
                    if not any(fp.value_text and fp.value_text.casefold() == canonical_val.casefold() for fp in filter_phrases):
                        filter_phrases.append(
                            ParsedFilterPhrase(
                                raw_text=canonical_val,
                                column_hint=de.attribute.name,
                                value_text=canonical_val,
                            )
                        )

    # 3. Extract BY phrases (prefer superlative 'Which <dim> has the most...', then 'top N <dim> by <metric>', then 'grouped by'/'by'/'per'/'each')
    by_phrases: List[str] = []
    measure_text = work_prompt
    which_match = re.match(
        r"^\s*which\s+([A-Za-z0-9_\s]+?)\s+"
        r"(?:has|have|had|brings|bring|generates|generate|drives|drive|shows|show|gets|get|produces|produce)\s+"
        r"(?:the\s+)?(?:most|highest|largest|biggest|greatest|top|lowest|smallest|fewest|least)\s+(.+?)[?.;]*$",
        work_prompt,
        flags=re.I,
    )
    if which_match:
        by_part = which_match.group(1).strip()
        rest_measure = which_match.group(2).strip()
        if by_part:
            by_phrases.extend(s.strip() for s in re.split(r",|\band\b", by_part, flags=re.I) if s.strip())
        measure_text = rest_measure
    else:
        by_match = re.search(
            r"\b(?:grouped\s+by|for\s+each|by)\s+([A-Za-z0-9_,\s&-]+?)"
            r"(?:\s+(?:in|during|for|where|with|from|have|has|had|do|does|did|is|are|was|were)\b|[?.;]|$)",
            work_prompt,
            flags=re.I,
        ) or re.search(
            r"\b(?:per|each)\s+([A-Za-z0-9_,\s&-]+?)"
            r"(?:\s+(?:in|during|for|where|with|from|have|has|had|do|does|did|is|are|was|were)\b|[?.;]|$)",
            work_prompt,
            flags=re.I,
        )
        top_n_by_match = re.search(
            r"\btop\s+\d+\s+([A-Za-z0-9_\s]+?)\s+by\s+([A-Za-z0-9_\s]+?)(?:\s+(?:in|during|for|where|with|from)\b|[?.;]|$)",
            work_prompt,
            flags=re.I,
        )
        is_top_n_metric_rank = False
        if top_n_by_match and lexicon:
            post_by = top_n_by_match.group(2).strip().casefold()
            post_by_lemmas = set(content_lemmas(post_by))
            if post_by_lemmas and any(post_by_lemmas & m.name_label_lemmas for m in lexicon.metrics):
                is_top_n_metric_rank = True
                raw_dim_part = top_n_by_match.group(1).strip()
                by_phrases.extend(s.strip() for s in re.split(r",|\band\b", raw_dim_part, flags=re.I) if s.strip())
                measure_text = top_n_by_match.group(2).strip()

        if not is_top_n_metric_rank and by_match:
            raw_by = by_match.group(1).strip()
            whole_match = False
            if lexicon:
                raw_by_cf = raw_by.casefold()
                whole_match = any(
                    raw_by_cf == de.attribute.name.casefold()
                    or any(raw_by_cf == s.casefold() for s in de.attribute.synonyms or [])
                    for de in lexicon.dimensions
                )
            if whole_match:
                by_phrases.append(raw_by)
            else:
                for part in re.split(r"\s*(?:,|\band\b)\s*", raw_by, flags=re.I):
                    cleaned_part = part.strip(" ?.;,")
                    if cleaned_part:
                        by_phrases.append(cleaned_part)
            measure_text = (work_prompt[: by_match.start()] + " " + work_prompt[by_match.end() :]).strip()

    # Strip year/quarter, current-period ('this month'/'this year'), and top N markers from measure_text
    measure_text = re.sub(r"\b(?:in|for|during)\s+(?:20[12][0-9]|Q[1-4])(?:\s+(?:20[12][0-9]|Q[1-4]))?\b", " ", measure_text, flags=re.I)
    measure_text = re.sub(r"\bthis\s+(?:year|quarter|month|week)\b", " ", measure_text, flags=re.I)
    measure_text = re.sub(r"\btop\s+\d+\b", " ", measure_text, flags=re.I)
    measure_text = re.sub(r"\b(last|past)\s+\d+\s+\w+\b", " ", measure_text, flags=re.I) if not any(
        any(tok in m.all_lemmas for tok in ("last", "2")) for m in (lexicon.metrics if lexicon else [])
    ) else measure_text
    # Normalize "Compare A with B" into "A and B" before coordinate splitting
    measure_text = re.sub(r"\bcompare\s+(.+?)\s+\bwith\b\s+", r"\1 and ", measure_text, flags=re.I)
    measure_text = measure_text.strip(" ?.;,")

    # 4. Split measure_text into coordinate sub-phrases with shared entity inheritance
    sub_clauses = [
        c.strip(" ?.;,")
        for c in re.split(r"\s*(?:,|\b(?:and|vs\.?|versus|compared\s+to)\b)\s*", measure_text, flags=re.I)
        if c and c.strip(" ?.;,")
    ]
    if not sub_clauses and measure_text:
        sub_clauses = [measure_text]

    measure_phrases: List[ParsedMeasurePhrase] = []
    head_concepts: List[str] = []
    head_metric_tokens: List[str] = []
    if sub_clauses:
        first_lemmas = content_lemmas(sub_clauses[0])
        if lexicon:
            known_concepts: Set[str] = set()
            for de in lexicon.dimensions:
                known_concepts.update(de.concept_lemmas)
            for me in lexicon.metrics:
                known_concepts.update(me.concept_lemmas)
            head_concepts = [tok for tok in first_lemmas if tok in known_concepts]
        head_metric_tokens = [tok for tok in first_lemmas if tok not in _TIME_SHIFT_TAGS]

        for idx, sub in enumerate(sub_clauses):
            c_lem = content_lemmas(sub)
            inherited: List[str] = []
            if idx > 0 and len(c_lem) <= 2:
                # Time-shift comparison clauses inherit the first clause's metric subject (e.g. 'vs previous year')
                if any(tok in _TIME_SHIFT_TAGS for tok in c_lem) and head_metric_tokens:
                    inherited = [tok for tok in head_metric_tokens if tok not in c_lem]
                elif head_concepts:
                    inherited = [tok for tok in head_concepts if tok not in c_lem]
            measure_phrases.append(
                ParsedMeasurePhrase(
                    raw_text=sub,
                    content_lemmas=c_lem,
                    inherited_subject_lemmas=inherited,
                )
            )

    return ParsedQuestion(
        original_prompt=clean,
        aggregation_intent=agg_intent,
        asks_pct_ratio=asks_pct_ratio,
        asks_time_intel=asks_time_intel,
        time_shift_tags=q_time_tags,
        year_filters=year_filters,
        quarter_filters=quarter_filters,
        top_n=top_n,
        by_phrases=by_phrases,
        filter_phrases=filter_phrases,
        measure_phrases=measure_phrases,
    )
