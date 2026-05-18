"""
Groq-Based DAX to SQL Translator.

Provides fallback translation for complex DAX expressions using Groq LLM
(llama-3.3-70b-versatile) when deterministic parsing fails.

Intended as Tier 5 fallback after Tier 0-4 attempts.
"""

import re
import json
import os
import hashlib
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from semabridge.utils.logger import get_logger

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv not required, fallback to os.getenv()

logger = get_logger(__name__)


@dataclass
class LLMTranslationResult:
    """Result of LLM translation attempt."""
    sql: Optional[str]
    confidence: float  # 0.0-1.0
    reasoning: str
    is_valid: bool
    error: Optional[str] = None
    cached: bool = False


class LLMDAXTranslator:
    """
    Uses Groq (llama-3.3-70b-versatile) to translate complex DAX expressions.
    
    Designed as a fallback when deterministic parsing fails.
    Implements safety checks, confidence scoring, and response caching.
    """
    
    # Dangerous SQL patterns that indicate injection attempts or errors
    DANGEROUS_SQL_PATTERNS = [
        r"DROP\s+", r"DELETE\s+", r"TRUNCATE\s+", 
        r"ALTER\s+TABLE", r"CREATE\s+TABLE",
        r"INSERT\s+INTO", r"UPDATE\s+", r"EXEC\s*\(",
        r";--", r"/*\*.*\*/", r"UNION\s+SELECT",
    ]
    
    def __init__(self):
        """Initialize LLM translator with Groq API credentials from .env or environment."""
        # Always initialise these attrs so callers don't get AttributeError
        self.client = None
        self.model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.cache_file = ".llm_dax_cache.json"
        self.cache: Dict[str, Any] = {}

        try:
            # Load .env — try the file-relative path first, then CWD
            try:
                from dotenv import load_dotenv
                # Path relative to this source file: src/semabridge/converter/../../..  = project root
                file_relative = Path(__file__).resolve().parent.parent.parent.parent / '.env'
                cwd_relative = Path.cwd() / '.env'
                for env_path in [file_relative, cwd_relative]:
                    if env_path.exists():
                        load_dotenv(env_path, override=True)
                        break
            except ImportError:
                pass

            self.api_key = os.getenv("GROQ_API_KEY")
            if not self.api_key:
                logger.warning("GROQ_API_KEY not set - LLM translation disabled. Check .env file or environment variables.")
            else:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
                # llama-3.3-70b-versatile: fast, smart, great for SQL translation
                # Alternatives: "llama-3.1-8b-instant" (faster/cheaper), "mixtral-8x7b-32768" (longer context)
                self.cache = self._load_cache()
        except ImportError:
            logger.warning("groq client not installed - LLM translation disabled. Run: uv add groq")
    
    def translate(self, 
                dax: str, 
                table_alias: str, 
                dataset_name: str,
                metric_name: str = "",
                schema_context: Optional[Dict[str, List[str]]] = None) -> LLMTranslationResult:
        """
        Translate complex DAX to SQL using OpenAI GPT-4.
        
        Args:
            dax: DAX expression to translate
            table_alias: SQL alias for main table (e.g., 'salesfact')
            dataset_name: Name of dataset for context
            metric_name: Name of the metric being translated
            schema_context: Dict of {table: [columns]} for reference
            
        Returns:
            LLMTranslationResult with SQL, confidence, and validation status
        """
        if not self.client:
            return LLMTranslationResult(
                sql=None,
                confidence=0.0,
                reasoning="LLM client not initialized",
                is_valid=False,
                error="GROQ_API_KEY not configured"
            )
        
        if not dax or not dax.strip():
            return LLMTranslationResult(
                sql=None,
                confidence=0.0,
                reasoning="Empty DAX expression",
                is_valid=False
            )
        
        # Check cache first
        cache_key = self._get_cache_key(dax, table_alias, dataset_name)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            result = LLMTranslationResult(**cached["result"])
            result.cached = True
            logger.debug(f"Using cached translation for metric '{metric_name}'")
            return result
        
        try:
            # Build prompt with context
            prompt = self._build_prompt(dax, table_alias, dataset_name, metric_name, schema_context)
            
            # Call Groq LLM
            logger.debug(f"Calling Groq ({self.model}) for DAX translation: {metric_name or 'unnamed'}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                temperature=0.2,
                max_tokens=800,
            )
            
            response_text = response.choices[0].message.content.strip()
            
            # Parse response
            result = self._parse_response(response_text, dax, table_alias, metric_name)
            
            # Cache the result
            self.cache[cache_key] = {
                "result": {
                    "sql": result.sql,
                    "confidence": result.confidence,
                    "reasoning": result.reasoning,
                    "is_valid": result.is_valid,
                    "error": result.error,
                    "cached": False
                },
                "timestamp": datetime.utcnow().isoformat()
            }
            self._save_cache()
            
            return result
            
        except Exception as e:
            logger.error(f"LLM translation failed for '{metric_name}': {str(e)}")
            return LLMTranslationResult(
                sql=None,
                confidence=0.0,
                reasoning="LLM API call failed",
                is_valid=False,
                error=str(e)
            )
    
    def _get_cache_key(self, dax: str, table_alias: str, dataset_name: str) -> str:
        """Generate cache key for translation result."""
        key_str = f"{dax}|{table_alias}|{dataset_name}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from file."""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
                    # Clean up stale entries (older than 7 days)
                    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
                    return {
                        k: v for k, v in cache.items()
                        if v.get("timestamp", "") > cutoff
                    }
        except Exception as e:
            logger.warning(f"Failed to load LLM cache: {e}")
        return {}
    
    def _save_cache(self) -> None:
        """Save cache to file."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save LLM cache: {e}")
    
    
    def _build_prompt(self,
                    dax: str,
                    table_alias: str,
                    dataset_name: str,
                    metric_name: str,
                    schema_context: Optional[Dict[str, List[str]]]) -> str:
        """Build a precise DAX → Databricks SQL prompt with few-shot examples for metric views."""

        schema_ref = ""
        if schema_context:
            schema_lines: List[str] = []
            for table, cols in schema_context.items():
                cols_str = ", ".join(c for i, c in enumerate(cols) if i < 8)
                if len(cols) > 8:
                    cols_str += f", ... ({len(cols) - 8} more)"
                schema_lines.append(f"  {table}: [{cols_str}]")
            schema_ref = "\n".join(schema_lines)

            prompt = f"""You are an expert data engineer converting Microsoft DAX measure expressions into valid Databricks SQL aggregation expressions for metric views (WITH METRICS LANGUAGE YAML).

