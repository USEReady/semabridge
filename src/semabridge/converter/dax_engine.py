#!/usr/bin/env python3
"""
Advanced DAX Translation Engine.

Production-grade deterministic DAX → Snowflake SQL translator with:
- Intelligent AST-based parsing
- Comprehensive CALCULATE/FILTER handling
- Time intelligence support
- Caching layer for repeated translations
- Capability detection (can_translate)
- Full observability

This engine aims for 80-90% deterministic coverage, using LLM only as fallback.
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer, DaxNode, FunctionCallNode
from semabridge.converter.complex_calculate_translator import ComplexCalculateTranslator
from semabridge.converter.row_context_translator import RowContextTranslator
from semabridge.converter.iterator_translator import IteratorTranslator
from semabridge.converter.llm_translator import LlmTranslator
from semabridge.utils.logger import get_logger
from semabridge.utils.naming import sanitize_column
from semabridge.converter.dax_rule_translator import rule_based_translation

logger = get_logger(__name__)


def balance_parentheses(sql: str) -> str:
    """Ensure parentheses are balanced by adding missing ones at the end."""
    if not sql or not isinstance(sql, str):
        return sql
    
    # Only balance if there are more opens than closes
    # This repairs (query(query) type truncation from LLMs
    open_count = sql.count('(')
    close_count = sql.count(')')
    if open_count > close_count:
        sql = sql + (')' * (open_count - close_count))
    return sql


def sanitize_llm_sql(sql: str) -> str:
    """Remove DAX leakage and unwrap simple SELECT projections from LLM SQL.
    
    Repairs common LLM failures like missing trailing parentheses or 
    wrapping the entire expression in an unnecessary SELECT subquery.
    """
    if not sql or not isinstance(sql, str):
        return ""

    cleaned = re.sub(
        r"^\s*```(?:sql|python|javascript|js|\w*)?\s*\n?",
        "",
        sql.strip(),
        flags=re.MULTILINE | re.IGNORECASE,
    )
    cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"(?is)^\s*sql\s*:\s*", "", cleaned).strip()
    cleaned = cleaned.strip().rstrip(";").strip()

    # If the model accidentally returned DAX with RETURN, keep the expression after RETURN.
    # We take everything after the LAST RETURN to be safe.
    # Do NOT delete VAR lines here; deleting full lines can drop parentheses.
    m_return = re.search(r"(?is).*\bRETURN\b\s*(.*)$", cleaned)
    if m_return:
        cleaned = m_return.group(1).strip()

    # Unwrap simple SELECT projections (including parenthesized subquery forms)
    # into scalar expressions usable in metric contexts.
    selectish = cleaned
    
    # Repair missing closing parentheses before checking for SELECT wrap
    selectish = balance_parentheses(selectish)
    
    if re.match(r"(?is)^\(\s*SELECT\b", selectish) and selectish.strip().endswith(")"):
        # Safely unwrap (SELECT ...) only if it's the entire string
        unwrapped = selectish.strip()[1:-1].strip()
        # Ensure we didn't break balance by unwrapping
        if unwrapped.count('(') == unwrapped.count(')'):
            selectish = unwrapped

    if re.match(r"(?is)^SELECT\b", selectish):
        # Extract projection portion from SELECT ... FROM ...
        match = re.search(r"(?is)SELECT\s+(.*?)\s+FROM\b", selectish)
        if match:
            cleaned = match.group(1).strip()
        else:
            # SELECT without FROM: take the projection portion
            cleaned = re.sub(r"(?is)^SELECT\s+", "", selectish).strip()
    else:
        cleaned = selectish

    # Final balance check after all transformations
    cleaned = balance_parentheses(cleaned)
    
    return " ".join(cleaned.split())


class TranslationStrategy(Enum):
    """Translation strategy used."""
    OVERRIDE = "override"          # Manual override
    DIRECT_AGG = "direct_agg"      # Direct aggregation (Tier 1)
    ARITHMETIC = "arithmetic"      # Arithmetic/branching (Tier 2)
    TIME_INTEL = "time_intel"      # Time intelligence (Tier 3)
    CALCULATE = "calculate"        # CALCULATE/FILTER (Tier 4)
    RULE_BASED = "rule_based"      # Rule-based deterministic
    AST_BASED = "ast_based"        # AST parser + renderer
    LLM_FALLBACK = "llm_fallback"  # LLM (last resort)
    FAILED = "failed"              # Could not translate


class CapabilityLevel(Enum):
    """How confident we are that this DAX can be translated."""
    FULLY_SUPPORTED = "fully_supported"      # 100% deterministic
    WELL_SUPPORTED = "well_supported"        # 95%+ coverage
    PARTIALLY_SUPPORTED = "partially_supported"  # 60-80% coverage
    EXPERIMENTAL = "experimental"            # 40-60% coverage
    UNSUPPORTED = "unsupported"              # Cannot translate


@dataclass
class TranslationMetrics:
    """Metrics for a translation attempt."""
    dax: str
    strategy: TranslationStrategy
    capability: CapabilityLevel
    sql: Optional[str]
    confidence: float  # 0.0-1.0
    execution_time_ms: float
    cached: bool
    error: Optional[str] = None
    timestamp: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class DaxTranslationCache:
    """Persistent and in-memory caching for DAX translations."""
    
    def __init__(self, cache_file: str = ".dax_translation_cache.jsonl"):
        self.cache_file = Path(cache_file)
        self.memory_cache: Dict[str, Dict[str, Any]] = {}
        self._load_disk_cache()
    
    def _make_key(self, dax: str, table_alias: str) -> str:
        """Generate cache key from DAX and table alias."""
        combined = f"{dax}|{table_alias}"
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def get(self, dax: str, table_alias: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached translation."""
        key = self._make_key(dax, table_alias)
        return self.memory_cache.get(key)
    
    def put(self, dax: str, table_alias: str, sql: str, strategy: TranslationStrategy):
        """Cache a translation."""
        key = self._make_key(dax, table_alias)
        value = {
            "sql": sql,
            "strategy": strategy.value,
            "timestamp": datetime.now().isoformat(),
        }
        self.memory_cache[key] = value
        self._append_to_disk(key, value)
    
    def _append_to_disk(self, key: str, value: Dict):
        """Append to disk cache."""
        try:
            with open(self.cache_file, "a") as f:
                f.write(json.dumps({"key": key, **value}) + "\n")
        except Exception as e:
            logger.warning(f"Failed to save translation cache: {e}")
    
    def _load_disk_cache(self):
        """Load cached translations from disk."""
        if not self.cache_file.exists():
            return
        
        try:
            with open(self.cache_file, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        key = data.pop("key")
                        self.memory_cache[key] = data
                    except json.JSONDecodeError:
                        pass
            logger.debug(f"Loaded {len(self.memory_cache)} translations from cache")
        except Exception as e:
            logger.warning(f"Failed to load translation cache: {e}")
    
    def clear(self):
        """Clear all caches."""
        self.memory_cache.clear()
        if self.cache_file.exists():
            self.cache_file.unlink()


class CapabilityDetector:
    """Detects if a DAX expression can be translated deterministically."""
    
    # Fully supported patterns
    FULLY_SUPPORTED_PATTERNS = [
        # Direct aggregations
        r"^\s*(SUM|AVERAGE|COUNT|MIN|MAX|DISTINCTCOUNT)\s*\(",
        # DIVIDE
        r"DIVIDE\s*\(",
        # Simple CALCULATE with FILTER
        r"CALCULATE\s*\([^)]+,\s*FILTER\s*\(",
    ]
    
    # Partially supported (requires AST but likely works)
    PARTIALLY_SUPPORTED_PATTERNS = [
        r"TOTALYTD|TOTALMTD|TOTALQTD",
        r"SAMEPERIODLASTYEAR|PREVIOUSYEAR|PREVIOUSMONTH|PREVIOUSQUARTER",
        r"IF\s*\(|SWITCH\s*\(|IFERROR\s*\(",
        r"ALL\s*\(|ALLEXCEPT\s*\(",
        r"EARLIER\s*\(",           # Phase 2 support
        r"RANKX\s*\(",             # Phase 2 support
        r"SUMX\s*\(|AVERAGEX\s*\(", # Phase 2 support
    ]
    
    # Unsupported patterns (require DAX engine)
    UNSUPPORTED_PATTERNS = [
        r"GENERATE\s*\(",          # Table generation - complex
        r"SUMMARIZECOLUMNS\s*\(",  # Multi-table aggregation
        r"USERELATIONSHIP\s*\(",   # Dynamic relationships
        r"CROSSFILTER\s*\(",       # Cross filtering
    ]
    
    @staticmethod
    def can_translate(dax: str) -> Tuple[bool, CapabilityLevel]:
        """
        Determine if DAX can be translated deterministically.
        
        Returns:
            (can_translate: bool, capability_level: CapabilityLevel)
        """
        if not dax or not isinstance(dax, str):
            return False, CapabilityLevel.UNSUPPORTED
        
        dax_upper = dax.upper()
        
        # Check unsupported patterns first
        for pattern in CapabilityDetector.UNSUPPORTED_PATTERNS:
            import re
            if re.search(pattern, dax_upper, re.IGNORECASE):
                logger.debug(f"Unsupported pattern detected: {pattern}")
                return False, CapabilityLevel.UNSUPPORTED
        
        # Check fully supported
        for pattern in CapabilityDetector.FULLY_SUPPORTED_PATTERNS:
            import re
            if re.search(pattern, dax_upper, re.IGNORECASE):
                logger.debug(f"Fully supported pattern: {pattern}")
                return True, CapabilityLevel.FULLY_SUPPORTED
        
        # Check partially supported
        for pattern in CapabilityDetector.PARTIALLY_SUPPORTED_PATTERNS:
            import re
            if re.search(pattern, dax_upper, re.IGNORECASE):
                logger.debug(f"Partially supported pattern: {pattern}")
                return True, CapabilityLevel.PARTIALLY_SUPPORTED
        
        # If it's just a simple aggregation or column reference, it's supported
        if re.match(r"^\s*[\[\']", dax):
            return True, CapabilityLevel.FULLY_SUPPORTED
        
        return True, CapabilityLevel.WELL_SUPPORTED  # Assume AST can handle it


class DaxTranslationEngine:
    """
    Production-grade DAX → Snowflake SQL translation engine.
    
    Architecture:
    1. Check cache
    2. Detect capability
    3. Try deterministic translation (AST parser + renderer)
    4. Fall back to LLM if needed
    5. Cache result
    6. Log metrics
    """
    
    def __init__(self, cache_enabled: bool = True):
        self.cache = DaxTranslationCache() if cache_enabled else None
        self.detector = CapabilityDetector()
        self.parser = DaxAstParser()
        self.metrics_log: List[TranslationMetrics] = []
        
        # Phase 2 Specialized Translators
        self.calculate_translator = ComplexCalculateTranslator()
        self.row_context_translator = RowContextTranslator()
        self.iterator_translator = IteratorTranslator()
        self.llm_translator = LlmTranslator()
    
    def translate(
        self,
        dax: str,
        table_alias: str = "fact",
        dataset_name: str = "",
        date_alias: str = "calendar",
        measure_map: Optional[Dict[str, str]] = None,
        metric_name: str = "",
        target_dialect: str = "snowflake"
    ) -> Tuple[Optional[str], TranslationMetrics]:
        """
        Translate a DAX expression to SQL.
        
        Args:
            dax: DAX expression to translate
            table_alias: SQL table alias (e.g., "sales")
            dataset_name: Name of the dataset
            date_alias: Calendar table alias
            measure_map: Pre-calculated measures mapping
            metric_name: Current metric name for caching
            target_dialect: snowflake or databricks
        """
        # Set LLM dialect
        self.llm_translator.dialect = target_dialect
        self.llm_translator.prompt_template = self.llm_translator.SYSTEM_PROMPT.format(dialect=target_dialect)
        import time
        start_time = time.time()
        
        if not dax or not dax.strip():
            metrics = TranslationMetrics(
                dax=dax or "",
                strategy=TranslationStrategy.FAILED,
                capability=CapabilityLevel.UNSUPPORTED,
                sql=None,
                confidence=0.0,
                execution_time_ms=0,
                cached=False,
                error="Empty DAX expression",
            )
            return None, metrics
        
        clean_dax = dax.strip()
        
        # Step 1: Check cache
        if self.cache:
            cached = self.cache.get(clean_dax, table_alias)
            if cached:
                elapsed = (time.time() - start_time) * 1000
                metrics = TranslationMetrics(
                    dax=clean_dax,
                    strategy=TranslationStrategy(cached["strategy"]),
                    capability=CapabilityLevel.FULLY_SUPPORTED,
                    sql=cached["sql"],
                    confidence=1.0,
                    execution_time_ms=elapsed,
                    cached=True,
                )
                logger.debug(f"✓ Cache hit for: {metric_name or clean_dax[:50]}")
                return cached["sql"], metrics
        
        # Step 2: Try rule-based translation (fast path)
        sql = rule_based_translation(
            clean_dax, 
            table_alias, 
            metric_name=metric_name,
            dialect=target_dialect
        )
        
        if sql:
            strategy = TranslationStrategy.RULE_BASED
            confidence = 1.0
            elapsed = (time.time() - start_time) * 1000
            
            if self.cache:
                self.cache.put(clean_dax, table_alias, sql, strategy)
                
            metrics = TranslationMetrics(
                dax=clean_dax,
                strategy=strategy,
                capability=CapabilityLevel.FULLY_SUPPORTED,
                sql=sql,
                confidence=confidence,
                execution_time_ms=elapsed,
                cached=False,
            )
            self.metrics_log.append(metrics)
            return sql, metrics

        # Step 3: Detect capability
        can_translate, capability = self.detector.can_translate(clean_dax)
        
        if not can_translate:
            logger.info(f"⚠ Pattern not deterministically supported, checking LLM fallback...")
            # We don't return None here, we proceed to try LLM at the end
            # but we skip Step 3 (Deterministic)
            sql = None
        else:
            # Step 3: Try deterministic translation (AST-based)
            sql = self._try_ast_translation(
                clean_dax,
                table_alias,
                date_alias,
                measure_map or {},
            )
        
        elapsed = (time.time() - start_time) * 1000
        
        if sql:
            strategy = TranslationStrategy.AST_BASED
            confidence = 0.95  # High confidence for AST-based
            
            # Cache the success
            if self.cache:
                self.cache.put(clean_dax, table_alias, sql, strategy)
            
            logger.info(
                f"✓ Deterministic translation: {metric_name or clean_dax[:50]}\n"
                f"  → SQL: {sql[:80]}"
            )
            
            metrics = TranslationMetrics(
                dax=clean_dax,
                strategy=strategy,
                capability=capability,
                sql=sql,
                confidence=confidence,
                execution_time_ms=elapsed,
                cached=False,
            )
            
            self.metrics_log.append(metrics)
            return sql, metrics
        
        # Step 4: Fallback to LLM (if available)
        logger.info(
            f"⚠ Deterministic translation failed, falling back to LLM: "
            f"{metric_name or clean_dax[:50]}"
        )
        
        llm_sql = sanitize_llm_sql(self.llm_translator.translate(clean_dax) or "")
        if re.search(r"(?i)\b(VAR|RETURN|SELECT|FROM|WITH|JOIN|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY)\b", llm_sql):
            logger.warning(
                "Rejected LLM fallback for %s because it was not a scalar metric expression: %s",
                metric_name or clean_dax[:50],
                llm_sql[:160],
            )
            llm_sql = ""
        elapsed = (time.time() - start_time) * 1000
        
        if llm_sql:
            strategy = TranslationStrategy.LLM_FALLBACK
            metrics = TranslationMetrics(
                dax=clean_dax,
                strategy=strategy,
                capability=capability,
                sql=llm_sql,
                confidence=0.7, # LLM is less certain than AST
                execution_time_ms=elapsed,
                cached=False,
            )
            
            if self.cache:
                self.cache.put(clean_dax, table_alias, llm_sql, strategy)
                
            self.metrics_log.append(metrics)
            return llm_sql, metrics
        
        metrics = TranslationMetrics(
            dax=clean_dax,
            strategy=TranslationStrategy.FAILED,
            capability=capability,
            sql=None,
            confidence=0.0,
            execution_time_ms=elapsed,
            cached=False,
            error="LLM translation failed",
        )
        
        self.metrics_log.append(metrics)
        return None, metrics

    def analyze_complexity(self, expression: str) -> Dict[str, Any]:
        """
        Analyze DAX complexity to match legacy interface.
        """
        if not expression:
            return {
                "tier": 1,
                "requires_time_intel": False,
                "group_by_dimensions": [],
                "depends_on_measures": [],
                "sync_enabled": True,
                "failure_reason": None
            }
            
        # Basic heuristic for now (real logic is inside translate)
        is_complex = any(kw in expression.upper() for kw in ["CALCULATE", "SUMX", "TOTALYTD", "FILTER"])
        
        return {
            "tier": 4 if is_complex else 1,
            "requires_time_intel": "DATE" in expression.upper() or "YTD" in expression.upper(),
            "group_by_dimensions": [],
            "depends_on_measures": [],
            "sync_enabled": True,
            "failure_reason": None
        }
    
    def _try_ast_translation(
        self,
        dax: str,
        table_alias: str,
        date_alias: str,
        measure_map: Dict[str, str],
    ) -> Optional[str]:
        """Attempt AST-based translation."""
        try:
            ast = self.parser.parse(dax)
            if ast is None:
                return None
            
            # Phase 2: Check for specialized translation needs
            if isinstance(ast, FunctionCallNode):
                fname = ast.func.upper()
                if fname == "CALCULATE":
                    return self.calculate_translator.translate_to_ctes(ast)
                elif fname in ("EARLIER", "EARLIEST"):
                    return self.row_context_translator.translate(ast)
                elif fname in ("SUMX", "AVERAGEX", "RANKX"):
                    return self.iterator_translator.translate(ast)
            
            renderer = DaxSqlRenderer(
                table_alias=table_alias,
                date_alias=date_alias,
                measure_sql_map=measure_map,
            )
            return renderer.render(ast)
        except Exception as e:
            logger.debug(f"AST translation error: {e}")
            return None
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of translation metrics."""
        if not self.metrics_log:
            return {"total": 0, "summary": {}}
        
        by_strategy = {}
        by_capability = {}
        
        for m in self.metrics_log:
            # Count by strategy
            by_strategy.setdefault(m.strategy.value, 0)
            by_strategy[m.strategy.value] += 1
            
            # Count by capability
            by_capability.setdefault(m.capability.value, 0)
            by_capability[m.capability.value] += 1
        
        successful = sum(1 for m in self.metrics_log if m.sql is not None)
        avg_time = sum(m.execution_time_ms for m in self.metrics_log) / len(self.metrics_log)
        
        return {
            "total": len(self.metrics_log),
            "successful": successful,
            "success_rate": f"{(successful/len(self.metrics_log)*100):.1f}%",
            "average_time_ms": f"{avg_time:.2f}",
            "by_strategy": by_strategy,
            "by_capability": by_capability,
        }


# Singleton instance
_engine = None


def get_translation_engine(cache_enabled: bool = True, target_dialect: str = "snowflake") -> DaxTranslationEngine:
    """Get singleton translation engine."""
    global _engine
    if _engine is None:
        _engine = DaxTranslationEngine(cache_enabled=cache_enabled)
    return _engine
