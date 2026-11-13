"""Snowflake METRICS-clause SQL normalization.

This is the last dialect-specific cleanup point before metric expressions are
written into semantic-view DDL. Translators and overrides can still be imperfect;
this module keeps Snowflake-only compatibility rules centralized.
"""

from __future__ import annotations

import re


def simplify_dead_arithmetic_sql(expr: str) -> str:
    """Apply NULL-safe, precedence-safe dead-arithmetic simplification.

    Simplifies identity operations (* 1, 1 *, / 1, + 0, 0 +, - 0) outside
    quoted identifiers and string literals while keeping * 0 and 0 - x intact
    so NULL propagation and subtraction semantics never change. Then folds
    constants without changing the result for any input, NULL included:
    x * (0) / 100 -> x * 0; COALESCE(x * 0, 0) -> 0; COALESCE(NULL, a) -> a;
    COALESCE(<number>, ...) -> number; a * NULL / NULL / a -> NULL;
    CASE WHEN (<number>) IS NULL [AND ...] THEN a ELSE b END -> (b).
    """
    if not expr or not isinstance(expr, str):
        return expr

    literals: list[str] = []

    def _mask(match: re.Match[str]) -> str:
        literals.append(match.group(0))
        return f"__SMLQ_{len(literals) - 1}__"

    # Mask single-quoted strings and double-quoted identifiers
    masked = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", _mask, expr)

    prev = None
    cur = masked
    passes = 0
    while cur != prev and passes < 5:
        prev = cur
        passes += 1
        # x * 1 / x / 1
        cur = re.sub(
            r"(?<=[A-Za-z0-9_)\]])\s*\*\s*1(?:\.0+)?(?![.\dA-Za-z_])",
            "",
            cur,
        )
        cur = re.sub(
            r"(?<=[A-Za-z0-9_)\]])\s*/\s*1(?:\.0+)?(?![.\dA-Za-z_])",
            "",
            cur,
        )
        # 1 * x (not preceded by / or digit/identifier)
        cur = re.sub(
            r"(^|[(\[,=]\s*)1(?:\.0+)?\s*\*\s*(?=[A-Za-z0-9_(\[])",
            r"\1",
            cur,
        )
        # x + 0 / x - 0 (never when followed by * or /)
        cur = re.sub(
            r"(?<=[A-Za-z0-9_)\]])\s*[+-]\s*0(?:\.0+)?(?![.\dA-Za-z_])(?!\s*[*/])",
            "",
            cur,
        )
        # 0 + x (only after start, '(', '[', ',', or '='; never after '-')
        cur = re.sub(
            r"(^|[(\[,=]\s*)0(?:\.0+)?\s*\+\s*(?=[A-Za-z0-9_(\[])",
            r"\1",
            cur,
        )

    # NULL-semantics-preserving constant folding (E22). Each rule keeps the
    # exact SQL result for every input, including NULL inputs.
    prev = None
    passes = 0
    while cur != prev and passes < 10:
        prev = cur
        passes += 1
        cur = _fold_zero_whatif_scaling(cur)
        cur = _fold_null_factors(cur)
        cur = _fold_coalesce(cur)
        cur = _fold_literal_is_null_case(cur)
        cur = re.sub(r"\(\s*NULL\s*\)", "NULL", cur)

    def _unmask(match: re.Match[str]) -> str:
        return literals[int(match.group(1))]

    return re.sub(r"__SMLQ_(\d+)__", _unmask, cur)


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|\d+(?:\.\d+)?|::|<=|>=|<>|!=|\S")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_BOUNDARY_WORDS = {"AND", "OR", "NOT", "WHEN", "THEN", "ELSE", "END", "CASE", "IS", "IN", "BETWEEN", "LIKE", "AS"}


def _tokens(sql: str):
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(sql)]


