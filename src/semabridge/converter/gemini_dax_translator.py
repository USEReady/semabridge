#!/usr/bin/env python3
"""
Google Gemini-based DAX to SQL translator
Replaces OpenAI with free Gemini API for cost-effective LLM translation

Uses centralized Gemini API service (gemini_api_service.py) for:
- Rate limiting (5 RPM)
- Exponential backoff on rate limits
- Hard fallback support
- Centralized logging
"""
import os
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# Load .env at module import time
try:
    from dotenv import load_dotenv
    
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

# Import centralized Gemini API service
from semabridge.converter.gemini_api_service import call_gemini, get_gemini_service

logger = logging.getLogger(__name__)


# Use centralized Gemini API service for all calls
gemini_service = get_gemini_service()



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


@dataclass
class GeminiBatchTranslationResult:
    """Result from batch Gemini DAX translation."""
    results: Dict[str, GeminiTranslationResult] = field(default_factory=dict)
    model: str = ""
    api_calls: int = 0  # Number of API calls made
    retry_attempts: int = 0  # Number of retries
    batch_size: int = 0  # Number of metrics in batch
    cached_count: int = 0  # Number of cached results
    successful_count: int = 0  # Number of successful translations
    failed_count: int = 0  # Number of failed translations
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
        """Initialize Gemini translator with centralized API service."""
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.use_gemini = os.getenv("USE_GEMINI", "true").lower() in ("true", "1", "yes")
        
        # Use centralized service
        self.gemini_service = get_gemini_service()
        self.model = self.gemini_service.MODEL  # Always: models/gemini-2.5-flash
        
        # Cache for translations
        self.cache_file = ".llm_dax_cache.json"
        self.cache = self._load_cache()
        
        if self.use_gemini and self.api_key and self.gemini_service.is_available:
            logger.info(f"✅ Gemini DAX translator initialized with {self.model}")
        else:
            logger.warning("⚠️  Gemini DAX translator disabled or unconfigured")
    
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
        
        if not self.use_gemini or not self.api_key:
            result.error = "Gemini translation disabled or API key not configured"
            logger.debug(result.error)
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
            
            # Define fallback function - returns None to force deterministic pipeline
            def fallback_response(prompt_unused):
                logger.warning(f"Gemini API unavailable for: {dax[:50]}... - returning None to use deterministic pipeline")
                return None
            
            # Call Gemini API using centralized service (with rate limiting, retries, etc.)
            try:
                sql_response = call_gemini(prompt, fallback_fn=fallback_response)
            except Exception as e:
                logger.error(f"Failed to call Gemini API: {e}")
                # Return None to force deterministic pipeline
                sql_response = None
            
            # Parse and validate response
            if not isinstance(sql_response, str) or not sql_response.strip():
                result.error = "Gemini returned empty response; falling back to deterministic pipeline"
                logger.warning(result.error)
                return result
            sql = sql_response.strip()
            sql = self._strip_markdown_code_blocks(sql)
            result.sql = sql
            result.is_valid = self._validate_sql(sql, table_alias)
            result.confidence = self._score_confidence(sql, dax)
            
            # Cache valid results
            if result.is_valid:
                self._cache_result(cache_key, result.sql, result.confidence, result.is_valid)
                logger.debug(f"✓ Valid translation cached: {sql[:60]}...")
            else:
                logger.warning(f"⚠️  Translation validation failed: {sql[:60]}...")
            
            return result
            
        except Exception as e:
            result.error = f"Translation failed: {str(e)}"
            logger.error(result.error)
            return result
    
    def translate_batch(self,
                       metrics: List[Tuple[str, str, str, str, Optional[Dict]]] = None,
                       batch_size: int = 20) -> GeminiBatchTranslationResult:
        """
        Batch translate multiple DAX expressions with minimal API calls.
        
        This reduces API calls by ~90% by batching 20+ metrics into a single request.
        
        Args:
            metrics: List of (metric_name, dax, table_alias, dataset_name, schema_context) tuples
            batch_size: Number of metrics to include per API request (default: 20)
            
        Returns:
            GeminiBatchTranslationResult with all translations and stats
        """
        batch_result = GeminiBatchTranslationResult(
            batch_size=len(metrics) if metrics else 0,
            timestamp=datetime.now().isoformat()
        )
        
        if not metrics:
            logger.debug("No metrics provided for batch translation")
            return batch_result
        
        if not self.api_key:
            batch_result.error = "GEMINI_API_KEY not configured"
            logger.warning(batch_result.error)
            return batch_result
        
        logger.info(f"🔄 Starting batch translation for {len(metrics)} metrics (batch size: {batch_size})")
        
        # Check cache for all metrics first
        uncached_metrics = []
        for metric_name, dax, table_alias, dataset_name, schema_context in metrics:
            cache_key = self._cache_key(dax, table_alias)
            if cache_key in self.cache:
                # Use cached result
                cached = self.cache[cache_key]
                cached_result = GeminiTranslationResult(
                    sql=cached.get('sql', ''),
                    is_valid=cached.get('is_valid', False),
                    confidence=cached.get('confidence', 0.0),
                    cached=True,
                    timestamp=cached.get('timestamp', '')
                )
                batch_result.results[metric_name] = cached_result
                batch_result.cached_count += 1
                logger.debug(f"✓ [{metric_name}] Cached translation: {cached_result.sql[:50]}...")
            else:
                uncached_metrics.append((metric_name, dax, table_alias, dataset_name, schema_context))
        
        logger.info(f"   ├─ Cached: {batch_result.cached_count}")
        logger.info(f"   ├─ To Translate: {len(uncached_metrics)}")
        
        # If all metrics were cached, return early
        if not uncached_metrics:
            batch_result.successful_count = batch_result.cached_count
            logger.info(f"✅ All metrics from cache (0 API calls)")
            return batch_result
        
        # Split into batches and translate
        for batch_idx in range(0, len(uncached_metrics), batch_size):
            batch = uncached_metrics[batch_idx:batch_idx + batch_size]
            batch_num = (batch_idx // batch_size) + 1
            total_batches = (len(uncached_metrics) + batch_size - 1) // batch_size
            
            logger.info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch)} metrics)")
            
            # Translate this batch with retry logic
            batch_translations = self._translate_batch_with_retry(batch)
            
            # Merge results into overall batch result
            for metric_name, translation_result in batch_translations.items():
                batch_result.results[metric_name] = translation_result
                if translation_result.is_valid and translation_result.sql:
                    batch_result.successful_count += 1
                else:
                    batch_result.failed_count += 1
            
            batch_result.api_calls += 1
        
        # Log final statistics
        logger.info(
            f"✅ Batch translation complete:\n"
            f"   ├─ Total metrics: {len(metrics)}\n"
            f"   ├─ API calls: {batch_result.api_calls} (vs {len(metrics)} without batching = {int((1 - batch_result.api_calls/len(metrics)) * 100)}% reduction)\n"
            f"   ├─ Cached: {batch_result.cached_count}\n"
            f"   ├─ Successful: {batch_result.successful_count}\n"
            f"   └─ Failed: {batch_result.failed_count}"
        )
        
        return batch_result
    
    def _translate_batch_with_retry(self, 
                                   batch: List[Tuple[str, str, str, str, Optional[Dict]]]) -> Dict[str, GeminiTranslationResult]:
        """
        Translate a batch of metrics using centralized Gemini API service.
        
        The service handles rate limiting, retries, and exponential backoff automatically.
        
        Args:
            batch: List of (metric_name, dax, table_alias, dataset_name, schema_context) tuples
            
        Returns:
            Dict of metric_name -> GeminiTranslationResult
        """
        results = {}
        
        try:
            # Build batch prompt
            prompt = self._build_batch_prompt(batch)
            
            # Define fallback function for batch - returns empty dict (no heuristic fallback)
            def batch_fallback(prompt_unused):
                logger.warning("Gemini API unavailable for batch - returning None for all metrics to use deterministic pipeline")
                # Return None to force deterministic translator for each metric
                return None
            
            # Call Gemini API via centralized service (handles retries, rate limiting)
            response_text = call_gemini(prompt, fallback_fn=batch_fallback)
            
            # Parse response
            results = self._parse_batch_response(response_text, batch)
            return results
            
        except Exception as e:
            logger.error(f"Batch translation failed: {e}")
            # Return error results for all metrics
            for metric_name, _, _, _, _ in batch:
                results[metric_name] = GeminiTranslationResult(
                    sql="",
                    is_valid=False,
                    error=f"Batch translation error: {str(e)}",
                    timestamp=datetime.now().isoformat()
                )
            return results

    
    def _build_batch_prompt(self, batch: List[Tuple[str, str, str, str, Optional[Dict]]]) -> str:
        """
        Build a prompt containing multiple DAX expressions to translate.
        
        Returns JSON-formatted results for easy parsing.
        """
        # Build list of metrics for the prompt
        metrics_text = ""
        for i, (metric_name, dax, table_alias, dataset_name, schema_context) in enumerate(batch, 1):
            metrics_text += f'{i}. "{metric_name}": {dax}\n'
        
        prompt = f"""You are a batch DAX to Snowflake SQL translator for metric expressions.

Your task: Convert multiple DAX expressions to Snowflake aggregation expressions.

CRITICAL REQUIREMENTS FOR ALL EXPRESSIONS - YOU MUST FOLLOW ALL:
1. Output ONLY raw SQL aggregation expressions (e.g., SUM(alias.AMOUNT), COUNT(DISTINCT alias.ID))
2. NO SELECT, FROM, WHERE, JOIN, or any clauses - only aggregation functions
3. NO markdown code blocks, backticks, or triple backticks
4. NO explanations, comments, or extra text
5. Use table aliases like {table_alias} for column references  
6. Use UPPERCASE column names without quotes: alias.COLUMN_NAME (not alias."Column_Name")
7. Return ONLY valid JSON with no other content

OUTPUT RULES (STRICT):
✗ WRONG: "metric1": "SELECT SUM(amount) FROM sales"
✗ WRONG: "metric1": "```sql SUM(sales.amount) ```"
✓ RIGHT: "metric1": "SUM(sales.AMOUNT)"
✓ RIGHT: "metric2": "COUNT(DISTINCT sales.PRODUCT_ID)"

JSON Structure:
{{
    "metric_name_1": "SUM(alias.AMOUNT)",
    "metric_name_2": "COUNT(DISTINCT alias.ID)",
    ...
}}

DAX Metrics to Convert:
{metrics_text}

Return ONLY the JSON object with no other content:"""
        
        return prompt
    
    def _parse_batch_response(self, 
                             response_text: str,
                             batch: List[Tuple[str, str, str, str, Optional[Dict]]]) -> Dict[str, GeminiTranslationResult]:
        """
        Parse JSON response from batch translation.
        
        Args:
            response_text: Raw response from Gemini
            batch: Original batch for validation context
            
        Returns:
            Dict of metric_name -> GeminiTranslationResult
        """
        results = {}
        
        try:
            # Strip markdown if present
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("Empty batch response from Gemini")
            response_text = self._strip_markdown_code_blocks(response_text)
            
            # Try to parse JSON
            try:
                json_data = json.loads(response_text)
            except json.JSONDecodeError:
                # Try to extract JSON from response if it's embedded
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_data = json.loads(json_match.group())
                else:
                    raise ValueError("Could not parse JSON from response")
            
            # Map batch metrics by name for validation context
            batch_dict = {name: (name, dax, alias, dataset, schema) for name, dax, alias, dataset, schema in batch}
            
            # Process each metric in the response
            for metric_name, sql_expr in json_data.items():
                if metric_name not in batch_dict:
                    logger.warning(f"   ⚠️  Response included unexpected metric: {metric_name}")
                    continue
                
                _, dax, table_alias, dataset_name, _ = batch_dict[metric_name]
                
                # Clean SQL expression
                sql_expr = sql_expr.strip() if sql_expr else ""
                sql_expr = self._strip_markdown_code_blocks(sql_expr)
                
                # Validate SQL
                is_valid = self._validate_sql(sql_expr, table_alias)
                confidence = self._score_confidence(sql_expr, dax)
                
                # Create result
                result = GeminiTranslationResult(
                    sql=sql_expr,
                    is_valid=is_valid,
                    confidence=confidence,
                    model=self.model,
                    timestamp=datetime.now().isoformat()
                )
                
                # Store in results
                results[metric_name] = result
                
                # Cache if valid
                if is_valid:
                    cache_key = self._cache_key(dax, table_alias)
                    self._cache_result(cache_key, sql_expr, confidence, is_valid)
                    logger.debug(f"   ✓ [{metric_name}] Valid: {sql_expr[:60]}...")
                else:
                    logger.debug(f"   ⚠️  [{metric_name}] Invalid: {sql_expr[:60]}...")
            
            # Mark any metrics from batch that weren't in response as failed
            for metric_name in batch_dict:
                if metric_name not in results:
                    logger.warning(f"   ❌ [{metric_name}] Not in response")
                    results[metric_name] = GeminiTranslationResult(
                        sql="",
                        is_valid=False,
                        error="Metric not in batch response",
                        timestamp=datetime.now().isoformat()
                    )
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to parse batch response: {str(e)}")
            # Return error results for all metrics in batch
            for metric_name, _, _, _, _ in batch:
                results[metric_name] = GeminiTranslationResult(
                    sql="",
                    is_valid=False,
                    error=f"Parse error: {str(e)}",
                    timestamp=datetime.now().isoformat()
                )
            return results
    
    def _build_prompt(self, 
                     dax: str, 
                     table_alias: str, 
                     dataset_name: str,
                     metric_name: str,
                     schema_context: Optional[Dict] = None) -> str:
        """Build strict context-aware prompt for Gemini with explicit format requirements."""
        prompt = f"""You are a DAX to Snowflake SQL translator for metric expressions ONLY.

Your task: Convert the DAX expression below to a Snowflake aggregation expression.

CRITICAL REQUIREMENTS - YOU MUST FOLLOW ALL:
1. Output ONLY a raw SQL aggregation expression (e.g., SUM(alias.col), COUNT(DISTINCT alias.col))
2. NO SELECT, FROM, WHERE, JOIN, or any clauses - only the aggregation part
3. NO markdown code blocks, backticks, or triple backticks
4. NO explanations, comments, or text - ONLY the SQL expression
5. Use the table alias '{table_alias}' for all column references
6. Use UPPERCASE column names without quotes: alias.COLUMN_NAME (not alias."Column_Name")
7. If you don't know the exact column, use a placeholder like alias.AMOUNT

DAX Expression to Convert:
{dax}

Context:
- Table alias: {table_alias}
- Dataset: {dataset_name}  
- Metric name: {metric_name or 'Unknown'}

OUTPUT RULES (STRICT):
✗ WRONG: SELECT SUM(amount) FROM sales
✗ WRONG: ```sql SUM(sales.amount) ```
✗ WRONG: SUM(sales."Amount") -- quoted identifiers
✓ RIGHT: SUM(sales.AMOUNT)
✓ RIGHT: COUNT(DISTINCT sales.PRODUCT_ID)

Return ONLY the SQL aggregation expression, nothing else:"""
        
        if schema_context:
            prompt += f"\n\nSchema Context:\n{json.dumps(schema_context, indent=2)}"
        
        return prompt
    
    def _validate_sql(self, sql: str, table_alias: str) -> bool:
        """Enhanced SQL validation for aggregation expressions.
        
        Stricter checks to ensure output is a valid aggregation expression,
        not a full SELECT statement or other invalid SQL.
        """
        import re
        
        if not sql or not isinstance(sql, str):
            logger.warning("⚠️  SQL is empty or not a string")
            return False
        
        sql_clean = sql.strip()
        sql_upper = sql_clean.upper()
        
        # CRITICAL CHECK 1: No full SELECT statements
        if sql_upper.startswith('SELECT'):
            logger.warning(f"⚠️  SQL starts with SELECT - invalid for METRICS clause")
            return False
        
        if ' FROM ' in sql_upper or ' WHERE ' in sql_upper or ' JOIN ' in sql_upper:
            logger.warning(f"⚠️  SQL contains FROM/WHERE/JOIN - must be aggregation expression only")
            return False
        
        # Check for dangerous patterns
        for pattern in self.DANGEROUS_SQL_PATTERNS:
            if re.search(pattern, sql_upper):
                logger.warning(f"⚠️  Dangerous SQL pattern detected: {pattern}")
                return False
        
        # CRITICAL CHECK 2: Must have an aggregation function
        aggregation_functions = ['SUM(', 'AVG(', 'COUNT(', 'MIN(', 'MAX(']
        has_aggregation = any(agg.upper() in sql_upper for agg in aggregation_functions)
        if not has_aggregation:
            logger.warning(f"⚠️  SQL missing aggregation function (SUM, AVG, COUNT, MIN, MAX)")
            return False
        
        # ENHANCEMENT: Check for improperly quoted identifiers
        # Snowflake METRICS expressions should use unquoted uppercase identifiers
        if re.search(r"\b(alias|'|\")\.\"[a-z]", sql, re.IGNORECASE):
            # This is a heuristic - if we see table alias followed by quoted lowercase, that's wrong
            logger.warning(f"⚠️  SQL uses quoted mixed-case identifiers - should use uppercase unquoted")
            # Don't fail yet, but log warning
        
        # ENHANCEMENT: Basic syntax check - balanced parentheses
        if sql_clean.count('(') != sql_clean.count(')'):
            logger.warning(f"⚠️  SQL has unbalanced parentheses")
            return False
        
        # ENHANCEMENT: Check for suspicious patterns like multiple SELECT/FROM in one expression
        if sql_upper.count('SELECT') > 1 or sql_upper.count('FROM') > 1:
            logger.warning(f"⚠️  SQL contains multiple SELECT/FROM keywords")
            return False
        
        return True
    
    def _strip_markdown_code_blocks(self, sql: str) -> str:
        """Remove markdown code blocks (```sql ... ``` or ``` ... ```) from LLM response.
        
        The LLM sometimes wraps SQL in markdown code fences despite instructions.
        This extracts clean SQL from the response.
        """
        import re
        
        # Remove opening ```sql or ``` with optional language specifier
        sql = re.sub(r'^```(?:sql|python|\\w*)?\\n?', '', sql, flags=re.MULTILINE)
        # Remove closing ```
        sql = re.sub(r'\\n?```$', '', sql, flags=re.MULTILINE)
        sql = sql.strip()
        
        return sql
    
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