TARGET: Databricks SQL (Delta Lake / Unity Catalog dialect)
OUTPUT: Return ONLY the raw SQL expression — no explanation, no markdown, no code fences, no semicolons.

═══════════════════════════════════════════
CRITICAL CONSTRAINTS FOR METRIC VIEWS
═══════════════════════════════════════════
- NO subqueries of any kind (no SELECT inside parentheses).
- NO window functions (OVER(), PARTITION BY, ORDER BY).
- NO explicit GROUP BY or HAVING.
- NO nested aggregate functions (e.g., SUM(MAX(x)) or MAX(SUM(y))). Never place an aggregate inside another aggregate.
- NO FILTER clause inside aggregates. Use CASE WHEN instead.
- Output must be a single aggregation expression: SUM(...), COUNT(...), MIN(...), MAX(...), AVG(...), COUNT(DISTINCT ...), or ANY_VALUE(...).
- Use ANY_VALUE(expression) for non-aggregate scalars (strings, dates, single values).
- Use MAX(current_date()) for TODAY(). Never use CURRENT_DATE or CURRENT_DATE() alone.
- IMPORTANT: ANY_VALUE(...) is an aggregate. Do NOT put another aggregate inside ANY_VALUE.

═══════════════════════════════════════════
ENFORCED PATTERNS - NO EXCEPTIONS
═══════════════════════════════════════════
1. Nested aggregates are FORBIDDEN. Never output patterns like SUM(MAX(...)), ANY_VALUE(MAX(...)), MAX(MAX(...)), or COUNT(DISTINCT MAX(...)).
2. Subqueries are FORBIDDEN. Never write (SELECT MAX(...) FROM ...). Use pre-computed anchors instead.
3. FILTER clause is FORBIDDEN inside aggregates. Use CASE WHEN instead.
4. Division must use NULLIF to avoid divide-by-zero: SUM(a) / NULLIF(SUM(b), 0).
5. For percentage calculations, always wrap division in parentheses before multiplying by 100: (SUM(a) / NULLIF(SUM(b), 0)) * 100.
6. Always prefer pre-computed flags or anchors if available (e.g., `_current_fiscal_period`, `max_date`). Never recompute MAX(date) or MAX(period) inside the measure.
7. Prefer simple SUM(column) over SUM(CASE ...) if filtering is already applied upstream.
8. Avoid redundant ELSE 0 when NULL is acceptable.
9. For string callouts, use ANY_VALUE(CASE WHEN ... THEN ... ELSE NULL END). Do not place aggregates inside the CASE body.

