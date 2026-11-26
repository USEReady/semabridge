"""Normalised metric-view definitions (Phase 3.8): what "unchanged" and
"drifted" mean.

A definition is compared as data, not text: the YAML is parsed and re-serialised
canonically (sorted keys, no comments, no formatting), so Databricks reformatting
the stored text does not look like a change, while any change to a field,
measure, join, filter or comment does. Strict on purpose:
* duplicate keys are an error (never silently merged: last-one-wins would hide a change);
* scalars keep their type (1, 1.0, "1" and true are all different);
* unknown keys are kept and compared like any other.

Live definitions are read back read-only, from the Unity Catalog table API
(``view_definition``) or, failing that, from ``SHOW CREATE TABLE``. The exact
shape Databricks returns for metric views is confirmed in the 3.8b live run;
until then extraction accepts the YAML alone or wrapped in a CREATE / ALTER text.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

_DOLLAR = re.compile(r"\$\$(.*)\$\$", re.DOTALL)


class DefinitionError(ValueError):
    pass


def _strict_loader():
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise DefinitionError(f"duplicate key {key!r} in the definition")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return StrictLoader


def canonical(yaml_text: str) -> Dict[str, Any]:
    import yaml

    try:
        doc = yaml.load(yaml_text, Loader=_strict_loader())  # noqa: S506 -- SafeLoader subclass
    except yaml.YAMLError as exc:
        raise DefinitionError(f"definition is not valid YAML: {exc}") from None
    if not isinstance(doc, dict):
        raise DefinitionError("definition is not a YAML mapping")
    return _norm(doc)


def _norm(value: Any) -> Any:
    """Keys as strings; scalar values tagged with their type so 1, 1.0, "1" and
    true never compare equal."""
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if value is None or isinstance(value, str):
        return value
    return {"__type__": type(value).__name__, "value": str(value)}


def definition_hash(yaml_text: str) -> str:
    doc = canonical(yaml_text)
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def extract_yaml(text: Optional[str]) -> Optional[str]:
    """The YAML body from a stored definition: a ``$$ ... $$`` body when present,
    otherwise the text itself when it parses as a YAML mapping."""
    if not text:
        return None
    m = _DOLLAR.search(text)
    body = m.group(1) if m else text
    try:
        canonical(body)
    except DefinitionError:
        return None
    return body


def names(yaml_text: str) -> Dict[str, List[str]]:
    """{"fields": [...], "measures": [...]} declared in a definition."""
    import yaml

    doc = yaml.load(yaml_text, Loader=_strict_loader())  # noqa: S506
    fields = [str(f.get("name")) for f in doc.get("fields") or doc.get("dimensions") or [] if isinstance(f, dict)]
    measures = [str(m.get("name")) for m in doc.get("measures") or [] if isinstance(m, dict)]
    return {"fields": fields, "measures": measures}
