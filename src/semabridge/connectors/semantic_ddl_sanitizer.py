"""Sanitization and structural normalization for Snowflake semantic-view DDL."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SemanticDDLSanitizer:
    """Handles identifier sanitization and DDL structural integrity for Snowflake."""

    def __init__(self, identifier_sanitizer: Any):
        self.identifier_sanitizer = identifier_sanitizer

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

    def sanitize_structure(self, ddl: str) -> str:
        """Normalize semantic-view clause structure before execution.

        - Rewrites trailing commas so only non-last clause items end with comma.
        - Removes empty RELATIONSHIPS block.
        - Ensures DIMENSIONS and METRICS are non-empty with safe fallbacks.
        """
        if not ddl or not re.search(r"\bSEMANTIC\s+VIEW\b", ddl, flags=re.IGNORECASE):
            return ddl

        lines = ddl.splitlines()

        def _find_block(header: str) -> tuple[int, int] | None:
            start = None
            for i, line in enumerate(lines):
                if line.strip().upper() == header:
                    start = i
                    break
            if start is None:
                return None
            end = start + 1
            while end < len(lines) and not lines[end].strip().startswith(")"):
                end += 1
            if end >= len(lines):
                return None
            return start, end

        def _get_items(start: int, end: int) -> list[str]:
            items: list[str] = []
            for i in range(start + 1, end):
                stripped = lines[i].strip()
                if not stripped or stripped == ",":
                    continue
                items.append(lines[i])
            return items

        def _set_items(start: int, end: int, items: list[str]) -> None:
            cleaned = []
            for item in items:
                base = re.sub(r',\s*$', '', item.rstrip())
                if base.strip():
                    cleaned.append(base)

            normalized = []
            for idx, base in enumerate(cleaned):
                normalized.append(f"{base}," if idx < len(cleaned) - 1 else base)
            lines[start + 1:end] = normalized

        # Determine first table alias + PK for safe fallbacks.
        fallback_alias = "DUMMY"
        fallback_pk = "ID"
        tables_block = _find_block("TABLES (")
        if tables_block:
            t_start, t_end = tables_block
            table_items = _get_items(t_start, t_end)
            if table_items:
                for first in table_items:
                    m = re.search(r'^\s*(\w+)\s+AS\s+.+?\bPRIMARY\s+KEY\s+\("([^"]+)"\)', first, flags=re.IGNORECASE)
                    if m:
                        fallback_alias = m.group(1)
                        fallback_pk = m.group(2)
                        break
                    else:
                        m2 = re.search(r'^\s*(\w+)\s+AS\s+', first, flags=re.IGNORECASE)
                        if m2:
                            fallback_alias = m2.group(1)
                _set_items(t_start, t_end, table_items)

        # RELATIONSHIPS: remove block entirely if empty.
        rel_block = _find_block("RELATIONSHIPS (")
        if rel_block:
            r_start, r_end = rel_block
            rel_items = _get_items(r_start, r_end)
            if not rel_items:
                del lines[r_start:r_end + 1]
            else:
                _set_items(r_start, r_end, rel_items)

        # DIMENSIONS: ensure at least one valid line.
        dim_block = _find_block("DIMENSIONS (")
        if dim_block:
            d_start, d_end = dim_block
            dim_items = _get_items(d_start, d_end)
            if not dim_items:
                dim_items = [
                    f'  {fallback_alias}."{fallback_pk}" AS {fallback_alias}."{fallback_pk}"'
                ]
            _set_items(d_start, d_end, dim_items)

        # METRICS: ensure at least one valid line.
        met_block = _find_block("METRICS (")
        if met_block:
            m_start, m_end = met_block
            met_items = _get_items(m_start, m_end)
            if not met_items:
                met_items = [
                    f'  {fallback_alias}."PLACEHOLDER_METRIC" AS NULL'
                ]
            _set_items(m_start, m_end, met_items)

        normalized_ddl = "\n".join(lines)
        # Final pass: remove any dangling comma immediately before a clause close.
        normalized_ddl = re.sub(r",\s*\n(\s*\)\s*;?)", r"\n\1", normalized_ddl)
        return normalized_ddl

    def remediate_invalid_identifier(
        self,
        ddl: str,
        invalid_identifier: str
    ) -> Tuple[str, bool]:
        """Best-effort repair for semantic-view invalid identifier failures."""
        if not ddl or not invalid_identifier:
            return ddl, False

        invalid_norm = invalid_identifier.upper().replace('"', "")
        invalid_alias: Optional[str] = None
        invalid_col: Optional[str] = None
        if "." in invalid_norm:
            invalid_alias, invalid_col = invalid_norm.split(".", 1)
        else:
            invalid_col = invalid_norm

        lines = ddl.splitlines()
        remediated_lines: list[str] = []
        in_tables = False
        in_relationships = False
        in_dimensions = False
        in_metrics = False
        changed = False
        
        metric_line_pattern = re.compile(r'^(\s*\w+\."[^"]+"\s+AS\s+).+?(?P<comma>,?)\s*$')
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
            if (in_tables or in_relationships or in_dimensions or in_metrics) and line.strip().startswith(")"):
                in_tables = in_relationships = in_dimensions = in_metrics = False
                remediated_lines.append(line)
                continue

            line_norm = line.upper().replace('"', "")
            contains_invalid = (
                invalid_norm in line_norm
                or bool(invalid_token_pattern.search(line_norm))
            )

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
                metric_match = metric_line_pattern.match(line)
                if metric_match:
                    prefix = metric_match.group(1)
                    comma = metric_match.group("comma") or ""
                    remediated_lines.append(f"{prefix}NULL{comma}")
                    changed = True
                    continue

            remediated_lines.append(line)

        self._normalize_all_clause_commas(remediated_lines)
        return "\n".join(remediated_lines), changed

    def _normalize_all_clause_commas(self, all_lines: list[str]) -> None:
        """Normalize trailing commas inside semantic-view clause blocks."""
        clauses = ["TABLES (", "RELATIONSHIPS (", "DIMENSIONS (", "METRICS ("]
        for clause in clauses:
            self._normalize_clause_commas(all_lines, clause)

    def _normalize_clause_commas(self, all_lines: list[str], clause_header: str) -> None:
        idx = 0
        while idx < len(all_lines):
            if all_lines[idx].strip().upper() != clause_header:
                idx += 1
                continue
            start = idx + 1
            end = start
            while end < len(all_lines) and not all_lines[end].strip().startswith(")"):
                end += 1
            item_idxs = [j for j in range(start, end) if all_lines[j].strip()]
            for pos, line_idx in enumerate(item_idxs):
                base = re.sub(r',\s*$', '', all_lines[line_idx].rstrip())
                all_lines[line_idx] = f"{base}," if pos < len(item_idxs) - 1 else base
            idx = end + 1
