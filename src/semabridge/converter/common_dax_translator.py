"""
Common DAX-to-SQL LLM translator.

This module owns the connector-neutral LLM fallback path. Connector-specific
publishers pass the target SQL dialect and schema context; the provider layer
handles Gemini/Groq fallback without duplicating DAX prompt code in every
connector.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from uuid import uuid4
from typing import Protocol

from semabridge.utils.logger import get_logger

try:
    from dotenv import load_dotenv

    for _env_path in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[3] / ".env",
    ):
        if _env_path.exists():
            load_dotenv(_env_path, override=False)
            break
except Exception:
    pass

logger = get_logger(__name__)


class SQLDialect(str, Enum):
    """Supported target SQL dialects for common DAX translation."""

    DATABRICKS = "databricks"
    SNOWFLAKE = "snowflake"
    FABRIC = "fabric"


class LLMProviderName(str, Enum):
    """Supported LLM providers for DAX translation."""

    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    GROQ = "groq"


@dataclass(frozen=True)
class LLMProviderConfig:
    """Runtime config for a single provider attempt."""

    name: LLMProviderName
    model: str
    api_key_env: str


@dataclass
class CommonDAXTranslationResult:
    """Result of a common DAX translation attempt."""

    sql: str = ""
    is_valid: bool = False
    provider: str = ""
    model: str = ""
    cached: bool = False
    fallback_used: bool = False
    error: str = ""
    attempted_providers: list[str] = field(default_factory=list)


class LLMProvider(Protocol):
    """Interface implemented by provider adapters."""

    config: LLMProviderConfig

    def generate(self, prompt: str, timeout_seconds: int) -> str:
        """Return raw model text for the prompt."""


class GeminiProvider:
    """Google Gemini provider adapter."""

    def __init__(self, model: str | None = None, api_key_env: str = "GEMINI_API_KEY") -> None:
        self.config = LLMProviderConfig(
            name=LLMProviderName.GEMINI,
            model=model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            api_key_env=api_key_env,
        )

    def generate(self, prompt: str, timeout_seconds: int) -> str:
        api_key = os.getenv(self.config.api_key_env) or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(f"{self.config.api_key_env} or GOOGLE_API_KEY is not configured")

        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError("google-generativeai package is not installed") from exc

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(self.config.model)
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.1, "max_output_tokens": 1024},
            request_options={"timeout": timeout_seconds},
        )
        text = getattr(response, "text", "") or ""
        if not text.strip():
            raise RuntimeError("Gemini returned an empty response")
        return text


class DeepSeekProvider:
    """DeepSeek OpenAI-compatible chat-completions provider adapter."""

    def __init__(self, model: str | None = None, api_key_env: str = "DEEPSEEK_API_KEY") -> None:
        self.config = LLMProviderConfig(
            name=LLMProviderName.DEEPSEEK,
            model=model or os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner"),
            api_key_env=api_key_env,
        )
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

    def generate(self, prompt: str, timeout_seconds: int) -> str:
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.config.api_key_env} is not configured")

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx package is not installed") from exc

        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a semantic-model compiler. Return only one valid "
                            "Databricks SQL scalar expression. No explanations."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 800,
                "stream": False,
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"].get("content") or ""
        if not text.strip():
            raise RuntimeError("DeepSeek returned an empty response")
        return text


class GroqProvider:
    """Groq chat-completions provider adapter."""

    def __init__(self, model: str | None = None, api_key_env: str = "GROQ_API_KEY") -> None:
        self.config = LLMProviderConfig(
            name=LLMProviderName.GROQ,
            model=model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            api_key_env=api_key_env,
        )

    def generate(self, prompt: str, timeout_seconds: int) -> str:
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.config.api_key_env} is not configured")

        try:
            from groq import Groq
        except ImportError as exc:
            raise RuntimeError("groq package is not installed") from exc

        client = Groq(api_key=api_key, timeout=timeout_seconds)
        response = client.chat.completions.create(
            model=self.config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
        )
        text = response.choices[0].message.content or ""
        if not text.strip():
            raise RuntimeError("Groq returned an empty response")
        return text


class CommonDAXTranslator:
    """Translate DAX expressions with a shared prompt and provider fallback."""

    DANGEROUS_SQL_PATTERNS = [
        r"\bDROP\b",
        r"\bDELETE\b",
        r"\bTRUNCATE\b",
        r"\bALTER\s+TABLE\b",
        r"\bCREATE\s+TABLE\b",
        r"\bINSERT\s+INTO\b",
        r"\bUPDATE\b",
        r"\bEXEC\s*\(",
        r";\s*--",
        r"/\*",
        r"\bUNION\s+SELECT\b",
    ]

    def __init__(
        self,
        *,
        dialect: SQLDialect | str = SQLDialect.DATABRICKS,
        provider_order: list[str] | None = None,
        timeout_seconds: int = 20,
        cache_enabled: bool = True,
        cache_file: str | Path = ".llm_dax_cache.json",
        raw_response_dir: str | Path = "output/debug/llm_dax_raw",
        fallback_to_placeholder: bool = True,
        placeholder_sql: str = "0",
        providers: list[LLMProvider] | None = None,
    ) -> None:
        self.dialect = dialect if isinstance(dialect, SQLDialect) else SQLDialect(str(dialect).lower())
        self.timeout_seconds = max(1, int(timeout_seconds or 20))
        self.cache_enabled = bool(cache_enabled)
        self.cache_file = Path(cache_file)
        self.raw_response_dir = Path(raw_response_dir)
        self.fallback_to_placeholder = bool(fallback_to_placeholder)
        self.placeholder_sql = placeholder_sql
        self.cache: dict[str, dict] = self._load_cache() if self.cache_enabled else {}
        self.providers = providers or self._build_providers(provider_order)

    def translate(
        self,
        *,
        dax: str,
        metric_name: str,
        dataset_name: str,
        table_alias: str,
        schema_context: dict[str, list[str]] | None = None,
    ) -> CommonDAXTranslationResult:
        """Translate one DAX expression with cached multi-provider fallback."""
        dax_expr = str(dax or "").strip()
        if not dax_expr:
            return CommonDAXTranslationResult(error="Empty DAX expression")

        cache_key = self._cache_key(dax_expr, metric_name, dataset_name, table_alias, schema_context)
        if self.cache_enabled and cache_key in self.cache:
            cached = self.cache[cache_key]
            cached_sql = str(cached.get("sql") or "").strip()
            cached_valid, _ = self._validate_sql(cached_sql)
            if (
                cached_valid
                and cached_sql != self.placeholder_sql
                and not bool(cached.get("fallback_used"))
            ):
                return CommonDAXTranslationResult(
                    sql=cached_sql,
                    is_valid=True,
                    provider=str(cached.get("provider") or ""),
                    model=str(cached.get("model") or ""),
                    cached=True,
                )
            self.cache.pop(cache_key, None)

        prompt = self._build_prompt(
            dax=dax_expr,
            metric_name=metric_name,
            dataset_name=dataset_name,
            table_alias=table_alias,
            schema_context=schema_context or {},
        )

        attempted: list[str] = []
        errors: list[str] = []
        for provider in self.providers:
            provider_name = provider.config.name.value
            attempted.append(provider_name)
            try:
                logger.info(
                    "Trying %s for DAX metric '%s' (%s dialect)",
                    provider_name,
                    metric_name,
                    self.dialect.value,
                )
                raw = provider.generate(prompt, self.timeout_seconds)
                self._save_raw_response(provider_name, metric_name, raw, "initial")
                sql = self._clean_sql(raw)
                valid, validation_error = self._validate_sql(sql)
                if not valid:
                    repair_prompt = self._build_repair_prompt(
                        dax=dax_expr,
                        invalid_sql=sql,
                        validation_error=validation_error,
                        metric_name=metric_name,
                        dataset_name=dataset_name,
                        table_alias=table_alias,
                        schema_context=schema_context or {},
                    )
                    repaired_raw = provider.generate(repair_prompt, self.timeout_seconds)
                    self._save_raw_response(provider_name, metric_name, repaired_raw, "repair")
                    repaired_sql = self._clean_sql(repaired_raw)
                    repaired_valid, repaired_error = self._validate_sql(repaired_sql)
                    if repaired_valid:
                        sql = repaired_sql
                        valid = True
                    else:
                        validation_error = f"{validation_error}; repair failed: {repaired_error}"

                if not valid:
                    errors.append(f"{provider_name}: {validation_error}")
                    logger.warning(
                        "%s generated invalid SQL for metric '%s': %s",
                        provider_name,
                        metric_name,
                        validation_error,
                    )
                    continue

                result = CommonDAXTranslationResult(
                    sql=sql,
                    is_valid=True,
                    provider=provider_name,
                    model=provider.config.model,
                    attempted_providers=attempted,
                )
                self._cache_result(cache_key, result)
                return result
            except Exception as exc:
                message = f"{provider_name}: {exc}"
                errors.append(message)
                logger.warning("LLM provider failed for metric '%s': %s", metric_name, message)

        if self.fallback_to_placeholder:
            result = CommonDAXTranslationResult(
                sql=self.placeholder_sql,
                is_valid=True,
                fallback_used=True,
                error="; ".join(errors),
                attempted_providers=attempted,
            )
            return result

        return CommonDAXTranslationResult(
            is_valid=False,
            error="; ".join(errors) or "No LLM providers configured",
            attempted_providers=attempted,
        )

    def _build_providers(self, provider_order: list[str] | None) -> list[LLMProvider]:
        configured = provider_order or self._provider_order_from_env()
        providers: list[LLMProvider] = []
        seen: set[str] = set()
        for raw_name in configured:
            name = str(raw_name or "").strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            if name == LLMProviderName.DEEPSEEK.value:
                providers.append(DeepSeekProvider())
            elif name == LLMProviderName.GEMINI.value:
                providers.append(GeminiProvider())
            elif name == LLMProviderName.GROQ.value:
                providers.append(GroqProvider())
            else:
                logger.warning("Ignoring unsupported DAX LLM provider '%s'", raw_name)
        return providers

    def _provider_order_from_env(self) -> list[str]:
        order = os.getenv("DAX_LLM_PROVIDER_ORDER") or os.getenv("LLM_PROVIDER_ORDER")
        if order:
            return [item.strip() for item in order.split(",") if item.strip()]
        fallback = ["deepseek", "gemini", "groq"]
        if os.getenv("USE_GEMINI", "true").lower() not in {"1", "true", "yes", "on"}:
            fallback = ["deepseek", "groq", "gemini"]
        return fallback

    def _build_prompt(
        self,
        *,
        dax: str,
        metric_name: str,
        dataset_name: str,
        table_alias: str,
        schema_context: dict[str, list[str]],
    ) -> str:
        schema_lines: list[str] = []
        for table, columns in sorted(schema_context.items()):
            limited = [str(col) for col in columns[:20]]
            suffix = f", ... ({len(columns) - 20} more)" if len(columns) > 20 else ""
            schema_lines.append(f"- {table}: {', '.join(limited)}{suffix}")
        schema_text = "\n".join(schema_lines) if schema_lines else "- No schema context supplied"

        dialect_name = self.dialect.value.upper()
        quote_rule = {
            SQLDialect.DATABRICKS: "Use Databricks backtick identifiers like `table_alias`.`column_name`.",
            SQLDialect.SNOWFLAKE: "Use Snowflake-compatible identifiers; prefer unquoted uppercase unless quoting is required.",
            SQLDialect.FABRIC: "Use Fabric SQL-compatible identifiers.",
        }[self.dialect]

        metric_view_rules = ""
        if self.dialect == SQLDialect.DATABRICKS:
            metric_view_rules = """