═══════════════════════════════════════════
PRE-COMPUTED ANCHORS (Your Source Provides)
═══════════════════════════════════════════
- `_current_fiscal_period` : maximum FISCAL_YR_PERIOD up to current date.
- `max_date` (if available) : maximum transaction date.

Use these directly in filters. Do not recalculate them.
- Corporate DSI style measures should use the anchor pattern directly, for example:
    SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.ioh_excldng_lifo_amt ELSE 0 END)

═══════════════════════════════════════════
CALCULATE RULES
═══════════════════════════════════════════
CALCULATE(<expression>[, <filter1>, ...])
Translate as:
     SUM(col)        → SUM(CASE WHEN <filters> THEN col ELSE 0 END)
     COUNT(col)      → COUNT(CASE WHEN <filters> THEN col ELSE NULL END)
     MIN/MAX(col)    → MIN/MAX(CASE WHEN <filters> THEN col ELSE NULL END)
     DISTINCTCOUNT   → COUNT(DISTINCT CASE WHEN <filters> THEN col ELSE NULL END)
     DIVIDE(a,b)     → COALESCE(SUM(CASE ... THEN a ELSE 0 END) / NULLIF(SUM(CASE ... THEN b ELSE 0 END), 0), 0)
     scalar/string   → ANY_VALUE(CASE WHEN <filters> THEN expr ELSE NULL END)

═══════════════════════════════════════════
EXAMPLES (Correct for Metric Views)
═══════════════════════════════════════════
1. TODAY()
    SQL: MAX(current_date())

2. Last Refreshed
    DAX: CONCATENATE("Last Refreshed: ", MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))
    SQL: ANY_VALUE(CONCAT('Last Refreshed: ', DATE_FORMAT(corporate_dsi_last_refreshed.gl_refresh_datetime, 'MM/dd/yyyy HH:mm:ss')))

3. Simple SUM
    DAX: SUM('Corporate DSI Aggregate'[DSI_MNTHLY])
    SQL: SUM(corporate_dsi_aggregate.dsi_mnthly)

4. DIVIDE with COALESCE
    DAX: DIVIDE([Corporate COS], [Corporate IOH])
    SQL: COALESCE(SUM(corporate_dsi_aggregate.cos_excldng_lifo_amt) / NULLIF(SUM(corporate_dsi_aggregate.ioh_excldng_lifo_amt), 0), 0)

5. CALCULATE with simple filter
    DAX: CALCULATE(SUM('Inventory Fact'[Total Stock Qty]), 'Business Units'[Business Unit] = "Subledger")
    SQL: SUM(CASE WHEN business_units.business_unit = 'Subledger' THEN inventory_fact.total_stock_qty ELSE 0 END)

6. COUNTROWS with filter
    DAX: CALCULATE(COUNTROWS('Customer'), Customer[City] = "London")
    SQL: COUNT(CASE WHEN customer.city = 'London' THEN 1 ELSE NULL END)

7. Fiscal cut-off (DSI pattern) – USE ANCHOR
    DAX: CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
    SQL: SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.ioh_excldng_lifo_amt ELSE 0 END)

8. YTD with calendar date (if max_date anchor available)
    DAX: TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])
    SQL: SUM(CASE WHEN date_col >= DATE_TRUNC('YEAR', max_date) AND date_col <= max_date THEN amount ELSE 0 END)

9. Percentage (Market Share)
    DAX: DIVIDE(SUM('Sales'[Amount]), CALCULATE(SUM('Sales'[Amount]), ALL('Product')))
    SQL: COALESCE(SUM(CASE WHEN product = 'VanArsdel' THEN amount ELSE 0 END) / NULLIF(SUM(amount), 0), 0) * 100

10. IF(ISBLANK(...), 0, ...)
     DAX: IF(ISBLANK([Sales]), 0, [Sales])
     SQL: COALESCE(sales, 0)

═══════════════════════════════════════════
CONTEXT FOR THIS TRANSLATION
═══════════════════════════════════════════
Metric name     : {metric_name or 'unnamed'}
Dataset         : {dataset_name}
Root table alias: {table_alias}
{f'Available schema:{chr(10)}{schema_ref}' if schema_ref else ''}

═══════════════════════════════════════════
DAX TO TRANSLATE
═══════════════════════════════════════════
{dax}

