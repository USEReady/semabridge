"""Databricks naming, policy revision 2: lowercase snake_case (25 Nov 2026).

Independent of Snowflake's rule (which upper-cases, spells % / $ as PCT / DOL
and prefixes COL_). Databricks users expect lowercase snake_case identifiers;
Unity Catalog stores object names in lowercase anyway. The original Power BI
label is kept as display name and synonym by the planner, so nothing a user
reads is lost.

normalize(label, kind, source_identity) -> (name, reasons)
  1. start from the original label
  2. fold accents (NFKD + a small pinned Latin map)
  3. symbols to words, longest first (>= gte, % pct, $ dollars, ...)
  4. tokenize: pinned brand words and plural acronyms are protected first
     (iPhone, eBay, SKUs, IDs), then camelCase / acronym boundaries
  5. lowercase, join with "_"
  6. letters that can't be transliterated: readable part + identity hash
     (or <kind>_<hash> when nothing readable is left); the loss is reported
  7. leading digit or a protected prefix (current_, _metadata): <kind>_ prefix
  8. usability keyword (select, from, null, ...): _<kind> suffix
  9. at most 255 characters (246 + "_" + 8 hex of an identity hash)

Allocation (clashes) and SQL quoting are separate steps (planner). Generated
names always match ^[a-z][a-z0-9_]*$. Snowflake, Fabric and no-target naming
are untouched.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, List, Optional, Tuple

from semabridge.target_rules.base import TargetNaming

POLICY_REVISION = 2
MAX_LEN = 255
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Words that break or confuse an UNQUOTED reference (every emitted reference
# is quoted anyway): clause keywords, expression-sensitive words, alias-
# sensitive join words, and the metric-view source keyword. Ordinary words such
# as date, count, value, year, name and sum are deliberately NOT here.
USABILITY_KEYWORDS = frozenset({
    # clauses
    "select", "from", "where", "group", "by", "order", "having", "limit", "offset", "join", "on",
    "using", "union", "intersect", "except", "minus", "with", "as", "case", "when", "then", "else",
    "end", "distinct", "all", "and", "or", "not", "in", "is", "like", "ilike", "rlike", "between",
    "exists", "any", "some", "into", "values", "table", "create", "drop", "alter", "insert",
    "update", "delete", "merge", "grant", "revoke",
    # expression-sensitive
    "null", "true", "false", "default", "lateral", "interval", "cast",
    # alias-sensitive
    "anti", "semi", "cross", "full", "inner", "left", "right", "natural", "outer",
    # metric-view internal
    "source",
})
PROTECTED_PREFIXES = ("current_", "metadata")

# Pinned brand words kept as one token (matched case-insensitively at a word start).
BRAND_WORDS = ("iphone", "ipad", "ipod", "imac", "ios", "macos", "ebay", "youtube", "linkedin",
               "powerbi", "github", "gitlab", "javascript", "typescript", "postgresql", "mysql",
               "openai", "pyspark", "databricks", "snowflake")

_LATIN = {
    "ß": "ss", "ẞ": "SS", "Ł": "L", "ł": "l", "Æ": "AE", "æ": "ae", "Ø": "O", "ø": "o",
    "Œ": "OE", "œ": "oe", "Đ": "D", "đ": "d", "Þ": "TH", "þ": "th", "Ð": "D", "ð": "d", "ı": "i",
}
# Longest operators first.
_SYMBOLS = (
    (">=", "gte"), ("<=", "lte"), ("!=", "ne"), ("<>", "ne"), ("≥", "gte"), ("≤", "lte"), ("≠", "ne"),
    ("%", "pct"), ("$", "dollars"), ("€", "eur"), ("£", "gbp"), ("₹", "inr"), ("¥", "yen_yuan"),
    ("#", "num"), ("@", "at"), ("&", "and"), ("+", "plus"), ("*", "star"), ("=", "eq"),
    ("<", "lt"), (">", "gt"), ("?", "question"), ("!", "excl"),
)
_APOSTROPHES = ("'", "’", "‘", "`")
_TOKEN = re.compile(
    r"[A-Z]{2,}s(?=[A-Z0-9]|$)"       # plural acronym: SKUs, IDs, KPIs, URLs(Visited)
    r"|[A-Z]+(?=[A-Z][a-z])"           # acronym before a word: HTTP|Status
    r"|[A-Z]?[a-z]+[0-9]*"             # word, digits attached: Total, Line2, x
    r"|[A-Z]+[0-9]*"                   # acronym / all caps, digits attached: LY, Q1, KPI01
    r"|[0-9]+[a-z]*"                   # number, ordinal suffix attached: 2024, 1st, 2nd
)

KIND_WORDS = {"field": "field", "measure": "measure", "implicit_measure": "measure", "table": "table",
              "column": "column", "view": "view", "join": "join"}


def _hash8(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(repr(p) for p in parts).encode("utf-8")).hexdigest()[:8]


def _fold(text: str) -> Tuple[str, bool]:
    """Accents folded, pinned Latin letters mapped; True when letters were lost."""
    out: List[str] = []
    lost = False
    for ch in text:
        if ch in _LATIN:
            out.append(_LATIN[ch])
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        for c in base:
            if c.isascii():
                out.append(c)
            elif unicodedata.category(c).startswith(("L", "N")):
                lost = True
                out.append(" ")
            else:
                out.append(" ")
    return "".join(out), lost


def _tokens(chunk: str) -> List[str]:
    """Split one alphanumeric chunk into words, brands and plural acronyms protected."""
    out: List[str] = []
    i = 0
    while i < len(chunk):
        low = chunk[i:].lower()
        brand = next((b for b in BRAND_WORDS if low.startswith(b)
                      and (len(chunk) == i + len(b) or not chunk[i + len(b)].islower())), None)
        if brand and (i == 0 or not chunk[i - 1].isalpha() or chunk[i].isupper() or chunk[i - 1].islower()):
            out.append(chunk[i:i + len(brand)])
            i += len(brand)
            continue
        m = _TOKEN.match(chunk, i)
        if not m or not m.group(0):
            out.append(chunk[i])
            i += 1
            continue
        out.append(m.group(0))
        i = m.end()
    return out


def base_normalize(label: str) -> Tuple[str, List[str], bool]:
    """Steps 1-5: the snake_case form of a label, the reasons, and whether letters were lost."""
    reasons: List[str] = []
    text, lost = _fold(str(label or ""))
    if text != str(label or "") and not lost:
        reasons.append("accents_folded")
    for sym, word in _SYMBOLS:
        if sym in text:
            text = text.replace(sym, f" {word} ")
            reasons.append(f"symbol {sym} -> {word}")
    for a in _APOSTROPHES:
        text = text.replace(a, "")
    words: List[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", text):
        if chunk:
            words.extend(_tokens(chunk))
    name = "_".join(w.lower() for w in words if w)
    name = re.sub(r"_+", "_", name).strip("_")
    return name, reasons, lost


def normalize(label: str, kind: str = "field", source_identity: Any = None) -> Tuple[str, List[str]]:
    """The generated Databricks name for an object, and why it differs from the label."""
    kind_word = KIND_WORDS.get(str(kind or "field").lower(), "field")
    identity = source_identity if source_identity is not None else (kind_word, label)
    if is_final_name(str(label or "")):
        return str(label), []   # already final (incl. our own hashed / truncated names): unchanged
    name, reasons, lost = base_normalize(label)
    if lost:
        reasons.append("letters_not_transliterated")
        name = f"{name}_{_hash8(identity)}" if name else f"{kind_word}_{_hash8(identity)}"
    elif not name:
        reasons.append("empty_label")
        name = f"{kind_word}_{_hash8(identity)}"
    if name[0].isdigit() or name.startswith(PROTECTED_PREFIXES):
        reasons.append(f"prefixed_{kind_word}")
        name = f"{kind_word}_{name}"
    if name in USABILITY_KEYWORDS:
        reasons.append(f"keyword_suffixed_{kind_word}")
        name = f"{name}_{kind_word}"
    name = fit_length(name, identity)
    if name != fit_length(name, identity):  # pragma: no cover - fit_length is idempotent
        raise AssertionError(name)
    return name, reasons


def fit_length(name: str, identity: Any) -> str:
    if len(name) <= MAX_LEN:
        return name
    return f"{name[:MAX_LEN - 9].rstrip('_')}_{_hash8(identity, name)}"


def is_final_name(name: str) -> bool:
    """Lowercase snake_case that the rule would leave as it is: right grammar and
    length, not a usability keyword, no protected or digit start."""
    return (is_generated_name(name) and name not in USABILITY_KEYWORDS
            and not name.startswith(PROTECTED_PREFIXES))


def is_generated_name(name: str) -> bool:
    return bool(NAME_PATTERN.match(name or "")) and len(name) <= MAX_LEN


def is_legal_identifier(name: str) -> bool:
    """Legal for Databricks when quoted (preserved deployed names): non-empty, <= 255,
    no control characters."""
    return bool(name) and len(name) <= MAX_LEN and not any(unicodedata.category(c) == "Cc" for c in name)


def _kind_for(source_path: Optional[str], entity_kind: Optional[str]) -> str:
    kind = str(entity_kind or "").strip().lower()
    if kind in ("metric", "measure"):
        return "measure"
    if kind in ("table", "dataset"):
        return "table"
    if kind in ("column", "dimension", "field", "attribute", "fact"):
        return "field"
    if source_path and ".metrics." in str(source_path):
        return "measure"
    return "field"


class DatabricksNaming(TargetNaming):
    platform_label = "Databricks"
    normalizes_user_names = True
    ignores_legacy_auto_names = False
    policy_revision = POLICY_REVISION

    def target_name(self, name: str, source_path: Optional[str] = None, entity_kind: Optional[str] = None) -> str:
        kind = _kind_for(source_path, entity_kind)
        return normalize(name, kind, (kind, source_path or "", name))[0]

    def reserved_words(self, entity_kind: Optional[str] = None) -> frozenset:
        # Nothing is ever blocked as "reserved": every identifier is backtick-quoted.
        # Usability keywords are handled by the normaliser (select -> select_field).
        return frozenset()

    def is_legacy_auto_name(self, source_path: Optional[str], name: str) -> bool:
        return False

    def accepts_target_name(self, name: str, entity_kind: Optional[str] = None) -> bool:
        """Lowercase snake_case names are final as they are (case matters here:
        the rule never upper-cases)."""
        return self.target_name(name, entity_kind=entity_kind) == str(name)

    # --- the policy, as methods (planner and table matching use these) ---------------

    def normalize(self, label: str, kind: str = "field", source_identity: Any = None) -> Tuple[str, List[str]]:
        return normalize(label, kind, source_identity)

    def base_normalize(self, label: str) -> Tuple[str, List[str], bool]:
        return base_normalize(label)

    def fit_length(self, name: str, identity: Any) -> str:
        return fit_length(name, identity)

    def is_generated_name(self, name: str) -> bool:
        return is_generated_name(name)

    def is_legal_identifier(self, name: str) -> bool:
        return is_legal_identifier(name)
