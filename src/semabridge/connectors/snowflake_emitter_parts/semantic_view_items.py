"""Item-level parsing and editing of ``CREATE SEMANTIC VIEW`` DDL.

Every deploy-time safety mechanism (static pre-deploy check, CREATE-failure
remediation, post-create Cortex compile checks, reconciliation) needs the
same primitive: find ONE item (a table, relationship, fact, dimension or
metric) inside the DDL and either neutralise it (metric -> NULL placeholder
with a COMMENT carrying the reason) or remove it (dimension / fact /
relationship), leaving every other item untouched. Historically each
mechanism used its own line regex, and each regex silently skipped some
metric syntaxes (``PRIVATE``, ``NON ADDITIVE BY``, derived ``"NAME" AS``),
so those metrics could be neither validated, nulled nor counted.

This module is the single parser for all of those syntaxes. It is purely
structural -- no model, table, column or metric name is special-cased.

Supported item shapes (case-insensitive, our own output and GET_DDL's
lower-case echo alike)::

    [PRIVATE|PUBLIC] alias."NAME" [NON ADDITIVE BY (...)] AS <expr>
                     [WITH SYNONYMS = (...)] [COMMENT = '...']
    [PRIVATE|PUBLIC] "NAME" AS <expr> ...          -- derived metric
    name AS from_alias (cols) REFERENCES to_alias [(cols)]   -- relationship
    alias AS db.schema.table [PRIMARY KEY (...)] [UNIQUE (...)] ...  -- table
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

CLAUSE_ORDER: Tuple[str, ...] = ("TABLES", "RELATIONSHIPS", "FACTS", "DIMENSIONS", "METRICS")
_OPTIONAL_CLAUSES = {"RELATIONSHIPS", "FACTS", "DIMENSIONS"}
NULL_METRIC_SQL = "CAST(NULL AS DOUBLE)"
_COMMENT_MAX_LEN = 400

_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_$][A-Za-z0-9_$]*)'


def unquote_ident(token: Optional[str]) -> str:
    t = str(token or "").strip()
    if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
        return t[1:-1].replace('""', '"')
    return t


def norm_ident(token: Optional[str]) -> str:
    return unquote_ident(token).upper()


# ---------------------------------------------------------------------------
# COMMENT escaping / NULL-placeholder detection
# ---------------------------------------------------------------------------

def escape_comment_literal(text: Optional[str], max_len: int = _COMMENT_MAX_LEN) -> str:
    """Make arbitrary text (including raw Snowflake error messages) safe to
    embed inside ``COMMENT = '...'``.

    Backslashes are escaped first (Snowflake string literals treat ``\\`` as
    an escape character), then single quotes are doubled. Whitespace is
    collapsed so the literal stays on one DDL line. ``*/`` and ``/*`` are
    neutralised too, so the text stays harmless even if some later pass
    were to wrap it in a block comment.
    """
    s = " ".join(str(text or "").split())
    s = s.replace("/*", "/ *").replace("*/", "* /")
    if len(s) > max_len:
        cut = s[: max_len - 3].rsplit(" ", 1)[0] or s[: max_len - 3]
        s = cut + "..."
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "''")
    return s


