"""Reference parsers.

Both turn a platform's definition text into the same normalised objects. The
split that matters is NAME on the left of the first top level "as", EXPRESSION on
the right, with trailing clauses kept out of the name.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

import yaml

from .models import (
    DIMENSION,
    FACT,
    METRIC,
    RELATIONSHIP,
    Capabilities,
    ObjectDef,
    ViewDefinition,
)

logger = logging.getLogger(__name__)

DDL_PATH = "ddl"
YAML_PATH = "yaml"
SQL_FALLBACK_PATH = "sql_fallback"

_SECTIONS = ("tables", "relationships", "facts", "dimensions", "metrics")
_MARKER = "__OBJECT_NAME_MARKER__"


class DefinitionUnreadable(Exception):
    """The definition could not be read, so nothing is compared."""


# ---------------------------------------------------------------- shared helpers

def split_top_level(text: str, separator: str = ",") -> List[str]:
    """Split on a separator that is outside brackets and outside quotes."""
    parts, depth, quote, current = [], 0, None, []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            current.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _find_top_level_as(text: str) -> int:
    """Index of the first " as " that is not inside brackets or quotes."""
    depth, quote, i = 0, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and text[i:i + 4].lower() == " as " :
            return i
        i += 1
    return -1


def strip_name(raw: str) -> str:
    """A deployed identifier and nothing else."""
    name = raw.strip()
    name = re.sub(r"^\s*private\s+", "", name, flags=re.I)  # visibility marker
    name = re.sub(r"\bnon\s+additive\s+by\s*\([^)]*\)", "", name, flags=re.I)
    name = re.sub(r"\bprimary\s+key\s*\([^)]*\)", "", name, flags=re.I)
    name = name.strip().strip(",")
    return "".join(part.strip().strip('"').strip("`") for part in [name]).strip()


def _split_clauses(tail: str) -> Tuple[str, str]:
    """Expression and description out of the right hand side of an entry."""
    description = ""
    m = re.search(r"\bcomment\s*=\s*'((?:''|[^'])*)'", tail, re.I)
    if m:
        description = m.group(1).replace("''", "'")
    expression = re.sub(r"\bcomment\s*=\s*'(?:''|[^'])*'", "", tail, flags=re.I)
    expression = re.sub(r"\bwith\s+synonyms\s*=\s*\([^)]*\)", "", expression, flags=re.I)
    expression = re.sub(r"\bnon\s+additive\s+by\s*\([^)]*\)", "", expression, flags=re.I)
    return expression.strip().rstrip(","), description


def _table_of(name: str) -> Tuple[str, str]:
    """A qualified object name split into its table part and its own name."""
    parts = [p.strip().strip('"').strip("`") for p in name.split(".") if p.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1] if parts else ""


# ---------------------------------------------------------------- DDL definitions

def _sections_of(text: str) -> dict:
    """Each named section's body, respecting nested brackets."""
    out, lowered = {}, text.lower()
    for section in _SECTIONS:
        for m in re.finditer(rf"\b{section}\s*\(", lowered):
            start = m.end()
            depth, i = 1, start
            while i < len(text) and depth:
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1
            if depth == 0:
                out[section] = text[start:i - 1]
                break
    return out


