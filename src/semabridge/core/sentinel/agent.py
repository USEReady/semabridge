"""
Sentinel AI Agent.

Interfaces with Large Language Models to analyze errors and suggest
remediation strategies (SQL fixes or YAML updates).
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional, Any

import httpx
from pydantic import SecretStr

from semabridge.core.settings import LLMConfig
from semabridge.core.sentinel.analyzer import ErrorContext
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class AgentAction(str, Enum):
    """Actions the agent can recommend."""
    SQL_FIX = "sql_fix"
    YAML_UPDATE = "yaml_update"
    EXPLAIN = "explain"
    NONE = "none"


class SentinelAgent:
    """
    AI Agent for Project Sentinel.
    
    Uses LLMs to analyze error context and generate fixes.
    Supports OpenAI-compatible APIs (OpenAI, Azure OpenAI, Local LLMs).
    """
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = httpx.Client(timeout=60.0)

    def suggest_fix(self, ctx: ErrorContext) -> tuple[AgentAction, str]:
        """
        Analyze the error and suggest a fix.
        
        Args:
            ctx: The error context containing failure details and analysis.
            
        Returns:
            Tuple of (Action Type, Content).
            Content is specific to the action (e.g., SQL statement or explanation).
        """
        if not self.config.is_configured:
            logger.warning("LLM not configured. Skipping AI analysis.")
            return AgentAction.NONE, "LLM not configured."

        # If we already have a deterministic fix from the analyzer, use it.
        if ctx.suggested_fix and ctx.root_cause:
            # But maybe we want the LLM to explain it nicely?
            # For now, let's trust the analyzer if it found something concrete.
            # Actually, the PRD says "The AI agent will analyze... and generate".
            # Let's use LLM to format/verify specific fixes or handle complex cases.
            pass

        prompt = self._build_prompt(ctx)
        
        try:
            response = self._call_llm(prompt)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return AgentAction.NONE, f"AI Analysis failed: {e}"

    def _build_prompt(self, ctx: ErrorContext) -> str:
        """Construct the prompt for the LLM."""
        
        # Base context
        system_prompt = """You are an automated Snowflake DBA and Semantic Layer expert.
        Your goal is to fix SQL compilation errors occurring during semantic model deployment.
        You will receive an error message, a failed SQL query, and analysis context.
        
        Output valid JSON with the following structure:
        {
            "action": "sql_fix" | "yaml_update" | "explain",
            "content": "The actual SQL to run, or the YAML change description, or the explanation.",
            "reasoning": "Brief explanation of why this fix is needed."
        }
        
        Rules:
        1. If it's a missing permission (002003), provide the GRANT statement.
        2. If it's an invalid identifier (000904), provide the corrected identifier or SQL.
        3. If the error implies a model change (e.g. column renamed), suggest a YAML update description.
        4. Be concise.
        """
        
        user_prompt = f"""
        Error Code: {ctx.failure.error_code}
        Error Message: {ctx.failure.error_message}
        Object: {ctx.object_name or 'Unknown'}
        
        Failed Query:
        ```sql
        {ctx.failure.query_text}
        ```
        """
        
        if ctx.root_cause:
            user_prompt += f"\nAnalyzer Findings: {ctx.root_cause}"
        if ctx.suggested_fix:
            user_prompt += f"\nAnalyzer Suggestion: {ctx.suggested_fix}"
            
        return f"{system_prompt}\n\nTask:\n{user_prompt}"

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM API."""
        # This is a simplified implementation targeting OpenAI Chat Completion API
        
        api_key = self.config.api_key.get_secret_value() if self.config.api_key else ""
        base_url = self.config.base_url or "https://api.openai.com/v1"
        
        # Handle Azure differences if needed (omitted for brevity, assuming OpenAI format)
        url = f"{base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.config.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"} # Force JSON if supported
        }
        
        response = self._client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        
        result = response.json()
        return result["choices"][0]["message"]["content"]

    def _parse_response(self, content: str) -> tuple[AgentAction, str]:
        """Parse LLM JSON response."""
        try:
            data = json.loads(content)
            action_str = data.get("action", "none").lower()
            
            action_map = {
                "sql_fix": AgentAction.SQL_FIX,
                "yaml_update": AgentAction.YAML_UPDATE,
                "explain": AgentAction.EXPLAIN
            }
            
            action = action_map.get(action_str, AgentAction.NONE)
            result_content = data.get("content", "")
            
            return action, result_content
            
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON response")
            return AgentAction.EXPLAIN, content