Databricks metric-view constraints:
- Return a single metric expression, not a SELECT statement.
- Do not include FROM, JOIN, WHERE, GROUP BY, HAVING, or semicolons.
- Avoid subqueries and window functions for metric-view output.
- Avoid nested aggregates such as SUM(MAX(x)) or ANY_VALUE(MAX(x)).
- Use ANY_VALUE only for scalar/string expressions that are not already aggregate expressions.
- For TODAY(), prefer MAX(current_date()) in metric-view expressions.
"""

        return f"""You are a Snowflake/Databricks SQL expert. Translate DAX to a single SQL expression for {dialect_name}.

STRICT RULES:
- NEVER output the keyword VAR.
- NEVER output DAX keywords such as RETURN or CALCULATE.
- NEVER output a full SELECT ... FROM query. Output ONLY the expression that can go inside a METRICS clause.
- Example correct: SUM(CASE WHEN region = 'North' THEN amount ELSE 0 END)
- Example incorrect: SELECT SUM(CASE WHEN region = 'North' THEN amount ELSE 0 END) FROM table
- For time intelligence, use window functions only when the target metric-view context supports them.
- For USERELATIONSHIP, generate explicit join-aware SQL only if schema context supplies the needed aliases.
- For SUMX(FILTER(...), ...), prefer conditional aggregates such as SUM(CASE WHEN ... THEN ... ELSE 0 END).
- Return only the SQL expression, no extra text, no markdown.
- If translation cannot be completed safely, return EXACTLY: {self.placeholder_sql}

