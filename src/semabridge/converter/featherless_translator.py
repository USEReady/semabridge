"""Direct Featherless translator wrapper.

First tries to use the multi-model translator if present, otherwise falls back
to a direct HTTP call to the Featherless chat completions endpoint.
"""
from typing import Optional
import os
import logging
import json
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

FEATHERLESS_KEY = os.getenv("FEATHERLESS_API_KEY") or os.getenv("Feather-Api-Key")
FEATHERLESS_URL = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1/chat/completions")



import os
from typing import Optional
from langchain_openai import ChatOpenAI
from semabridge.utils.logger import get_logger
from dotenv import load_dotenv
load_dotenv()

# Featherless API configuration
FEATHERLESS_API_KEY = os.getenv("Feather-Api-Key") or os.getenv("FEATHERLESS_API_KEY")
FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"

# List of models to try in order (priority)
FEATHERLESS_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    "Qwen/Qwen2.5-7B-Instruct",
    "mistralai/Mixtral-8x7B-Instruct-v0.1"
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
            timeout=10,
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
            
            # Clean markdown and reasoning tags
            import re
            sql = re.sub(r"<think>.*?</think>", "", sql, flags=re.IGNORECASE | re.DOTALL)
            sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
            sql = sql.strip()
            
            # Basic validation
            if sql and not any(x in sql.upper() for x in ["SELECT", "FROM", "JOIN", "WITH", "OVER"]):
                logger.info(f"✅ Featherless ({model}) translated {metric_name}")
                return sql
            else:
                logger.warning(f"Featherless ({model}) returned invalid SQL for {metric_name}: {sql}")
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"Featherless ({model}) failed for {metric_name}: {err_msg}")
            if "upgrade_required" in err_msg or "model_gated" in err_msg or "403" in err_msg or "429" in err_msg:
                break # Break on auth/rate limits
            if "404" in err_msg or "not found" in err_msg.lower():
                continue # Continue to next model if this specific model is missing
    
    return None

# Example usage
if __name__ == "__main__":
    test_dax = "TOTALYTD(SUM(SalesFact[Units]), 'Date'[Date])"
    result = translate_with_featherless(test_dax, "Total Units YTD")
    print(result)
