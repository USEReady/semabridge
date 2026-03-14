import google.generativeai as genai


class APIClient:
    def __init__(self, config):
        if not config.api_key:
            raise ValueError("Missing API key for LLM")

        genai.configure(api_key=config.api_key)

        self.model = genai.GenerativeModel(config.model)
        self.timeout = config.timeout_seconds

    def call_llm(self, prompt: str) -> str:
        response = self.model.generate_content(prompt)

        if not response or not response.text:
            return None

        return response.text.strip()