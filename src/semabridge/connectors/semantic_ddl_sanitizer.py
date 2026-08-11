"""Sanitization and structural normalization for Snowflake semantic-view DDL."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from semabridge.utils.logger import get_logger
from semabridge.core.drop_ledger import DropLedger

logger = get_logger(__name__)


class SemanticDDLSanitizer:
    """Handles identifier sanitization and DDL structural integrity for Snowflake."""

    def __init__(self, identifier_sanitizer: Any, drop_ledger: Optional[DropLedger] = None):
        self.identifier_sanitizer = identifier_sanitizer
        # Falls back to a private DropLedger() when the caller doesn't share
        # one — same pattern as every other builder in this package (see
        # core/drop_ledger.py's DropLedger docstring) — so
        # self.drop_ledger.record(...) is always safe to call unconditionally.
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()

    def sanitize_semantic_name(self, name: str) -> str:
        """Sanitize semantic name and ensure it does not start with a digit."""
        sanitized = self.identifier_sanitizer.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def to_snowflake_relationship_name(self, name: str) -> str:
        """Convert canonical relationship name to Snowflake-layer identifier."""
        rel_name = self.sanitize_semantic_name(name)
        if rel_name.startswith("REL_"):
            return rel_name[4:]
        return rel_name

    def format_physical_column_ref(
        self,
        alias: str,
        phys_col: str,
        *,
        model_name: Optional[str] = None
    ) -> str:
        """Format a physical column reference for semantic-view emission."""
        if alias:
            # Quote the table alias if it's a reserved word
            safe_alias = f'"{alias}"' if alias.lower() in self.identifier_sanitizer._reserved else alias
            
            if self.is_client_data_model(model_name) and "$" in phys_col:
                # Client Data model compatibility path: non-quoted dollar-sign columns
                return f"{safe_alias}.{phys_col}"
            return f'{safe_alias}."{phys_col}"'
        return f'"{phys_col}"'

    @staticmethod
    def is_client_data_model(model_name: Optional[str]) -> bool:
        """Return True only for the legacy Client Data model compatibility path."""
        return str(model_name or "").strip().lower() == "client data"

    def remediate_invalid_identifier(
        self,
        ddl: str,
        invalid_identifier: str
    ) -> Tuple[str, bool, List[str]]:
        """Best-effort repair for semantic-view invalid identifier failures.

        Returns (fixed_ddl, changed, nulled_metric_names). nulled_metric_names
        lists every metric whose expression this call replaced with
        CAST(NULL AS DOUBLE) in the METRICS-clause sweep below — a single
        invalid identifier (e.g. a shared anchor column referenced by many
        metrics) can null out several metrics in one pass, and the caller
        must record a drop for each of them, not just one. Always empty for
        the two anchor-substitution special cases (MAX_DATE/MAX_MONTHINDEX)
        below, since those keep every metric's real semantics instead of
        nulling anything, and for TABLES/RELATIONSHIPS/DIMENSIONS-only fixes,
        since no metric was nulled in those cases either.
        """
        if not ddl or not invalid_identifier:
            return ddl, False, []

        invalid_norm = invalid_identifier.upper().replace('"', "")

        # Special case: MAX_DATE is a synthetic anchor that the translator
        # injected by replacing CURRENT_DATE().  Snowflake semantic views do
        # not have this column in scope, but CURRENT_DATE() is valid.  Replace
        # all bare MAX_DATE tokens globally rather than nulling out metrics.
        if invalid_norm == "MAX_DATE":
            # Replace bare MAX_DATE (not inside quotes) with CURRENT_DATE()
            fixed = re.sub(r'(?<!["\w])MAX_DATE(?!["\w])', 'CURRENT_DATE()', ddl)
            if fixed != ddl:
                return fixed, True, []
            return ddl, False, []

        if invalid_norm == "MAX_MONTHINDEX":
            # MAX_MONTHINDEX is a synthetic anchor column for rolling-period metrics.
            # Replace with EXTRACT(MONTH FROM CURRENT_DATE()) * 12 + EXTRACT(YEAR FROM CURRENT_DATE())
            # as a close semantic approximation (months since epoch).  This keeps the
            # metric alive with a sensible current-period value.
            fixed = re.sub(
                r'(?<!["\w])MAX_MONTHINDEX(?!["\w])',
                '(EXTRACT(YEAR FROM CURRENT_DATE()) * 12 + EXTRACT(MONTH FROM CURRENT_DATE()))',
                ddl
            )
            if fixed != ddl:
                return fixed, True, []
            return ddl, False, []

        invalid_alias: Optional[str] = None
        invalid_col: Optional[str] = None
        if "." in invalid_norm:
            invalid_alias, invalid_col = invalid_norm.split(".", 1)
        else:
            invalid_col = invalid_norm

        lines = ddl.splitlines()
        remediated_lines: list[str] = []
        nulled_metric_names: List[str] = []
        in_tables = False
        in_relationships = False
        in_dimensions = False
        in_metrics = False
        changed = False

        metric_line_pattern = re.compile(r'^(\s*\w+\."([^"]+)"\s+AS\s+).+?(?P<comma>,?)\s*$')
        table_pk_pattern = re.compile(
            r'^(\s*)(\w+)(\s+AS\s+.+?)\s+PRIMARY\s+KEY\s+\("([^"]+)"\)\s*(?P<comma>,?)\s*$',
            flags=re.IGNORECASE,
        )
        dim_fallback_pattern = re.compile(
            r'^\s*(\w+)\."[^"]+"\s+AS\s+\w+\."([^"]+)"\s*,?\s*$',
            flags=re.IGNORECASE,
        )
        invalid_token_pattern = re.compile(
            rf'(?<![A-Z0-9_]){re.escape(invalid_col or invalid_norm)}(?![A-Z0-9_])'
        )

        # Determine deterministic fallback PK columns from DIMENSIONS by alias.
        fallback_pk_by_alias: dict[str, str] = {}
        in_dim_scan = False
        for line in lines:
            stripped_upper = line.strip().upper()
            if stripped_upper.startswith("DIMENSIONS ("):
                in_dim_scan = True
                continue
            if in_dim_scan and line.strip().startswith(")"):
                in_dim_scan = False
                continue
            if not in_dim_scan:
                continue
            dim_match = dim_fallback_pattern.match(line)
            if not dim_match:
                continue
            alias_name = dim_match.group(1).upper()
            physical_name = dim_match.group(2).upper()
            if physical_name == (invalid_col or ""):
                continue
            fallback_pk_by_alias.setdefault(alias_name, physical_name)

        for line in lines:
            stripped_upper = line.strip().upper()
            if stripped_upper.startswith("TABLES ("):
                in_tables = True
                in_relationships = in_dimensions = in_metrics = False
                remediated_lines.append(line)
                continue
            if stripped_upper.startswith("RELATIONSHIPS ("):
                in_relationships = True
                in_tables = in_dimensions = in_metrics = False
                remediated_lines.append(line)
                continue
            if stripped_upper.startswith("DIMENSIONS ("):
                in_dimensions = True
                in_tables = in_relationships = in_metrics = False
                remediated_lines.append(line)
                continue
            if stripped_upper.startswith("METRICS ("):
                in_metrics = True
                in_tables = in_relationships = in_dimensions = False
                remediated_lines.append(line)
                continue
            # A bare ")" line closes the current clause — but only if it is
            # truly a clause-closing paren (i.e. the stripped line is exactly
            # ")" or ");").  Inner parentheses inside multi-line expressions
            # start with ")" but are followed by more content.
            if (in_tables or in_relationships or in_dimensions or in_metrics) and re.match(r'^\s*\)\s*;?\s*$', line):
                in_tables = in_relationships = in_dimensions = in_metrics = False
                remediated_lines.append(line)
                continue

            line_norm = line.upper().replace('"', "")
            # Match on the word-bounded token pattern only -- a plain
            # substring check (``invalid_norm in line_norm``) used to be
            # OR'd in here as well, but for a short invalid identifier
            # (e.g. a bare "_") that matches almost every line in the
            # clause, since most identifiers contain an underscore
            # somewhere (TOTAL_UNITS, SALESFACT_DATE_DATE_DATE, ...).
            # invalid_token_pattern already requires non-identifier
            # characters on both sides, so it correctly isolates a
            # standalone bad token without also matching underscores (or
            # any other invalid_norm substring) embedded inside a longer,
            # perfectly valid identifier. Single-quoted string literals are
            # scrubbed first so a SQL literal that happens to contain the
            # token (e.g. a LIKE '_%' wildcard) is never mistaken for an
            # identifier reference either.
            line_norm_for_match = re.sub(r"'(?:''|[^'])*'", "''", line_norm)
            contains_invalid = bool(invalid_token_pattern.search(line_norm_for_match))

            if in_tables and contains_invalid:
                pk_match = table_pk_pattern.match(line)
                if pk_match and invalid_col:
                    line_alias = pk_match.group(2).upper()
                    line_pk = pk_match.group(4).upper()
                    alias_matches = (invalid_alias is None) or (line_alias == invalid_alias)
                    if alias_matches and line_pk == invalid_col:
                        indent = pk_match.group(1)
                        alias_token = pk_match.group(2)
                        as_clause = pk_match.group(3)
                        comma = pk_match.group("comma") or ""
                        fallback_pk_col = fallback_pk_by_alias.get(line_alias)
                        if fallback_pk_col:
                            remediated_lines.append(
                                f'{indent}{alias_token}{as_clause} PRIMARY KEY ("{fallback_pk_col}"){comma}'
                            )
                        else:
                            remediated_lines.append(f"{indent}{alias_token}{as_clause}{comma}")
                        changed = True
                        continue

            if in_relationships and contains_invalid:
                changed = True
                continue

            if in_dimensions and contains_invalid:
                changed = True
                continue

            if in_metrics and contains_invalid:
                # Replace the metric expression with a NULL placeholder so the
                # metric declaration survives but produces no data.  The
                # metric_line_pattern extracts the alias/name prefix; if it
                # doesn't match (e.g. synonym suffix or unusual formatting)
                # we still drop the whole line so the clause stays valid.
                metric_match = metric_line_pattern.match(line)
                if metric_match:
                    prefix = metric_match.group(1)
                    nulled_metric_names.append(metric_match.group(2))
                    # Strip any trailing synonym clause from the original line
                    # so we can reconstruct a clean replacement.
                    raw_after_as = line[metric_match.end(1):]
                    # Detect trailing comma (before optional WITH SYNONYMS or $)
                    trailing_comma = "," if raw_after_as.rstrip().endswith(",") or "," in raw_after_as else ""
                    remediated_lines.append(f"{prefix}CAST(NULL AS DOUBLE){trailing_comma}")
                else:
                    # Continuation line or unrecognised format — drop it entirely;
                    # _normalize_all_clause_commas will fix up trailing commas.
                    # No name is resolvable here, so this metric (if any) can't
                    # be added to nulled_metric_names — same pre-existing
                    # limitation the caller's own fallback attribution has.
                    pass
                changed = True
                continue

            remediated_lines.append(line)

        self._normalize_all_clause_commas(remediated_lines)
        self._strip_empty_optional_clauses(remediated_lines)
        return "\n".join(remediated_lines), changed, nulled_metric_names

    @staticmethod
    def _strip_empty_optional_clauses(
        all_lines: list[str],
        clause_headers: Tuple[str, ...] = ("RELATIONSHIPS (", "DIMENSIONS ("),
    ) -> None:
        """Remove a clause's header and closing paren entirely if remediation
        left it with zero real content lines, mutating ``all_lines`` in place.

        Dropping individual invalid-identifier lines above can leave a clause
        with nothing between its header and closing paren (e.g.
        ``RELATIONSHIPS (\\n)``), which is syntactically invalid Snowflake
        DDL. RELATIONSHIPS and DIMENSIONS are both optional clauses in a
        CREATE SEMANTIC VIEW statement, so when remediation empties one out
        the whole clause is removed rather than left dangling — this is
        defense-in-depth for any invalid-identifier scenario that empties a
        clause, not just the one that motivated it.
        """
        for clause_header in clause_headers:
            idx = 0
            while idx < len(all_lines):
                if all_lines[idx].strip().upper() != clause_header:
                    idx += 1
                    continue
                start = idx + 1
                end = start
                depth = 1  # one level inside "CLAUSE_HEADER ("
                while end < len(all_lines):
                    line_stripped = all_lines[end].strip()
                    depth += line_stripped.count('(') - line_stripped.count(')')
                    if depth <= 0:
                        break
                    end += 1
                if end >= len(all_lines):
                    # Unterminated clause — leave it alone rather than guess.
                    idx += 1
                    continue
                has_content = any(all_lines[j].strip() for j in range(start, end))
                if has_content:
                    idx = end + 1
                else:
                    del all_lines[idx:end + 1]
                    # Don't advance idx — re-scan the same position in case
                    # deletion moved another occurrence of this header there.

    def _normalize_all_clause_commas(self, all_lines: list[str]) -> None:
        """Normalize trailing commas inside semantic-view clause blocks.

        TABLES is intentionally excluded — its items may span multiple lines
        (inline subqueries) and the sanitizer never removes TABLES entries,
        so comma normalization there is both unnecessary and dangerous.
        """
        clauses = ["RELATIONSHIPS (", "DIMENSIONS (", "METRICS ("]
        for clause in clauses:
            self._normalize_clause_commas(all_lines, clause)

    def _normalize_clause_commas(self, all_lines: list[str], clause_header: str) -> None:
        idx = 0
        while idx < len(all_lines):
            if all_lines[idx].strip().upper() != clause_header:
                idx += 1
                continue
            start = idx + 1
            # Track paren depth so multi-line inline subqueries inside TABLES
            # (e.g. "SALESFACT AS (\n  SELECT ...\n) PRIMARY KEY (...)") are
            # not mistaken for the closing paren of the clause itself.
            end = start
            depth = 1  # we are one level inside "CLAUSE_HEADER ("
            while end < len(all_lines):
                line_stripped = all_lines[end].strip()
                depth += line_stripped.count('(') - line_stripped.count(')')
                if depth <= 0:
                    break
                end += 1
            # Only normalise top-level items (depth==1 lines, i.e. not nested
            # inside an inline subquery).  A top-level item line is one where
            # the running depth before that line is exactly 1.
            item_idxs = []
            d = 1
            for j in range(start, end):
                line_stripped = all_lines[j].strip()
                if d == 1 and line_stripped:
                    item_idxs.append(j)
                d += line_stripped.count('(') - line_stripped.count(')')
            for pos, line_idx in enumerate(item_idxs):
                base = re.sub(r',\s*$', '', all_lines[line_idx].rstrip())
                all_lines[line_idx] = f"{base}," if pos < len(item_idxs) - 1 else base
            idx = end + 1