def _strip_sql_comments(expr: str) -> str:
    out: List[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if expr[j] == "'" and j + 1 < n and expr[j + 1] == "'":
                    j += 2
                    continue
                if expr[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if expr[j] == "'":
                    break
                j += 1
            out.append(expr[i:j + 1])
            i = j + 1
            continue
        if expr.startswith("/*", i):
            end = expr.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        if expr.startswith("--", i):
            end = expr.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_NULL_BODY_RE = re.compile(
    r"^\(*\s*(?:CAST\s*\(\s*NULL\s+AS\s+[A-Z0-9_]+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?\s*\)"
    r"|NULL(?:\s*::\s*[A-Z0-9_]+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)?)\s*\)*$",
    re.IGNORECASE,
)


def is_null_placeholder_expr(expr: Optional[str]) -> bool:
    """True when a metric body is (only) a NULL placeholder -- tolerant of
    trailing ``COMMENT = '...'``, ``WITH SYNONYMS = (...)``, block/line
    comments, redundant parentheses, whitespace and case."""
    if not expr:
        return False
    body, _syn, _comment = split_trailing_clauses(str(expr))
    body = _strip_sql_comments(body).strip().rstrip(",").strip()
    return bool(_NULL_BODY_RE.match(body))


# ---------------------------------------------------------------------------
# Low-level scanning
# ---------------------------------------------------------------------------

def _scan_balanced(text: str, open_idx: int) -> int:
    """Index of the ``)`` matching ``text[open_idx] == '('`` (quotes and
    comments respected); -1 if unbalanced."""
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            q = ch
            j = i + 1
            while j < n:
                if text[j] == q and j + 1 < n and text[j + 1] == q:
                    j += 2
                    continue
                if q == "'" and text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == q:
                    break
                j += 1
            i = j + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level_commas(body: str) -> List[str]:
    items: List[str] = []
    depth = 0
    start = 0
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch in ("'", '"'):
            q = ch
            j = i + 1
            while j < n:
                if body[j] == q and j + 1 < n and body[j + 1] == q:
                    j += 2
                    continue
                if q == "'" and body[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if body[j] == q:
                    break
                j += 1
            i = j + 1
            continue
        if body.startswith("/*", i):
            end = body.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if body.startswith("--", i):
            end = body.find("\n", i)
            i = n if end < 0 else end
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
        i += 1
    items.append(body[start:])
    return [it.strip() for it in items if it.strip()]


_TRAILING_COMMENT_RE = re.compile(r"\s+COMMENT\s*=\s*'((?:''|\\.|[^'\\])*)'\s*$", re.IGNORECASE | re.DOTALL)


def split_trailing_clauses(expr: str) -> Tuple[str, str, str]:
    """Split ``<body> [WITH SYNONYMS = (...)] [COMMENT = '...']`` into
    (body, synonyms_clause_text, comment_literal_text). Either trailing
    clause may come first."""
    body = str(expr or "").strip().rstrip(",").rstrip()
    synonyms = ""
    comment = ""
    for _ in range(2):
        m = _TRAILING_COMMENT_RE.search(body)
        if m and not comment:
            comment = m.group(1)
            body = body[: m.start()].rstrip()
            continue
        m_syn = re.search(r"\s+WITH\s+SYNONYMS\s*=?\s*\(", body, re.IGNORECASE)
        if m_syn and not synonyms:
            open_idx = body.find("(", m_syn.start())
            close_idx = _scan_balanced(body, open_idx)
            if close_idx >= 0 and not body[close_idx + 1:].strip():
                synonyms = body[m_syn.start():].strip()
                body = body[: m_syn.start()].rstrip()
                continue
        break
    return body, synonyms, comment


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SemanticItem:
    clause: str
    text: str
    name: str = ""                 # semantic name (unquoted, original case)
    alias: Optional[str] = None    # owning table alias (metric/dim/fact)
    private: str = ""              # "PRIVATE " / "PUBLIC " / ""
    non_additive: str = ""         # full " NON ADDITIVE BY (...)" text
    expr: str = ""                 # body after AS (no synonyms/comment)
    synonyms: str = ""
    comment: str = ""
    # relationships
    from_alias: Optional[str] = None
    from_cols: List[str] = field(default_factory=list)
    to_alias: Optional[str] = None
    to_cols: List[str] = field(default_factory=list)
    # tables
    physical: Optional[str] = None
    pk_cols: List[str] = field(default_factory=list)
    unique_keys: List[List[str]] = field(default_factory=list)

    @property
    def ident(self) -> str:
        """``ALIAS."NAME"`` (or ``"NAME"`` for derived metrics)."""
        if self.clause in ("METRICS", "DIMENSIONS", "FACTS"):
            return f'{self.alias}."{self.name}"' if self.alias else f'"{self.name}"'
        return self.name

    @property
    def key(self) -> Tuple[str, str]:
        return (str(self.alias or "").upper(), self.name.upper())

    @property
    def is_null_placeholder(self) -> bool:
        return self.clause == "METRICS" and is_null_placeholder_expr(self.expr)


_FIELD_RE = re.compile(
    rf'^\s*(?P<priv>(?:PRIVATE|PUBLIC)\s+)?(?:(?P<alias>{_IDENT})\s*\.\s*)?(?P<name>{_IDENT})'
    rf'(?P<nab>\s+NON\s+ADDITIVE\s+BY\s*\((?:[^()]|\([^()]*\))*\))?\s+AS\s+(?P<expr>.+)$',
    re.IGNORECASE | re.DOTALL,
)
_REL_RE = re.compile(
    rf'^\s*(?:(?P<name>{_IDENT})\s+AS\s+)?(?P<from>{_IDENT})\s*\((?P<fcols>[^()]*)\)\s+REFERENCES\s+'
    rf'(?P<to>{_IDENT})(?:\s*\((?P<tcols>[^()]*)\))?',
    re.IGNORECASE | re.DOTALL,
)


def _cols(text: Optional[str]) -> List[str]:
    return [unquote_ident(c) for c in str(text or "").split(",") if c.strip()]


def parse_item(clause: str, text: str) -> SemanticItem:
    item = SemanticItem(clause=clause, text=text)
    if clause in ("METRICS", "DIMENSIONS", "FACTS"):
        m = _FIELD_RE.match(text)
        if m:
            item.private = (m.group("priv") or "").upper()
            item.alias = unquote_ident(m.group("alias")) if m.group("alias") else None
            item.name = unquote_ident(m.group("name"))
            item.non_additive = m.group("nab") or ""
            body, syn, comment = split_trailing_clauses(m.group("expr"))
            item.expr, item.synonyms, item.comment = body, syn, comment
    elif clause == "RELATIONSHIPS":
        m = _REL_RE.match(text)
        if m:
            item.from_alias = unquote_ident(m.group("from"))
            item.to_alias = unquote_ident(m.group("to"))
            item.from_cols = _cols(m.group("fcols"))
            item.to_cols = _cols(m.group("tcols"))
            item.name = unquote_ident(m.group("name")) if m.group("name") else f"{item.from_alias}->{item.to_alias}"
    elif clause == "TABLES":
        m = re.match(rf'^\s*(?P<alias>{_IDENT})\s+AS\s+(?P<phys>\S+)', text, re.IGNORECASE)
        if m and not m.group("phys").startswith("("):
            item.name = unquote_ident(m.group("alias"))
            item.physical = m.group("phys")
        else:
            m2 = re.match(rf'^\s*(?P<alias>{_IDENT})', text)
            item.name = unquote_ident(m2.group("alias")) if m2 else ""
            item.physical = item.name
        pk = re.search(r'PRIMARY\s+KEY\s*\(([^()]*)\)', text, re.IGNORECASE)
        if pk:
            item.pk_cols = _cols(pk.group(1))
        item.unique_keys = [_cols(u) for u in re.findall(r'\bUNIQUE\s*\(([^()]*)\)', text, re.IGNORECASE)]
        item.alias = item.name
    return item


def render_metric(item: SemanticItem) -> str:
    lhs = f'{item.alias}."{item.name}"' if item.alias else f'"{item.name}"'
    syn = f" {item.synonyms}" if item.synonyms else ""
    comment = f" COMMENT = '{item.comment}'" if item.comment else ""
    return f"  {item.private}{lhs}{item.non_additive} AS {item.expr}{syn}{comment}"


# ---------------------------------------------------------------------------
# Whole-DDL view
# ---------------------------------------------------------------------------

class SemanticViewDDL:
    """Parsed, editable view of one CREATE SEMANTIC VIEW statement."""

    def __init__(self, ddl: str):
        self.original = ddl or ""
        self.prefix = ""
        self.suffix = ""
        self.clauses: Dict[str, List[SemanticItem]] = {}
        self.clause_order: List[str] = []
        self.parsed = False
        self.changed = False
        self._parse()

    # -- parsing -----------------------------------------------------------
    def _parse(self) -> None:
        text = self.original
        if "SEMANTIC VIEW" not in re.sub(r"\s+", " ", text.upper()):
            return
        pos = 0
        first_start: Optional[int] = None
        last_end = 0
        header_re = re.compile(r'\b(' + "|".join(CLAUSE_ORDER) + r')\s*\(', re.IGNORECASE)
        while True:
            m = header_re.search(text, pos)
            if not m:
                break
            open_idx = text.find("(", m.start())
            close_idx = _scan_balanced(text, open_idx)
            if close_idx < 0:
                return
            clause = m.group(1).upper()
            if clause in self.clauses:
                # Same keyword inside an expression would have been consumed
                # by the previous clause; a repeated header is malformed.
                return
            if first_start is None:
                first_start = m.start()
            body = text[open_idx + 1: close_idx]
            self.clauses[clause] = [parse_item(clause, it) for it in _split_top_level_commas(body)]
            self.clause_order.append(clause)
            pos = close_idx + 1
            last_end = close_idx + 1
        if first_start is None:
            return
        self.prefix = text[:first_start].rstrip()
        self.suffix = text[last_end:]
        self.parsed = True

    # -- accessors ---------------------------------------------------------
    def items(self, clause: str) -> List[SemanticItem]:
        return self.clauses.get(clause, [])

    def all_items(self) -> Iterable[SemanticItem]:
        for c in self.clause_order:
            yield from self.clauses[c]

    def table_aliases(self) -> Dict[str, SemanticItem]:
        return {t.name.upper(): t for t in self.items("TABLES") if t.name}

    def find(self, clause: str, name: str, alias: Optional[str] = None) -> Optional[SemanticItem]:
        n = norm_ident(name)
        a = norm_ident(alias) if alias else None
        for it in self.items(clause):
            if it.name.upper() == n and (a is None or str(it.alias or "").upper() == a):
                return it
        return None

    def find_any(self, name: str, alias: Optional[str] = None) -> Optional[SemanticItem]:
        for clause in ("METRICS", "DIMENSIONS", "FACTS", "RELATIONSHIPS"):
            it = self.find(clause, name, alias)
            if it is not None:
                return it
        return None

    def metric_names(self) -> Tuple[Set[str], Set[str]]:
        live: Set[str] = set()
        dead: Set[str] = set()
        for it in self.items("METRICS"):
            if not it.name:
                continue
            (dead if it.is_null_placeholder else live).add(it.name.upper())
        return live, dead

    def semantic_names(self) -> Set[str]:
        return {
            it.name.upper()
            for c in ("FACTS", "DIMENSIONS", "METRICS")
            for it in self.items(c) if it.name
        }

    # -- reference search --------------------------------------------------
    @staticmethod
    def _ref_pattern(alias: Optional[str], name: str) -> re.Pattern:
        """Qualified ``alias.name`` / ``alias."name"`` reference, or -- when
        *alias* is None -- a bare ``name`` / ``"name"`` reference that is NOT
        the column half of some ``x.name`` qualified reference."""
        n = re.escape(unquote_ident(name))
        if alias:
            a = re.escape(unquote_ident(alias))
            return re.compile(rf'(?<![\w$"])"?{a}"?\s*\.\s*(?:"{n}"|{n}(?![\w$]))', re.IGNORECASE)
        return re.compile(rf'(?<![\w$."])(?:"{n}"|{n}(?![\w$"]))', re.IGNORECASE)

    def items_referencing(
        self,
        alias: Optional[str],
        name: str,
        clauses: Iterable[str] = ("METRICS", "DIMENSIONS", "FACTS"),
        *,
        include_expr: bool = True,
    ) -> List[SemanticItem]:
        pat = self._ref_pattern(alias, name)
        out: List[SemanticItem] = []
        for c in clauses:
            for it in self.items(c):
                if include_expr:
                    body = f"{it.non_additive} {it.expr}"
                else:
                    # Only places that MUST name a declared dimension.
                    excl = re.findall(r"PARTITION\s+BY\s+EXCLUDING\s+[^)]*", it.expr, re.IGNORECASE)
                    body = f"{it.non_additive} {' '.join(excl)}"
                body = re.sub(r"'(?:''|[^'])*'", "''", body)
                if pat.search(body):
                    out.append(it)
        return out

    def relationships_using(self, alias: Optional[str], col: Optional[str]) -> List[SemanticItem]:
        a = norm_ident(alias) if alias else None
        c = norm_ident(col) if col else None
        out = []
        for r in self.items("RELATIONSHIPS"):
            sides = [(r.from_alias, r.from_cols), (r.to_alias, r.to_cols)]
            for s_alias, s_cols in sides:
                if a and str(s_alias or "").upper() != a:
                    continue
                if c is None or c in {x.upper() for x in s_cols}:
                    out.append(r)
                    break
        return out

    # -- editing -----------------------------------------------------------
    def null_metric(self, item: SemanticItem, reason: str) -> bool:
        if item.clause != "METRICS" or item not in self.items("METRICS"):
            return False
        item.expr = NULL_METRIC_SQL
        # A NON ADDITIVE BY dimension may itself be what failed; a NULL
        # placeholder never needs it.
        item.non_additive = ""
        item.comment = escape_comment_literal(reason)
        item.text = render_metric(item)
        self.changed = True
        return True

    def remove(self, item: SemanticItem) -> bool:
        lst = self.clauses.get(item.clause)
        if not lst or item not in lst:
            return False
        lst.remove(item)
        self.changed = True
        return True

    def render(self) -> str:
        if not self.parsed or not self.changed:
            return self.original
        blocks: List[str] = []
        for clause in self.clause_order:
            items = self.clauses[clause]
            if not items and clause in _OPTIONAL_CLAUSES:
                continue
            rendered = []
            for it in items:
                t = it.text.strip("\n")
                if not t.startswith(" "):
                    t = "  " + t.strip()
                rendered.append(t)
            blocks.append(f"{clause} (\n" + ",\n".join(rendered) + "\n)")
        suffix = self.suffix if self.suffix.strip() else ";"
        return self.prefix + "\n" + "\n".join(blocks) + suffix


# ---------------------------------------------------------------------------
# Item-level isolation with dependency cascade
# ---------------------------------------------------------------------------

@dataclass
class ItemOutcome:
    """One item neutralised/removed by an isolation pass."""
    entity_kind: str          # "metric" | "dimension" | "fact" | "relationship" | "table"
    name: str
    alias: Optional[str]
    action: str               # "nulled" | "dropped"
    reason: str
    detail: Optional[str] = None


def isolate_item(
    view: SemanticViewDDL,
    item: SemanticItem,
    reason: str,
    detail: Optional[str] = None,
    _seen: Optional[Set[Tuple[str, str, str]]] = None,
) -> List[ItemOutcome]:
    """Null (metric) or remove (dimension/fact/relationship) *item*, then
    cascade to every item that can no longer compile without it:

    * a removed dimension/fact -> metrics/dimensions referencing it are
      nulled/removed (``depends on [...]``);
    * a nulled metric -> metrics whose expression references it are nulled
      too, so a derived metric never silently computes over a NULL input.
    """
    seen = _seen if _seen is not None else set()
    sig = (item.clause, str(item.alias or "").upper(), item.name.upper())
    if sig in seen:
        return []
    seen.add(sig)
    outcomes: List[ItemOutcome] = []
    kind = {"METRICS": "metric", "DIMENSIONS": "dimension", "FACTS": "fact",
            "RELATIONSHIPS": "relationship", "TABLES": "table"}[item.clause]
    if item.clause == "METRICS":
        if item.is_null_placeholder:
            return []
        if not view.null_metric(item, reason):
            return []
        outcomes.append(ItemOutcome(kind, item.name, item.alias, "nulled", reason, detail))
    else:
        if not view.remove(item):
            return []
        outcomes.append(ItemOutcome(kind, item.name, item.alias, "dropped", reason, detail))
        if item.clause == "TABLES":
            for rel in list(view.items("RELATIONSHIPS")):
                if norm_ident(rel.from_alias) == item.name.upper() or norm_ident(rel.to_alias) == item.name.upper():
                    outcomes += isolate_item(view, rel, f"depends on table [{item.name}]: {reason}", detail, seen)
            for clause in ("FACTS", "DIMENSIONS", "METRICS"):
                for dep in list(view.items(clause)):
                    if dep.alias and dep.alias.upper() == item.name.upper():
                        outcomes += isolate_item(view, dep, f"depends on table [{item.name}]: {reason}", detail, seen)
    if item.clause in ("METRICS", "DIMENSIONS", "FACTS") and item.name:
        dep_reason = f"depends on [{item.name}]: {reason}"
        if item.clause == "METRICS":
            refs = view.items_referencing(item.alias, item.name, ("METRICS",))
            # Derived metrics reference other metrics by bare name too.
            refs += [r for r in view.items_referencing(None, item.name, ("METRICS",)) if r not in refs]
        else:
            # A dimension/fact whose body is just the same-named physical
            # column shares its spelling with plain column references, so
            # only places that must name a DECLARED dimension (NON ADDITIVE
            # BY / PARTITION BY EXCLUDING) are proven dependents. Anything
            # else that really breaks is caught by the next CREATE attempt.
            plain_col = bool(re.fullmatch(
                rf'\s*"?{re.escape(str(item.alias or ""))}"?\s*\.\s*"?{re.escape(item.name)}"?\s*',
                item.expr or "", re.IGNORECASE,
            ))
            refs = view.items_referencing(item.alias, item.name, include_expr=not plain_col)
        for dep in refs:
            if dep is item:
                continue
            outcomes += isolate_item(view, dep, dep_reason, detail, seen)
    return outcomes


# ---------------------------------------------------------------------------
# Relationship graph conflicts (cycles / multiple paths)
# ---------------------------------------------------------------------------

def select_conflicting_relationships(
    edges: List[Tuple[str, str]],
    priority: Optional[List[int]] = None,
) -> Dict[int, str]:
    """Given directed ``(from_alias, to_alias)`` relationship edges, return
    ``{edge_index: reason}`` for the edges that must be dropped so the
    remaining graph has no cycle and at most one join path between any
    ordered pair of tables (Snowflake rejects both).

    Edges are admitted greedily in *priority* order (lower value = kept
    first; ties keep source order), so the lowest-priority edge of a
    conflict -- e.g. an inactive relationship -- is the one dropped.
    """
    prio = priority or [0] * len(edges)
    order = sorted(range(len(edges)), key=lambda i: (prio[i], i))
    reach: Dict[str, Set[str]] = {}

    def reachable(src: str) -> Set[str]:
        seen = {src}
        stack = [src]
        while stack:
            cur = stack.pop()
            for nxt in reach.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    dropped: Dict[int, str] = {}
    for i in order:
        u, v = (str(edges[i][0]).upper(), str(edges[i][1]).upper())
        if u == v:
            dropped[i] = (
                f"relationship cycle: '{u}' references itself; Snowflake semantic "
                "views do not support circular relationships"
            )
            continue
        from_v = reachable(v)
        if u in from_v:
            dropped[i] = (
                f"relationship cycle: '{u}' -> '{v}' closes a cycle ('{v}' already "
                f"reaches '{u}'); Snowflake semantic views do not support circular relationships"
            )
            continue
        # Every node that reaches u, and every node v reaches: adding u->v
        # must not create a second path between any such pair.
        sources = {n for n in (set(reach) | {u}) if u in reachable(n)}
        conflict = None
        for x in sources:
            already = reachable(x)
            for y in from_v:
                if y in already and y != x:
                    conflict = (x, y)
                    break
            if conflict:
                break
        if conflict:
            dropped[i] = (
                f"multiple relationship paths: '{u}' -> '{v}' would create a second join "
                f"path from '{conflict[0]}' to '{conflict[1]}', which Snowflake semantic "
                "views reject as ambiguous"
            )
            continue
        reach.setdefault(u, set()).add(v)
    return dropped


# ---------------------------------------------------------------------------
# Snowflake error -> offending item(s)
# ---------------------------------------------------------------------------

_INVALID_ID_RE = re.compile(r"invalid identifier\s+'([^']+)'", re.IGNORECASE)
_AMBIGUOUS_RE = re.compile(r"ambiguous column name\s+'([^']+)'", re.IGNORECASE)
_CLAUSE_PRIORITY = {c: i for i, c in enumerate(("METRICS", "DIMENSIONS", "FACTS", "RELATIONSHIPS", "TABLES"))}


def _split_qualified(token: str) -> Tuple[Optional[str], str]:
    parts = [p.strip() for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', token.strip()) if p.strip()]
    if len(parts) >= 2:
        return unquote_ident(parts[-2]), unquote_ident(parts[-1])
    return None, unquote_ident(token.strip())


def map_error_to_items(view: SemanticViewDDL, error_text: str) -> List[SemanticItem]:
    """Best-effort mapping of a Snowflake CREATE SEMANTIC VIEW error to the
    item(s) it is about. Returns [] when the error can't be attributed to
    any specific item (the caller must then fail with a clear message)."""
    err = " ".join(str(error_text or "").split())
    if not view.parsed:
        return []

    # 1. invalid / ambiguous identifier
    for rx in (_INVALID_ID_RE, _AMBIGUOUS_RE):
        m = rx.search(err)
        if not m:
            continue
        alias, col = _split_qualified(m.group(1))
        exact = view.find_any(col, alias) if alias else None
        if exact is not None:
            return [exact]
        hits: List[SemanticItem] = []
        if alias:
            hits = view.items_referencing(alias, col)
            hits += [r for r in view.relationships_using(alias, col) if r not in hits]
        if not hits:
            any_ref = re.compile(rf'(?<![\w$])(?:"{re.escape(col)}"|{re.escape(col)}(?![\w$"]))', re.IGNORECASE)
            for it in view.all_items():
                if it.clause == "TABLES":
                    continue
                if it.clause == "RELATIONSHIPS":
                    if col.upper() in {c.upper() for c in it.from_cols + it.to_cols}:
                        hits.append(it)
                    continue
                body = re.sub(r"'(?:''|[^'])*'", "''", f"{it.non_additive} {it.expr}")
                if any_ref.search(body):
                    hits.append(it)
        if not hits:
            for t in view.items("TABLES"):
                keys = [t.pk_cols] + t.unique_keys
                if any(col.upper() in {k.upper() for k in key} for key in keys) and (
                    not alias or t.name.upper() == alias.upper()
                ):
                    hits.append(t)
        return hits

    # 2. relationship-structure errors: cycles / multiple paths
    if re.search(r"cycl|circular|multiple (?:join )?paths|more than one path|ambiguous (?:join )?path", err, re.IGNORECASE):
        named = [
            r for r in view.items("RELATIONSHIPS")
            if r.name and re.search(rf"(?<![\w$]){re.escape(r.name)}(?![\w$])", err, re.IGNORECASE)
        ]
        if named:
            return named[-1:]
        rels = view.items("RELATIONSHIPS")
        conflicts = select_conflicting_relationships([(r.from_alias or "", r.to_alias or "") for r in rels])
        return [rels[i] for i in sorted(conflicts)]

    # 3. any quoted token naming a declared item (metric > dim > fact > rel > table)
    found: List[SemanticItem] = []
    for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", err):
        alias, name = _split_qualified(a or b)
        it = view.find_any(name, alias) if alias else view.find_any(name)
        if it is None and not alias:
            it = view.find("TABLES", name)
        if it is not None and it not in found:
            found.append(it)
    if found:
        found.sort(key=lambda x: _CLAUSE_PRIORITY.get(x.clause, 9))
        return found[:1]

    # 4. bare (unquoted) item names mentioned in the message
    for clause in ("METRICS", "DIMENSIONS", "FACTS", "RELATIONSHIPS"):
        for it in view.items(clause):
            if it.name and len(it.name) > 3 and re.search(rf"(?<![\w$]){re.escape(it.name)}(?![\w$])", err, re.IGNORECASE):
                return [it]
    return []
