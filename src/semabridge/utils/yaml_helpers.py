from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import yaml

# Preferred ordering for canonical dumps — keys appearing here are emitted
# first in this order when present in the mapping. Remaining keys are emitted
# in alphabetical order to keep output stable across runs.
PREFERRED_ORDER = [
    "model_name",
    "version",
    "source_platform",
    "target_platform",
    "datasets",
    "metrics",
    "dimensions",
    "relationships",
]


def load_yaml(source: str | Path) -> Any:
    """Load YAML from a string or file path and return plain Python objects.

    Returns nested dict/list structures suitable for JSONB storage.
    """
    text = None
    p = Path(source)
    if p.exists():
        text = p.read_text(encoding="utf-8")
    else:
        text = str(source)

    value = yaml.safe_load(text)
    return value


def _order_mapping(obj: Any) -> Any:
    """Reorder mappings deterministically for YAML output.

    - For dicts: put preferred keys first (in PREFERRED_ORDER), then the
      remaining keys in alphabetical order. Recurses into nested mappings
      and lists.
    - For lists and scalars: return as-is (lists preserve order).
    """
    if isinstance(obj, dict):
        ordered = OrderedDict()
        # Preferred keys first
        for key in PREFERRED_ORDER:
            if key in obj:
                ordered[key] = _order_mapping(obj[key])
        # Remaining keys sorted alphabetically
        remaining = [k for k in obj.keys() if k not in PREFERRED_ORDER]
        for key in sorted(remaining):
            ordered[key] = _order_mapping(obj[key])
        return ordered
    if isinstance(obj, list):
        return [_order_mapping(v) for v in obj]
    return obj


def dump_yaml(obj: Any, path: Optional[Path] = None) -> str:
    """Serialize `obj` to YAML deterministically and optionally write to `path`.

    The function reorders mappings using `_order_mapping` and then emits YAML
    with `sort_keys=False` so the chosen order is preserved. This produces a
    stable representation suitable for snapshot tests.
    """
    ordered = _order_mapping(obj)

    def _to_plain(o: Any) -> Any:
        # Convert OrderedDict -> dict (preserving insertion order) and
        # recurse into lists/dicts so PyYAML's SafeDumper can represent
        # only built-in types.
        if isinstance(o, OrderedDict):
            return {k: _to_plain(v) for k, v in o.items()}
        if isinstance(o, dict):
            return {k: _to_plain(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_to_plain(v) for v in o]
        return o

    plain = _to_plain(ordered)
    text = yaml.safe_dump(
        plain,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )
    if path:
        Path(path).write_text(text, encoding="utf-8")
    return text


def roundtrip_yaml(obj: Any) -> Any:
    """Helper: dump -> load and return the reloaded object.

    Useful for tests asserting deterministic round-trips.
    """
    text = dump_yaml(obj)
    return load_yaml(text)
