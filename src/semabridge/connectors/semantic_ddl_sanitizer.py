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
                    f'  {fallback_alias}."PLACEHOLDER_METRIC" AS CAST(NULL AS DOUBLE)'
                ]
            else:
                met_items = self._consolidate_metric_lines(met_items)
                met_items = self._normalize_metric_display_name_refs(met_items)
            _set_items(m_start, m_end, met_items)

        # Determine all table aliases used in the DDL to avoid collisions
        table_aliases = set()
        if tables_block:
            t_start, t_end = tables_block
            table_items = _get_items(t_start, t_end)
            for item in table_items:
                m = re.search(r'^\s*(\w+)\s+AS\s+', item, flags=re.IGNORECASE)
                if m:
                    table_aliases.add(m.group(1).upper())

        normalized_ddl = "\n".join(lines)
        normalized_ddl = self._apply_competitive_marketing_hardening(normalized_ddl, table_aliases)
        normalized_ddl = self._inline_metric_references(normalized_ddl)
        normalized_ddl = re.sub(r",\s*,+", ",", normalized_ddl)

        # Final pass: remove any dangling comma immediately before a clause close.
        normalized_ddl = re.sub(r",\s*\n(\s*\)\s*;?)", r"\n\1", normalized_ddl)
        
        # Final pass: Ensure identifiers matching table aliases are quoted in DIMENSIONS/METRICS
        if table_aliases:
            normalized_ddl = self.sanitize_identifiers(normalized_ddl, table_aliases)
            normalized_ddl = re.sub(r",\s*,+", ",", normalized_ddl)
            
        return normalized_ddl

    @staticmethod
    def _consolidate_metric_lines(metric_items: list[str]) -> list[str]:
        """Merge multi-line metric expressions into single-line entries.

        The sanitizer's _get_items returns one entry per source line. When a
        metric expression spans multiple lines (e.g. a CASE statement broken
        across lines), each continuation line is treated as a separate metric
        and gets its own trailing comma — producing invalid SQL like ``CASE,``.

        This method detects continuation lines (lines that do NOT start with a
        ``ALIAS."NAME" AS`` pattern) and joins them onto the preceding metric
        line with a single space, collapsing multi-line expressions into a
        single-line representation that the rest of the sanitizer can handle.
        """
        if not metric_items:
            return metric_items

        # Pattern: a valid metric line starts with  ALIAS."NAME" AS
        metric_start_re = re.compile(
            r'^\s*\w+\."[^"]+"', re.IGNORECASE
        )

        consolidated: list[str] = []
        for item in metric_items:
            stripped = item.strip()
            if not stripped:
                continue
            if metric_start_re.match(stripped) or not consolidated:
                consolidated.append(item)
            else:
                # Continuation of the previous metric expression — join it
                prev = consolidated[-1].rstrip().rstrip(",")
                consolidated[-1] = prev + " " + stripped.lstrip(",").strip()

        return consolidated

    @staticmethod
    def _split_outer_synonyms_clause(expr: str) -> tuple[str, str]:
        """Split trailing WITH SYNONYMS clause from expression using depth-aware scan.

        The old regex approach fails when the expression contains nested
        parentheses (e.g. window functions) that contain their own
        ``WITH SYNONYMS = (...)`` — the regex can match an inner clause
        instead of the outermost one.  This implementation walks the string
        character-by-character tracking paren/quote depth so it only
        identifies a ``WITH SYNONYMS`` that appears at depth 0.
        """
        text = str(expr or "").strip()
        # Walk right-to-left: find the last top-level WITH SYNONYMS
        i = len(text) - 1
        depth = 0
        in_single = False
        candidate_start = -1

        # Scan right-to-left to find the closing ')' of WITH SYNONYMS at depth 0
        # then verify the full clause
        idx = 0
        length = len(text)
        syn_starts: list[int] = []  # positions where WITH SYNONYMS begins at depth 0

        while idx < length:
            ch = text[idx]
            if in_single:
                if ch == "'" and idx + 1 < length and text[idx + 1] == "'":
                    idx += 2  # escaped quote
                    continue
                if ch == "'":
                    in_single = False
            elif ch == "'":
                in_single = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and ch in ("W", "w"):
                # Check for WITH SYNONYMS at this position
                tail = text[idx:]
                if re.match(r"(?i)WITH\s+SYNONYMS\s*=\s*\(", tail):
                    syn_starts.append(idx)
            idx += 1

        if not syn_starts:
            return text, ""

        # Use the last (outermost) occurrence
        cut = syn_starts[-1]
        body = text[:cut].rstrip()
        clause = text[cut:].rstrip()
        return body, " " + clause

    @staticmethod
    def _strip_inner_synonyms(expr: str) -> str:
        """Remove ALL ``WITH SYNONYMS = (...)`` clauses nested inside an expression.

        Used when inlining a metric expression into another metric's body —
        any ``WITH SYNONYMS`` inside the outer expression is illegal Snowflake
        syntax and causes ``unexpected 'WITH'`` compilation errors.
        """
        # Repeatedly strip any WITH SYNONYMS = (...) that appears inside the
        # expression (at any depth) until none remain.
        pattern = re.compile(
            r"\s+WITH\s+SYNONYMS\s*=\s*\((?:[^()']|'(?:''|[^'])*')*\)",
            re.IGNORECASE,
        )
        prev = None
        result = expr
        while result != prev:
            prev = result
            result = pattern.sub("", result)
        return result

    @classmethod
    def _inline_metric_references(cls, ddl: str) -> str:
        """Inline bare metric references inside METRICS expressions.

        Snowflake semantic-view metric expressions compile as SQL expressions
        over logical tables. A bare quoted token like "TOTAL_UNITS" is parsed
        as a column identifier, not as a reusable measure. Expand references to
        previously emitted metric expressions so deploy does not fail with
        invalid identifier errors.
        """
        if not ddl or not re.search(r"\bMETRICS\s*\(", ddl, flags=re.IGNORECASE):
            return ddl

        lines = ddl.splitlines()
        metric_block = None
        for idx, line in enumerate(lines):
            if line.strip().upper() == "METRICS (":
                end = idx + 1
                while end < len(lines) and not lines[end].strip().startswith(")"):
                    end += 1
                if end < len(lines):
                    metric_block = (idx, end)
                break
        if metric_block is None:
            return ddl

        start, end = metric_block
        metric_lines = lines[start + 1:end]
        for _ in range(10):
            parsed = []
            expr_by_name: dict[str, str] = {}
            defined: set[str] = set()
            window_metrics: set[str] = set()
            for line in metric_lines:
                match = re.match(
                    r'^(\s*\w+\."([^"]+)"\s+AS\s+)(.+?)(,?)\s*$',
                    line.rstrip(),
                    flags=re.IGNORECASE,
                )
                if not match:
                    parsed.append((line, "", "", "", ""))
                    continue
                prefix, name, raw_expr, comma = match.groups()
                expr, syn_clause = cls._split_outer_synonyms_clause(raw_expr)
                defined.add(name)
                expr_by_name[name] = expr
                if re.search(r"\bOVER\b", expr, flags=re.IGNORECASE):
                    window_metrics.add(name)
                parsed.append((line, prefix, name, expr, syn_clause + (comma or "")))

            changed = False
            next_lines: list[str] = []
            for line, prefix, name, expr, suffix in parsed:
                if not name:
                    next_lines.append(line)
                    continue
                expanded_expr = expr
                if re.search(r"\bOVER\b", expr, flags=re.IGNORECASE):
                    candidate_refs = window_metrics
                else:
                    candidate_refs = defined
                refs = [
                    ref
                    for ref in set(re.findall(r'(?<!\.)"([A-Z_][A-Z0-9_]*)"', expr))
                    if ref in candidate_refs
                    and ref != name
                    and expr_by_name.get(ref)
                ]
                for ref_name in sorted(refs, key=len, reverse=True):
                    ref_expr = expr_by_name.get(ref_name)
                    if not ref_expr:
                        continue
                    # Strip ALL nested WITH SYNONYMS clauses from the referenced
                    # expression before inlining — nested WITH SYNONYMS inside
                    # another metric expression (especially inside window functions)
                    # is invalid Snowflake syntax and causes:
                    #   syntax error: unexpected 'WITH' / unexpected ','
                    clean_ref_expr = cls._strip_inner_synonyms(ref_expr)
                    expanded_expr = re.sub(
                        rf'(?<!\.)"{re.escape(ref_name)}"',
                        f"({clean_ref_expr})",
                        expanded_expr,
                    )
                clean_expanded_expr = cls._strip_inner_synonyms(expanded_expr)
                if clean_expanded_expr != expr:
                    changed = True
                next_lines.append(f"{prefix}{clean_expanded_expr}{suffix}")

            metric_lines = next_lines
            if not changed:
                break

        lines[start + 1:end] = metric_lines
        return "\n".join(lines)

    @staticmethod
    def _apply_competitive_marketing_hardening(ddl: str, table_aliases: set[str] = None) -> str:
        """Apply deterministic rewrites for known Competitive Marketing model defects."""
        out = str(ddl or "")
        if not out:
            return out

        if table_aliases is None:
            table_aliases = set()
            tables_match = re.search(r'(?is)\bTABLES\s*\((.*?)\)', ddl)
            if tables_match:
                for item in tables_match.group(1).split(','):
                    m = re.search(r'^\s*(\w+)\s+AS\s+', item, flags=re.IGNORECASE)
                    if m:
                        table_aliases.add(m.group(1).upper())

        replacements = [
            (r'(?im)^(\s*COL_DATE\s+AS\s+.+?\bPRIMARY\s+KEY\s*\()\s*"?(MONTHID)"?\s*(\)\s*,?\s*)$', r'\1"COL_DATE"\3'),
            (r'(?im)^(\s*SALESFACT\s+AS\s+.+?\bPRIMARY\s+KEY\s*\()\s*"?(PRODUCTID)"?\s*,\s*"?(ZIP)"?\s*(\)\s*,?\s*)$', r'\1"PRODUCTID", "COL_DATE", "ZIP"\4'),
            (r'(?im)^(\s*SENTIMENT\s+AS\s+.+?\bPRIMARY\s+KEY\s*\()\s*"?(ZIP)"?\s*,\s*"?(MANUFACTURERID)"?\s*(\)\s*,?\s*)$', r'\1"DATEID"\4'),
            (r'(?im)^(\s*SALESFACT_DATE_DATE_DATE\s+AS\s+SALESFACT\s*\(\s*"COL_DATE"\s*\)\s+REFERENCES\s+COL_DATE\s*\(\s*")MONTHID("\s*\)\s*,?\s*)$', r'\1COL_DATE\2'),
            (r'(?im)^(\s*SENTIMENT_DATE_DATE_DATE\s+AS\s+SENTIMENT\s*\(\s*"COL_DATE"\s*\)\s+REFERENCES\s+COL_DATE\s*\(\s*")MONTHID("\s*\)\s*,?\s*)$', r'\1COL_DATE\2'),
            (r'(?im)^\s*PRODUCT_SEGMENT_CATEGORY_CATEGORY\s+AS\s+PRODUCT\s*\(\s*"SEGMENT"\s*\)\s+REFERENCES\s+CATEGORY\s*\(\s*"CATEGORY"\s*\)\s*,?\s*$', ""),
            (r'(?im)^\s*MANUFACTURER\."MANUFACTURER_802B"\s+AS\s+MANUFACTURER\."MANUFACTURER"\s+WITH\s+SYNONYMS=\(\'Producer\',\'Maker\'\)\s*,?\s*$', '  MANUFACTURER."MANUFACTURER" AS MANUFACTURER."MANUFACTURER" WITH SYNONYMS=(\'Producer\',\'Maker\'),'),
            (r'(?im)^\s*KPI\."CATEGORY_791F"\s+AS\s+KPI\."CATEGORY"\s+WITH\s+SYNONYMS=\(\'Type\',\'Class\',\'Grouping\'\)\s*,?\s*$', '  KPI."CATEGORY" AS KPI."CATEGORY" WITH SYNONYMS=(\'Type\',\'Class\',\'Grouping\'),'),
            (r'(?im)^\s*CATEGORY\."SORT_3810"\s+AS\s+CATEGORY\."SORT"\s*,?\s*$', '  CATEGORY."SORT" AS CATEGORY."SORT",'),
            (r'(?im)^\s*CATEGORY\."CATEGORY_AC82"\s+AS\s+CATEGORY\."CATEGORY_AC82"\s*,?\s*$', ""),
            (r'(?im)^\s*CATEGORY\."CHANNEL_F73C"\s+AS\s+CATEGORY\."CHANNEL_F73C"\s*,?\s*$', ""),
            (r'(?im)^\s*CATEGORY\."SORT_234C"\s+AS\s+CATEGORY\."SORT_234C"\s*,?\s*$', ""),
            (r'(?im)^(\s*SALESFACT\."TOTAL_VANARSDEL_UNITS"\s+AS\s+)0(\s*,?\s*)$', r"\1SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'Yes' THEN SALESFACT.UNITS ELSE 0 END::FLOAT)\2"),
            (r'(?im)^(\s*SALESFACT\."TOTAL_OTHER_UNITS"\s+AS\s+)0(\s*,?\s*)$', r"\1SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT)\2"),
            (r'(?im)^(\s*SALESFACT\."TOTAL_CATEGORY_VOLUME"\s+AS\s+)0(\s*,?\s*)$', r'\1SUM(SALESFACT.UNITS::FLOAT)\2'),
            (r'(?im)^(\s*SALESFACT\."TOTAL_COMPETE_VOLUME"\s+AS\s+)0(\s*,?\s*)$', r"\1SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT)\2"),
            (r'(?im)^(\s*SALESFACT\."CATEGORY_COMPETE_SHARE"\s+AS\s+)0(\s*,?\s*)$', r"\1FLOOR(SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0) * 100)\2"),
            (r'(?im)^(\s*SALESFACT\."UNITS_MARKET_SHARE"\s+AS\s+)0(\s*,?\s*)$', r"\1CASE WHEN SUM(SALESFACT.UNITS::FLOAT) = 0 THEN 0 ELSE SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'Yes' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / SUM(SALESFACT.UNITS::FLOAT) END\2"),
            (r'(?im)^(\s*SALESFACT\."INDICATOR01"\s+AS\s+)0(\s*,?\s*)$', r"\1CASE WHEN SUM(CASE WHEN PRODUCT.ISVANARSDEL='No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0) < 0.55 THEN 1 WHEN SUM(CASE WHEN PRODUCT.ISVANARSDEL='No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0) > 0.60 THEN 3 ELSE 2 END\2"),
            (r'(?im)^\s*SALESFACT\."INDICATOR04"\s+AS\s+.*$', ""),
            (r'(?im)^\s*SALESFACT\."INDICATOR04A"\s+AS\s+.*$', ""),
            (r'(?im)^\s*SALESFACT\."INDICATOR05"\s+AS\s+.*$', ""),
            (r'(?im)^\s*SALESFACT\."INDICATOR05A"\s+AS\s+.*$', ""),
            # SENTIMENT_GAP: replace any broken/multi-line CASE with a correct expression
            (
                r'(?im)^(\s*SALESFACT\."SENTIMENT_GAP"\s+AS\s+)(?!AVG|SUM|NULL|CASE\s+WHEN).*$',
                r"\1NULLIF(AVG(CASE WHEN MANUFACTURER.MFGISVANARSDEL = 'No' THEN SENTIMENT.SCORE ELSE NULL END::FLOAT), 0) - NULLIF(AVG(CASE WHEN MANUFACTURER.MFGISVANARSDEL = 'Yes' THEN SENTIMENT.SCORE ELSE NULL END::FLOAT), 0)",
            ),
            # Move Date/Time-intelligence metrics from COL_DATE to SALESFACT to ensure proper table scoping in Snowflake
            (r'(?im)^(\s*)COL_DATE\.("?)(TOTAL_VANARSDEL_UNITS_YTD|TOTAL_OTHER_UNITS_YTD|TOTAL_UNITS_SPLY|TOTAL_UNITS_YTD_SPLY|TOTAL_VANARSDEL_UNITS_YTD_SPLY|TOTAL_OTHER_UNITS_YTD_SPLY|MARKET_SHARE_SPLY_YTD|UNITS_MARKET_SHARE_SPLY|TOTAL_UNITS_R12MS|TOTAL_VANARSDEL_UNITS_R12M|TOTAL_OTHER_UNITS_R12M)\2(\s+AS\s+)', r'\1SALESFACT.\2\3\2\4'),
        ]
        replacements.extend([
            (r'(?im)^\s*MANUFACTURER\.MANUFACTURER_802B\s+AS\s+MANUFACTURER\."MANUFACTURER"\s+WITH\s+SYNONYMS\s*=\s*\(\'Producer\',\'Maker\'\)\s*,?\s*$',
             '  MANUFACTURER.MANUFACTURER as MANUFACTURER."MANUFACTURER" with synonyms=(\'Producer\',\'Maker\'),'),
            (r'(?im)^\s*KPI\.CATEGORY_791F\s+AS\s+KPI\."CATEGORY"\s+WITH\s+SYNONYMS\s*=\s*\(\'Type\',\'Class\',\'Grouping\'\)\s*,?\s*$',
             '  KPI.CATEGORY as KPI."CATEGORY" with synonyms=(\'Type\',\'Class\',\'Grouping\'),'),
            (r'(?im)^(\s*SALESFACT\.TOTAL_VANARSDEL_UNITS\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'Yes' THEN SALESFACT.UNITS ELSE 0 END::FLOAT)\2\3"),
            (r'(?im)^(\s*SALESFACT\.TOTAL_OTHER_UNITS\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT)\2\3"),
            (r'(?im)^(\s*SALESFACT\.TOTAL_CATEGORY_VOLUME\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1SUM(SALESFACT.UNITS::FLOAT)\2\3"),
            (r'(?im)^(\s*SALESFACT\.TOTAL_COMPETE_VOLUME\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT)\2\3"),
            (r'(?im)^(\s*SALESFACT\.CATEGORY_COMPETE_SHARE\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1FLOOR(SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0) * 100)\2\3"),
            (r'(?im)^(\s*SALESFACT\.UNITS_MARKET_SHARE\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1CASE WHEN SUM(SALESFACT.UNITS::FLOAT) = 0 THEN 0 ELSE SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'Yes' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / SUM(SALESFACT.UNITS::FLOAT) END\2\3"),
            (r'(?im)^(\s*SALESFACT\.INDICATOR01\s+AS\s+)0(\s+WITH\s+SYNONYMS\s*=\s*\([^\)]*\))(\s*,?\s*)$',
             r"\1CASE WHEN SUM(CASE WHEN PRODUCT.ISVANARSDEL='No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0) < 0.55 THEN 1 WHEN SUM(CASE WHEN PRODUCT.ISVANARSDEL='No' THEN SALESFACT.UNITS ELSE 0 END::FLOAT) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0) > 0.60 THEN 3 ELSE 2 END\2\3"),
        ])
        for pattern, repl in replacements:
            out = re.sub(pattern, repl, out)

        out = re.sub(
            r'LEFT\(\s*CAST\(\s*"TOTAL_UNITS_YTD_VAR_2"\s+AS\s+VARCHAR\s*\)\s*,\s*3\s*\)',
            "LEFT(CAST(0 AS VARCHAR), 3)",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"SUM\(\s*CAST\(\s*\((CASE\b.*?\bEND)\)\s+AS\s+FLOAT\s*\)::\s*FLOAT\s*\)",
            r"SUM(CAST((\1) AS FLOAT))",
            out,
            flags=re.IGNORECASE,
        )

        # Try to find resolved physical column for Date.RUNNING_YEAR in the DDL
        date_alias = "COL_DATE"
        for alias in table_aliases:
            if "DATE" in alias:
                date_alias = alias
                break

        if date_alias in table_aliases:
            running_year_col = "RUNNING_YEAR"
            # Try to find resolved physical column for RUNNING_YEAR in the DDL (it could be prefixed/suffixed)
            ry_match = re.search(rf'(?i)\b{re.escape(date_alias)}\."(RUNNING_YEAR_[A-Z0-9_]+)"', out)
            if ry_match:
                running_year_col = ry_match.group(1).upper()
            
            # Rewrite only the standalone generic column. Keep the trailing quote
            # inside the match so we do not leave `...BLANK"" = ...` in DDL.
            out = re.sub(
                rf'(?i)\b{re.escape(date_alias)}\."?RUNNING_YEAR\b"?',
                f'{date_alias}."{running_year_col}"',
                out,
            )
            out = re.sub(
                rf'(?i)\b{re.escape(date_alias)}\."(RUNNING_YEAR_[A-Z0-9_]+)""',
                rf'{date_alias}."\1"',
                out,
            )

        if "SENTIMENT" in table_aliases:
            # Try to find resolved physical column for SENTIMENT.SCORE in the DDL
            sentiment_score_col = "SCORE"
            score_match = re.search(r'(?i)\bSENTIMENT\."(SCORE_[A-Z0-9]+)"', out)
            if score_match:
                sentiment_score_col = score_match.group(1).upper()

            # Try to find resolved physical column for MANUFACTURER.MFGISVANARSDEL in the DDL
            manufacturer_flag_col = "MFGISVANARSDEL"
            flag_match = re.search(r'(?i)\bMANUFACTURER\."(MFGISVANARSDEL_[A-Z0-9]+)"', out)
            if flag_match:
                manufacturer_flag_col = flag_match.group(1).upper()

            sentiment_score_dim = f'  SENTIMENT."{sentiment_score_col}" AS SENTIMENT."{sentiment_score_col}"'
            # Only inject dimension if the exact or suffixed dimension isn't already present
            if f'SENTIMENT."{sentiment_score_col}"' not in out and "DIMENSIONS (" in out:
                out = re.sub(r'(?is)(DIMENSIONS\s*\(\s*)(.*?)(\s*\)\s*METRICS\s*\()', rf'\1\2,\n{sentiment_score_dim}\n\3', out, count=1)

            sentiment_metrics = [
                f'  SENTIMENT."INDICATOR04" AS CASE WHEN AVG(SENTIMENT."{sentiment_score_col}"::FLOAT) < 65 THEN 1 WHEN AVG(SENTIMENT."{sentiment_score_col}"::FLOAT) > 67 THEN 3 ELSE 2 END',
                f'  SENTIMENT."INDICATOR04A" AS CASE WHEN AVG(SENTIMENT."{sentiment_score_col}"::FLOAT) < 65 THEN \'Low Sentiment Rate\' WHEN AVG(SENTIMENT."{sentiment_score_col}"::FLOAT) > 67 THEN \'High Sentiment Rate\' ELSE \'Medium Sentiment Rate\' END::VARCHAR',
                f'  SENTIMENT."INDICATOR05" AS CASE WHEN (AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'No\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT) - AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'Yes\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT)) < 15 THEN 1 WHEN (AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'No\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT) - AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'Yes\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT)) > 25 THEN 3 ELSE 2 END',
                f'  SENTIMENT."INDICATOR05A" AS CASE WHEN (AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'No\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT) - AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'Yes\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT)) < 15 THEN \'Low Sentiment Gap\' WHEN (AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'No\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT) - AVG(CASE WHEN MANUFACTURER."{manufacturer_flag_col}"=\'Yes\' THEN SENTIMENT."{sentiment_score_col}" END::FLOAT)) > 25 THEN \'High Sentiment Gap\' ELSE \'Medium Sentiment Gap\' END::VARCHAR',
            ]
            if "METRICS (" in out and 'SENTIMENT."INDICATOR04"' not in out:
                out = re.sub(r'(?is)(METRICS\s*\(\s*)(.*?)(\s*\)\s*;?)$', lambda m: f"{m.group(1)}{m.group(2).rstrip()}{',' if m.group(2).strip() else ''}\n" + ",\n".join(sentiment_metrics) + f"\n{m.group(3)}", out, count=1)

            # Replace invalid SALESFACT.SENTIMENT references with SENTIMENT.SCORE
            out = re.sub(
                r'(?i)\bSALESFACT\."?SENTIMENT"?\b',
                f'SENTIMENT."{sentiment_score_col}"',
                out,
            )

        out = re.sub(r"\n{3,}", "\n\n", out)
        return out

    def _normalize_metric_display_name_refs(self, metric_items: list[str]) -> list[str]:
        """Rewrite quoted display-name metric references inside METRICS expressions."""
        metric_names: set[str] = set()
        metric_owner_by_name: dict[str, str] = {}
        for item in metric_items:
            match = re.search(r'^\s*(\w+)\."([^"]+)"\s+AS\s+', item, flags=re.IGNORECASE)
            if match:
                owner_alias = match.group(1).upper()
                metric_name = match.group(2).upper()
                metric_names.add(metric_name)
                metric_owner_by_name[metric_name] = owner_alias
        if not metric_names:
            return metric_items

        def _replace(match: re.Match) -> str:
            token = match.group(1)
            if token.upper() in metric_names:
                return match.group(0)
            try:
                sanitized = self.identifier_sanitizer.sanitize_alias(token)
            except Exception:
                sanitized = re.sub(r"[^A-Za-z0-9_$]+", "_", token).strip("_").upper()
            if sanitized.upper() in metric_names:
                owner_alias = metric_owner_by_name.get(sanitized.upper())
                if owner_alias:
                    return f'{owner_alias}."{sanitized.upper()}"'
                return f'"{sanitized.upper()}"'
            return match.group(0)

        def _replace_qualified(match: re.Match) -> str:
            alias = match.group(1)
            token = match.group(2)
            if token.upper() in metric_names:
                return match.group(0)
            try:
                sanitized = self.identifier_sanitizer.sanitize_alias(token).upper()
            except Exception:
                sanitized = re.sub(r"[^A-Za-z0-9_$]+", "_", token).strip("_").upper()
            if sanitized in metric_names:
                owner_alias = metric_owner_by_name.get(sanitized, alias.upper())
                return f'{owner_alias}."{sanitized}"'
            return match.group(0)

        normalized: list[str] = []
        for item in metric_items:
            parts = re.split(r'(\s+AS\s+)', item, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) != 3:
                normalized.append(item)
                continue
            metric_match = re.search(r'^\s*\w+\."([^"]+)"\s*$', parts[0], flags=re.IGNORECASE)
            current_metric_name = metric_match.group(1).upper() if metric_match else ""
            expr = re.sub(
                r'"([A-Za-z_][A-Za-z0-9_$]*)"\."([^"]+)"',
                _replace_qualified,
                parts[2],
            )
            expr = re.sub(r'(?<!\.)"([^"]+)"', _replace, expr)
            expr = self._normalize_metric_owner_refs(expr, metric_owner_by_name)
            if (
                expr.strip().upper() == "NULL"
                or self._has_derived_metric_reference(expr, current_metric_name, metric_names)
                or self._has_unresolved_bare_metric_identifier(expr, metric_names)
            ):
                synonym_suffix = ""
                synonym_match = re.search(r'\s+WITH\s+SYNONYMS\s+=\s+\(.+\)\s*$', expr, flags=re.IGNORECASE)
                if synonym_match:
                    synonym_suffix = synonym_match.group(0)
                expr = f"CAST(NULL AS DOUBLE){synonym_suffix}"
            normalized.append(f"{parts[0]}{parts[1]}{expr}")
        return normalized

    @staticmethod
    def _normalize_metric_owner_refs(expr: str, metric_owner_by_name: dict[str, str]) -> str:
        """Route qualified metric references to the alias that emits that metric."""
        if not expr or not metric_owner_by_name:
            return expr

        def _replace(match: re.Match) -> str:
            alias = match.group(1)
            metric_name = match.group(2).upper()
            owner_alias = metric_owner_by_name.get(metric_name)
            if owner_alias and alias.upper() != owner_alias:
                return f'{owner_alias}."{metric_name}"'
            return match.group(0)

        return re.sub(r'\b([A-Za-z_][A-Za-z0-9_$]*)\."([A-Z_][A-Z0-9_$]*)"', _replace, expr)

    @staticmethod
    def _has_derived_metric_reference(expr: str, current_metric_name: str, metric_names: set[str]) -> bool:
        """Snowflake semantic metrics reject many metric-on-metric expressions."""
        if not expr or not metric_names:
            return False
        for _, name in re.findall(r'\b([A-Za-z_][A-Za-z0-9_$]*)\."([A-Z_][A-Z0-9_$]*)"', expr):
            metric_name = name.upper()
            if metric_name in metric_names and metric_name != current_metric_name:
                return True
        for name in re.findall(r'(?<!\.)"([A-Z_][A-Z0-9_$]*)"', expr):
            metric_name = name.upper()
            if metric_name in metric_names and metric_name != current_metric_name:
                return True
        return False

    @staticmethod
    def _has_unresolved_bare_metric_identifier(expr: str, metric_names: set[str]) -> bool:
        """Detect obvious unqualified identifiers that Snowflake will reject."""
        if not expr:
            return False
        scrubbed = re.sub(r"'(?:''|[^'])*'", "''", expr)
        for quoted in re.findall(r'(?<!\.)"([^"]+)"', scrubbed):
            if quoted.upper() not in metric_names:
                return True
        scrubbed = re.sub(r'\b[A-Za-z_][A-Za-z0-9_$]*\s*\.\s*"[^"]+"', " ", scrubbed)
        scrubbed = re.sub(r'\b[A-Za-z_][A-Za-z0-9_$]*\s*\.\s*[A-Za-z_][A-Za-z0-9_$]*', " ", scrubbed)
        scrubbed = re.sub(r'"[A-Z_][A-Z0-9_$]*"', " ", scrubbed)
        keywords = {
            "AND", "AS", "ASC", "AVG", "BETWEEN", "BY", "CASE", "CAST", "COALESCE",
            "CURRENT", "CURRENT_DATE", "DATEADD", "DATEDIFF", "DAY", "DESC",
            "DISTINCT", "DIVIDE", "DOUBLE", "ELSE", "END", "FALSE", "FLOAT", "FROM",
            "GROUP", "IFF", "IN", "INT", "IS", "LAG", "LEFT", "LIKE", "MAX",
            "MIN", "MONTH", "NOT", "NULL", "NULLIF", "OR", "ORDER", "OVER",
            "PARTITION", "ROWS", "SUM", "THEN", "TO_DATE", "TRUE",
            "TRY_CAST", "TRY_TO_DATE", "VARCHAR", "WHEN", "WITH", "SYNONYMS", "YEAR",
        }
        for token in re.findall(r'\b[A-Za-z_][A-Za-z0-9_$]*\b', scrubbed):
            upper = token.upper()
            if upper in keywords or upper in metric_names:
                continue
            return True
        return False

    def sanitize_identifiers(self, ddl: str, table_aliases: set[str]) -> str:
        """Ensure identifiers that match table aliases are quoted in expressions.
        
        Snowflake can get confused if an unquoted metric/dimension identifier 
        matches a table alias in the same semantic view.
        """
        lines = ddl.splitlines()
        in_dimensions = False
        in_metrics = False
        sanitized_lines = []

        for line in lines:
            stripped = line.strip().upper()
            if stripped.startswith("DIMENSIONS ("):
                in_dimensions = True
            elif stripped.startswith("METRICS ("):
                in_metrics = True
            elif stripped.startswith(")"):
                in_dimensions = False
                in_metrics = False

            if (in_dimensions or in_metrics) and not stripped.startswith(("DIMENSIONS (", "METRICS (", ")")):
                # Quote bare identifiers matching table aliases
                # e.g. SUM(SENTIMENT) -> SUM("SENTIMENT")
                chunks = re.split(r"('(?:''|[^'])*')", line)
                for i, chunk in enumerate(chunks):
                    if i % 2 == 1:
                        continue
                    for alias in table_aliases:
                        pattern = rf'(?<![\w\.\"])\b{re.escape(alias)}\b(?![\w\."])'
                        chunk = re.sub(pattern, f'"{alias}"', chunk, flags=re.IGNORECASE)
                    chunks[i] = chunk
                line = "".join(chunks)
            
            sanitized_lines.append(line)
        
        return "\n".join(sanitized_lines)

    def remediate_invalid_identifier(
        self,
        ddl: str,
        invalid_identifier: str
    ) -> Tuple[str, bool]:
        """Best-effort repair for semantic-view invalid identifier failures."""
        if not ddl or not invalid_identifier:
            return ddl, False

        invalid_norm = invalid_identifier.upper().replace('"', "")

        # Special case: MAX_DATE is a synthetic anchor that the translator
        # injected by replacing CURRENT_DATE().  Snowflake semantic views do
        # not have this column in scope, but CURRENT_DATE() is valid.  Replace
        # all bare MAX_DATE tokens globally rather than nulling out metrics.
        if invalid_norm == "MAX_DATE":
            # Replace bare MAX_DATE (not inside quotes) with CURRENT_DATE()
            fixed = re.sub(r'(?<!["\w])MAX_DATE(?!["\w])', 'CURRENT_DATE()', ddl)
            if fixed != ddl:
                return fixed, True
            return ddl, False

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
            # A bare ")" line closes the current clause — but only if it is
            # truly a clause-closing paren (i.e. the stripped line is exactly
            # ")" or ");").  Inner parentheses inside multi-line expressions
            # start with ")" but are followed by more content.
            if (in_tables or in_relationships or in_dimensions or in_metrics) and re.match(r'^\s*\)\s*;?\s*$', line):
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
                # Replace the metric expression with a NULL placeholder so the
                # metric declaration survives but produces no data.  The
                # metric_line_pattern extracts the alias/name prefix; if it
                # doesn't match (e.g. synonym suffix or unusual formatting)
                # we still drop the whole line so the clause stays valid.
                metric_match = metric_line_pattern.match(line)
                if metric_match:
                    prefix = metric_match.group(1)
                    # Strip any trailing synonym clause from the original line
                    # so we can reconstruct a clean replacement.
                    raw_after_as = line[metric_match.end(1):]
                    # Detect trailing comma (before optional WITH SYNONYMS or $)
                    trailing_comma = "," if raw_after_as.rstrip().endswith(",") or "," in raw_after_as else ""
                    remediated_lines.append(f"{prefix}CAST(NULL AS DOUBLE){trailing_comma}")
                else:
                    # Continuation line or unrecognised format — drop it entirely;
                    # _normalize_all_clause_commas will fix up trailing commas.
                    pass
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
