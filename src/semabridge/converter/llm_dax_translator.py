"""
OpenAI-Based DAX to SQL Translator.

Provides fallback translation for complex DAX expressions using OpenAI GPT-4 Turbo
when deterministic parsing fails.

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
    Uses OpenAI GPT-4 Turbo to translate complex DAX expressions.
    
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
        """Initialize LLM translator with API credentials from .env or environment."""
        try:
            # Load .env again to ensure it's available (in case __init__ is called independently)
            try:
                from dotenv import load_dotenv
                env_path = Path(__file__).resolve().parent.parent.parent / '.env'
                if env_path.exists():
                    load_dotenv(env_path, override=False)
            except ImportError:
                pass
            
            self.api_key = os.getenv("OPENAI_API_KEY")
            if not self.api_key:
                logger.warning("OPENAI_API_KEY not set - LLM translation disabled. Check .env file or environment variables.")
                self.client = None
            else:
                import openai
                self.client = openai.OpenAI(api_key=self.api_key)
                # Use GPT-4o: Latest, most capable, excellent for complex code translation
                # Alternatives: "gpt-4o-mini" (faster, cheaper), "gpt-3.5-turbo" (budget)
                self.model = "gpt-4o"
                self.cache_file = ".llm_dax_cache.json"
                self.cache = self._load_cache()
        except ImportError:
            logger.warning("openai client not installed - LLM translation disabled")
            self.client = None
    
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
                error="OPENAI_API_KEY not configured"
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
            
            # Call GPT-4 Turbo
            logger.debug(f"Calling OpenAI GPT-4-Turbo for DAX translation: {metric_name or 'unnamed'}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                temperature=0.2,
                max_tokens=800,
                timeout=10
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
        """Build the prompt for GPT-4 Turbo."""
        
        schema_ref = ""
        if schema_context:
            schema_lines = []
            for table, cols in schema_context.items():
                cols_str = ", ".join(cols[:5])  # Limit to 5 columns per table
                if len(cols) > 5:
                    cols_str += f", ... ({len(cols) - 5} more)"
                schema_lines.append(f"  {table}: [{cols_str}]")
            schema_ref = "\n".join(schema_lines)
        
        prompt = f"""Convert this DAX measure expression to Snowflake SQL. Return ONLY the SQL expression.

RULES:
1. Use Snowflake syntax only (not T-SQL or other dialects)
2. Format column references as: {table_alias}."ColumnName" (with double quotes)
3. Use Snowflake aggregations: SUM, AVG, COUNT, MIN, MAX (not AVERAGE)
4. For safe division use: CASE WHEN denominator = 0 THEN 0 ELSE numerator/denominator END
5. For dates use: YEAR(), MONTH(), QUARTER(), DATE_TRUNC()
6. For time intelligence use: window functions with PARTITION BY and ORDER BY
7. For conditionals use: CASE WHEN ... THEN ... ELSE ... END
8. NO semicolons, NO comments, NO markdown, NO code blocks
9. Make expression suitable for running aggregates

CONTEXT:
- Metric: {metric_name or 'unnamed'}
- Dataset: {dataset_name}
- Table: {table_alias}
{f'- Schema:{chr(10)}{schema_ref}' if schema_ref else ''}

DAX:
{dax}

Snowflake SQL:"""
        
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
        
        # Remove markdown code blocks if present
        if sql.startswith("```"):
            # Extract content between backticks
            match = re.search(r"```(?:sql)?\s*(.*?)\s*```", sql, re.DOTALL)
            if match:
                sql = match.group(1).strip()
        
        # Remove trailing semicolon
        sql = sql.rstrip(";").strip()
        
        # Validate SQL
        is_valid, validation_errors = self._validate_sql(sql, original_dax, table_alias)
        
        if not is_valid:
            logger.warning(f"GPT-4 generated SQL failed validation for '{metric_name}': {validation_errors}")
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
            reasoning="GPT-4 translation successful",
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
        
        # Check for basic SQL structure
        sql_upper = sql.upper()
        
        # Should have at least one of: aggregation, CASE, expression
        has_structure = any([
            keyword in sql_upper 
            for keyword in ["SUM", "AVG", "COUNT", "MIN", "MAX", "CASE", "WHEN"]
        ])
        
        if not has_structure:
            return False, "No aggregation or conditional logic detected"
        
        # Check for table alias usage (if we can infer it should be there)
        # Only if original DAX references column names
        if "[" in original_dax and "]" in original_dax:
            if not any(alias in sql for alias in [table_alias, f'"{table_alias}']):
                # Column references without table alias might be OK in some cases
                # but worth noting
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
