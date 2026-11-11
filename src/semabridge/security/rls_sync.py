"""Bidirectional Row-Level Security (RLS) & Column Masking Policy Translator.

Synchronizes security governance between:
- Snowflake / Databricks Data Layer (ROW ACCESS POLICIES)
- BI Layer (Power BI / Fabric TMDL Roles and DAX Table Permissions)

All DAX RLS expressions are parsed with a deterministic recursive-descent
parser (never regex rest-of-line capture). Expressions that use dynamic/row-context
DAX functions or unparseable syntax are cleanly refused with an explicit reason.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("semabridge.security.rls_sync")

_SIMPLE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUM_LIT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

_UNSUPPORTED_DAX_FUNCTIONS = frozenset({
    "USERPRINCIPALNAME",
    "USERNAME",
    "USEROBJECTID",
    "CUSTOMDATA",
    "LOOKUPVALUE",
    "RELATED",
    "RELATEDTABLE",
    "CALCULATE",
    "FILTER",
    "ALL",
    "ALLEXCEPT",
    "CONTAINS",
    "CONTAINSROW",
    "PATHCONTAINS",
    "PATHITEM",
    "SWITCH",
    "IF",
})


class RLSTranslationError(ValueError):
    """Raised when an RLS filter cannot be safely or deterministically translated."""


class TMDLTablePermission(BaseModel):
    table_name: str
    dax_filter_expression: str
    column_types: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional mapping of column name -> Snowflake SQL type (e.g. {'YEAR': 'NUMBER'}).",
    )


class TMDLSecurityRole(BaseModel):
    name: str
    model_name: Optional[str] = None
    description: Optional[str] = None
    table_permissions: List[TMDLTablePermission] = Field(default_factory=list)
    members: List[str] = Field(default_factory=list)


class SnowflakeRowAccessPolicy(BaseModel):
    policy_name: str
    target_table: str
    column_name: str
    column_type: str = "VARCHAR"
    body_expression: str
    assigned_roles: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic DAX RLS Filter Tokenizer & Recursive-Descent Parser
# ---------------------------------------------------------------------------

@dataclass
class _Tok:
    kind: str  # COL, STR, NUM, OP, ANDAND, OROR, LPAREN, RPAREN, LBRACE, RBRACE, COMMA, IN, WORD, EOF
    val: str
    pos: int


def _tokenize_dax_rls(expr: str) -> List[_Tok]:
    tokens: List[_Tok] = []
    i = 0
    n = len(expr)

    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue

        # Multi-char operators
        if expr.startswith("&&", i):
            tokens.append(_Tok("ANDAND", "&&", i))
            i += 2
            continue
        if expr.startswith("||", i):
            tokens.append(_Tok("OROR", "||", i))
            i += 2
            continue
        if expr.startswith("==", i):
            tokens.append(_Tok("OP", "=", i))
            i += 2
            continue
        if expr.startswith("<>", i) or expr.startswith("!=", i):
            tokens.append(_Tok("OP", "<>", i))
            i += 2
            continue
        if expr.startswith(">=", i) or expr.startswith("<=", i):
            tokens.append(_Tok("OP", expr[i : i + 2], i))
            i += 2
            continue
        if ch in ("=", ">", "<"):
            tokens.append(_Tok("OP", ch, i))
            i += 1
            continue
        if ch == "(":
            tokens.append(_Tok("LPAREN", ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Tok("RPAREN", ch, i))
            i += 1
            continue
        if ch == "{":
            tokens.append(_Tok("LBRACE", ch, i))
            i += 1
            continue
        if ch == "}":
            tokens.append(_Tok("RBRACE", ch, i))
            i += 1
            continue
        if ch == ",":
            tokens.append(_Tok("COMMA", ch, i))
            i += 1
            continue

        # DAX string literal "..." (with "" escape)
        if ch == '"':
            j = i + 1
            buf: List[str] = []
            closed = False
            while j < n:
                if expr[j] == '"':
                    if j + 1 < n and expr[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    closed = True
                    j += 1
                    break
                buf.append(expr[j])
                j += 1
            if not closed:
                raise RLSTranslationError("Unterminated double-quoted string literal in DAX filter.")
            tokens.append(_Tok("STR", "".join(buf), i))
            i = j
            continue

        # DAX table-qualified column: 'Table'[Col] or Table[Col]
        if ch == "'":
            j = i + 1
            tbl_buf: List[str] = []
            closed_tbl = False
            while j < n:
                if expr[j] == "'":
                    if j + 1 < n and expr[j + 1] == "'":
                        tbl_buf.append("'")
                        j += 2
                        continue
                    closed_tbl = True
                    j += 1
                    break
                tbl_buf.append(expr[j])
                j += 1
            if not closed_tbl:
                raise RLSTranslationError(
                    "Invalid single-quoted literal/table syntax in DAX filter (use double quotes for DAX strings)."
                )
            while j < n and expr[j].isspace():
                j += 1
            if j >= n or expr[j] != "[":
                raise RLSTranslationError(
                    "Single-quoted text must be followed by a [Column] reference; single-quoted string literals are invalid in DAX."
                )
            # Fall through to parse [Column] starting at j
            col_start = j + 1
            k = col_start
            col_buf: List[str] = []
            closed_col = False
            while k < n:
                if expr[k] == "]":
                    if k + 1 < n and expr[k + 1] == "]":
                        col_buf.append("]")
                        k += 2
                        continue
                    closed_col = True
                    k += 1
                    break
                col_buf.append(expr[k])
                k += 1
            if not closed_col or not "".join(col_buf).strip():
                raise RLSTranslationError("Malformed [Column] reference in DAX filter.")
            tokens.append(_Tok("COL", "".join(col_buf).strip(), i))
            i = k
            continue

        # Unqualified [Column]
        if ch == "[":
            k = i + 1
            col_buf = []
            closed_col = False
            while k < n:
                if expr[k] == "]":
                    if k + 1 < n and expr[k + 1] == "]":
                        col_buf.append("]")
                        k += 2
                        continue
                    closed_col = True
                    k += 1
                    break
                col_buf.append(expr[k])
                k += 1
            if not closed_col or not "".join(col_buf).strip():
                raise RLSTranslationError("Malformed [Column] reference in DAX filter.")
            tokens.append(_Tok("COL", "".join(col_buf).strip(), i))
            i = k
            continue

        # Signed or unsigned number
        num_match = re.match(r"[+-]?(?:\d+(?:\.\d+)?)", expr[i:])
        if num_match and (ch.isdigit() or ch in "+-"):
            # Ensure '+'/'-' only parsed as numeric sign if digits follow
            tokens.append(_Tok("NUM", num_match.group(0), i))
            i += len(num_match.group(0))
            continue

        # Identifier word: either Table[Column], IN keyword, or unsupported DAX function/keyword
        word_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", expr[i:])
        if word_match:
            word = word_match.group(0)
            after = i + len(word)
            # Check if Table[Column]
            ws = after
            while ws < n and expr[ws].isspace():
                ws += 1
            if ws < n and expr[ws] == "[":
                k = ws + 1
                col_buf = []
                closed_col = False
                while k < n:
                    if expr[k] == "]":
                        if k + 1 < n and expr[k + 1] == "]":
                            col_buf.append("]")
                            k += 2
                            continue
                        closed_col = True
                        k += 1
                        break
                    col_buf.append(expr[k])
                    k += 1
                if not closed_col or not "".join(col_buf).strip():
                    raise RLSTranslationError("Malformed Table[Column] reference in DAX filter.")
                tokens.append(_Tok("COL", "".join(col_buf).strip(), i))
                i = k
                continue

            upper_w = word.upper()
            if upper_w == "IN":
                tokens.append(_Tok("IN", "IN", i))
            else:
                tokens.append(_Tok("WORD", word, i))
            i = after
            continue

        raise RLSTranslationError(f"Unsupported character {ch!r} at position {i} in DAX RLS filter.")

    tokens.append(_Tok("EOF", "", n))
    return tokens


def _quote_sf_ident(ident: str) -> str:
    raw = ident.strip()
    if not raw:
        raise RLSTranslationError("Identifier cannot be blank.")
    if _SIMPLE_IDENT_RE.fullmatch(raw):
        return raw.upper()
    return '"' + raw.replace('"', '""') + '"'


def _sanitize_policy_part(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_").upper()
    return cleaned or "POLICY"


def _escape_sql_string(val: str) -> str:
    return "'" + val.replace("'", "''") + "'"


class _DaxFilterParser:
    """Recursive-descent parser producing safe Snowflake SQL + inferred column types."""

    def __init__(self, tokens: List[_Tok], column_types: Optional[Dict[str, str]] = None):
        self.tokens = tokens
        self.idx = 0
        self.column_types_override = {k.strip().upper(): v.strip().upper() for k, v in (column_types or {}).items()}
        self.columns_seen: Dict[str, str] = {}  # upper_key -> sql_type ("VARCHAR" | "NUMBER")
        self.column_raw_names: Dict[str, str] = {}

    def peek(self) -> _Tok:
        return self.tokens[self.idx]

    def consume(self, kind: Optional[str] = None) -> _Tok:
        tok = self.tokens[self.idx]
        if kind is not None and tok.kind != kind:
            raise RLSTranslationError(f"Expected {kind}, found {tok.kind} ({tok.val!r}) at pos {tok.pos}.")
        self.idx += 1
        return tok

    def parse(self) -> str:
        sql = self.parse_or()
        if self.peek().kind != "EOF":
            tok = self.peek()
            raise RLSTranslationError(
                f"Unexpected trailing token {tok.val!r} at position {tok.pos}; refusing ambiguous DAX filter."
            )
        return sql

    def parse_or(self) -> str:
        parts = [self.parse_and()]
        while self.peek().kind == "OROR":
            self.consume("OROR")
            parts.append(self.parse_and())
        if len(parts) == 1:
            return parts[0]
        return "(" + " OR ".join(parts) + ")"

    def parse_and(self) -> str:
        parts = [self.parse_primary()]
        while self.peek().kind == "ANDAND":
            self.consume("ANDAND")
            parts.append(self.parse_primary())
        if len(parts) == 1:
            return parts[0]
        return "(" + " AND ".join(parts) + ")"

    def _record_column(self, col_name: str, inferred_type: str) -> str:
        key = col_name.strip().upper()
        self.column_raw_names[key] = col_name.strip()
        explicit = self.column_types_override.get(key)
        if explicit:
            self.columns_seen[key] = explicit
        else:
            prev = self.columns_seen.get(key)
            if prev is None:
                self.columns_seen[key] = inferred_type
            elif prev != inferred_type:
                # Fallback to VARCHAR if mixed literal types hit the same column without explicit metadata
                self.columns_seen[key] = "VARCHAR"
        return _quote_sf_ident(col_name)

    def _parse_literal(self) -> Tuple[str, str]:
        """Parse strictly one scalar literal (string or number). Returns (sql_literal, inferred_type)."""
        tok = self.peek()
        if tok.kind == "STR":
            self.consume("STR")
            return _escape_sql_string(tok.val), "VARCHAR"
        if tok.kind == "NUM":
            self.consume("NUM")
            if not _NUM_LIT_RE.fullmatch(tok.val):
                raise RLSTranslationError(f"Invalid numeric literal {tok.val!r}.")
            return tok.val, "NUMBER"
        if tok.kind == "WORD" and tok.val.upper() in _UNSUPPORTED_DAX_FUNCTIONS:
            raise RLSTranslationError(
                f"Dynamic or lookup DAX function {tok.val}() cannot be translated to static Snowflake Row Access Policy."
            )
        raise RLSTranslationError(
            f"Expected single literal value (quoted string or number), found {tok.val!r} at pos {tok.pos}."
        )

    def parse_primary(self) -> str:
        tok = self.peek()
        if tok.kind == "LPAREN":
            self.consume("LPAREN")
            inner = self.parse_or()
            self.consume("RPAREN")
            return f"({inner})"

        if tok.kind == "WORD":
            upper_w = tok.val.upper()
            if upper_w in _UNSUPPORTED_DAX_FUNCTIONS:
                raise RLSTranslationError(
                    f"Dynamic or context-dependent DAX function '{tok.val}' is not supported for Snowflake RAP translation."
                )
            if upper_w in ("OR", "AND"):
                raise RLSTranslationError(
                    f"Keyword '{tok.val}' is invalid as a standalone expression or infix operator (use '||' / '&&')."
                )
            raise RLSTranslationError(
                f"Unsupported identifier or function '{tok.val}' at pos {tok.pos} in DAX RLS filter."
            )

        if tok.kind != "COL":
            raise RLSTranslationError(f"Expected [Column] or '(' at pos {tok.pos}, found {tok.val!r}.")

        col_tok = self.consume("COL")
        nxt = self.peek()

        if nxt.kind == "IN":
            self.consume("IN")
            self.consume("LBRACE")
            lits: List[Tuple[str, str]] = [self._parse_literal()]
            while self.peek().kind == "COMMA":
                self.consume("COMMA")
                lits.append(self._parse_literal())
            self.consume("RBRACE")
            inferred = "NUMBER" if all(t == "NUMBER" for _, t in lits) else "VARCHAR"
            sf_col = self._record_column(col_tok.val, inferred)
            return f"{sf_col} IN ({', '.join(v for v, _ in lits)})"

        if nxt.kind == "OP":
            op = self.consume("OP").val
            sql_lit, inferred = self._parse_literal()
            sf_col = self._record_column(col_tok.val, inferred)
            return f"{sf_col} {op} {sql_lit}"

        raise RLSTranslationError(
            f"Expected comparison operator or 'IN' after [{col_tok.val}] at pos {nxt.pos}, found {nxt.val!r}."
        )


def _translate_dax_filter_to_sql(
    dax_filter_expression: str,
    column_types: Optional[Dict[str, str]] = None,
) -> Tuple[str, List[Tuple[str, str]]]:
    """Parse DAX RLS filter safely and return (sql_predicate, [(col_ident, col_type), ...])."""
    expr = (dax_filter_expression or "").strip()
    if not expr:
        raise RLSTranslationError("DAX filter expression is empty.")
    tokens = _tokenize_dax_rls(expr)
    parser = _DaxFilterParser(tokens, column_types=column_types)
    sql_expr = parser.parse()
    if not parser.columns_seen:
        raise RLSTranslationError("DAX filter must reference at least one table column.")
    cols: List[Tuple[str, str]] = []
    for key, col_type in parser.columns_seen.items():
        cols.append((_quote_sf_ident(parser.column_raw_names[key]), col_type))
    return sql_expr, cols


def _merge_column_signatures(col_lists: List[List[Tuple[str, str]]]) -> List[Tuple[str, str]]:
    """Combine policy parameter signatures cleanly across permissions for the same table."""
    merged: Dict[str, str] = {}
    order: List[str] = []
    for clist in col_lists:
        for col_ident, col_type in clist:
            if col_ident not in merged:
                merged[col_ident] = col_type
                order.append(col_ident)
            elif merged[col_ident] != col_type:
                merged[col_ident] = "VARCHAR"
    return [(c, merged[c]) for c in order]


class SecurityPolicyTranslator:
    """Translates row-level security and masking policies bidirectionally."""

    @staticmethod
    def translate_tmdl_roles_to_snowflake(
        roles: List[TMDLSecurityRole],
        target_database: str = "PROD_DWH",
        target_schema: str = "ANALYTICS",
    ) -> Dict[str, Any]:
        """Translate one or more TMDL roles into one consolidated Snowflake ROW ACCESS POLICY per table."""
        if not roles:
            return {"status": "not translated", "ddl": "", "reason": "At least one role is required."}  # hardcoding-ok: RBAC term

        db = (target_database or "").strip()
        schema = (target_schema or "").strip()
        if not db or not schema:
            return {"status": "not translated", "ddl": "", "reason": "target_database and target_schema must not be blank."}

        db_q = _quote_sf_ident(db)
        schema_q = _quote_sf_ident(schema)

        # Group by physical table (Snowflake permits only ONE row access policy per table)
        per_table: Dict[str, Dict[str, Any]] = {}

        for role in roles:
            role_name = (role.name or "").strip()
            if not role_name:
                return {"status": "not translated", "ddl": "", "reason": "Role name must not be blank."}  # hardcoding-ok: RBAC term
            if not role.table_permissions:
                return {
                    "status": "not translated",
                    "ddl": "",
                    "reason": f"Role '{role_name}' has no table_permissions defined.",  # hardcoding-ok: RBAC term
                }

            role_sql_lit = _escape_sql_string(role_name.upper())
            role_safe = _sanitize_policy_part(role_name)

            # Group permissions on this role per table
            role_table_exprs: Dict[str, List[str]] = {}
            role_table_cols: Dict[str, List[List[Tuple[str, str]]]] = {}
            role_table_raw: Dict[str, str] = {}

            for perm in role.table_permissions:
                raw_tbl = (perm.table_name or "").strip()
                if not raw_tbl:
                    return {
                        "status": "not translated",
                        "ddl": "",
                        "reason": f"Blank table_name in role '{role_name}'.",  # hardcoding-ok: RBAC term
                    }
                try:
                    sql_pred, cols = _translate_dax_filter_to_sql(
                        perm.dax_filter_expression,
                        column_types=perm.column_types,
                    )
                except RLSTranslationError as exc:
                    return {
                        "status": "not translated",
                        "ddl": "",
                        "reason": f"Refused DAX filter on table '{raw_tbl}' for role '{role_name}': {exc}",  # hardcoding-ok: RBAC term
                    }

                tbl_key = raw_tbl.upper()
                role_table_raw[tbl_key] = raw_tbl
                role_table_exprs.setdefault(tbl_key, []).append(sql_pred)
                role_table_cols.setdefault(tbl_key, []).append(cols)

            for tbl_key, preds in role_table_exprs.items():
                combined_role_pred = preds[0] if len(preds) == 1 else " AND ".join(f"({p})" for p in preds)
                entry = per_table.setdefault(
                    tbl_key,
                    {
                        "raw_table": role_table_raw[tbl_key],
                        "role_parts": [],
                        "role_names_safe": [],
                        "col_lists": [],
                    },
                )
                entry["role_parts"].append(f"(IS_ROLE_IN_SESSION({role_sql_lit}) AND ({combined_role_pred}))")
                entry["role_names_safe"].append(role_safe)
                entry["col_lists"].extend(role_table_cols[tbl_key])

        lines: List[str] = [
            "-- =============================================================================",
            f"-- Snowflake Row Access Policies (Roles: {', '.join(r.name.strip() for r in roles)})",
            "-- Uses IS_ROLE_IN_SESSION() to respect Snowflake role hierarchies.",  # hardcoding-ok: Snowflake RLS DDL comment
            "-- Roles not explicitly granted access by a clause receive 0 rows.",
            "-- =============================================================================",
        ]

        for tbl_key, entry in per_table.items():
            raw_tbl = entry["raw_table"]
            tbl_q = _quote_sf_ident(raw_tbl)
            tbl_safe = _sanitize_policy_part(raw_tbl)
            role_suffix = "_".join(dict.fromkeys(entry["role_names_safe"]))
            policy_name = f"RAP_{tbl_safe}_{role_suffix}"

            sig_cols = _merge_column_signatures(entry["col_lists"])
            sig_ddl = ", ".join(f"{col_ident} {col_type}" for col_ident, col_type in sig_cols)
            on_cols_ddl = ", ".join(col_ident for col_ident, _ in sig_cols)

            admin_clause = "IS_ROLE_IN_SESSION('ACCOUNTADMIN') OR IS_ROLE_IN_SESSION('SECURITYADMIN')"
            role_clauses = "\n    OR ".join(entry["role_parts"])

            returns_bool = "RETURNS BOOLEAN"  # hardcoding-ok: Snowflake RLS DDL syntax
            lines.append(
                f"CREATE OR REPLACE ROW ACCESS POLICY {db_q}.{schema_q}.{policy_name}\n"
                f"AS ({sig_ddl}) {returns_bool} ->\n"
                f"    {admin_clause}\n"
                f"    OR {role_clauses};"
            )
            lines.append(
                f"ALTER TABLE {db_q}.{schema_q}.{tbl_q}\n"
                f"    ADD ROW ACCESS POLICY {db_q}.{schema_q}.{policy_name} ON ({on_cols_ddl});"
            )
            lines.append("")

        return {"status": "translated", "ddl": "\n".join(lines), "reason": ""}

    @staticmethod
    def translate_tmdl_role_to_snowflake(
        role: TMDLSecurityRole,
        target_database: str = "PROD_DWH",
        target_schema: str = "ANALYTICS",
    ) -> Dict[str, Any]:
        """Translate a single TMDL role to Snowflake DDL with explicit status/reason."""
        return SecurityPolicyTranslator.translate_tmdl_roles_to_snowflake(
            [role],
            target_database=target_database,
            target_schema=target_schema,
        )

    @staticmethod
    def tmdl_role_to_snowflake_ddl(
        role: TMDLSecurityRole,
        target_database: str = "PROD_DWH",
        target_schema: str = "ANALYTICS",
    ) -> str:
        """Translate a TMDL DAX security role into Snowflake ROW ACCESS POLICY DDL string."""
        res = SecurityPolicyTranslator.translate_tmdl_role_to_snowflake(
            role,
            target_database=target_database,
            target_schema=target_schema,
        )
        if res["status"] != "translated":
            raise RLSTranslationError(res["reason"])
        return res["ddl"]

    @staticmethod
    def translate_snowflake_policy_to_tmdl(policy: SnowflakeRowAccessPolicy) -> Dict[str, Any]:
        """Translate simple Snowflake Row Access Policies back into Fabric TMDL, refusing unparseable bodies."""
        body = (policy.body_expression or "").strip()
        if not body:
            return {
                "status": "not translated",
                "tmdl_content": "",
                "reason": "Snowflake policy body_expression is empty.",
            }

        upper_body = body.upper()
        for forbidden in ("EXISTS", "SELECT ", "FROM ", "JOIN ", "CASE ", "CURRENT_USER", "GETVARIABLE"):
            if forbidden in upper_body:
                return {
                    "status": "not translated",
                    "tmdl_content": "",
                    "reason": (
                        f"Cannot deterministically translate complex Snowflake policy containing '{forbidden.strip()}' "
                        "to static TMDL DAX."
                    ),
                }

        # Strip admin bypass prefix(es) like "CURRENT_ROLE() IN ('ACCOUNTADMIN') OR (...)"
        cleaned = body
        while True:
            updated = re.sub(
                r"^\s*(?:CURRENT_ROLE\s*\(\s*\)\s+IN\s*\([^)]*\)|IS_ROLE_IN_SESSION\s*\([^)]*\))\s+OR\s+",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()
            if updated == cleaned:
                break
            cleaned = updated

        # Unwrap outer parentheses around role clause if balanced at ends
        while cleaned.startswith("(") and cleaned.endswith(")"):
            depth = 0
            balanced_outer = True
            for idx, ch in enumerate(cleaned):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and idx < len(cleaned) - 1:
                        balanced_outer = False
                        break
            if balanced_outer and depth == 0:
                cleaned = cleaned[1:-1].strip()
            else:
                break

        # Strip role header guard e.g. CURRENT_ROLE() = 'X' AND ... or IS_ROLE_IN_SESSION('X') AND ...
        cleaned = re.sub(
            r"^\s*\(?\s*(?:CURRENT_ROLE\s*\(\s*\)\s*=\s*'(?:[^']|'')*'|IS_ROLE_IN_SESSION\s*\(\s*'(?:[^']|'')*'\s*\))\s*AND\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = cleaned[1:-1].strip()

        # Match simple SQL predicate: COL <op> 'STR' or NUM
        m = re.fullmatch(
            r"""([A-Za-z_][A-Za-z0-9_]*)\s*(=|<>|!=|>=|<=|>|<)\s*(?:'((?:[^']|'')*)'|([+-]?\d+(?:\.\d+)?))""",
            cleaned,
        )
        if not m:
            return {
                "status": "not translated",
                "tmdl_content": "",
                "reason": "Snowflake policy body is too complex for deterministic static TMDL DAX translation.",
            }

        col = m.group(1)
        op = "==" if False else ("<>" if m.group(2) == "!=" else m.group(2))
        if m.group(3) is not None:
            str_val = m.group(3).replace("''", "'").replace('"', '""')
            dax_rhs = f'"{str_val}"'
        else:
            dax_rhs = m.group(4)

        dax_filter = f"[{col}] {op} {dax_rhs}"
        role_name = policy.assigned_roles[0] if policy.assigned_roles else "RestrictedRole"
        escaped_role = role_name.replace("'", "''")
        escaped_table = policy.target_table.replace("'", "''")

        lines = [
            f"role '{escaped_role}'",  # hardcoding-ok: TMDL role syntax
            "\tmodelPermission: read",
            "",
            f"\ttablePermission '{escaped_table}' = {dax_filter}",
            "",
        ]
        if policy.assigned_roles:
            lines.append(f"\tmember '{escaped_role}' = role")  # hardcoding-ok: TMDL role syntax

        return {"status": "translated", "tmdl_content": "\n".join(lines), "reason": ""}

    @staticmethod
    def snowflake_policy_to_tmdl(policy: SnowflakeRowAccessPolicy) -> str:
        """Translate a Snowflake Row Access Policy into Microsoft Fabric TMDL role syntax."""
        res = SecurityPolicyTranslator.translate_snowflake_policy_to_tmdl(policy)
        if res["status"] != "translated":
            raise RLSTranslationError(res["reason"])
        return res["tmdl_content"]
