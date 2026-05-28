"""Direct Featherless translator wrapper.

First tries to use the multi-model translator if present, otherwise falls back
to a direct HTTP call to the Featherless chat completions endpoint.
"""
from typing import Optional
import os
import logging
import json

logger = logging.getLogger(__name__)

FEATHERLESS_KEY = os.getenv("FEATHERLESS_API_KEY") or os.getenv("Feather-Api-Key")
FEATHERLESS_URL = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1/chat/completions")


def translate_with_featherless(dax: str, metric_name: str, prompt: Optional[str] = None) -> Optional[str]:
    """Attempt to translate DAX using Featherless.

    Returns SQL expression string or None.
    """
    try:
        # Prefer multi_model_translator if available
        from semabridge.converter.multi_model_translator import translate_with_featherless as mm_tf
        return mm_tf(dax, metric_name)
    except Exception:
        pass

    if not FEATHERLESS_KEY:
        logger.debug("Featherless API key not configured")
        return None

    body = {
        "model": os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V4-Pro"),
        "messages": [
            {"role": "system", "content": "Translate Power BI DAX to a single Snowflake METRICS SQL expression. Return only the expression."},
            {"role": "user", "content": prompt or f"Metric: {metric_name}\nDAX: {dax}"},
        ],
        "max_tokens": int(os.getenv("FEATHERLESS_MAX_TOKENS", "500")),
    }

    try:
        import requests

        headers = {"Authorization": f"Bearer {FEATHERLESS_KEY}", "Content-Type": "application/json"}
        resp = requests.post(FEATHERLESS_URL, json=body, headers=headers, timeout=60)
        if resp.status_code != 200:
            logger.debug("Featherless HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        # Try to extract text from common shapes
        choice = None
        if isinstance(data, dict):
            # OpenAI-like shape
            choices = data.get("choices") or data.get("outputs")
            if choices and isinstance(choices, list) and len(choices) > 0:
                first = choices[0]
                if isinstance(first, dict):
                    choice = first.get("message", {}).get("content") or first.get("text") or first.get("content")
        text = (choice or "" ).strip()
        if text:
            # sanitize markdown
            text = text.replace("```sql", "").replace("```", "").strip()
            return text
    except Exception as exc:
        logger.debug("Featherless translate failed: %s", exc)
    return None
import os
from typing import Optional
from langchain_openai import ChatOpenAI
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Featherless API configuration
FEATHERLESS_API_KEY = os.getenv("Feather-Api-Key") or os.getenv("FEATHERLESS_API_KEY")
FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"

# List of models to try in order (priority)
FEATHERLESS_MODELS = [
    "deepseek-ai/DeepSeek-V4-Pro",
    "Qwen/Qwen3-0.6B",
    "Qwen/Qwen3.6-27B",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "meta-llama/Llama-3.2-3B-Instruct",
]

def get_featherless_llm(model: str = None) -> Optional[ChatOpenAI]:
    """Initialize Featherless LangChain client with API key from env."""
    if not FEATHERLESS_API_KEY:
        logger.warning("Feather-Api-Key not found in environment variables")
        return None
    
    model_to_use = model or FEATHERLESS_MODELS[0]
    
    try:
        llm = ChatOpenAI(
            api_key=FEATHERLESS_API_KEY,
            model=model_to_use,
            base_url=FEATHERLESS_BASE_URL,
            temperature=0.1,
            max_tokens=500,
            timeout=30,
        )
        logger.info(f"✅ Featherless client initialized with model: {model_to_use}")
        return llm
    except Exception as e:
        logger.error(f"Failed to initialize Featherless with {model_to_use}: {e}")
        return None

def translate_with_featherless(dax: str, metric_name: str, prompt: str = None) -> Optional[str]:
    """Translate DAX using Featherless models with automatic failover."""
    if not FEATHERLESS_API_KEY:
        logger.warning("Skipping Featherless – no API key")
        return None
    
    for model in FEATHERLESS_MODELS:
        llm = get_featherless_llm(model)
        if not llm:
            continue
        
        prompt_to_use = prompt or f"""You are a DAX to Snowflake SQL translator.

Metric name: {metric_name}
DAX: {dax}

Rules:
- Return ONLY the SQL expression, no explanations.
- No SELECT, FROM, JOIN, subqueries, or window functions (OVER).
- Use SUM(CASE WHEN ... THEN ... ELSE 0 END) for filtered aggregations.
- Use COALESCE(expr / NULLIF(denom, 0), 0) for division.
- Use MAX_DATE for YTD, not CURRENT_DATE.
- If impossible, return CAST(NULL AS DOUBLE).

SQL:"""
        
        try:
            response = llm.invoke(prompt_to_use)
            sql = response.content.strip()
            
            # Clean markdown
            import re
            sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
            
            # Basic validation
            if sql and not any(x in sql.upper() for x in ["SELECT", "FROM", "JOIN", "WITH", "OVER"]):
                logger.info(f"✅ Featherless ({model}) translated {metric_name}")
                return sql
            else:
                logger.warning(f"Featherless ({model}) returned invalid SQL for {metric_name}: {sql}")
        except Exception as e:
            logger.warning(f"Featherless ({model}) failed for {metric_name}: {e}")
    
    return None

# Example usage
if __name__ == "__main__":
    test_dax = "TOTALYTD(SUM(SalesFact[Units]), 'Date'[Date])"
    result = translate_with_featherless(test_dax, "Total Units YTD")
    print(result)