def parse_ddl(text: str, view_name: str, platform: str = "snowflake") -> ViewDefinition:
    """Definition text in DDL form into normalised objects."""
    if not text or not text.strip():
        raise DefinitionUnreadable("empty definition")
    sections = _sections_of(text)
    if not sections:
        raise DefinitionUnreadable("no recognisable sections in the definition")

    objects: List[ObjectDef] = []

    for entry in split_top_level(sections.get("tables", "")):
        idx = _find_top_level_as(entry)
        if idx >= 0:
            name, tail = entry[:idx], entry[idx + 4:]
        else:
            name, tail = entry, entry
        clean = strip_name(name)
        _, short = _table_of(clean)
        expression, description = _split_clauses(tail)
        objects.append(ObjectDef(FACT, short, short, strip_name(expression) or short, description))

    for entry in split_top_level(sections.get("relationships", "")):
        idx = _find_top_level_as(entry)
        name = strip_name(entry[:idx]) if idx >= 0 else ""
        body = entry[idx + 4:] if idx >= 0 else entry
        m = re.match(r"\s*([\w\".`]+)\s*\(([^)]*)\)\s*references\s+([\w\".`]+)\s*\(([^)]*)\)",
                     body, re.I)
        if not m:
            logger.info("Relationship entry not recognised; skipped")
            continue
        from_t, from_c, to_t, to_c = (strip_name(m.group(1)), m.group(2),
                                      strip_name(m.group(3)), m.group(4))
        from_cols = sorted(c.strip().strip('"').strip("`").casefold()
                           for c in from_c.split(",") if c.strip())
        to_cols = sorted(c.strip().strip('"').strip("`").casefold()
                         for c in to_c.split(",") if c.strip())
        identity = f"{from_t}->{to_t}::{'+'.join(from_cols)}={'+'.join(to_cols)}"
        objects.append(ObjectDef(RELATIONSHIP, from_t, identity,
                                 identity, "", {"declared_name": name}))

    for section, kind in (("facts", FACT), ("dimensions", DIMENSION), ("metrics", METRIC)):
        if section == "facts" and "tables" in sections:
            kind = DIMENSION if False else FACT
        for entry in split_top_level(sections.get(section, "")):
            idx = _find_top_level_as(entry)
            if idx < 0:
                logger.info("Entry without an expression; skipped")
                continue
            name = strip_name(entry[:idx])
            expression, description = _split_clauses(entry[idx + 4:])
            table, short = _table_of(name)
            objects.append(ObjectDef(kind, table, short, expression, description))

    return ViewDefinition(view_name=view_name, platform=platform, source_path=DDL_PATH,
                          objects=objects, capabilities=Capabilities())


# ---------------------------------------------------------------- YAML definitions

def parse_yaml(text: str, view_name: str, platform: str = "databricks",
               capabilities: Optional[Capabilities] = None) -> ViewDefinition:
    """Definition text in YAML form into the same normalised objects."""
    if not text or not text.strip():
        raise DefinitionUnreadable("empty definition")
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - malformed input is a clear refusal
        raise DefinitionUnreadable(f"definition not parseable: {exc}") from exc
    if not isinstance(data, dict):
        raise DefinitionUnreadable("definition is not a mapping")

    objects: List[ObjectDef] = []
    source = data.get("source")
    if isinstance(source, str) and source.strip():
        _, short = _table_of(source)
        objects.append(ObjectDef(FACT, short, short, source.strip(), ""))
    for item in data.get("tables") or []:
        if isinstance(item, dict) and item.get("name"):
            name = str(item["name"])
            _, short = _table_of(name)
            objects.append(ObjectDef(FACT, short, short,
                                     str(item.get("source") or name),
                                     str(item.get("comment") or "")))

    for key, kind in (("measures", METRIC), ("dimensions", DIMENSION)):
        for item in data.get(key) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            table, short = _table_of(str(item["name"]))
            objects.append(ObjectDef(kind, table, short,
                                     str(item.get("expr") or ""),
                                     str(item.get("comment") or item.get("description") or "")))

    for item in data.get("joins") or []:
        if not isinstance(item, dict):
            continue
        to_table = str(item.get("to") or "")
        on = str(item.get("on") or "")
        cols = sorted(c.strip().casefold() for c in re.split(r"[=\s]+", on) if c.strip())
        _, to_short = _table_of(to_table)
        identity = f"->{to_short}::{'+'.join(cols)}"
        objects.append(ObjectDef(RELATIONSHIP, to_short, identity, identity, "",
                                 {"declared_name": str(item.get("name") or "")}))

    return ViewDefinition(view_name=view_name, platform=platform, source_path=YAML_PATH,
                          objects=objects, capabilities=capabilities or Capabilities())


# ---------------------------------------------------------------- guard

def assert_names_are_identifiers(definition: ViewDefinition) -> None:
    """A parsed name must be an identifier, never a fragment of the definition.

    This one check catches a parser that swaps name and expression, drags in a
    trailing clause, or runs past a section boundary.
    """
    bad = []
    for obj in definition.objects:
        name = obj.name or ""
        if obj.kind == RELATIONSHIP:
            continue  # relationship identity is built, not read
        if (not name
                or any(ch in name for ch in "()'\"\n")
                or re.search(r"\b(as|comment|synonyms|private)\b", name, re.I)):
            bad.append(name)
    if bad:
        raise AssertionError(f"parsed names are not identifiers: {bad[:5]}")
