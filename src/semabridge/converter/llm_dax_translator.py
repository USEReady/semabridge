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

        prompt = f"""You are an expert data engineer converting Microsoft DAX measure expressions into valid Databricks SQL aggregation expressions for **metric views** (WITH METRICS LANGUAGE YAML).

TARGET: Databricks SQL (Delta Lake / Unity Catalog dialect)
OUTPUT: Return ONLY the raw SQL expression — no explanation, no markdown, no code fences, no semicolons.

═══════════════════════════════════════════
CRITICAL CONSTRAINTS FOR METRIC VIEWS
═══════════════════════════════════════════
- NO subqueries of any kind (no SELECT inside parentheses).
- NO window functions (OVER(), PARTITION BY, ORDER BY).
- NO explicit GROUP BY or HAVING.
- NO nested aggregate functions (e.g., SUM(MAX(x)) or MAX(SUM(y))). NEVER place an aggregate function inside another aggregate function. Databricks rejects this. ALWAYS flatten to a single layer of aggregation.
- Output must be a single aggregation expression: 
    SUM(...), COUNT(...), MIN(...), MAX(...), AVG(...), 
    COUNT(DISTINCT ...), or ANY_VALUE(...).
- Use ANY_VALUE(expression) for non-aggregate scalars (e.g., strings, dates, single values).
- Use MAX(current_date()) for TODAY() so it is a valid aggregate expression.
- IMPORTANT: ANY_VALUE(...) is an aggregate. Do NOT put another aggregate inside ANY_VALUE (no ANY_VALUE(MAX(...))).

═══════════════════════════════════════════
CALCULATE RULES (Very Important)
═══════════════════════════════════════════
CALCULATE(<expression>[, <filter1> [, <filter2> [, ...]]])
The first parameter <expression> is itself a measure. It can be ANY expression, not just SUM.

How to translate:
1. Identify the aggregation inside <expression>:
     SUM(col)          → SUM(CASE WHEN <filters> THEN col ELSE 0 END)
     COUNT(col)        → COUNT(CASE WHEN <filters> THEN col ELSE NULL END)
     MIN/MAX(col)      → MIN/MAX(CASE WHEN <filters> THEN col ELSE NULL END)
     DISTINCTCOUNT(col)→ COUNT(DISTINCT CASE WHEN <filters> THEN col ELSE NULL END)
     DIVIDE(a, b)      → COALESCE(SUM(CASE WHEN <f> THEN a ELSE 0 END) / NULLIF(SUM(CASE WHEN <f> THEN b ELSE 0 END), 0), 0)
     scalar/string     → ANY_VALUE(CASE WHEN <filters> THEN <expression> ELSE NULL END)
2. Filter predicates are ANDed inside the CASE WHEN condition.

═══════════════════════════════════════════
TRANSLATION RULES
═══════════════════════════════════════════
1. Column references → backtick-quoted: `{table_alias}`.`column_name` (lowercase snake_case)
2. Aggregations → preserve the original DAX function (SUM, AVG, COUNT, MIN, MAX). DAX AVERAGE is SQL AVG.
3. TODAY()  → MAX(current_date())
4. NOW()    → MAX(current_timestamp())
5. DIVIDE(num, den [, alt]) → COALESCE( (num) / NULLIF((den), 0), COALESCE(alt, 0) )
6. CONCATENATE(a, b) → CONCAT(a, b)
7. FORMAT(expr, fmt) → date_format(expr, fmt) (only for date columns)
8. IF(cond, true_val, false_val) → CASE WHEN cond THEN true_val ELSE false_val END
9. BLANK() → NULL
281. Time intelligence (SAMEPERIODLASTYEAR, TOTALYTD, etc.) → not supported directly. Instead, use conditional aggregation with `_current_fiscal_period` where applicable.
282. For measures involving fiscal year periods and `_current_fiscal_period`, use:
     SUM(CASE WHEN `joined_table`.`fiscal_yr_period` < _current_fiscal_period THEN `joined_table`.`column_name` ELSE NULL END)
283. For "Last Refreshed" patterns, use:
     CONCAT('Last Refreshed - ', CAST(MAX(`gl_refresh_datetime`) AS STRING))
284. Measure references [Measure Name] → use the pre‑computed snake_case column name if available, otherwise inline the resolved SQL (but avoid recursion).
285. String literals: DAX "text" → SQL 'text'
13. No SELECT, FROM, WHERE, GROUP BY – output only the expression.

═══════════════════════════════════════════
FEW-SHOT EXAMPLES (Correct for Metric Views)
═══════════════════════════════════════════
DAX: TODAY()
SQL: MAX(current_date())

DAX: CONCATENATE("Last Refreshed: ", MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))
SQL: ANY_VALUE(CONCAT('Last Refreshed: ', DATE_FORMAT(`corporate_dsi_last_refreshed`.`gl_refresh_datetime`, 'MM/dd/yyyy HH:mm:ss')))

DAX: SUM('Corporate DSI Aggregate'[DSI_MNTHLY])
SQL: SUM(`corporate_dsi_aggregate`.`dsi_mnthly`)

DAX: DIVIDE([Corporate COS], [Corporate IOH])
SQL: COALESCE(SUM(`corporate_dsi_aggregate`.`cos_excldng_lifo_amt`) / NULLIF(SUM(`corporate_dsi_aggregate`.`ioh_excldng_lifo_amt`), 0), 0)

DAX: CALCULATE(SUM('Inventory Fact'[Total Stock Qty]), 'Business Units'[Business Unit] = "Subledger")
SQL: SUM(CASE WHEN `business_units`.`business_unit` = 'Subledger' THEN `inventory_fact`.`total_stock_qty` ELSE 0 END)

DAX: CALCULATE(COUNTROWS('Customer'), Customer[City] = "London")
SQL: COUNT(CASE WHEN `customer`.`city` = 'London' THEN 1 ELSE NULL END)

DAX: CALCULATE(DISTINCTCOUNT('Product'[ID]), Product[Category] = "Electronics")
SQL: COUNT(DISTINCT CASE WHEN `product`.`category` = 'Electronics' THEN `product`.`id` ELSE NULL END)

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
        
        # Validate SQL
        is_valid, validation_errors = self._validate_sql(sql, original_dax, table_alias)
        
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
    
    
    def _validate_sql(self, sql: str, original_dax: str, table_alias: str) -> tuple[bool, str]:
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
                "CAST", "TRY_CAST", "ANY_VALUE",
            ]
        )

        if not has_structure:
            return False, "No recognisable SQL construct detected (aggregation, CASE, COALESCE, CONCAT, etc.)"

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
        
        return min(1.0, score)


# Global singleton instance
_llm_translator: Optional[LLMDAXTranslator] = None


def get_llm_translator() -> LLMDAXTranslator:
    """Get or create the global LLM translator instance."""
    global _llm_translator
    if _llm_translator is None:
        _llm_translator = LLMDAXTranslator()
    return _llm_translator
