"""
DAX to SQL Translator.

Implements the "Tiered Safety" translation strategy to convert Fabric DAX expressions
into Snowflake-compatible SQL for Semantic Views.
"""

import os
import re
from typing import Optional, Tuple, Dict, List, Any

from semabridge.utils.logger import get_logger
from semabridge.utils.naming import sanitize_column, to_alias
from semabridge.converter.dax_rule_translator import is_simple_metric, rule_based_translation

logger = get_logger(__name__)


class DAXTranslationResult:
    """Result of a DAX translation attempt."""
    
    def __init__(self, sql: Optional[str], tier: int, original_dax: str):
        self.sql = sql
        self.tier = tier  # 0=Override, 1=Direct, 2=Branching/Arith, 3=Opaque (Failed)
        self.original_dax = original_dax
        self.is_success = sql is not None


class DAXTranslator:
    """
    Translates DAX expressions to SQL.
    
    Tier 1: Direct Aggregations (SUM, AVG, MIN, MAX, COUNT, DISTINCTCOUNT)
    Tier 2: Arithmetic & Branching (A + B, A / B, DIVIDE)
    Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD) - Window functions
    Tier 4: Complex (CALCULATE with filters, iterators) - AST/rules/LLM fallback
    """
    
    _cached_tier1_functions = None
    _cached_time_intel_functions = None
    _cached_date_table_from_snowflake = None

    def __init__(self, behavior_config: Optional[Dict[str, Any]] = None, cursor: Any = None, model: Any = None):
        self.behavior_config = behavior_config or {}
        self.cursor = cursor
        self.model = model
        
        # Load yaml config
        self._yaml_config = self._load_yaml_config()
        
        # DISCOVER EVERYTHING DYNAMICALLY
        self.date_table = self._sanitize_date_name(self._discover_date_table_from_snowflake() or self._get_fallback_date_table())
        self.date_column = self._sanitize_date_name(self._discover_date_column())
        self.tier1_functions = self._discover_tier1_functions()
        self.time_intel_functions = self._discover_time_intel_functions()
        self.blocked_functions = self._discover_blocked_functions()
        self.unsafe_time_offset_functions = self._discover_unsafe_functions()
        self.unsupported_patterns_list = self._discover_unsupported_patterns()
        
        # Build patterns dynamically
        self._TIER1_PATTERN = self._build_tier1_pattern()
        self.TIME_INTEL_PATTERNS = self._build_time_intel_patterns()
        self._DIVIDE_PATTERN = self._build_divide_pattern()
        self._ARITHMETIC_PATTERN = self._build_arithmetic_pattern()
        self._MEASURE_REF_PATTERN = re.compile(r"\[([^\]]+)\]")
        
        self.TIME_INTEL_FUNCTIONS = self.time_intel_functions
        self.STRICT_BLOCKED_FUNCTIONS = self.blocked_functions
        self.UNSAFE_TIME_OFFSET_FUNCTIONS = self.unsafe_time_offset_functions
        self.UNSUPPORTED_PATTERNS = self.unsupported_patterns_list
        
        # Load SQL templates
        self.sql_templates = self._load_sql_templates()
        
        # Date resolution
        self.date_table = self._sanitize_date_name(self._get_date_alias())
        self.date_column = self._sanitize_date_name(self._discover_date_column())

    def _load_yaml_config(self) -> Dict[str, Any]:
        """Load the translation_rules.yaml file."""
        import yaml
        try:
            config_path = os.path.join(os.getcwd(), "config", "translation_rules.yaml")
            if not os.path.exists(config_path):
                # try finding from src directory upwards
                import pathlib
                base = pathlib.Path(__file__).parent.parent.parent.parent.parent
                config_path = base / "config" / "translation_rules.yaml"
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.debug(f"Failed to load translation_rules.yaml: {e}")
            return {}

    def _load_from_config_file(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self._yaml_config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def _get_config_value(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self.behavior_config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return self._load_from_config_file(key, default)
        return value if value is not None else self._load_from_config_file(key, default)

    def _discover_tier1_functions(self) -> List[str]:
        """Discover tier1 functions from config or Snowflake."""
        if DAXTranslator._cached_tier1_functions:
            return DAXTranslator._cached_tier1_functions
        config_funcs = self.behavior_config.get("tier1_functions")
        if config_funcs:
            return config_funcs
        if self.cursor:
            try:
                self.cursor.execute("""
                    SELECT DISTINCT FUNCTION_NAME 
                    FROM INFORMATION_SCHEMA.FUNCTIONS 
                    WHERE FUNCTION_TYPE = 'AGGREGATE'
                      AND FUNCTION_NAME IN ('SUM', 'AVG', 'MIN', 'MAX', 'COUNT', 'DISTINCTCOUNT')
                """)
                functions = [row[0].upper() for row in self.cursor.fetchall()]
                if functions:
                    DAXTranslator._cached_tier1_functions = functions
                    return functions
            except:
                pass
        return self._load_from_config_file("tier1_functions", ["SUM", "AVERAGE", "AVG", "MIN", "MAX", "COUNT", "COUNTA", "DISTINCTCOUNT"])

    def _discover_time_intel_functions(self) -> List[str]:
        """Discover time intelligence functions from Snowflake or config."""
        if DAXTranslator._cached_time_intel_functions:
            return DAXTranslator._cached_time_intel_functions
        config_funcs = self.behavior_config.get("time_intelligence_functions")
        if config_funcs:
            return config_funcs
        if self.cursor:
            try:
                self.cursor.execute("SHOW FUNCTIONS")
                functions = []
                for row in self.cursor.fetchall():
                    name = row[0].upper()
                    patterns = self._get_config_value("time_intel_patterns", ["YTD", "MTD", "QTD", "DATEADD", "PERIOD"])
                    if any(pattern in name for pattern in patterns):
                        functions.append(name)
                if functions:
                    DAXTranslator._cached_time_intel_functions = functions
                    return functions
            except:
                pass
        return self._load_from_config_file("time_intelligence_functions", [
            "TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", 
            "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD",
            "DATESYTD", "DATESMTD", "DATESQTD", "PARALLELPERIOD"
        ])

    def _discover_blocked_functions(self) -> List[str]:
        return self._get_config_value("blocked_functions", ["ALL", "ALLEXCEPT", "CROSSFILTER", "USERELATIONSHIP", "EARLIER", "RANKX"])

    def _discover_unsafe_functions(self) -> List[str]:
        return self._get_config_value("unsafe_time_offset_functions", ["SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD", "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"])

    def _discover_unsupported_patterns(self) -> List[str]:
        return self._get_config_value("unsupported_patterns", [r"CALCULATE\s*\([^)]+,\s*FILTER\s*\(", r"SUMX\s*\(\s*FILTER\s*\(", r"EARLIER\s*\(", r"RANKX\s*\(", r"USERELATIONSHIP\s*\(", r"CROSSFILTER\s*\(", r"ALL\s*\([^)]*\)\s*\)", r"ALLEXCEPT\s*\("])

    def _build_tier1_pattern(self) -> re.Pattern:
        escaped = [re.escape(f) for f in self.tier1_functions]
        pattern = '|'.join(escaped)
        return re.compile(rf"^\s*({pattern})\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*$", re.IGNORECASE)

    def _build_time_intel_patterns(self) -> Dict[str, re.Pattern]:
        patterns = {}
        tier1_pattern = '|'.join([re.escape(f) for f in self.tier1_functions])
        ytd_func = self._get_config_value("time_intel_patterns.ytd", "TOTALYTD")
        mtd_func = self._get_config_value("time_intel_patterns.mtd", "TOTALMTD")
        qtd_func = self._get_config_value("time_intel_patterns.qtd", "TOTALQTD")
        
        patterns["YTD"] = re.compile(rf"{ytd_func}\s*\(\s*({tier1_pattern})\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]", re.IGNORECASE)
        patterns["MTD"] = re.compile(rf"{mtd_func}\s*\(\s*({tier1_pattern})\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]", re.IGNORECASE)
        patterns["QTD"] = re.compile(rf"{qtd_func}\s*\(\s*({tier1_pattern})\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]", re.IGNORECASE)
        return patterns

    def _build_arithmetic_pattern(self) -> re.Pattern:
        return re.compile(r"^\s*(\[.+?\])\s*([\+\-\*\/])\s*(\[.+?\])\s*$", re.IGNORECASE)

    def _build_divide_pattern(self) -> re.Pattern:
        divide_funcs = self._get_config_value("divide_functions", ["DIVIDE"])
        div_funcs_pipe = "|".join([re.escape(f) for f in divide_funcs])
        return re.compile(rf"^\s*(?:{div_funcs_pipe})\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*(?:,.+?)?\)\s*$", re.IGNORECASE)

    def _load_sql_templates(self) -> Dict[str, str]:
        return self._get_config_value("sql_templates", {})

    def _generate_sql_from_template(self, template_name: str, **kwargs) -> str:
        """Generate SQL from dynamic template — no hardcoded SQL."""
        template = self.sql_templates.get(template_name)
        if not template:
            template = self.sql_templates.get("case_when_default", "{agg_func}(CASE WHEN {condition} THEN {value} ELSE 0 END)")
        return template.format(**kwargs)

    def _discover_date_column(self) -> str:
        return self._get_config_value("date_defaults.default_date_column", "DATE")

    def _get_fallback_date_table(self) -> str:
        return self._get_config_value("date_defaults.fallback_date_table", "DATE")

    def _sanitize_date_name(self, name: str) -> str:
        """Sanitize DATE to COL_DATE since DATE is a reserved keyword in Snowflake DDL."""
        if name and name.upper() == "DATE":
            return "COL_DATE"
        return name

    def _get_date_alias(self) -> str:
        if self.model:
            from semabridge.converter.date_resolution import DateResolutionConfig
            resolved = DateResolutionConfig().resolve(self.model)
            if resolved and resolved.table:
                return resolved.table
        
        date_table = self._get_config_value("date_defaults.default_date_table")
        if date_table:
            return date_table
            
        env_alias = os.getenv("SEMABRIDGE_DATE_ALIAS") or os.getenv("SEMABRIDGE_DATE_TABLE")
        if env_alias:
            return env_alias
            
        if self.cursor:
            discovered = self._discover_date_table_from_snowflake()
            if discovered:
                return discovered
                
        return self._get_config_value("date_defaults.fallback_date_table", "DATE")

    def _discover_date_table_from_snowflake(self) -> Optional[str]:
        if DAXTranslator._cached_date_table_from_snowflake:
            return DAXTranslator._cached_date_table_from_snowflake
        try:
            patterns = self._get_config_value("date_table_patterns", ["%DATE%", "%CALENDAR%", "%TIME%"])
            for pattern in patterns:
                self.cursor.execute(f"SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME ILIKE '{pattern}' LIMIT 1")
                result = self.cursor.fetchone()
                if result:
                    DAXTranslator._cached_date_table_from_snowflake = result[0]
                    return result[0]
        except:
            pass
        return None

    def translate(self, 
                  dax: str, 
                  table_alias: str, 
                  dataset_name: str, 
                  metric_name: str = None,
                  metrics_context: List[Any] = None,
                  model: Any = None) -> DAXTranslationResult:
        res = self._translate_impl(dax, table_alias, dataset_name, metric_name, metrics_context, model)
        if res and res.sql:
            res.sql = re.sub(r"DATE_PART\s*\(\s*['\"]*([A-Za-z_]+)['\"]*\s*,", lambda m: "DATE_PART('" + m.group(1).strip("\"'").lower() + "', ", res.sql, flags=re.IGNORECASE)
        return res

    def _translate_impl(self, 
                  dax: str, 
                  table_alias: str, 
                  dataset_name: str, 
                  metric_name: str = None,
                  metrics_context: List[Any] = None,
                  model: Any = None) -> DAXTranslationResult:
        if not dax:
            return DAXTranslationResult(None, 3, "")
        
        clean_dax = dax.strip()

        # Tier 1-4 are handled before the broader deterministic fallback so
        # common patterns remain predictable and do not get over-simplified.
        tiered_sql = self._try_tiered_translation(model, 
            clean_dax,
            table_alias,
            dataset_name,
            metrics_context,
        )
        if tiered_sql:
            return tiered_sql
        
        # **NEW PRIMARY FLOW: Try deterministic translator first**
        # This enforces pipeline as single source of truth
        try:
            from semabridge.converter.deterministic_translator import DeterministicTranslator
            det_translator = DeterministicTranslator()
            det_result = det_translator.translate(
                clean_dax,
                table_alias,
                dataset_name,
                metric_name=metric_name
            )
            if det_result.is_success and det_result.sql:
                logger.info(f"✓ Deterministic translation successful: {det_result.sql[:60]}")
                return DAXTranslationResult(det_result.sql, det_result.tier, clean_dax)
            else:
                logger.debug(f"Deterministic translator returned no SQL, falling back to Tier logic")
        except Exception as e:
            logger.warning(f"Deterministic translator error, falling back to Tier logic: {e}")
        
        # Tier 1: Direct Aggregations
        tier1_sql = self._try_tier1(clean_dax, table_alias)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)

        strict_blocked = any(
            re.search(rf"\b{pattern}\b", clean_dax, re.IGNORECASE)
            for pattern in self.STRICT_BLOCKED_FUNCTIONS
        )
        if strict_blocked:
            logger.debug(
                "Strict translator rejected unsupported DAX pattern; continuing with AST/LLM: %s",
                clean_dax[:80],
            )

        if not strict_blocked:
            strict_sql = self._try_strict_translation(clean_dax, table_alias, metrics_context)
            if strict_sql:
                return DAXTranslationResult(strict_sql, 2, clean_dax)

        if metrics_context:
            dependency_sql = self._try_dependency_translation(
                clean_dax,
                table_alias,
                dataset_name,
                metrics_context,
            )
            if dependency_sql:
                return DAXTranslationResult(dependency_sql, 2, clean_dax)
        
        # Tier 2: Branching & Arithmetic
        # e.g. [Net Sales] = [Gross Sales] - [Discounts]
        if metrics_context:
            tier2_sql = self._try_branching(clean_dax, metrics_context)
            if tier2_sql:
                return DAXTranslationResult(tier2_sql, 2, clean_dax)
        
        # Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD, SAMEPERIODLASTYEAR, etc.)
        # Uses the deterministic AST parser + Snowflake window function renderer.
        is_time_intel = any(func.upper() in dax.upper() for func in self.TIME_INTEL_FUNCTIONS)
        if is_time_intel:
            from semabridge.converter.dax_ast_parser import try_ast_translate
            resolved_measures = {}
            if metrics_context:
                for m in metrics_context:
                    if m.sql_expression:
                        resolved_measures[m.unique_name] = m.sql_expression
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self.date_table,
                date_column=self.date_column,
                measure_sql_map=resolved_measures,
                behavior_config=self.behavior_config,
                cursor=self.cursor,
                sql_templates=self.sql_templates,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 3, clean_dax)

        # Tier 4: Complex CALCULATE / FILTER / ALL / ALLEXCEPT — attempt AST translation
        is_complex = any(
            re.search(pattern, clean_dax, re.IGNORECASE)
            for pattern in self.UNSUPPORTED_PATTERNS
        )
        if is_complex:
            from semabridge.converter.dax_ast_parser import try_ast_translate
            resolved_measures = {}
            if metrics_context:
                for m in metrics_context:
                    if m.sql_expression:
                        resolved_measures[m.unique_name] = m.sql_expression
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self.date_table,
                date_column=self.date_column,
                measure_sql_map=resolved_measures,
                behavior_config=self.behavior_config,
                cursor=self.cursor,
                sql_templates=self.sql_templates,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 4, clean_dax)

        # Tier 5: LLM Fallback - Use Claude for complex expressions deterministic parsing couldn't handle
        # Only attempt if LLM is available and enabled
        llm_result = self._try_llm_fallback(
            clean_dax, 
            table_alias,
            dataset_name,
            metric_name
        )
        if llm_result:
            return llm_result

        # Ultimate fallback — never skip! (all tiers exhausted)
        fallback_sql = f"CAST(NULL AS DOUBLE) /* TODO: translate {metric_name or 'unknown'} */"
        logger.warning(f"Could not translate metric '{metric_name or 'unknown'}', using safe fallback")
        return DAXTranslationResult(fallback_sql, 4, clean_dax)

    def _try_tiered_translation(
        self,
        model: Any,
        clean_dax: str,
        table_alias: str,
        dataset_name: str,
        metrics_context: List[Any] = None,
    ) -> Optional[DAXTranslationResult]:
        tier1_sql = self._try_tier1(clean_dax, table_alias)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)

        strict_blocked = any(
            re.search(rf"\b{pattern}\b", clean_dax, re.IGNORECASE)
            for pattern in self.STRICT_BLOCKED_FUNCTIONS
        )
        if not strict_blocked:
            strict_sql = self._try_strict_translation(clean_dax, table_alias, metrics_context)
            if strict_sql:
                return DAXTranslationResult(strict_sql, 2, clean_dax)

        if metrics_context:
            dependency_sql = self._try_dependency_translation(
                clean_dax,
                table_alias,
                dataset_name,
                metrics_context,
            )
            if dependency_sql:
                return DAXTranslationResult(dependency_sql, 2, clean_dax)

            tier2_sql = self._try_branching(clean_dax, metrics_context)
            if tier2_sql:
                return DAXTranslationResult(tier2_sql, 2, clean_dax)

        resolved_measures = {}
        if metrics_context:
            for metric in metrics_context:
                if getattr(metric, "sql_expression", None):
                    resolved_measures[metric.unique_name] = metric.sql_expression

        is_time_intel = any(func.upper() in clean_dax.upper() for func in self.TIME_INTEL_FUNCTIONS)
        if is_time_intel:
            from semabridge.converter.dax_ast_parser import try_ast_translate

            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self.date_table,
                date_column=self.date_column,
                measure_sql_map=resolved_measures,
                behavior_config=self.behavior_config,
                cursor=self.cursor,
                sql_templates=self.sql_templates,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 3, clean_dax)

        is_complex = any(
            re.search(pattern, clean_dax, re.IGNORECASE)
            for pattern in self.UNSUPPORTED_PATTERNS
        )
        if is_complex:
            from semabridge.converter.dax_ast_parser import try_ast_translate

            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self.date_table,
                date_column=self.date_column,
                measure_sql_map=resolved_measures,
                behavior_config=self.behavior_config,
                cursor=self.cursor,
                sql_templates=self.sql_templates,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 4, clean_dax)

        return None

    def _contains_unsafe_time_offset(self, dax: str) -> bool:
        upper_dax = (dax or "").upper()
        return any(
            re.search(rf"\b{re.escape(func)}\s*\(", upper_dax)
            for func in self.UNSAFE_TIME_OFFSET_FUNCTIONS
        )

    def _try_dependency_translation(
        self,
        dax: str,
        table_alias: str,
        dataset_name: str,
        metrics_context: List[Any],
        visiting: Optional[set[str]] = None,
    ) -> Optional[str]:
        """Resolve pure measure references and arithmetic chains by expanding dependencies first."""
        if not dax or not metrics_context:
            return None

        visiting = visiting or set()
        clean_dax = " ".join((dax or "").split())
        metric_index = {
            str(getattr(m, "unique_name", "")).casefold(): m
            for m in metrics_context
            if getattr(m, "unique_name", None)
        }

        def resolve_measure(name: str) -> Optional[str]:
            key = (name or "").strip().casefold()
            if not key:
                return None
            if key in visiting:
                logger.warning(f"Circular measure dependency detected for '{name}'")
                return None

            metric = metric_index.get(key)
            if not metric:
                return None

            existing_sql = (getattr(metric, "sql_expression", None) or "").strip()
            if existing_sql:
                return self._strip_outer_parens(existing_sql)

            metric_expr = (getattr(metric, "expression", None) or "").strip()
            if not metric_expr:
                return None

            visiting.add(key)
            try:
                tier1 = self._try_tier1(metric_expr, table_alias)
                if tier1:
                    return self._strip_outer_parens(tier1)

                strict = self._try_strict_translation(metric_expr, table_alias, metrics_context)
                if strict:
                    return self._strip_outer_parens(strict)

                nested = self._try_dependency_translation(
                    metric_expr,
                    table_alias,
                    dataset_name,
                    metrics_context,
                    visiting,
                )
                if nested:
                    return self._strip_outer_parens(nested)

                return None
            finally:
                visiting.remove(key)

        def split_args(args_text: str) -> List[str]:
            args: List[str] = []
            current: List[str] = []
            depth = 0
            for ch in args_text:
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth > 0:
                    depth -= 1
                elif ch == "," and depth == 0:
                    value = "".join(current).strip()
                    if value:
                        args.append(value)
                    current = []
                    continue
                current.append(ch)
            tail = "".join(current).strip()
            if tail:
                args.append(tail)
            return args

        pure_ref = re.fullmatch(r"\[([^\]]+)\]", clean_dax)
        if pure_ref:
            return resolve_measure(pure_ref.group(1))

        if clean_dax.upper().startswith("DIVIDE(") and clean_dax.endswith(")"):
            args = split_args(clean_dax[7:-1])
            if len(args) >= 2:
                numerator_sql = self._try_dependency_translation(args[0], table_alias, dataset_name, metrics_context, visiting)
                denominator_sql = self._try_dependency_translation(args[1], table_alias, dataset_name, metrics_context, visiting)
                if numerator_sql and denominator_sql:
                    alt = args[2].strip() if len(args) >= 3 and args[2].strip() else "0"
                    return f"COALESCE(({numerator_sql}) / NULLIF(({denominator_sql}), 0), {alt})"

        refs = [match.group(1).strip() for match in self._MEASURE_REF_PATTERN.finditer(clean_dax)]
        refs = [ref for ref in refs if ref]
        if refs:
            replaced = clean_dax
            for ref in refs:
                dep_sql = resolve_measure(ref)
                if not dep_sql:
                    return None
                replaced = re.sub(rf"\[{re.escape(ref)}\]", f"({dep_sql})", replaced, flags=re.IGNORECASE)

            if replaced != clean_dax:
                upper_replaced = replaced.upper()
                if any(keyword in upper_replaced for keyword in ("CALCULATE", "SAMEPERIODLASTYEAR", "DATEADD", "DATESYTD", "TOTALYTD", "IF(", "BLANK(")):
                    return None
                if "[" in replaced or "]" in replaced:
                    return None
                return replaced

        return None

    def _split_dax_arguments(self, args_text: str) -> List[str]:
        """Split a comma-separated DAX argument list at top level only."""
        args: List[str] = []
        current: List[str] = []
        depth = 0
        in_string = False
        string_quote = ""

        for ch in args_text:
            if in_string:
                current.append(ch)
                if ch == string_quote:
                    in_string = False
                continue

            if ch in ('"', "'"):
                in_string = True
                string_quote = ch
                current.append(ch)
                continue

            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                value = "".join(current).strip()
                if value:
                    args.append(value)
                current = []
                continue

            current.append(ch)

        tail = "".join(current).strip()
        if tail:
            args.append(tail)
        return args

    def _strip_outer_parens(self, expr: str) -> str:
        """Remove wrapping parentheses when they fully enclose the expression."""
        clean = (expr or "").strip()
        while clean.startswith("(") and clean.endswith(")"):
            depth = 0
            wrapped = True
            for index, ch in enumerate(clean):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and index != len(clean) - 1:
                        wrapped = False
                        break
            if not wrapped or depth != 0:
                break
            clean = clean[1:-1].strip()
        return clean

    def _parse_sql_aggregation(self, sql: str) -> Optional[Tuple[str, str]]:
        """Return the aggregate function and inner expression for simple SQL."""
        clean = self._strip_outer_parens(sql)
        distinct_match = re.match(r"^COUNT\s*\(\s*DISTINCT\s+(.+?)\s*\)$", clean, re.IGNORECASE)
        if distinct_match:
            return ("COUNT_DISTINCT", distinct_match.group(1).strip())

        match = re.match(r"^(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(.+?)\s*\)$", clean, re.IGNORECASE)
        if match:
            return (match.group(1).upper(), match.group(2).strip())
        return None

    def _extract_simple_filter_predicate(self, filter_expr: str) -> Optional[str]:
        """Extract a direct predicate from FILTER(table, predicate) when safely possible."""
        clean_filter = self._strip_outer_parens(filter_expr)
        if not re.match(r"^FILTER\s*\(", clean_filter, re.IGNORECASE):
            return clean_filter

        args = self._split_dax_arguments(clean_filter[len("FILTER("):-1]) if clean_filter.endswith(")") else []
        if len(args) != 2:
            return None

        predicate = self._strip_outer_parens(args[1])
        # Keep strict mode deterministic: reject nested table expressions/functions.
        if re.search(r"\b(FILTER|ALL|ALLEXCEPT|SAMEPERIODLASTYEAR|PREVIOUSYEAR|PREVIOUSMONTH|PREVIOUSQUARTER|DATEADD|DATESYTD|DATESMTD|DATESQTD|PARALLELPERIOD|OPENINGBALANCEYEAR|CLOSINGBALANCEYEAR)\b", predicate, re.IGNORECASE):
            return None
        return predicate

    def _resolve_measure_sql(self, name: str, metrics_context: Optional[List[Any]], table_alias: str) -> Optional[str]:
        """Resolve a measure name to SQL using the provided dependency cache."""
        if not metrics_context:
            return None

        for metric in metrics_context:
            if str(getattr(metric, "unique_name", "")).casefold() != str(name or "").casefold():
                continue

            sql_expression = (getattr(metric, "sql_expression", None) or "").strip()
            if sql_expression:
                return self._strip_outer_parens(sql_expression)

            metric_expr = (getattr(metric, "expression", None) or "").strip()
            if metric_expr:
                direct_sql = self._try_tier1(metric_expr, table_alias)
                if direct_sql:
                    return self._strip_outer_parens(direct_sql)

                strict_sql = self._try_strict_translation(metric_expr, table_alias, metrics_context)
                if strict_sql:
                    return self._strip_outer_parens(strict_sql)

                dependency_sql = self._try_dependency_translation(
                    metric_expr,
                    table_alias,
                    "",
                    metrics_context,
                    visiting=set(),
                )
                if dependency_sql:
                    return self._strip_outer_parens(dependency_sql)

                translated = self._try_branching(metric_expr, metrics_context)
                if translated:
                    return self._strip_outer_parens(translated)
            return None

        return None

    def _try_strict_translation(
        self,
        dax: str,
        table_alias: str,
        metrics_context: Optional[List[Any]] = None,
        model: Any = None,
    ) -> Optional[str]:
        """Translate only the two strict forms supported by the prompt."""
        clean_dax = " ".join((dax or "").split())
        upper_dax = clean_dax.upper()



        if upper_dax.startswith("CALCULATE(") and clean_dax.endswith(")"):
            args = self._split_dax_arguments(clean_dax[len("CALCULATE("):-1])
            if len(args) != 2:
                return None

            base_expr = self._strip_outer_parens(args[0])
            filter_expr = self._strip_outer_parens(args[1])
            filter_expr = self._extract_simple_filter_predicate(filter_expr)
            if not filter_expr:
                return None

            if re.search(
                r"\b(FILTER|ALL|ALLEXCEPT|SAMEPERIODLASTYEAR|PREVIOUSYEAR|PREVIOUSMONTH|PREVIOUSQUARTER|DATEADD|DATESYTD|DATESMTD|DATESQTD|PARALLELPERIOD|OPENINGBALANCEYEAR|CLOSINGBALANCEYEAR)\b",
                filter_expr,
                re.IGNORECASE,
            ):
                return None

            filter_match = re.match(
                r"^(?:'(?P<table_quoted>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\[(?P<column>[^\]]+)\]\s*=\s*(?P<value>.+)$",
                filter_expr,
                re.IGNORECASE,
            )
            if not filter_match:
                return None

            base_sql = self._try_tier1(base_expr, table_alias)
            if not base_sql and re.fullmatch(r"\[([^\]]+)\]", base_expr):
                base_sql = self._resolve_measure_sql(base_expr[1:-1], metrics_context, table_alias)
            if not base_sql:
                return None

            agg = self._parse_sql_aggregation(base_sql)
            if not agg:
                return None

            agg_func, value_expr = agg
            table_name = filter_match.group("table_quoted") or filter_match.group("table") or ""
            column_name = filter_match.group("column")
            value_text = filter_match.group("value").strip()

            if re.fullmatch(r'"(?:[^"\\]|\\.)*"', value_text):
                literal = "'" + value_text[1:-1].replace("'", "''") + "'"
            elif re.fullmatch(r"'(?:[^'\\]|\\.)*'", value_text):
                literal = value_text
            elif re.fullmatch(r"-?\d+(?:\.\d+)?", value_text):
                literal = value_text
            else:
                return None

            lhs_table = sanitize_column(table_name, force_uppercase=True)
            lhs_column = sanitize_column(column_name, force_uppercase=True)
            condition_sql = f"{lhs_table}.{lhs_column} = {literal}"

            if agg_func == "SUM":
                return self._generate_sql_from_template(
                    "case_when_sum",
                    agg_func=agg_func,
                    condition=condition_sql,
                    value=value_expr
                )
            if agg_func == "COUNT_DISTINCT":
                return self._generate_sql_from_template(
                    "case_when_count_distinct",
                    agg_func=agg_func,
                    condition=condition_sql,
                    value=value_expr
                )
            if agg_func in {"COUNT", "AVG", "MIN", "MAX"}:
                return self._generate_sql_from_template(
                    f"case_when_{agg_func.lower()}",
                    agg_func=agg_func,
                    condition=condition_sql,
                    value=value_expr
                )

        return None
    def _try_llm_fallback(self,
                          dax: str,
                          table_alias: str,
                          dataset_name: str,
                          metric_name: Optional[str] = None) -> Optional[DAXTranslationResult]:
        """
        Attempt Tier 5 LLM translation for complex expressions.
        
        First checks if the metric is simple enough for rule-based translation.
        Only uses Google Gemini as a fallback for truly complex expressions.
        Returns None if LLM is not available or declines to translate.
        """
        # TIER 4.5: Check if metric is simple enough for rule-based translation
        # This dramatically reduces LLM API usage by 60-80%
        if is_simple_metric(dax):
            logger.info(f"🟢 Metric classified as SIMPLE - using rule-based translation: {metric_name or dax[:50]}")
            
            # Try rule-based translation
            sql = rule_based_translation(dax, table_alias)
            if sql:
                logger.debug(f"   ✓ Rule-based translation succeeded: {sql[:80]}")
                return DAXTranslationResult(sql, 4, dax)  # Tier 4 for rule-based (deterministic)
            else:
                logger.debug(f"   ✗ Rule-based translation failed, will fall through to LLM")
        else:
            logger.info(f"🟠 Metric classified as COMPLEX - requesting LLM translation: {metric_name or dax[:50]}")
        
        # PRIORITY 0: OpenAI translation first if OPENAI_API_KEY is configured
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key and not getattr(self, "_llm_circuit_breaker", False):
            try:
                from openai import OpenAI
                model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
                client = OpenAI(api_key=openai_api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
                
                logger.info(f"🤖 Calling OpenAI API for measure '{metric_name or dax[:30]}'...")
                
                prompt = (
                    "Dialect: Snowflake Semantic View METRICS clause\n"
                    f"Metric name: {metric_name or 'unnamed'}\n"
                    f"Default table alias: {table_alias}\n"
                    "Rules:\n"
                    "- Return only a single SQL expression, no explanation.\n"
                    "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
                    "- Do not nest aggregate functions like SUM(MAX(...)).\n"
                    "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN column ELSE 0 END).\n"
                    "- Quote identifiers only when needed as ALIAS.\"COLUMN\"; uppercase Snowflake column names.\n"
                    "- If a pattern is impossible, return CAST(NULL AS DOUBLE).\n"
                    "DAX:\n"
                    f"{dax}"
                )
                
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. "
                                "Return only one SQL expression. Do not use markdown."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                    max_tokens=int(os.getenv("OPENAI_DAX_MAX_TOKENS", "500")),
                    timeout=float(os.getenv("OPENAI_DAX_TIMEOUT", "30")),
                )
                sql = (response.choices[0].message.content or "").strip()
                
                if sql.startswith("```"):
                    sql = re.sub(r"^```(?:sql|python|.*?)\n", "", sql, flags=re.IGNORECASE)
                    sql = re.sub(r"\n```$", "", sql, flags=re.IGNORECASE)
                    sql = sql.strip()
                
                if sql:
                    sql_upper = sql.upper()
                    forbidden = (
                        " SELECT ",
                        "(SELECT",
                        " FROM ",
                        " JOIN ",
                        " WITH ",
                        " DROP ",
                        " DELETE ",
                        " TRUNCATE ",
                        " INSERT ",
                        " UPDATE ",
                        " ALTER ",
                        ";",
                    )
                    padded = f" {sql_upper} "
                    if any(token in padded for token in forbidden):
                        logger.warning(
                            "OpenAI DAX translation rejected (forbidden SQL tokens) for metric '%s': %s",
                            metric_name,
                            sql[:120],
                        )
                    else:
                        logger.info(f"✓ [{metric_name or dax[:30]}]: OpenAI translation")
                        return DAXTranslationResult(sql, 5, dax)
            except Exception as exc:
                err_msg = str(exc)
                logger.warning("OpenAI individual translation fallback failed: %s", err_msg)
                if "insufficient_quota" in err_msg or "429" in err_msg or "403" in err_msg:
                    DAXTranslator._llm_circuit_breaker = True

        # PRIORITY 1: Featherless fallback if configured.
        if not getattr(DAXTranslator, "_llm_circuit_breaker", False):
            try:
                from semabridge.converter.featherless_translator import translate_with_featherless

                logger.info("Calling Featherless DAX fallback for measure '%s'...", metric_name or dax[:30])
                featherless_sql = translate_with_featherless(dax, metric_name or "unnamed")
                if featherless_sql:
                    sql_upper = featherless_sql.upper()
                    forbidden = (
                        " SELECT ",
                        "(SELECT",
                        " FROM ",
                        " JOIN ",
                        " WITH ",
                        " DROP ",
                        " DELETE ",
                        " TRUNCATE ",
                        " INSERT ",
                        " UPDATE ",
                        " ALTER ",
                        ";",
                    )
                    padded = f" {sql_upper} "
                    if any(token in padded for token in forbidden):
                        logger.warning(
                            "Featherless DAX translation rejected (forbidden SQL tokens) for metric '%s': %s",
                            metric_name,
                            featherless_sql[:120],
                        )
                    else:
                        logger.info("Featherless translation accepted for '%s' (tier: 5)", metric_name)
                        return DAXTranslationResult(featherless_sql, 5, dax)
            except Exception as exc:
                err_msg = str(exc)
                logger.warning("Featherless individual translation fallback failed: %s", err_msg)
                if "insufficient_quota" in err_msg or "429" in err_msg or "403" in err_msg:
                    DAXTranslator._llm_circuit_breaker = True

        # Tier 5: LLM Fallback - Use Gemini for genuinely complex expressions
        if not getattr(DAXTranslator, "_llm_circuit_breaker", False):
            try:
                from semabridge.converter.gemini_dax_translator import get_gemini_translator
                
                translator = get_gemini_translator()
                if not translator.api_key:
                    logger.debug("LLM API key not configured")
                    return None
                
                # Attempt LLM translation
                llm_result = translator.translate(
                    dax=dax,
                    table_alias=table_alias,
                    dataset_name=dataset_name,
                    metric_name=metric_name
                )
                
                # Only use LLM result if:
                # 1. Translation succeeded (is_valid=True)
                # 2. Confidence is acceptable (>= 0.55)
                if llm_result.is_valid and llm_result.sql and llm_result.confidence >= 0.55:
                    logger.info(
                        f"LLM translation accepted for '{metric_name}' "
                        f"(confidence: {llm_result.confidence:.2f}, tier: 5)"
                    )
                    return DAXTranslationResult(llm_result.sql, 5, dax)
                elif llm_result.sql and llm_result.confidence > 0.4:
                    # Low confidence - log but don't use
                    logger.warning(
                        f"LLM translation low confidence for '{metric_name}': "
                        f"confidence={llm_result.confidence:.2f}. Falling back to None. "
                        f"SQL was: {llm_result.sql[:100]}..."
                    )
                    return None
                else:
                    logger.debug(
                        f"LLM translation declined for '{metric_name}': "
                        f"confidence={llm_result.confidence:.2f}. Error: {llm_result.error}"
                    )
                    return None
                    
            except ImportError:
                logger.debug("LLM translator module not available")
                return None
            except Exception as e:
                err_msg = str(e)
                logger.warning(f"Unexpected error in LLM fallback: {err_msg}")
                if "rate limit" in err_msg.lower() or "429" in err_msg or "quota" in err_msg.lower():
                    DAXTranslator._llm_circuit_breaker = True
                return None
    
    def batch_translate_tier5(self,
                             metrics_list: List[Tuple[str, str, str, str]]) -> Dict[str, Optional[DAXTranslationResult]]:
        """
        Batch translate multiple metrics that failed Tier 1-4 using LLM (Tier 5).
        
        First separates simple metrics (which use rule-based translation) from complex ones
        (which need LLM). This dramatically reduces API calls by preventing simple metrics
        from being sent to Gemini.
        
        When there are complex metrics, batches them into groups to minimize API calls
        (batching 20+ metrics into a single request).
        
        Args:
            metrics_list: List of (metric_name, dax, table_alias, dataset_name) tuples
            
        Returns:
            Dict of metric_name -> DAXTranslationResult (or None if no LLM result)
        """
        if not metrics_list:
            return {}
        
        results = {}
        
        # CLASSIFICATION STEP: Separate simple from complex metrics
        simple_metrics = []
        complex_metrics = []
        simple_failed_for_llm = []
        
        for metric_name, dax, table_alias, dataset_name in metrics_list:
            if is_simple_metric(dax):
                simple_metrics.append((metric_name, dax, table_alias, dataset_name))
            else:
                complex_metrics.append((metric_name, dax, table_alias, dataset_name))
        
        logger.info(
            f"🔄 Batch processing {len(metrics_list)} metrics:\n"
            f"   ├─ SIMPLE (rule-based): {len(simple_metrics)} metrics\n"
            f"   └─ COMPLEX (LLM): {len(complex_metrics)} metrics"
        )
        
        # RULE-BASED TRANSLATION: Process simple metrics without API calls
        simple_api_calls = 0
        for metric_name, dax, table_alias, dataset_name in simple_metrics:
            sql = rule_based_translation(dax, table_alias)
            if sql:
                results[metric_name] = DAXTranslationResult(sql, 4, dax)
                logger.debug(f"   ✓ [{metric_name}] Rule-based translation: {sql[:60]}...")
            else:
                # IMPORTANT: do not drop simple metrics when deterministic rules fail.
                # Escalate them to Tier-5 LLM fallback.
                simple_failed_for_llm.append((metric_name, dax, table_alias, dataset_name))
                logger.debug(
                    f"   ⚠ [{metric_name}] Rule-based translation failed; escalating to LLM"
                )
        
        llm_candidates = complex_metrics + simple_failed_for_llm
        
        # ─────────────────────────────────────────────────────────
        # PRIORITY 0: OPENAI BATCH
        # ─────────────────────────────────────────────────────────
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_translated = {}
        import json
        
        if openai_api_key and llm_candidates:
            try:
                from openai import OpenAI
                model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
                client = OpenAI(api_key=openai_api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
                
                # Chunk candidates into batches of 20
                chunk_size = 20
                for start in range(0, len(llm_candidates), chunk_size):
                    chunk = llm_candidates[start:start + chunk_size]
                    logger.info(f"🤖 Calling OpenAI API for batch of {len(chunk)} measures...")
                    
                    # Build batch prompt
                    metric_lines = []
                    for metric_name, dax, alias, dataset in chunk:
                        dax_clean = " ".join(dax.split())
                        metric_lines.append(
                            f'{{"name":"{metric_name}","dataset":"{dataset}","table_alias":"{alias}","dax":"{dax_clean}"}}'
                        )
                    
                    prompt = (
                        "Dialect: Snowflake Semantic View METRICS clause\n"
                        "Rules:\n"
                        "- Return ONLY valid JSON object mapping metric name to SQL expression.\n"
                        "- No markdown, no extra keys, no prose.\n"
                        "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
                        "- Do not nest aggregate functions like SUM(MAX(...)).\n"
                        "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
                        "- Quote identifiers only when needed as ALIAS.\"COLUMN\"; uppercase Snowflake column names.\n"
                        "- If a pattern is impossible in a metric expression, return CAST(NULL AS DOUBLE).\n"
                        "Metrics:\n"
                        + "\n".join(metric_lines)
                    )
                    
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. "
                                    "Return JSON only mapping measure names to translated SQL expressions."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                        max_tokens=int(os.getenv("OPENAI_DAX_BATCH_MAX_TOKENS", "2200")),
                        timeout=float(os.getenv("OPENAI_DAX_TIMEOUT", "30")),
                    )
                    
                    payload = (response.choices[0].message.content or "").strip()
                    logger.info(f"✅ OpenAI batch response received for {len(chunk)} measures")
                    
                    # Parse JSON payload
                    if payload.startswith("```"):
                        payload = re.sub(r"^```(?:json|sql|python|.*?)\n", "", payload, flags=re.IGNORECASE)
                        payload = re.sub(r"\n```$", "", payload, flags=re.IGNORECASE)
                        payload = payload.strip()
                    payload = re.sub(r"^json\s*", "", payload, flags=re.IGNORECASE)
                    
                    parsed = {}
                    try:
                        parsed = json.loads(payload)
                    except Exception:
                        pass
                    
                    if isinstance(parsed, dict):
                        for metric_name, dax, alias, dataset in chunk:
                            sql = parsed.get(metric_name)
                            if sql:
                                sql = sql.strip()
                                if sql.startswith("```"):
                                    sql = re.sub(r"^```(?:sql|python|.*?)\n", "", sql, flags=re.IGNORECASE)
                                    sql = re.sub(r"\n```$", "", sql, flags=re.IGNORECASE)
                                    sql = sql.strip()
                                
                                # Safety validation
                                sql_upper = sql.upper()
                                forbidden = (
                                    " SELECT ", "(SELECT", " FROM ", " JOIN ", " WITH ", " OVER ",
                                    " DROP ", " DELETE ", " TRUNCATE ", " INSERT ", " UPDATE ", " ALTER ", ";"
                                )
                                padded = f" {sql_upper} "
                                if any(token in padded for token in forbidden):
                                    logger.warning(
                                        "OpenAI batch translation rejected (forbidden SQL tokens) for metric '%s': %s",
                                        metric_name,
                                        sql[:120],
                                    )
                                else:
                                    logger.info(f"✓ [{metric_name}]: OpenAI translation")
                                    results[metric_name] = DAXTranslationResult(sql, 5, dax)
                                    openai_translated[metric_name] = sql
                
                # Remove successfully translated candidates so they aren't processed by Gemini
                llm_candidates = [c for c in llm_candidates if c[0] not in openai_translated]
                
            except Exception as exc:
                logger.error(f"OpenAI batch translation failed: {exc}")
        
        # LLM TRANSLATION: Only send remaining complex metrics to Gemini
        if llm_candidates:
            try:
                from semabridge.converter.gemini_dax_translator import get_gemini_translator
                
                translator = get_gemini_translator()
                if not translator.api_key:
                    logger.debug("LLM API key not configured for batch translation")
                    for metric_name, _, _, _ in llm_candidates:
                        if metric_name not in results:
                            results[metric_name] = None
                    return results
                
                # Prepare batch for Gemini translator (only complex metrics)
                # Format: (metric_name, dax, table_alias, dataset_name, None)
                batch = [
                    (metric_name, dax, table_alias, dataset_name, None)
                    for metric_name, dax, table_alias, dataset_name in llm_candidates
                ]
                
                logger.info(
                    f"🔄 Batch translating {len(batch)} COMPLEX metrics via Tier 5 LLM "
                    f"(expected API calls: {(len(batch) + 19) // 20}) - "
                    f"API CALL REDUCTION: {len(simple_metrics) - len(simple_failed_for_llm) + len(openai_translated)} / {len(metrics_list)} metrics skipped LLM"
                )
                
                # Call batch translation
                batch_result = translator.translate_batch(batch, batch_size=20)
                simple_api_calls = batch_result.api_calls
                
                # Process results - convert to DAXTranslationResult with confidence filtering
                for metric_name, gemini_result in batch_result.results.items():
                    if gemini_result.is_valid and gemini_result.sql and gemini_result.confidence >= 0.55:
                        # High confidence - use result
                        results[metric_name] = DAXTranslationResult(gemini_result.sql, 5, 
                                                                    next((dax for name, dax, _, _ in llm_candidates if name == metric_name), ""))
                        logger.debug(f"   ✓ [{metric_name}] LLM translated (conf: {gemini_result.confidence:.2f})")
                    elif gemini_result.sql and gemini_result.confidence > 0.4:
                        # Low confidence - log warning but don't use
                        logger.warning(
                            f"   ⚠️ [{metric_name}] Low confidence: {gemini_result.confidence:.2f}"
                        )
                        results[metric_name] = None
                    else:
                        # Failed translation
                        logger.debug(f"   ❌ [{metric_name}] Could not translate: {gemini_result.error}")
                        results[metric_name] = None
                
                # Log summary with classification insight
                successful = sum(1 for r in results.values() if r is not None)
                if llm_candidates:
                    api_call_reduction = (simple_api_calls / len(metrics_list)) * 100
                    quota_reduction = ((len(simple_metrics) - len(simple_failed_for_llm) + len(openai_translated)) / len(metrics_list)) * 100
                else:
                    api_call_reduction = 0
                    quota_reduction = 100
                
                logger.info(
                    f"✅ Batch translation complete:\n"
                    f"   ├─ Total metrics: {len(metrics_list)}\n"
                    f"   ├─ Simple (no API calls): {len(simple_metrics) - len(simple_failed_for_llm)} ({(len(simple_metrics) - len(simple_failed_for_llm)) / len(metrics_list) * 100:.0f}%)\n"
                    f"   ├─ LLM candidates: {len(llm_candidates)} ({len(llm_candidates) / len(metrics_list) * 100:.0f}%)\n"
                    f"   ├─ API calls: {simple_api_calls} (vs {len(metrics_list)} per-metric)\n"
                    f"   ├─ Successful: {successful}/{len(metrics_list)}\n"
                    f"   └─ QUOTA REDUCTION: {quota_reduction:.0f}% metrics avoided LLM calls"
                )
                
                return results
                
            except ImportError:
                logger.debug("Batch LLM translator not available")
                for metric_name, _, _, _ in llm_candidates:
                    if metric_name not in results:
                        results[metric_name] = None
                return results
            except Exception as e:
                logger.error(f"Unexpected error in batch Tier 5 translation: {str(e)}")
                for metric_name, _, _, _ in llm_candidates:
                    if metric_name not in results:
                        results[metric_name] = None
                return results
        else:
            # All metrics were simple or translated by OpenAI, no Gemini needed
            successful = sum(1 for r in results.values() if r is not None)
            logger.info(
                f"✅ Batch translation complete:\n"
                f"   ├─ Total metrics: {len(metrics_list)}\n"
                f"   ├─ Successful: {successful}/{len(metrics_list)}"
            )
            return results
    
    def _try_tier1(self, dax: str, table_alias: str) -> Optional[str]:
        """Attempt Tier 1 translation."""
        match = self._TIER1_PATTERN.match(dax)
        if not match:
            return None
        
        func = match.group(1).upper()
        # Group 2 has Table[Column] format col name, Group 3 has [Column] format
        col_name = match.group(2) or match.group(3)
        
        # Map DAX function to SQL function
        func_map = self._get_config_value("function_mappings", {
            "SUM": "SUM",
            "AVERAGE": "AVG",
            "MIN": "MIN",
            "MAX": "MAX",
            "COUNT": "COUNT",
            "DISTINCTCOUNT": "COUNT(DISTINCT {col})"
        })
        
        col_ref = f"{table_alias}.{self._quote(col_name)}"
        sql_template = func_map.get(func)
        
        if not sql_template:
            return None
            
        if "{col}" in sql_template:
            return sql_template.format(col=col_ref)
        else:
            return f"{sql_template}({col_ref})"
    
    def _try_branching(self, dax: str, metrics: List[Any]) -> Optional[str]:
        """
        Attempt to resolve references to other measures.
        Handles simple cases:
        1. Single measure ref: [Measure]
        2. Simple arithmetic: [A] + [B]
        3. DIVIDE: DIVIDE([A], [B])
        """
        
        if not dax or not metrics:
            return None

        metric_index = {
            str(getattr(m, "unique_name", "")).casefold(): m
            for m in metrics
            if getattr(m, "unique_name", None)
        }
        cache: Dict[str, str] = {}

        def split_args(args_text: str) -> List[str]:
            args: List[str] = []
            current: List[str] = []
            depth = 0
            for ch in args_text:
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth > 0:
                    depth -= 1
                elif ch == "," and depth == 0:
                    args.append("".join(current).strip())
                    current = []
                    continue
                current.append(ch)
            tail = "".join(current).strip()
            if tail:
                args.append(tail)
            return args

        def extract_measure_refs(expr: str) -> List[str]:
            refs: List[str] = []
            for match in self._MEASURE_REF_PATTERN.finditer(expr or ""):
                start = match.start()
                if start > 0 and expr[start - 1] == "'":
                    # Skip table-qualified column refs like 'Table'[Column]
                    continue
                refs.append(match.group(1).strip())
            return refs

        def replace_measure_refs(expr: str, visiting: set[str]) -> Optional[str]:
            replaced = expr
            refs = extract_measure_refs(expr)
            for ref in refs:
                dep_sql = resolve_measure_sql(ref, visiting)
                if not dep_sql:
                    return None
                token_pattern = re.compile(rf"\[{re.escape(ref)}\]", re.IGNORECASE)
                replaced = token_pattern.sub(f"({dep_sql})", replaced)
            return replaced

        def resolve_measure_sql(name: str, visiting: set[str]) -> Optional[str]:
            key = (name or "").strip().casefold()
            if not key:
                return None
            if key in cache:
                return cache[key]
            if key in visiting:
                logger.warning(f"Circular measure dependency detected for '{name}'")
                return None

            metric = metric_index.get(key)
            if not metric:
                return None

            existing_sql = (getattr(metric, "sql_expression", None) or "").strip()
            if existing_sql:
                cache[key] = existing_sql
                return existing_sql

            metric_expr = (getattr(metric, "expression", None) or "").strip()
            if not metric_expr:
                return None

            visiting.add(key)
            try:
                alias = to_alias(getattr(metric, "dataset", "") or "fact")

                tier1 = self._try_tier1(metric_expr, alias)
                if tier1:
                    cache[key] = tier1
                    return tier1

                resolved = resolve_expression(metric_expr, alias, visiting)
                if resolved:
                    cache[key] = resolved
                    return resolved

                return None
            finally:
                visiting.remove(key)

        def resolve_expression(expr: str, table_alias: str, visiting: set[str]) -> Optional[str]:
            clean_expr = (expr or "").strip()
            if not clean_expr:
                return None

            # Handle DIVIDE(A, B [, alt]) with safe division semantics.
            if clean_expr.upper().startswith("DIVIDE(") and clean_expr.endswith(")"):
                inner = clean_expr[7:-1]
                args = split_args(inner)
                if len(args) >= 2:
                    numerator_sql = replace_measure_refs(args[0], visiting)
                    denominator_sql = replace_measure_refs(args[1], visiting)
                    if not numerator_sql or not denominator_sql:
                        return None
                    alt = args[2].strip() if len(args) >= 3 and args[2].strip() else "0"
                    return (
                        f"COALESCE(({numerator_sql}) / NULLIF(({denominator_sql}), 0), {alt})"
                    )

            # Handle TOTALYTD(Expression, Date) using metadata injection.
            if False and clean_expr.upper().startswith("TOTALYTD(") and clean_expr.endswith(")"):
                inner = clean_expr[9:-1]
                args = split_args(inner)
                if args:
                    inner_expr = args[0].strip()
                    inner_sql = (
                        self._try_tier1(inner_expr, table_alias)
                        or replace_measure_refs(inner_expr, visiting)
                        or self._try_dependency_translation(
                            inner_expr,
                            table_alias,
                            "",
                            metrics,
                            visiting=set(),
                        )
                    )
                    if inner_sql:
                        inner_sql = self._strip_outer_parens(inner_sql)
                        parsed_agg = self._parse_sql_aggregation(inner_sql)

                        # Snowflake semantic metric windows are safest when PARTITION/ORDER
                        # columns come from the same metric entity alias.
                        year_ref = f"{table_alias}.YEAR"
                        period_ref = f"{table_alias}.PERIOD"
                        if parsed_agg:
                            agg_func, value_expr = parsed_agg
                            if agg_func == "COUNT_DISTINCT":
                                return (
                                    f"COUNT(DISTINCT {value_expr}) OVER "
                                    f"(PARTITION BY {year_ref} ORDER BY {period_ref})"
                                )
                            return (
                                f"{agg_func}({value_expr}) OVER "
                                f"(PARTITION BY {year_ref} ORDER BY {period_ref})"
                            )

                        return (
                            f"SUM({inner_sql}) OVER (PARTITION BY {year_ref} "
                            f"ORDER BY {period_ref})"
                        )

            # Generic arithmetic replacement for [A] +/-/*// [B].
            replaced = replace_measure_refs(clean_expr, visiting)
            if replaced and replaced != clean_expr:
                # Do not emit partially-rewritten DAX function syntax as SQL.
                # If DAX-only keywords remain, force fallback handling instead.
                dax_only_keywords = tuple(self._get_config_value("dax_only_keywords", [
                    "CALCULATE",
                    "SAMEPERIODLASTYEAR",
                    "DATEADD",
                    "DATESYTD",
                    "TOTALYTD",
                    "IF(",
                    "BLANK(",
                ]))
                upper_replaced = replaced.upper()
                if any(keyword in upper_replaced for keyword in dax_only_keywords):
                    return None
                if "[" in replaced or "]" in replaced:
                    return None
                return replaced

            return None

        return resolve_expression(dax, "fact", set())
    
    def _quote(self, identifier: str) -> str:
        """Quote SQL identifier, replacing spaces and illegal chars."""
        clean_id = sanitize_column(identifier, force_uppercase=True)
        return f'"{clean_id}"'
    
    def analyze_complexity(self, dax: str, model: Any = None) -> dict:
        """
        Analyze DAX expression complexity and return metadata for sync decisions.
        
        Returns:
            dict with keys:
                - tier: int (1-4)
                - requires_time_intel: bool
                - group_by_dimensions: list[str]
                - depends_on_measures: list[str]
                - sync_enabled: bool
                - failure_reason: Optional[str]
        """
        if not dax:
            return {
                "tier": 0,
                "requires_time_intel": False,
                "group_by_dimensions": [],
                "depends_on_measures": [],
                "sync_enabled": False,
                "failure_reason": "Empty expression"
            }
        
        clean_dax = dax.strip()
        upper_dax = clean_dax.upper()
        
        result = {
            "tier": 1,
            "requires_time_intel": False,
            "group_by_dimensions": [],
            "depends_on_measures": [],
            "sync_enabled": True,
            "failure_reason": None
        }
        
        # Extract measure dependencies [MeasureName]
        measure_refs = re.findall(r"\[([^\]]+)\]", clean_dax)
        # Filter out column refs (those from 'Table'[Column] patterns)
        table_col_pattern = re.findall(r"'[\w\s]+'\[([^\]]+)\]", clean_dax)
        pure_measure_refs = [m for m in measure_refs if m not in table_col_pattern]
        result["depends_on_measures"] = list(set(pure_measure_refs))
        
        # Check for unsupported patterns first (Tier 4)
        for pattern in self.UNSUPPORTED_PATTERNS:
            if re.search(pattern, upper_dax, re.IGNORECASE):
                result["tier"] = 4
                result["sync_enabled"] = False
                result["failure_reason"] = f"Unsupported DAX pattern detected"
                return result
        
        # Check for Time Intelligence functions (Tier 3)
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                result["tier"] = 3
                result["requires_time_intel"] = True
                # Time Intelligence requires Date dimension for proper evaluation
                date_alias = self._get_date_alias() if hasattr(self, "_get_date_alias") else "CALENDAR"
                result["group_by_dimensions"] = [f"'{date_alias}'[Date]"]
                
                # Check if we can translate this specific pattern
                can_translate = False
                for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
                    if pattern.search(clean_dax):
                        can_translate = True
                        break
                
                if not can_translate:
                    # Complex Time Intelligence we can't translate
                    result["sync_enabled"] = False
                    result["failure_reason"] = f"Complex Time Intelligence ({func}) not automatically translatable"
                break
        
        # Check for simple CALCULATE without complex filters (still Tier 3)
        if "CALCULATE" in upper_dax and result["tier"] < 3:
            result["tier"] = 3
            # Simple CALCULATE might still be translatable if it's just wrapping an aggregation
            if not re.search(r"CALCULATE\s*\([^)]+,\s*\w+\s*\(", upper_dax):
                # CALCULATE with simple filter - might work
                pass
            else:
                result["sync_enabled"] = False
                result["failure_reason"] = "CALCULATE with complex filter not automatically translatable"
        
        # Check for arithmetic/branching (Tier 2)
        if result["tier"] == 1 and result["depends_on_measures"]:
            result["tier"] = 2
        
        return result
    
    def try_tier3_time_intel(self, dax, table_alias, date_alias: str = None):
        if not date_alias:
            date_alias = self._get_date_alias()
        """
        Attempt Tier 3 Time Intelligence translation using Snowflake window functions.
        
        Converts DAX Time Intelligence to SQL window functions:
        - TOTALYTD -> Cumulative sum partitioned by Year
        - TOTALMTD -> Cumulative sum partitioned by Year, Month
        - TOTALQTD -> Cumulative sum partitioned by Year, Quarter
        
        Args:
            dax: The DAX expression
            table_alias: SQL alias for the measure's source table
            date_alias: SQL alias for the Date dimension table
            
        Returns:
            SQL expression or None if translation not possible
        """
        for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
            match = pattern.search(dax)
            if match:
                agg_func = match.group(1).upper()
                col_name = match.group(2) or match.group(3)
                # Groups 4 and 5 are the date table and column
                
                col_ref = f"{table_alias}.{self._quote(col_name)}"
                sql_agg = self._get_config_value("aggregation_mappings", {
                    "SUM": "SUM",
                    "AVERAGE": "AVG",
                    "COUNT": "COUNT",
                    "MIN": "MIN",
                    "MAX": "MAX"
                }).get(agg_func, "SUM")
                
                # Generate Snowflake window function for period-to-date
                if period_type == "YTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "MTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."MONTH"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "QTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."QUARTER"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
        
        return None
    
    def get_required_dimensions(self, dax: str) -> list[str]:
        """
        Extract dimension columns that should be included in GROUP BY for proper measure evaluation.
        
        Analyzes the DAX expression to determine what dimensions are needed for
        context-dependent calculations.
        
        Returns:
            List of dimension column references (e.g., ["'Date'[Year]", "'Region'[Name]"])
        """
        dimensions = []
        upper_dax = dax.upper() if dax else ""
        
        # Time Intelligence always needs date context
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                date_alias = self._get_date_alias() if hasattr(self, "_get_date_alias") else "CALENDAR"
                dimensions.append(f"'{date_alias}'[Date]")
                break
        
        # Extract explicit table[column] references that might indicate required dimensions
        # Pattern: 'TableName'[ColumnName] 
        table_col_refs = re.findall(r"'([\w\s]+)'\[(\w+)\]", dax or "")
        for table, col in table_col_refs:
            dim_ref = f"'{table}'[{col}]"
            if dim_ref not in dimensions:
                # Only add if it looks like a dimension (not a measure column)
                col_upper = col.upper()
                measure_patterns = self._get_config_value("measure_column_patterns", ["AMOUNT", "SALES", "REVENUE", "PRICE", "COST", "QTY"])
                if not any(m in col_upper for m in measure_patterns):
                    dimensions.append(dim_ref)
        
        return dimensions