Target dialect: {dialect_name}
Metric name: {metric_name or "unknown"}
Dataset: {dataset_name or "unknown"}
Root table alias: {table_alias or "source"}

Identifier rule:
- {quote_rule}

Source schema context:
{schema_text}

Translation guidance:
- Use source schema columns when mapping DAX references.
- SUM, AVERAGE, COUNT, DISTINCTCOUNT, MIN, MAX map to their SQL aggregate equivalents.
- CALCULATE filters should become CASE WHEN predicates inside the aggregate.
- DIVIDE(num, den, alt) should become COALESCE((num) / NULLIF((den), 0), alt or 0) when num/den are already scalar; otherwise preserve aggregate-safe numerator and denominator.
- CONCATENATE should become a SQL string concatenation expression for the target dialect.
- BLANK() should become NULL.
- If DAX uses a filter context, preserve that logic in SQL rather than dropping it.
- Do not invent tables or columns outside the source schema context.
- Prefer {self.placeholder_sql} over malformed SQL. Never return CAST(NULL AS DOUBLE).
{metric_view_rules}
Examples:
DAX: SUM('Sales'[Revenue])
SQL: SUM(`sales`.`revenue`)

DAX: CALCULATE(SUM('Sales'[Revenue]), 'Sales'[Region] = "North")
SQL: SUM(CASE WHEN `sales`.`region` = 'North' THEN `sales`.`revenue` ELSE 0 END)

