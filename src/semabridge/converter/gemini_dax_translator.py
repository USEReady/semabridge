#!/usr/bin/env python3
"""
Google Gemini-based DAX to SQL translator
Replaces OpenAI with free Gemini API for cost-effective LLM translation
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

# Load .env at module import time
try:
    from dotenv import load_dotenv
    import os
    
    # Try multiple potential paths for .env
    possible_paths = [
        Path(__file__).resolve().parent.parent.parent / '.env',  # Up 3 levels from converter/
        Path.cwd() / '.env',  # Current working directory
        Path(__file__).resolve().parent.parent.parent.parent / '.env',  # Up 4 levels
    ]
    
    for env_path in possible_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass

import google.generativeai as genai

logger = logging.getLogger(__name__)


@dataclass
class GeminiTranslationResult:
    """Result from Gemini DAX translation."""
    sql: str = ""
    is_valid: bool = False
    confidence: float = 0.0
    model: str = ""
    cached: bool = False
    error: Optional[str] = None
    timestamp: str = ""


class GeminiDAXTranslator:
    """
    Uses Google Gemini to translate complex DAX expressions.
    
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
        """Initialize Gemini translator with API credentials from .env or environment."""
        try:
            # Load .env to ensure variables are available
            try:
                from dotenv import load_dotenv
                import os
                
                possible_paths = [
                    Path(__file__).resolve().parent.parent.parent / '.env',
                    Path.cwd() / '.env',
                    Path(__file__).resolve().parent.parent.parent.parent / '.env',
                ]
                
                for env_path in possible_paths:
                    if env_path.exists():
                        load_dotenv(env_path, override=False)
                        break
            except ImportError:
                pass
            
            self.api_key = os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                logger.warning("GEMINI_API_KEY not set - LLM translation disabled. Check .env file or environment variables.")
                self.client = None
            else:
                genai.configure(api_key=self.api_key)
                # Try models in order of preference (all free tier)
                self.model_preferences = [
                    "models/gemini-2.5-flash",      # Newest, fast free model
                    "models/gemini-2.5-flash-preview-tts",  # Alternative
                    "models/gemini-2.0-flash",      # Proven free model
                    "models/gemini-flash-latest",   # Latest available
                    "models/gemini-2.0-flash-lite", # Lightweight option
                ]
                self.model = self.model_preferences[0]  # Start with first preference
                self.cache_file = ".llm_dax_cache.json"
                self.cache = self._load_cache()
                logger.info(f"✅ Gemini translator initialized with {self.model}")
        except ImportError:
            logger.warning("google.generativeai client not installed - LLM translation disabled")
            self.client = None
            self.model_preferences = []
    
    def translate(self, 
                  dax: str, 
                  table_alias: str, 
                  dataset_name: str,
                  metric_name: str = "",
                  schema_context: Optional[Dict[str, List[str]]] = None) -> GeminiTranslationResult:
        """
        Translate complex DAX to SQL using Google Gemini.
        
        Args:
            dax: DAX expression to translate
            table_alias: SQL alias for main table (e.g., 'salesfact')
            dataset_name: Name of dataset for context
            metric_name: Name of metric being translated
            schema_context: Optional schema information (table columns, relationships)
            
        Returns:
            GeminiTranslationResult with SQL, validation, and confidence score
        """
        result = GeminiTranslationResult(
            model=self.model,
            timestamp=datetime.now().isoformat()
        )
        
        if not self.api_key:
            result.error = "GEMINI_API_KEY not configured"
            logger.warning(result.error)
            return result
        
        # Check cache first
        cache_key = self._cache_key(dax, table_alias)
        if cache_key in self.cache:
            cached_result = self.cache[cache_key]
            result.sql = cached_result.get('sql', '')
            result.confidence = cached_result.get('confidence', 0.0)
            result.is_valid = cached_result.get('is_valid', False)
            result.cached = True
            logger.debug(f"✓ DAX cached: {dax[:50]}...")
            return result
        
        try:
            # Build context-aware prompt
            prompt = self._build_prompt(dax, table_alias, dataset_name, metric_name, schema_context)
            
            # Try models in order of preference
            last_error = None
            for model_name in self.model_preferences:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(prompt, stream=False)
                    
                    if not response or not response.text:
                        last_error = f"Empty response from {model_name}"
                        continue
                    
                    # Success! Update model and break
                    self.model = model_name
                    result.model = model_name
                    
                    # Parse and validate response
                    sql = response.text.strip()
                    result.sql = sql
                    result.is_valid = self._validate_sql(sql, table_alias)
                    result.confidence = self._score_confidence(sql, dax)
                    
                    # Cache valid results
                    if result.is_valid:
                        self._cache_result(cache_key, result.sql, result.confidence, result.is_valid)
                    else:
                        logger.warning(f"Translation invalid with {model_name}")
                    
                    return result
                    
                except Exception as model_error:
                    last_error = str(model_error)
                    if "404" in last_error or "not found" in last_error.lower():
                        logger.debug(f"Model not available: {model_name}")
                        continue
                    elif "quota" in last_error.lower() or "429" in last_error:
                        logger.debug(f"Quota exceeded for {model_name}, trying next...")
                        continue
                    else:
                        logger.warning(f"Error with {model_name}: {last_error[:100]}")
                        continue
            
            # All models exhausted
            result.error = f"All models failed. Last error: {last_error}"
            logger.error(result.error)
            return result
            
        except Exception as e:
            result.error = f"Gemini translation failed: {str(e)}"
            logger.error(result.error)
            return result
    
    def _build_prompt(self, 
                     dax: str, 
                     table_alias: str, 
                     dataset_name: str,
                     metric_name: str,
                     schema_context: Optional[Dict] = None) -> str:
        """Build context-aware prompt for Gemini."""
        prompt = f"""Convert the following DAX expression to Snowflake SQL:

DAX Expression:
{dax}

Context:
- Table alias: {table_alias}
- Dataset: {dataset_name}
- Metric: {metric_name or 'Unknown'}

Requirements:
1. Only output valid Snowflake SQL
2. Use the table alias '{table_alias}' for column references
3. No explanations, comments, or markdown - just SQL
4. Preserve all DAX logic and aggregations
5. Handle NULL values appropriately
6. Quote identifiers with double quotes

Output only the SQL statement:"""
        
        if schema_context:
            prompt += f"\n\nSchema Context:\n{json.dumps(schema_context, indent=2)}"
        
        return prompt
    
    def _validate_sql(self, sql: str, table_alias: str) -> bool:
        """Validate SQL for safety and correctness."""
        import re
        
        if not sql or not isinstance(sql, str):
            return False
        
        # Check for dangerous patterns
        sql_upper = sql.upper()
        for pattern in self.DANGEROUS_SQL_PATTERNS:
            if re.search(pattern, sql_upper):
                logger.warning(f"⚠️  Dangerous SQL pattern detected: {pattern}")
                return False
        
        # CRITICAL FIX: For metric expressions, we need aggregation expressions, NOT SELECT statements
        # Snowflake METRICS clause expects: Table."metric" AS SUM(column), not full SELECT
        if 'SELECT' in sql_upper:
            logger.warning(f"⚠️  SQL contains SELECT statement - invalid for METRICS clause. Use aggregation expression instead.")
            return False
        
        # Must have some aggregation function
        aggregation_functions = ['SUM(', 'AVG(', 'COUNT(', 'MIN(', 'MAX(', 'COUNT(DISTINCT']
        has_aggregation = any(agg in sql_upper for agg in aggregation_functions)
        if not has_aggregation:
            logger.warning(f"⚠️  SQL missing aggregation function (SUM, AVG, COUNT, MIN, MAX)")
            return False
        
        return True
    
    def _score_confidence(self, sql: str, original_dax: str) -> float:
        """Score confidence in the translation quality."""
        confidence = 0.5  # Start with base confidence
        
        # Increase confidence for known good patterns
        if 'SUM(' in sql.upper():
            confidence += 0.1
        if 'AVG(' in sql.upper() or 'AVERAGE(' in sql.upper():
            confidence += 0.1
        if 'COUNT(' in sql.upper():
            confidence += 0.05
        
        # Penalize SELECT statements (invalid for METRICS clause)
        if 'SELECT' in sql.upper() or 'FROM' in sql.upper() or 'WHERE' in sql.upper():
            confidence -= 0.3  # Heavily penalize full SELECT statements
            logger.warning(f"⚠️  Translation contains SELECT/FROM/WHERE - will likely fail in METRICS clause")
        
        # Heuristic: longer DAX may be more complex = lower confidence
        if len(original_dax) > 200:
            confidence -= 0.1
        
        # Cap at [0.0, 1.0]
        return max(0.0, min(confidence, 1.0))
    
    def _cache_key(self, dax: str, table_alias: str) -> str:
        """Generate cache key for DAX expression."""
        return f"{table_alias}::{dax[:100]}"
    
    def _load_cache(self) -> Dict:
        """Load cache from file."""
        cache_file = Path(self.cache_file)
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load cache: {e}")
        return {}
    
    def _cache_result(self, key: str, sql: str, confidence: float, is_valid: bool) -> None:
        """Cache translation result."""
        try:
            self.cache[key] = {
                'sql': sql,
                'confidence': confidence,
                'is_valid': is_valid,
                'timestamp': datetime.now().isoformat()
            }
            
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not cache result: {e}")


# Global instance
_gemini_translator = None

def get_gemini_translator() -> GeminiDAXTranslator:
    """Get or create the global Gemini translator instance."""
    global _gemini_translator
    if _gemini_translator is None:
        _gemini_translator = GeminiDAXTranslator()
    return _gemini_translator
