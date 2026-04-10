"""TMSL transformer helpers for complex Fabric DAX fallback.

Module Purpose:
- Provide a focused utility for translating complex Fabric DAX measures
  into Cube.js YAML measure definitions using an LLM.

Responsibilities:
- Validate measure payload shape.
- Call the OpenAI chat-completions API with a strict system prompt.
- Return raw YAML text suitable for direct insertion into Cube.js configs.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from semabridge.core.exceptions import ConversionError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MODEL = "gpt-4o-mini"
_SYSTEM_PROMPT = (
    "You are an expert Data Engineer. Translate this Fabric measure JSON into a "
    "Cube.js YAML measure definition. Map simple aggregations to Cube types. "
    "If it uses Time Intelligence (like DATESYTD), translate this into Cube.js "
    "rollingWindow configurations. Output ONLY the raw YAML code."
)


def _normalize_measure_payload(measure_json: Dict[str, Any]) -> Dict[str, str]:
    """Validate and normalize incoming measure JSON payload."""
    if not isinstance(measure_json, dict):
        raise ConversionError(
            "Complex measure translation requires a dictionary payload",
            source_format="fabric_dax_json",
            target_format="cubejs_yaml",
            details={"received_type": type(measure_json).__name__},
        )

    name = str(measure_json.get("name", "")).strip()
    expression = str(measure_json.get("expression", "")).strip()

    if not name:
        raise ConversionError(
            "Measure payload is missing required field 'name'",
            source_format="fabric_dax_json",
            target_format="cubejs_yaml",
        )
    if not expression:
        raise ConversionError(
            "Measure payload is missing required field 'expression'",
            source_format="fabric_dax_json",
            target_format="cubejs_yaml",
            details={"measure_name": name},
        )

    return {"name": name, "expression": expression}


def _strip_markdown_fences(text: str) -> str:
    """Strip optional markdown code fences if returned by the model."""
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_response_text(response: Any) -> str:
    """Extract text content from OpenAI chat completion response."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ConversionError(
            "OpenAI response did not contain a valid completion message",
            source_format="fabric_dax_json",
            target_format="cubejs_yaml",
        ) from exc

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()

    return str(content or "").strip()


def translate_complex_measure_to_cube(
    measure_json: Dict[str, Any],
    *,
    model_name: Optional[str] = None,
    client: Any = None,
) -> str:
    """Translate complex Fabric DAX measure JSON into Cube.js YAML.

    Args:
        measure_json: Dict containing at least `name` and `expression`.
        model_name: Optional model override. Defaults to env var
            OPENAI_CUBE_TRANSLATION_MODEL or `gpt-4o-mini`.
        client: Optional injected OpenAI client for testing.

    Returns:
        Raw Cube.js YAML measure definition string.
    """
    payload = _normalize_measure_payload(measure_json)
    resolved_model = model_name or os.getenv("OPENAI_CUBE_TRANSLATION_MODEL") or _DEFAULT_MODEL

    openai_client = client
    if openai_client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConversionError(
                "openai package is required for complex DAX translation fallback",
                source_format="fabric_dax_json",
                target_format="cubejs_yaml",
                details={"hint": "Install the openai package to enable this fallback."},
            ) from exc

        api_key = os.getenv("OPENAI_API_KEY")
        openai_client = OpenAI(api_key=api_key) if api_key else OpenAI()

    user_prompt = (
        "Fabric measure JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "Return Cube.js YAML for measures only."
    )

    try:
        response = openai_client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
    except Exception as exc:
        logger.error(
            "Complex DAX LLM translation failed for measure '%s' using model '%s': %s",
            payload["name"],
            resolved_model,
            exc,
        )
        raise ConversionError(
            "Failed to translate complex Fabric DAX measure via OpenAI",
            source_format="fabric_dax_json",
            target_format="cubejs_yaml",
            details={"measure_name": payload["name"], "model": resolved_model},
        ) from exc

    yaml_text = _strip_markdown_fences(_extract_response_text(response))
    if not yaml_text:
        raise ConversionError(
            "OpenAI returned an empty Cube.js YAML translation",
            source_format="fabric_dax_json",
            target_format="cubejs_yaml",
            details={"measure_name": payload["name"], "model": resolved_model},
        )
    return yaml_text