Databricks SQL expression (only one line, no subqueries, no extra text):"""

        return prompt
    
    
    def _parse_response(self,
                        response: str,
                        original_dax: str,
                        table_alias: str,
                        metric_name: str) -> LLMTranslationResult:
        """
        Parse and validate the GPT-4 response.
        
        Returns:
            LLMTranslationResult with validation status
        """
        sql = response.strip()
        
        try:
            from semabridge.converter.dax_engine import sanitize_llm_sql

            sql = sanitize_llm_sql(sql)
        except Exception:
            # Remove markdown code blocks if present
            if sql.startswith("```"):
                match = re.search(r"```(?:sql)?\s*(.*?)\s*```", sql, re.DOTALL)
                if match:
                    sql = match.group(1).strip()
        
        # Remove trailing semicolon
        sql = sql.rstrip(";").strip()

        # Auto-fix bare CURRENT_DATE to the supported aggregate wrapper.
        def fix_current_date(expr: str) -> str:
            fixed = re.sub(
                r"\bCURRENT_DATE\s*(?:\(\s*\))?",
                "MAX(current_date())",
                expr,
                flags=re.IGNORECASE,
            )
            fixed = re.sub(
                r"MAX\s*\(\s*MAX\s*\(\s*current_date\s*\(\s*\)\s*\)\s*\)",
                "MAX(current_date())",
                fixed,
                flags=re.IGNORECASE,
            )
            return fixed

        sql = fix_current_date(sql)
        
        # Validate SQL
        is_valid, validation_errors = self._validate_sql(sql, original_dax, table_alias, metric_name)
        
        if not is_valid:
            logger.warning(f"Groq generated SQL failed validation for '{metric_name}': {validation_errors}")
            return LLMTranslationResult(
                sql=sql,  # Return it anyway for debugging
                confidence=0.2,
                reasoning=f"Validation failed: {validation_errors}",
                is_valid=False,
                error=validation_errors
            )
        
        # Score confidence
        confidence = self._score_confidence(sql, original_dax)
        
        return LLMTranslationResult(
            sql=sql,
            confidence=confidence,
            reasoning="Groq translation successful",
            is_valid=True
        )
    
    
    def _validate_business_rules(self, sql: str, metric_name: str) -> tuple[bool, str]:
        """Apply optional business-specific validation rules."""
        metric_lower = metric_name.lower()
        sql_lower = sql.lower()

        if "dsi" in metric_lower and "callout" not in metric_lower:
            if not any(token in sql_lower for token in ["ioh", "cos", "dsi_"]):
                return False, "DSI measure must reference a DSI-related source column"

        if "share" in metric_lower and "/" not in sql:
            return False, "Market share metric must include division (/)"

        return True, ""

    def _validate_sql(self, sql: str, original_dax: str, table_alias: str, metric_name: str = "") -> tuple[bool, str]:
        """
        Validate the generated SQL for safety and correctness.
        
        Returns:
            (is_valid, error_message)
        """
        if not sql:
            return False, "Empty SQL generated"
        
        # Check for dangerous patterns
        for pattern in self.DANGEROUS_SQL_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE):
                return False, f"Dangerous SQL pattern detected: {pattern}"
        
        # Check for unmatched parentheses
        if sql.count("(") != sql.count(")"):
            return False, "Unmatched parentheses"

        # Block subqueries and unsupported bare CURRENT_DATE usage.
        if re.search(r"\(\s*SELECT\s+", sql, re.IGNORECASE):
            return False, "Subquery detected (SELECT inside parentheses) - not allowed in metric views"

        if re.search(r"\bCURRENT_DATE\b", sql, re.IGNORECASE):
            if not re.search(r"MAX\s*\(\s*current_date\s*\(\s*\)\s*\)", sql, re.IGNORECASE):
                return False, "CURRENT_DATE must be wrapped in MAX(current_date())"

        # Division safety: require NULLIF when division is present.
        sql_without_string_literals = re.sub(r"'(?:''|[^'])*'", "''", sql)
        if "/" in sql_without_string_literals and "NULLIF" not in sql.upper():
            return False, "Division operator (/) requires NULLIF to prevent division by zero"

        # Reject SQL FILTER clauses inside aggregates.
        if re.search(r"\bFILTER\s*\(\s*WHERE\b", sql, re.IGNORECASE):
            return False, "FILTER clause is not allowed inside aggregates; use CASE WHEN instead"

        # Reject leftover DAX iterator / time-intelligence constructs in SQL output.
        forbidden_dax_patterns = [
            r"\bFILTER\s*\(",
            r"\bSUMX\s*\(",
            r"\bAVERAGEX\s*\(",
            r"\bRANKX\s*\(",
            r"\bTOTALYTD\s*\(",
            r"\bTOTALMTD\s*\(",
            r"\bTOTALQTD\s*\(",
            r"\bSAMEPERIODLASTYEAR\b",
            r"\bCOUNTROWS\s*\(",
            r"\bCOUNTBLANK\s*\(",
            r"\bSELECTEDVALUE\s*\(",
            r"\bSWITCH\s*\(",
            r"\bCALCULATE\s*\(",
            r"\bVAR\b",
            r"\bRETURN\b",
            r"\bIF\s*\(",
        ]
        for pattern in forbidden_dax_patterns:
            if re.search(pattern, sql, re.IGNORECASE):
                return False, f"Unsupported DAX construct detected in SQL output: {pattern}"

        def find_matching_paren(text: str, open_index: int) -> int:
            depth = 0
            in_single_quote = False
            in_double_quote = False
            escape_next = False

            for idx in range(open_index, len(text)):
                ch = text[idx]

                if escape_next:
                    escape_next = False
                    continue

                if ch == "\\":
                    escape_next = True
                    continue

                if ch == "'" and not in_double_quote:
                    in_single_quote = not in_single_quote
                    continue

                if ch == '"' and not in_single_quote:
                    in_double_quote = not in_double_quote
                    continue

                if in_single_quote or in_double_quote:
                    continue

                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return idx

            return -1

        aggregate_pattern = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|ANY_VALUE)\s*\(", re.IGNORECASE)
        for match in aggregate_pattern.finditer(sql):
            open_index = sql.find("(", match.start())
            close_index = find_matching_paren(sql, open_index)
            if close_index == -1:
                return False, "Unmatched parentheses"

            body = sql[open_index + 1:close_index]
            if aggregate_pattern.search(body):
                return False, "Nested aggregate function detected"
        
        # Check for basic SQL structure (Databricks dialect)
        sql_upper = sql.upper()

        # Accept any recognised SQL construct: aggregation, conditional, scalar functions
        has_structure = any(
            keyword in sql_upper
            for keyword in [
                "SUM", "AVG", "COUNT", "MIN", "MAX",
                "CASE", "WHEN",
                "COALESCE", "NULLIF",
                "CONCAT", "CONCAT_WS",
                "CURRENT_DATE", "CURRENT_TIMESTAMP",
                "DATE_FORMAT", "DATE_TRUNC",
                "CAST", "TRY_CAST", "ANY_VALUE", "COUNT_IF",
            ]
        )

        if not has_structure:
            return False, "No recognisable SQL construct detected (aggregation, CASE, COALESCE, CONCAT, etc.)"

        if metric_name:
            ok, msg = self._validate_business_rules(sql, metric_name)
            if not ok:
                return False, msg

        # Check for table alias usage (Databricks uses backticks)
        if "[" in original_dax and "]" in original_dax:
            if not any(alias in sql for alias in [table_alias, f"`{table_alias}`", f'"{table_alias}"']):
                logger.warning(f"Generated SQL missing table alias '{table_alias}'")
        
        return True, ""
    
    def _score_confidence(self, sql: str, original_dax: str) -> float:
        """
        Score confidence in the LLM translation (0.0-1.0).
        
        Factors:
        - Presence of aggregation functions
        - Matching complexity between input and output
        - Proper quote escaping
        """
        score = 0.5  # Base score
        
        # Has aggregation
        if any(agg in sql.upper() for agg in ["SUM", "AVG", "COUNT", "MIN", "MAX"]):
            score += 0.2
        
        # Has proper aggregation logic (not just bare column)
        if "(" in sql and ")" in sql:
            score += 0.15
        
        # Has proper quoting
        if '"' in sql or "'" in sql:
            score += 0.1
        
        # Reasonable length (not too short)
        if len(sql) > 20:
            score += 0.05
        
        # DAX complexity suggests LLM is appropriate
        if any(keyword in original_dax.upper() for keyword in ["CALCULATE", "FILTER", "ALLEXCEPT"]):
            score += 0.05

        # Penalties for risky patterns.
        if "CURRENT_DATE" in sql.upper():
            score -= 0.3
        if "SELECT" in sql.upper():
            score -= 0.5
        if "CASE WHEN" not in sql.upper() and "CALCULATE" in original_dax.upper():
            score -= 0.2
        if "/" in sql and "NULLIF" not in sql.upper():
            score -= 0.15
        
        return min(1.0, max(0.0, score))


# Global singleton instance
_llm_translator: Optional[LLMDAXTranslator] = None


def get_llm_translator() -> LLMDAXTranslator:
    """Get or create the global LLM translator instance."""
    global _llm_translator
    if _llm_translator is None:
        _llm_translator = LLMDAXTranslator()
    return _llm_translator
