"""
MainOrchestrator

Production-safe orchestration wrappers around the existing sync flow.

Important:
- The core sync() implementation is intentionally untouched.
- Enhancements are applied as post-processing to avoid behavioral regressions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.SyncModule import sync


def prepare_output_directories(base_output_dir: Path = Path("output")) -> Path:
    """Create output directories required by sync post-processing.

    This is intentionally separated from sync() so directory preparation can
    evolve independently without changing sync behavior.

    Args:
        base_output_dir: Root output directory used by sync artifacts.

    Returns:
        The resolved debug output directory path.
    """
    debug_dir = base_output_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def _safe_folder_name(name: str) -> str:
    """Convert a model name into a filesystem-safe folder name."""
    cleaned = name.strip().replace("/", "_").replace("\\", "_")
    return cleaned or "UnnamedModel"


def _extract_models(payload: Any, fallback_name: str = "DefaultModel") -> Dict[str, Any]:
    """Normalize parsed content into a model-name -> model-content mapping.

    Supports:
    - Multi-model payload: {"ModelA": {...}, "ModelB": {...}}
    - Wrapped payload: {"models": {...}} or {"models": [{"name": ...}, ...]}
    - Single-model payload: treated as one model under fallback name
    """
    if payload is None:
        return {}

    if isinstance(payload, dict):
        models_obj = payload.get("models")
        if isinstance(models_obj, dict):
            return {str(k): v for k, v in models_obj.items()}

        if isinstance(models_obj, list):
            extracted: Dict[str, Any] = {}
            for idx, model in enumerate(models_obj):
                if isinstance(model, dict):
                    model_name = str(
                        model.get("name")
                        or model.get("model_name")
                        or model.get("id")
                        or f"Model{idx + 1}"
                    )
                    extracted[model_name] = model
                else:
                    extracted[f"Model{idx + 1}"] = model
            return extracted

        # Direct top-level grouped models: all values are nested objects.
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return {str(k): v for k, v in payload.items()}

        return {fallback_name: payload}

    return {fallback_name: payload}


def _load_json(path: Path) -> Optional[Any]:
    """Load a JSON document, raising ValueError on invalid JSON."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def _load_yaml(path: Path) -> Optional[Any]:
    """Load a YAML document, raising ValueError on invalid YAML."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc


def split_semantic_models(
    debug_dir: Path = Path("output") / "debug",
    json_name: str = "osi_inferred.json",
    yaml_name: str = "osi_inferred.yaml",
    overwrite: bool = False,
) -> Dict[str, int]:
    """Split grouped semantic model outputs into per-model directories.

    This is a post-processing enhancement by design. We do not modify sync()
    to preserve production behavior and keep risk isolated to file layout logic.

    Args:
        debug_dir: Directory where sync writes inferred artifacts.
        json_name: Grouped JSON filename produced by sync.
        yaml_name: Grouped YAML filename produced by sync.
        overwrite: Whether to overwrite existing per-model files.

    Returns:
        Summary dictionary with counts for processed and written files.

    Raises:
        ValueError: If JSON/YAML is invalid.
    """
    debug_dir = Path(debug_dir)
    json_path = debug_dir / json_name
    yaml_path = debug_dir / yaml_name

    json_payload = _load_json(json_path)
    yaml_payload = _load_yaml(yaml_path)

    # Empty or missing inputs are valid no-op scenarios.
    if json_payload is None and yaml_payload is None:
        return {"models": 0, "json_written": 0, "yaml_written": 0, "skipped": 0}

    models: Dict[str, Dict[str, Any]] = {}

    if json_payload is not None:
        for model_name, model_data in _extract_models(json_payload).items():
            models.setdefault(model_name, {})["json"] = model_data

    if yaml_payload is not None:
        for model_name, model_data in _extract_models(yaml_payload).items():
            models.setdefault(model_name, {})["yaml"] = model_data

    summary = {"models": len(models), "json_written": 0, "yaml_written": 0, "skipped": 0}

    for model_name, model_formats in models.items():
        model_dir = debug_dir / _safe_folder_name(model_name)
        model_dir.mkdir(parents=True, exist_ok=True)

        if "json" in model_formats:
            out_json = model_dir / "osi_inferred.json"
            if out_json.exists() and not overwrite:
                summary["skipped"] += 1
            else:
                out_json.write_text(json.dumps(model_formats["json"], ensure_ascii=False, indent=2), encoding="utf-8")
                summary["json_written"] += 1

        if "yaml" in model_formats:
            out_yaml = model_dir / "osi_inferred.yaml"
            if out_yaml.exists() and not overwrite:
                summary["skipped"] += 1
            else:
                out_yaml.write_text(yaml.safe_dump(model_formats["yaml"], sort_keys=False, allow_unicode=False), encoding="utf-8")
                summary["yaml_written"] += 1

    return summary


def run_sync() -> Dict[str, int]:
    """Run the non-breaking wrapper flow around sync.

    Flow:
    1. prepare_output_directories()
    2. sync()                     # untouched core behavior
    3. split_semantic_models()    # post-processing enhancement
    """
    debug_dir = prepare_output_directories()
    sync()
    return split_semantic_models(debug_dir=debug_dir)


if __name__ == "__main__":
    summary = run_sync()
    print(summary)
