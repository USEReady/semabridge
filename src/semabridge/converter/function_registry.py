"""Load and render DAX function templates from YAML registry."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
import os
import yaml

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PATH = Path(os.getenv("SEMABRIDGE_DAX_FUNCTION_REGISTRY", "Config/dax_function_registry.yaml"))


class FunctionRegistry:
    def __init__(self, registry_path: Optional[Path] = None) -> None:
        self.registry_path = registry_path or _DEFAULT_PATH
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            path = self.registry_path
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if not path.exists():
                logger.debug("Function registry not found at %s", path)
                return
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._registry = {str(k).upper(): (v or {}) for k, v in data.items()}
        except Exception as exc:
            logger.warning("Failed to load function registry: %s", exc)

    def get(self, func_name: str) -> Optional[Dict[str, Any]]:
        self._load()
        return self._registry.get(str(func_name or "").upper())

    def render(self, func_name: str, args: list[str], *, table_alias: str, date_alias: str) -> Optional[str]:
        entry = self.get(func_name)
        if not entry:
            return None
        template = entry.get("template")
        if not template:
            return None
        mapping = {
            "table_alias": table_alias,
            "date_alias": date_alias,
        }
        for idx, arg in enumerate(args):
            mapping[f"arg{idx}"] = arg
        try:
            return str(template).format(**mapping)
        except Exception as exc:
            logger.debug("Function template render failed for %s: %s", func_name, exc)
            return None
