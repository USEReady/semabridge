"""Multi-model DAX to SQL translator using LangChain with Featherless."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MultiModelDAXTranslator:
    """DAX translator with multiple LLM models and automatic failover."""

    def __init__(self) -> None:
        self.clients: Dict[str, Any] = {}
        self._initialize_clients()

    def _initialize_clients(self) -> None:
        """Initialize all model clients."""
        from dotenv import load_dotenv
        load_dotenv()
        
        # 1. Initialize Featherless DeepSeek-V4-Pro Client
        featherless_key = os.getenv("FEATHERLESS_API_KEY") or os.getenv("Feather-Api-Key")
        if featherless_key:
            try:
                from langchain_openai import ChatOpenAI
                self.clients["deepseek-v4"] = ChatOpenAI(
                    api_key=featherless_key,
                    model=os.getenv("FEATHERLESS_MODEL", "meta-llama/Llama-3.3-70B-Instruct"),
                    base_url=os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1"),
                    temperature=float(os.getenv("FEATHERLESS_TEMPERATURE", "0.1")),
                    max_tokens=int(os.getenv("FEATHERLESS_MAX_TOKENS", "500")),
                    timeout=float(os.getenv("FEATHERLESS_TIMEOUT", "60")),
                )
                logger.info("Initialized Featherless client")
            except Exception as e:
                logger.warning("Failed to initialize Featherless client: %s", e)

        # 2. Initialize Groq LangChain Client (if API key exists)
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            try:
                from langchain_groq import ChatGroq
                self.clients["groq"] = ChatGroq(
                    api_key=groq_key,
                    model=os.getenv("GROQ_DAX_MODEL", "mixtral-8x7b-32768"),
                    temperature=0.1,
                    max_tokens=500,
                    timeout=30.0,
                )
                logger.info("✅ Initialized groq via LangChain")
            except Exception as e:
                logger.warning(f"Failed to initialize groq via LangChain: {e}")

    def translate_with_failover(
        self,
        dax: str,
        metric_name: str,
        prompt: str,
        fallback_translator: Any = None,
        metric: Any = None,
        table_alias: str = "",
        dataset_col_lookup: Optional[Dict[str, set[str]]] = None,
    ) -> Optional[str]:
        """Try multiple models in sequence until one works."""
        # 1. Try Featherless DeepSeek-V4-Pro
        if "deepseek-v4" in self.clients:
            try:
                logger.info("Attempting translation for '%s' using Featherless...", metric_name)
                result = self._translate_with_deepseek(prompt, metric_name)
                if result:
                    logger.info("Featherless successfully translated '%s'", metric_name)
                    return result
            except Exception as e:
                err_msg = str(e)
                logger.warning("Featherless failed for '%s': %s", metric_name, err_msg)
                if "insufficient_quota" in err_msg or "403" in err_msg or "429" in err_msg or "upgrade_required" in err_msg:
                    raise Exception(err_msg)

        # 2. Try Fallback to OpenAI (existing logic)
        try:
            logger.info("Attempting translation for '%s' using OpenAI fallback...", metric_name)
            result = self._translate_with_openai(
                fallback_translator=fallback_translator,
                dax=dax,
                metric=metric,
                table_alias=table_alias,
                dataset_col_lookup=dataset_col_lookup or {},
            )
            if result:
                logger.info("OpenAI successfully translated '%s'", metric_name)
                return result
        except Exception as e:
            err_msg = str(e)
            logger.warning("OpenAI fallback failed for '%s': %s", metric_name, err_msg)
            if "insufficient_quota" in err_msg or "403" in err_msg or "429" in err_msg:
                raise Exception(err_msg)

        # 3. Try Fallback to Groq
        try:
            logger.info("Attempting translation for '%s' using Groq fallback...", metric_name)
            result = self._translate_with_groq(prompt, metric_name)
            if result:
                logger.info("Groq successfully translated '%s'", metric_name)
                return result
        except Exception as e:
            err_msg = str(e)
            logger.warning("Groq fallback failed for '%s': %s", metric_name, err_msg)
            if "decommissioned" in err_msg or "400" in err_msg or "insufficient_quota" in err_msg or "429" in err_msg:
                raise Exception(err_msg)

        return None

    def _translate_with_deepseek(self, prompt: str, metric_name: str) -> Optional[str]:
        """Translate using DeepSeek-V4 via Featherless."""
        response = self.clients["deepseek-v4"].invoke(
            [
                ("system", "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. Return only one SQL expression. Do not use markdown."),
                ("user", prompt),
            ]
        )
        sql = response.content.strip()

        # Clean reasoning tags and markdown block wraps
        sql = re.sub(r"<think>.*?</think>", "", sql, flags=re.IGNORECASE | re.DOTALL)
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
        sql = sql.strip()

        if self._is_valid_metric_sql(sql):
            return sql
        logger.warning("Featherless generated invalid or unsafe SQL for '%s': %s", metric_name, sql)
        return None

    def _translate_with_openai(
        self,
        fallback_translator: Any,
        dax: str,
        metric: Any,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
    ) -> Optional[str]:
        """Call existing OpenAI translation in the translator instance."""
        if fallback_translator and hasattr(fallback_translator, "_try_openai_dax_translation"):
            return fallback_translator._try_openai_dax_translation(
                dax_expression=dax,
                metric=metric,
                table_alias=table_alias,
                dataset_col_lookup=dataset_col_lookup,
            )
        return None

    def _translate_with_groq(self, prompt: str, metric_name: str) -> Optional[str]:
        """Fallback to Groq."""
        if "groq" in self.clients:
            try:
                response = self.clients["groq"].invoke(
                    [
                        ("system", "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. Return only one SQL expression. Do not use markdown."),
                        ("user", prompt),
                    ]
                )
                sql = response.content.strip()
                sql = re.sub(r"<think>.*?</think>", "", sql, flags=re.IGNORECASE | re.DOTALL)
                sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
                sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
                sql = sql.strip()
                if self._is_valid_metric_sql(sql):
                    return sql
            except Exception as e:
                logger.warning("Groq ChatGroq failed for '%s': %s", metric_name, e)

        # Try direct openai client fallback for Groq
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            return None

        try:
            from openai import OpenAI
            client = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
            response = client.chat.completions.create(
                model=os.getenv("GROQ_DAX_MODEL", "mixtral-8x7b-32768"),
                messages=[
                    {
                        "role": "system",
                        "content": "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. Return only one SQL expression. Do not use markdown."
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=500,
                timeout=30.0,
            )
            sql = (response.choices[0].message.content or "").strip()
            sql = re.sub(r"<think>.*?</think>", "", sql, flags=re.IGNORECASE | re.DOTALL)
            sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
            sql = sql.strip()
            if self._is_valid_metric_sql(sql):
                return sql
        except Exception as exc:
            logger.warning("Groq direct translation failed for '%s': %s", metric_name, exc)

        return None

    def _is_valid_metric_sql(self, sql: str) -> bool:
        """Validate SQL is safe for Snowflake Semantic View METRICS clause."""
        if not sql:
            return False
        upper = sql.upper()
        # Defensive validation
        forbidden = [" SELECT ", "(SELECT", " FROM ", " JOIN ", " WITH ", " OVER ", " DROP "]
        for word in forbidden:
            if word in upper:
                return False
        return True


# Singleton instance
_multi_model_translator: Optional[MultiModelDAXTranslator] = None


def get_multi_model_translator() -> MultiModelDAXTranslator:
    global _multi_model_translator
    if _multi_model_translator is None:
        _multi_model_translator = MultiModelDAXTranslator()
    return _multi_model_translator
