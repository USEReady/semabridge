"""SQL guard for the Databricks planner path (Phase 3.3).

Every statement the Databricks client sends passes through
``validate_databricks_sql`` first (before any network call or retry).
Deny by default:

* READ mode (dry runs, table matching, verification): only SELECT,
  WITH ... SELECT, SHOW, DESCRIBE / DESC, EXPLAIN and USE.
* WRITE mode (deploy) additionally allows, and only for objects this run's
  ``WritePlan`` names (fully qualified catalog.schema.name):
    - CREATE [OR REPLACE] VIEW ... WITH METRICS   (planned metric views and
      their staging copies);
    - ALTER VIEW <planned view> AS ...;
    - DROP VIEW [IF EXISTS] <staging view>        (our own staging copies only);
    - GRANT ... ON [VIEW|TABLE] <planned view> TO ... (re-applying captured
      grants to a view we replaced).

Always refused, in any mode: DROP/CREATE/ALTER of tables, schemas,
catalogs; TRUNCATE; INSERT / UPDATE / DELETE / MERGE / COPY INTO; REVOKE /
DENY; OPTIMIZE / VACUUM / RESTORE; SET / RESET; CALL; EXECUTE IMMEDIATE;
and anything else not listed above.

Lessons carried from the Snowflake guard (Databricks carry-over checklist
C-5/C-6/C-7): the allowed objects belong to one run (a WritePlan object),
never a process-wide list; only the statement's head is inspected, never
string literals, backtick names or the ``$$ ... $$`` YAML body; backtick
names are parsed properly; exactly one statement per call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, List, Optional, Tuple

READ = "read"
WRITE = "write"


class DatabricksGuardError(PermissionError):
    """A statement was refused by the Databricks SQL guard."""


@dataclass(frozen=True)
class WritePlan:
    """Objects one deploy run may create or change (fully qualified names).

    views          -- metric views the run creates or alters;
    staging_views  -- their staging copies (may also be dropped);
    grant_views    -- views whose captured grants may be re-applied.
    Names are compared case-insensitively, without backticks.
    """

    views: FrozenSet[str] = field(default_factory=frozenset)
    staging_views: FrozenSet[str] = field(default_factory=frozenset)
    grant_views: FrozenSet[str] = field(default_factory=frozenset)

    @staticmethod
    def of(views: Iterable[str] = (), staging_views: Iterable[str] = (), grant_views: Iterable[str] = ()) -> "WritePlan":
        return WritePlan(
            views=frozenset(normalize_name(v) for v in views),
            staging_views=frozenset(normalize_name(v) for v in staging_views),
            grant_views=frozenset(normalize_name(v) for v in grant_views),
        )


@dataclass(frozen=True)
class GuardDecision:
    kind: str                     # e.g. "SELECT", "CREATE_METRIC_VIEW"
    object_name: Optional[str] = None


# --- splitting the statement into head tokens ---------------------------------------


class _Scan:
    """Walks SQL text outside literals, quoted names, comments and $$ bodies."""

    def __init__(self, sql: str) -> None:
        self.sql = sql

    def top_level_semicolons(self) -> List[int]:
        return [i for i, ch in self._code_chars() if ch == ";"]

    def _code_chars(self):
        s, n, i = self.sql, len(self.sql), 0
        while i < n:
            ch = s[i]
            two = s[i:i + 2]
            if two == "--":
                j = s.find("\n", i)
                i = n if j < 0 else j + 1
                continue
            if two == "/*":
                j = s.find("*/", i + 2)
                if j < 0:
                    raise DatabricksGuardError("unterminated /* comment")
                i = j + 2
                continue
            if two == "$$":
                j = s.find("$$", i + 2)
                if j < 0:
                    raise DatabricksGuardError("unterminated $$ body")
                yield i, "$"  # a body counts as one opaque token
                i = j + 2
                continue
            if ch in ("'", '"', "`"):
                j = i + 1
                while True:
                    if j >= n:
                        raise DatabricksGuardError(f"unterminated {ch} quote")
                    if s[j] == "\\" and ch != "`":
                        j += 2
                        continue
                    if s[j] == ch:
                        if j + 1 < n and s[j + 1] == ch:  # doubled quote = escaped
                            j += 2
                            continue
                        break
                    j += 1
                yield i, ch  # the quoted item as one token start
                i = j + 1
                continue
            yield i, ch
            i += 1


def _strip(sql: str) -> str:
    text = sql.strip()
    semis = _Scan(text).top_level_semicolons()
    if semis:
        if semis != [len(text) - 1] or len(semis) > 1:
            raise DatabricksGuardError("exactly one statement per call (found ';' inside the statement)")
        text = text[:-1].rstrip()
    if not text:
        raise DatabricksGuardError("empty statement")
    return text


def _tokens(sql: str, limit: Optional[int] = None) -> List[str]:
    """Upper-cased keywords/identifiers and punctuation from the code part of
    the statement; quoted names keep their exact text; literals and $$
    bodies become placeholders."""
    out: List[str] = []
    s = sql
    word: List[str] = []

    def flush() -> None:
        if word:
            out.append("".join(word).upper())
            word.clear()

    prev_pos = -2
    for pos, ch in _Scan(s)._code_chars():
        if limit is not None and len(out) >= limit:
            break
        if pos != prev_pos + 1:
            flush()  # a skipped comment/quote separates words
        prev_pos = pos
        if ch in ("'", '"', "`", "$"):
            flush()
            if ch == "`":
                # find the end of the backtick name to keep it exactly
                j = pos + 1
                while True:
                    if s[j] == "`":
                        if j + 1 < len(s) and s[j + 1] == "`":
                            j += 2
                            continue
                        break
                    j += 1
                out.append(s[pos:j + 1])
            elif ch == "$":
                out.append("$$BODY$$")
            else:
                out.append("<LITERAL>")
            continue
        if ch.isalnum() or ch == "_":
            word.append(ch)
            continue
        flush()
        if not ch.isspace():
            out.append(ch)
    flush()
    return out


def normalize_name(name: str) -> str:
    """`Cat`.`Sch`.`View` / cat.sch.view -> cat.sch.view (lower case)."""
    parts = _split_name_parts(name)
    return ".".join(p.lower() for p in parts)


def _split_name_parts(name: str) -> List[str]:
    parts: List[str] = []
    i, s, n = 0, str(name).strip(), len(str(name).strip())
    while i < n:
        if s[i] == "`":
            j, buf = i + 1, []
            while j < n:
                if s[j] == "`":
                    if j + 1 < n and s[j + 1] == "`":
                        buf.append("`")
                        j += 2
                        continue
                    break
                buf.append(s[j])
                j += 1
            parts.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and s[j] != ".":
                j += 1
            parts.append(s[i:j].strip())
            i = j
        if i < n and s[i] == ".":
            i += 1
    return parts


def _object_name(tokens: List[str], start: int) -> Tuple[Optional[str], int]:
    """Reads a (possibly dotted, possibly backtick) name starting at tokens[start]."""
    parts: List[str] = []
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("`"):
            parts.append(tok)
        elif tok.replace("_", "").isalnum():
            parts.append(tok)
        else:
            break
        i += 1
        if i < len(tokens) and tokens[i] == ".":
            i += 1
            continue
        break
    if not parts:
        return None, start
    return normalize_name(".".join(parts)), i


# --- the rules ----------------------------------------------------------------------

_READ_HEADS = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "USE", "VALUES"}
# Keywords that must not appear in the code part of a read statement.
_WRITE_WORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "DENY", "COPY", "OPTIMIZE", "VACUUM", "RESTORE", "REFRESH",
    "CALL", "EXECUTE", "SET", "RESET", "CACHE", "UNCACHE", "MSCK", "REPAIR",
}
_ALWAYS_REFUSED = {
    ("DROP", "TABLE"), ("DROP", "SCHEMA"), ("DROP", "DATABASE"), ("DROP", "CATALOG"),
    ("CREATE", "TABLE"), ("CREATE", "SCHEMA"), ("CREATE", "DATABASE"), ("CREATE", "CATALOG"),
    ("ALTER", "TABLE"), ("ALTER", "SCHEMA"), ("ALTER", "DATABASE"), ("ALTER", "CATALOG"),
}


def _refuse(reason: str, sql: str) -> DatabricksGuardError:
    preview = " ".join(sql.split())[:120]
    return DatabricksGuardError(f"Databricks SQL guard refused: {reason}: {preview!r}")


def validate_databricks_sql(sql: str, mode: str = READ, plan: Optional[WritePlan] = None) -> GuardDecision:
    """Raise DatabricksGuardError unless `sql` is allowed; return what it is."""
    if mode not in (READ, WRITE):
        raise ValueError(f"unknown guard mode {mode!r}")
    text = _strip(sql)
    toks = _tokens(text)
    if not toks:
        raise _refuse("no statement", sql)
    head = toks[0]

    # Read statements, in both modes.
    if head in _READ_HEADS or head == "WITH" or head == "(":
        bad = sorted({t for t in toks if t in _WRITE_WORDS})
        if bad:
            raise _refuse(f"write keyword(s) {bad} inside a read statement", sql)
        return GuardDecision(kind=head if head != "(" else "SELECT")

    # Always refused, whatever the plan says.
    if len(toks) >= 2:
        pair = (head, toks[1])
        if pair in _ALWAYS_REFUSED or (head == "CREATE" and "TABLE" in toks[1:4] and "VIEW" not in toks[1:4]):
            raise _refuse(f"{head} {toks[1]} is never allowed", sql)
    if head in {"TRUNCATE", "INSERT", "UPDATE", "DELETE", "MERGE", "COPY", "REVOKE", "DENY", "OPTIMIZE",
                "VACUUM", "RESTORE", "SET", "RESET", "CALL", "EXECUTE", "REFRESH", "CACHE", "UNCACHE",
                "MSCK", "REPAIR"}:
        raise _refuse(f"{head} is never allowed", sql)

    if mode != WRITE or plan is None:
        raise _refuse(f"{head} is not allowed in read mode", sql)

    # Write statements: only planned objects.
    if head == "CREATE":
        i = 1
        if toks[i:i + 2] == ["OR", "REPLACE"]:
            i += 2
        if i < len(toks) and toks[i] == "VIEW":
            i += 1
            if toks[i:i + 3] == ["IF", "NOT", "EXISTS"]:
                i += 3
            name, after = _object_name(toks, i)
            if name is None:
                raise _refuse("CREATE VIEW without a name", sql)
            _require_qualified(name, sql)
            if name not in plan.views and name not in plan.staging_views:
                raise _refuse(f"view {name} is not in this run's plan", sql)
            head_part = toks[after:]
            body_at = head_part.index("$$BODY$$") if "$$BODY$$" in head_part else len(head_part)
            before_body = head_part[:body_at]
            if not _has_pair(before_body, "WITH", "METRICS"):
                raise _refuse("only metric views (WITH METRICS) may be created", sql)
            return GuardDecision(kind="CREATE_METRIC_VIEW", object_name=name)
        raise _refuse(f"CREATE {toks[i] if i < len(toks) else ''} is not allowed", sql)

    if head == "ALTER" and len(toks) > 1 and toks[1] == "VIEW":
        name, after = _object_name(toks, 2)
        if name is None:
            raise _refuse("ALTER VIEW without a name", sql)
        _require_qualified(name, sql)
        if name not in plan.views:
            raise _refuse(f"view {name} is not in this run's plan", sql)
        if after >= len(toks) or toks[after] != "AS":
            raise _refuse("only ALTER VIEW ... AS <definition> is allowed", sql)
        return GuardDecision(kind="ALTER_VIEW", object_name=name)

    if head == "DROP" and len(toks) > 1 and toks[1] == "VIEW":
        i = 2
        if toks[i:i + 2] == ["IF", "EXISTS"]:
            i += 2
        name, after = _object_name(toks, i)
        if name is None:
            raise _refuse("DROP VIEW without a name", sql)
        _require_qualified(name, sql)
        if name not in plan.staging_views:
            raise _refuse(f"only this run's staging views may be dropped, not {name}", sql)
        if after != len(toks):
            raise _refuse("unexpected text after DROP VIEW", sql)
        return GuardDecision(kind="DROP_STAGING_VIEW", object_name=name)

    if head == "GRANT":
        if "ON" not in toks:
            raise _refuse("GRANT without ON", sql)
        i = toks.index("ON") + 1
        if i < len(toks) and toks[i] in ("VIEW", "TABLE"):
            i += 1
        name, after = _object_name(toks, i)
        if name is None:
            raise _refuse("GRANT without an object", sql)
        _require_qualified(name, sql)
        if name not in plan.grant_views:
            raise _refuse(f"grants may only be re-applied to this run's views, not {name}", sql)
        if after >= len(toks) or toks[after] != "TO":
            raise _refuse("GRANT ... ON <view> TO <principal> expected", sql)
        return GuardDecision(kind="GRANT", object_name=name)

    raise _refuse(f"{head} is not allowed", sql)


def _has_pair(tokens: List[str], a: str, b: str) -> bool:
    return any(tokens[i] == a and tokens[i + 1] == b for i in range(len(tokens) - 1))


def _require_qualified(name: str, sql: str) -> None:
    if len(name.split(".")) != 3:
        raise _refuse(f"object name must be catalog.schema.name, got {name}", sql)


class DatabricksSqlGuard:
    """The guard for one client / run: a mode and (for deploys) a plan."""

    def __init__(self, mode: str = READ, plan: Optional[WritePlan] = None) -> None:
        if mode == WRITE and plan is None:
            raise ValueError("write mode needs a WritePlan")
        self.mode = mode
        self.plan = plan

    def check(self, sql: str) -> GuardDecision:
        return validate_databricks_sql(sql, self.mode, self.plan)
