from google import genai
from google.genai import types

from semabridge.core.llm_config import LLMConfig
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class APIClient:
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise ValueError(
                f"Missing API key in environment variable '{config.api_key_env}'"
            )

        self.config = config
        self.client = genai.Client(api_key=config.api_key)

        logger.info(f"Initialized Gemini client (model={config.model})")

    def call_llm(self, prompt: str):
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                ),
            )

            if response and response.text:
                return response.text.strip()

            return None

        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return None