def _match_paren(sql: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_args(inner: str) -> list:
    args, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(inner[start:i])
            start = i + 1
    args.append(inner[start:])
    return args


def _strip_parens(text: str) -> str:
    t = text.strip()
    while t.startswith("(") and _match_paren(t, 0) == len(t) - 1:
        t = t[1:-1].strip()
    return t


def _top_level_tokens(expr: str) -> list:
    """Tokens of `expr` at parenthesis depth 0 (CASE...END counted as nested)."""
    out, depth, case_depth = [], 0, 0
    for tok, _s, _e in _tokens(expr):
        up = tok.upper()
        if tok == "(":
            depth += 1
            continue
        if tok == ")":
            depth -= 1
            continue
        if up == "CASE":
            case_depth += 1
            continue
        if up == "END" and case_depth:
            case_depth -= 1
            continue
        if depth == 0 and case_depth == 0:
            out.append(tok)
    return out


def _is_pure_product(expr: str) -> bool:
    """Only * and / at the top level (no + - comparisons or logic)."""
    toks = _top_level_tokens(expr)
    for i, tok in enumerate(toks):
        if tok in ("+", "-") and i > 0:
            return False
        if tok in ("=", "<", ">", "<=", ">=", "<>", "!=", "||") or tok.upper() in _BOUNDARY_WORDS:
            return False
    return True


def _fold_zero_whatif_scaling(sql: str) -> str:
    """`x * (0) / 100` -> `x * 0` (0/100 = 0; NULL stays NULL)."""
    return re.sub(
        r"\*\s*(?:\(\s*0(?:\.0+)?\s*\)|0(?:\.0+)?)\s*/\s*(?:\(\s*\d+(?:\.\d+)?\s*\)|\d+(?:\.\d+)?(?![.\dA-Za-z_(]))",
        "* 0",
        sql,
    )


def _fold_coalesce(sql: str) -> str:
    """COALESCE(NULL, a, ...) -> COALESCE(a, ...); COALESCE(a) -> a;
    COALESCE(<non-null numeric literal>, ...) -> literal;
    COALESCE(<pure product ending in * 0>, 0) -> 0 (it is 0 or NULL -> 0)."""
    for m in re.finditer(r"\bCOALESCE\s*\(", sql, re.IGNORECASE):
        open_idx = m.end() - 1
        close_idx = _match_paren(sql, open_idx)
        if close_idx < 0:
            continue
        args = _split_args(sql[open_idx + 1:close_idx])
        stripped = [_strip_parens(a) for a in args]
        replacement = None
        if len(args) > 1 and stripped[0].upper() == "NULL":
            rest = args[1:]
            replacement = rest[0].strip() if len(rest) == 1 else "COALESCE(" + ",".join(rest).strip() + ")"
            if len(rest) == 1 and not _NUM_RE.match(_strip_parens(rest[0])) and _strip_parens(rest[0]).upper() != "NULL":
                replacement = f"({_strip_parens(rest[0])})"
        elif len(args) == 1:
            replacement = f"({stripped[0]})"
        elif _NUM_RE.match(stripped[0]):
            replacement = stripped[0]
        elif (
            len(args) == 2 and stripped[1] in ("0", "0.0")
            and _is_pure_product(stripped[0])
            and re.search(r"\*\s*0(?:\.0+)?\s*$", stripped[0])
        ):
            replacement = "0"
        if replacement is not None:
            return sql[:m.start()] + replacement + sql[close_idx + 1:]
    return sql


def _fold_null_factors(sql: str) -> str:
    """A product/quotient with a literal NULL factor is NULL:
    `a * NULL`, `NULL * a`, `a / NULL`, `NULL / a` -> NULL (the whole
    multiplicative term, bounded by + - , ( ) and SQL keywords)."""
    toks = _tokens(sql)
    for i, (tok, start, end) in enumerate(toks):
        if tok.upper() != "NULL":
            continue
        prev_tok = toks[i - 1][0] if i > 0 else ""
        next_tok = toks[i + 1][0] if i + 1 < len(toks) else ""
        if prev_tok not in ("*", "/") and next_tok not in ("*", "/"):
            continue
        if prev_tok.upper() in ("IS", "AS"):
            continue
        # Expand left to the start of the multiplicative term.
        left = i
        depth = 0
        j = i - 1
        while j >= 0:
            t = toks[j][0]
            if t == ")":
                depth += 1
            elif t == "(":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and (t in ("+", "-", ",", "=", "<", ">", "<=", ">=", "<>", "!=", "||") or t.upper() in _BOUNDARY_WORDS):
                break
            left = j
            j -= 1
        right = i
        depth = 0
        j = i + 1
        while j < len(toks):
            t = toks[j][0]
            if t == "(":
                depth += 1
            elif t == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and (t in ("+", "-", ",", "=", "<", ">", "<=", ">=", "<>", "!=", "||", "::") or t.upper() in _BOUNDARY_WORDS):
                break
            right = j
            j += 1
        if left == right:
            continue
        return sql[:toks[left][1]] + "NULL" + sql[toks[right][2]:]
    return sql


def _fold_literal_is_null_case(sql: str) -> str:
    """CASE WHEN (<numeric literal>) IS NULL [AND ...] THEN a ELSE b END -> (b)
    when it is the only WHEN and the condition has no top-level OR: a numeric
    literal is never NULL, so the condition is FALSE."""
    pat = re.compile(
        r"\bCASE\s+WHEN\s+\(?\s*-?\d+(?:\.\d+)?\s*\)?\s+IS\s+NULL\b", re.IGNORECASE
    )
    for m in pat.finditer(sql):
        toks = _tokens(sql[m.end():])
        depth = case_depth = 0
        then_pos = else_pos = end_pos = None
        extra_when = has_or = False
        for tok, s, e in toks:
            up = tok.upper()
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1
                if depth < 0:
                    break
            elif up == "CASE":
                case_depth += 1
            elif up == "END":
                if case_depth:
                    case_depth -= 1
                elif depth == 0:
                    end_pos = (m.end() + s, m.end() + e)
                    break
            elif depth == 0 and case_depth == 0:
                if up == "THEN" and then_pos is None:
                    then_pos = m.end() + s
                elif up == "WHEN":
                    extra_when = True
                elif up == "ELSE" and then_pos is not None:
                    else_pos = (m.end() + s, m.end() + e)
                elif up == "OR" and then_pos is None:
                    has_or = True
        if end_pos is None or then_pos is None or extra_when or has_or:
            continue
        else_expr = sql[else_pos[1]:end_pos[0]].strip() if else_pos else "NULL"
        return sql[:m.start()] + f"({else_expr})" + sql[end_pos[1]:]
    return sql


def normalize_snowflake_metric_sql(expr: str) -> str:
    """Normalize a scalar metric expression for Snowflake semantic views."""
    if not expr:
        return expr
    normalized = _rewrite_function(expr, "INT", lambda inner: f"CAST(({inner}) AS INT)")
    return simplify_dead_arithmetic_sql(normalized)


def validate_snowflake_metric_sql(expr: str) -> tuple[bool, str | None]:
    """Validate a scalar Snowflake semantic-view metric expression."""
    if not expr or not str(expr).strip():
        return False, "empty metric SQL"

    scrubbed = _strip_string_literals(expr)
    upper = f" {scrubbed.upper()} "
    forbidden = (
        " SELECT ",
        "(SELECT",
        " FROM ",
        " JOIN ",
        " WITH ",
        " UNION ",
        " OVER ",
        " PARTITION BY ",
        " DROP ",
        " DELETE ",
        " TRUNCATE ",
        " INSERT ",
        " UPDATE ",
        " ALTER ",
        ";",
    )
    for token in forbidden:
        if token in upper:
            return False, f"forbidden token {token.strip()}"

    if re.search(r"\bINT\s*\(", scrubbed, re.IGNORECASE):
        return False, "unsupported INT(...) function"

    if _find_matching_paren(f"({expr})", 0) is None:
        return False, "unbalanced parentheses"

    return True, None


def _rewrite_function(expr: str, func_name: str, render) -> str:
    pattern = re.compile(rf"\b{re.escape(func_name)}\s*\(", re.IGNORECASE)
    out: list[str] = []
    pos = 0

    while True:
        match = pattern.search(expr, pos)
        if not match:
            out.append(expr[pos:])
            break

        open_idx = expr.find("(", match.start())
        close_idx = _find_matching_paren(expr, open_idx)
        if close_idx is None:
            out.append(expr[pos:])
            break

        out.append(expr[pos:match.start()])
        inner = expr[open_idx + 1:close_idx].strip()
        out.append(render(inner))
        pos = close_idx + 1

    return "".join(out)


def _find_matching_paren(text: str, open_idx: int) -> int | None:
    depth = 0
    quote: str | None = None
    i = open_idx

    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    return None


def _strip_string_literals(text: str) -> str:
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)