DAX: DIVIDE([Profit], [Revenue], 0)
SQL: COALESCE(SUM(`profit`) / NULLIF(SUM(`revenue`), 0), 0)

DAX expression:
{dax}

SQL expression:"""

    def _build_repair_prompt(
        self,
        *,
        dax: str,
        invalid_sql: str,
        validation_error: str,
        metric_name: str,
        dataset_name: str,
        table_alias: str,
        schema_context: dict[str, list[str]],
    ) -> str:
        base_prompt = self._build_prompt(
            dax=dax,
            metric_name=metric_name,
            dataset_name=dataset_name,
            table_alias=table_alias,
            schema_context=schema_context,
        )
        return f"""{base_prompt}

The previous output was invalid.
Validation error: {validation_error}
Invalid output: {invalid_sql}

Repair it now. Return ONLY the corrected scalar SQL expression.
If you cannot repair it safely, return EXACTLY: {self.placeholder_sql}
"""

    def _clean_sql(self, value: str) -> str:
        sql = str(value or "").strip()
        try:
            from semabridge.converter.dax_engine import sanitize_llm_sql

            sql = sanitize_llm_sql(sql)
        except Exception:
            fenced = re.search(r"```(?:sql)?\s*(.*?)\s*```", sql, flags=re.IGNORECASE | re.DOTALL)
            if fenced:
                sql = fenced.group(1).strip()
            sql = re.sub(r"(?m)^.*\bVAR\b.*$", "", sql, flags=re.IGNORECASE)
            if re.match(r"^\s*SELECT\s+", sql, re.IGNORECASE):
                match = re.search(r"SELECT\s+(.*?)\s+FROM\b", sql, re.IGNORECASE | re.DOTALL)
                if match:
                    sql = match.group(1).strip()
        sql = re.sub(r"(?is)^\s*sql\s*:\s*", "", sql).strip()
        sql = sql.rstrip(";").strip()
        return " ".join(sql.split())

    def _validate_sql(self, sql: str) -> tuple[bool, str]:
        if not sql:
            return False, "Empty SQL"
        if sql == self.placeholder_sql:
            return True, ""
        sql_upper = sql.upper()
        if sql_upper.startswith("SELECT"):
            return False, "Full SELECT statements are not valid metric expressions"
        if re.search(r"(?i)\b(VAR|RETURN|CALCULATE)\b", sql):
            return False, "SQL contains DAX keywords instead of only SQL"
        if sql_upper == "CAST(NULL AS DOUBLE)":
            return False, "Placeholder CAST(NULL AS DOUBLE) is not allowed"
        # Expression-only constraint: disallow query-level clauses.
        # NOTE: Snowflake metric expressions may legitimately contain window clauses
        # like `... OVER (ORDER BY ...)`, so we do NOT blanket-reject ORDER BY there.
        banned = ["SELECT", "FROM", "WITH", "JOIN", "WHERE", r"GROUP\s+BY", "HAVING"]
        if self.dialect != SQLDialect.SNOWFLAKE:
            banned.append(r"ORDER\s+BY")
        if re.search(r"(?i)\b(" + "|".join(banned) + r")\b", sql):
            return False, "SQL contains a query clause instead of only an expression"
        if sql.count("(") != sql.count(")"):
            return False, "Unbalanced parentheses"
        for pattern in self.DANGEROUS_SQL_PATTERNS:
            if re.search(pattern, sql, flags=re.IGNORECASE):
                return False, f"Dangerous SQL pattern detected: {pattern}"
        nested = re.search(
            r"(?is)\b(SUM|AVG|COUNT|MIN|MAX|ANY_VALUE)\s*\([^)]*\b(SUM|AVG|COUNT|MIN|MAX|ANY_VALUE)\s*\(",
            sql,
        )
        if nested:
            return False, "Nested aggregate functions are not allowed in metric expressions"
        try:
            import sqlglot
            from sqlglot import exp

            read_dialect = {
                SQLDialect.DATABRICKS: "databricks",
                SQLDialect.SNOWFLAKE: "snowflake",
                SQLDialect.FABRIC: "tsql",
            }.get(self.dialect, "databricks")
            parsed = sqlglot.parse_one(sql, read=read_dialect)
            if parsed.find(exp.Select) or parsed.find(exp.Subquery) or parsed.find(exp.CTE):
                return False, "SQL AST contains query constructs instead of only a scalar expression"
        except ImportError:
            pass
        except Exception as exc:
            return False, f"SQL parse failed: {exc}"
        if self.dialect == SQLDialect.DATABRICKS and re.search(
            r"(?i)\b(SUM|AVG|COUNT|MIN|MAX|ANY_VALUE|CASE|COALESCE|NULLIF|CONCAT|CURRENT_DATE|CURRENT_TIMESTAMP|CAST)\s*\(",
            sql,
        ):
            return True, ""
        if re.search(r"(?i)[A-Za-z_`\"']+", sql):
            return True, ""
        return False, "No recognizable SQL expression"

    def _cache_key(
        self,
        dax: str,
        metric_name: str,
        dataset_name: str,
        table_alias: str,
        schema_context: dict[str, list[str]] | None,
    ) -> str:
        payload = {
            "dialect": self.dialect.value,
            "dax": dax,
            "metric": metric_name,
            "dataset": dataset_name,
            "alias": table_alias,
            "schema": schema_context or {},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict[str, dict]:
        try:
            if not self.cache_file.exists():
                return {}
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            return {
                key: value
                for key, value in raw.items()
                if str(value.get("timestamp", "")) > cutoff
                and not bool(value.get("fallback_used"))
                and str(value.get("sql") or "").strip() != self.placeholder_sql
            }
        except Exception as exc:
            logger.warning("Failed to load common DAX LLM cache: %s", exc)
            return {}

    def _cache_result(self, cache_key: str, result: CommonDAXTranslationResult) -> None:
        if not self.cache_enabled or not cache_key or not result.sql or result.fallback_used:
            return
        self.cache[cache_key] = {
            "sql": result.sql,
            "is_valid": result.is_valid,
            "provider": result.provider,
            "model": result.model,
            "fallback_used": result.fallback_used,
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:
            self.cache_file.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save common DAX LLM cache: %s", exc)

    def _save_raw_response(
        self,
        provider_name: str,
        metric_name: str,
        raw_response: str,
        attempt_kind: str,
    ) -> None:
        """Persist raw LLM responses for debugging before cleanup/parsing."""
        try:
            safe_metric = re.sub(r"[^A-Za-z0-9_.-]+", "_", metric_name or "metric").strip("_")
            safe_metric = safe_metric[:80] or "metric"
            self.raw_response_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            path = self.raw_response_dir / (
                f"{stamp}_{safe_metric}_{provider_name}_{attempt_kind}_{uuid4().hex[:8]}.txt"
            )
            path.write_text(str(raw_response or ""), encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to save raw LLM response: %s", exc)
