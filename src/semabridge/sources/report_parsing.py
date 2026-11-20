"""Power BI report-layer parsing, shared by the PBIX connector and the Fabric report layer.

Moved verbatim from connectors/local_pbix_connector.py (which re-exports the
same names, so PBIX behaviour and imports are unchanged):

* parse_json_candidate           JSON bytes with UTF-8 / UTF-16 fallbacks
* pbir_layout                    PBIR (definition/ tree) -> legacy {"sections": [...]} layout
* pbir_visual_to_legacy_shape    one PBIR visual.json -> legacy config/query strings
* extract_field_references       fields referenced by one visual
* parse_presentation_metadata    layout -> (title->field candidates, unresolved references)
* best_matching_field            unambiguous stale-name resolution
* summarize_report_aliases       presentation metadata -> (field_aliases, ambiguous aliases)

Inputs are plain bytes / dicts, so a report can come from a .pbix archive or
from a Fabric report definition.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger("semabridge.connectors.local_pbix_connector")


def parse_json_candidate(data: bytes) -> Optional[Dict[str, Any]]:
    """Attempt to parse bytes as JSON using robust encoding fallbacks.

    Some PBIX files store JSON metadata with UTF-16 encodings.
    Others may include binary payloads that should be skipped.
    """
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue

        normalized = text.replace("\x00", "").strip()
        if not normalized:
            continue

        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return parsed

    return None


def pbir_layout(
    read_file: Callable[[str], Optional[bytes]],
    names: Iterable[str],
    *,
    root: str = "Report/definition",
    read_member: Optional[Callable[[str], Optional[bytes]]] = None,
) -> Optional[Dict[str, Any]]:
    """Fallback layout extraction for the PBIR (Power BI enhanced report)
    format: one page.json per page and one visual.json per visual under
    ``<root>/`` (``Report/definition`` inside a .pbix, ``definition`` in a
    Fabric report definition), instead of a single Report/Layout file.
    Reconstructs the same {"sections": [...]} shape parse_presentation_metadata
    already expects.

    Returns the layout in the legacy shape, or None if this report doesn't use
    the PBIR format either.
    """
    read_member = read_member or read_file
    pages_data = read_file(f"{root}/pages/pages.json")
    if not pages_data:
        return None

    pages_meta = parse_json_candidate(pages_data)
    page_order = pages_meta.get("pageOrder") if pages_meta else None
    if not isinstance(page_order, list) or not page_order:
        return None

    archive_names = list(names)
    sections: List[Dict[str, Any]] = []

    for page_id in page_order:
        if not isinstance(page_id, str) or not page_id:
            continue
        page_prefix = f"{root}/pages/{page_id}/"
        page_json_data = read_file(f"{page_prefix}page.json")
        page_meta = parse_json_candidate(page_json_data) if page_json_data else None
        display_name = (page_meta or {}).get("displayName") or page_id

        visuals_prefix = f"{page_prefix}visuals/"
        visual_containers: List[Dict[str, Any]] = []
        for name in archive_names:
            if not (name.startswith(visuals_prefix) and name.endswith("/visual.json")):
                continue
            visual_entry = parse_json_candidate(read_member(name) or b"")
            if not visual_entry:
                continue
            config_str, query_str = pbir_visual_to_legacy_shape(visual_entry)
            visual_containers.append({
                "config": config_str,
                "query": query_str,
                "filters": "[]",
            })

        sections.append({
            "displayName": display_name,
            "visualContainers": visual_containers,
        })

    if not sections:
        return None

    logger.info(
        "Report layout extracted successfully from PBIR-format Report/definition/ tree (%d pages)",
        len(sections),
    )
    return {"sections": sections}


def pbir_visual_to_legacy_shape(visual_entry: Dict[str, Any]) -> Tuple[str, str]:
    """Translate one PBIR visual.json into the legacy config/query JSON
    string shape _parse_presentation_metadata and
    _extract_field_references already parse.

    PBIR differs from the legacy Report/Layout shape in two ways that
    matter here:
      - the title lives under "visualContainerObjects" instead of
        "vcObjects" (identical nested shape otherwise, so it's copied
        through unchanged under the legacy key name);
      - a field binding's SourceRef carries the table name directly
        ({"SourceRef": {"Entity": "SalesFact"}}) instead of an alias
        resolved via a separate "From" clause. A synthetic "From"
        entry and alias are fabricated per distinct entity referenced,
        so the existing alias-resolution logic in
        _extract_field_references needs no changes for either format.
    """
    visual = visual_entry.get("visual") if isinstance(visual_entry, dict) else None
    if not isinstance(visual, dict):
        visual = {}

    visual_type = visual.get("visualType", "")
    vco = visual.get("visualContainerObjects")
    title = vco.get("title") if isinstance(vco, dict) else None

    config: Dict[str, Any] = {
        "singleVisual": {
            "visualType": visual_type,
            "vcObjects": {"title": title} if title is not None else {},
        }
    }

    query_node = visual.get("query")
    query_state = query_node.get("queryState") if isinstance(query_node, dict) else None
    projections: List[Dict[str, Any]] = []
    if isinstance(query_state, dict):
        for role in query_state.values():
            if isinstance(role, dict):
                role_projections = role.get("projections")
                if isinstance(role_projections, list):
                    projections.extend(role_projections)

    entity_alias: Dict[str, str] = {}
    select_items: List[Dict[str, Any]] = []
    for proj in projections:
        if not isinstance(proj, dict):
            continue
        field = proj.get("field")
        if not isinstance(field, dict):
            continue
        for node_key in ("Measure", "Column"):
            node = field.get(node_key)
            if not isinstance(node, dict):
                continue
            expr = node.get("Expression")
            source_ref = expr.get("SourceRef") if isinstance(expr, dict) else None
            entity = source_ref.get("Entity") if isinstance(source_ref, dict) else None
            if not isinstance(entity, str) or not entity:
                continue
            if entity not in entity_alias:
                entity_alias[entity] = f"e{len(entity_alias) + 1}"
            select_items.append({
                node_key: {
                    "Expression": {"SourceRef": {"Source": entity_alias[entity]}},
                    "Property": node.get("Property"),
                },
                "Name": proj.get("queryRef", ""),
            })

    query = {
        "Commands": [
            {
                "SemanticQuery": {
                    "From": [
                        {"Name": alias, "Entity": entity, "Type": 0}
                        for entity, alias in entity_alias.items()
                    ],
                    "Select": select_items,
                }
            }
        ]
    }

    return json.dumps(config), json.dumps(query)

def extract_field_references(visual: Dict[str, Any]) -> Set[Tuple[str, str, Optional[str]]]:
    """Recursively traverse a visual container's fields to extract measure
    and column field references, resolved to their source table where
    possible.

    Both DAX measures and column-bound visuals encode their binding as a
    node named after the entity kind (``Measure`` or ``Column``) with a
    ``Property`` holding the field name — confirmed against real PBIX
    Report/Layout JSON, where a column-bound Select entry has the
    identical shape as a measure-bound one:
        {"Column": {"Expression": {"SourceRef": {"Source": "p"}}, "Property": "isVanArsdel"}}
        {"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Total Units"}}

    Only references found inside a ``Select`` clause are extracted —
    that's the query's field-projection list, i.e. what the visual
    actually displays. References inside a ``Where`` clause (filter
    conditions applied to the visual) are deliberately excluded: on
    real reports, a visual almost always carries filter context bound
    to fields it never displays (e.g. a KPI card filtered to a specific
    Date/Category), and including those would make nearly every visual
    look "multi-field" even when it displays a single measure —
    confirmed empirically against a real-world report, where field
    counts dropped from double digits per visual to the visual's true
    display-field count once ``Where`` was excluded.

    Args:
        visual: Visual container dictionary.

    Returns:
        Set of (field_name, field_kind, table_name) tuples. field_kind is
        "measure", "column", or "unknown" (bare queryRef match with no
        adjacent Measure/Column node — confirmed empirically to never
        carry table information either). table_name is the resolved
        source table (from the query's From clause) for columns, or None
        when unresolvable. Measures are left unscoped (None) since
        measure names are globally unique in valid TMSL — no From-clause
        resolution is attempted for them.
    """
    fields: Set[Tuple[str, str, Optional[str]]] = set()
    alias_to_entity: Dict[str, str] = {}

    def _clean_field_name(name: str) -> str:
        name = name.strip()
        if "[" in name and name.endswith("]"):
            start = name.rfind("[")
            name = name[start + 1 : -1]
        elif "." in name:
            parts = name.split(".")
            if parts:
                name = parts[-1]
        return name.strip()

    def _collect_aliases(data: Any) -> None:
        if isinstance(data, dict):
            from_clause = data.get("From")
            if isinstance(from_clause, list):
                for source in from_clause:
                    if isinstance(source, dict):
                        alias = source.get("Name")
                        entity = source.get("Entity")
                        if isinstance(alias, str) and isinstance(entity, str) and entity.strip():
                            alias_to_entity[alias] = entity
            for val in data.values():
                _collect_aliases(val)
        elif isinstance(data, list):
            for item in data:
                _collect_aliases(item)

    def _resolve_table(node: Dict[str, Any]) -> Optional[str]:
        expr = node.get("Expression")
        if isinstance(expr, dict):
            source_ref = expr.get("SourceRef")
            if isinstance(source_ref, dict):
                alias = source_ref.get("Source")
                if isinstance(alias, str):
                    return alias_to_entity.get(alias)
        return None

    def _traverse(data: Any, in_select: bool = False) -> None:
        if isinstance(data, dict):
            if in_select:
                # Case 1: Measure -> Property, or Column -> Property (identical shape)
                for node_key, field_kind in (("Measure", "measure"), ("Column", "column")):
                    node = data.get(node_key)
                    if isinstance(node, dict):
                        prop = node.get("Property")
                        if isinstance(prop, str) and prop.strip():
                            cleaned = _clean_field_name(prop)
                            if cleaned:
                                table_name = _resolve_table(node) if field_kind == "column" else None
                                fields.add((cleaned, field_kind, table_name))

                # Case 2: queryRef (no adjacent Measure/Column node — confirmed
                # empirically to never carry a resolvable table either)
                query_ref = data.get("queryRef")
                if isinstance(query_ref, str) and query_ref.strip():
                    cleaned = _clean_field_name(query_ref)
                    if cleaned:
                        fields.add((cleaned, "unknown", None))

            for key, val in data.items():
                if key == "Where":
                    continue
                _traverse(val, in_select=(in_select or key == "Select"))
        elif isinstance(data, list):
            for item in data:
                _traverse(item, in_select=in_select)

    # Parse the four visual container JSON properties once, then run two
    # passes over them: first collect every From-clause alias->table
    # mapping (order-independent — doesn't rely on From preceding Select
    # in the JSON), then resolve Column/Measure references against it.
    parsed_fields: List[Any] = []
    for field_name in ("config", "query", "filters", "dataTransforms"):
        field_val = visual.get(field_name)
        if not field_val:
            continue

        parsed_val = None
        if isinstance(field_val, dict):
            parsed_val = field_val
        elif isinstance(field_val, str):
            try:
                parsed_val = json.loads(field_val)
            except Exception as exc:
                logger.warning(
                    "Malformed JSON in visual container '%s' field: %s",
                    field_name,
                    exc,
                )
                continue

        if parsed_val:
            parsed_fields.append(parsed_val)

    for parsed_val in parsed_fields:
        _collect_aliases(parsed_val)
    for parsed_val in parsed_fields:
        _traverse(parsed_val)

    return fields

def parse_presentation_metadata(
    layout: Dict[str, Any],
    tables: List[Dict[str, Any]],
    measures: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse report layout sections and visual containers to extract
    presentation metadata, in a single pass over every visual.

    Two independent things are derived from the same per-visual field
    scan (each visual's fields are only ever extracted once):

    1. Presentation metadata (title -> field candidates). Only visuals
       bound to exactly one field are considered — a visual's title
       describes what it shows as a whole, which is only an unambiguous
       stand-in for a single field's name when that field is the
       visual's sole binding (e.g. a KPI card). Multi-field visuals
       (e.g. a chart combining a measure and a dimension) are skipped so
       their title never gets attributed to any one field.

    2. Unresolved field references: field names the report queries that
       don't match any field in the current model schema. Renaming a
       column or measure doesn't always rewrite the field-name text
       already serialized into a visual's saved query — a report can
       keep querying a field by its old name indefinitely if the visual
       is never re-edited. That stale name is exactly the kind of
       vocabulary a business user would still type into a
       natural-language query (they learned the field by its old name
       from the report, not from the model). This check runs
       unconditionally on every visual's fields, regardless of title or
       field count — a stale reference is a fact about that one field,
       independent of how many other fields share its visual or whether
       it has a title at all. It deliberately does NOT guess which
       current field a stale name used to refer to (e.g. that "Channel"
       now means "Ch") — that requires human confirmation. It only
       surfaces the fact that the report references a name absent from
       the current schema, scoped to a table where resolvable, for a
       person to act on via the existing manual synonym override.

    Args:
        layout: Parsed layout JSON dictionary.
        tables: This connector's extracted table list (each with a
            "name" and "columns" list of {"name": ...} dicts).
        measures: This connector's extracted measure list (each with a
            "name").

    Returns:
        Tuple of (presentation metadata list, unresolved field
        reference list — each {"field", "field_type", "table",
        "occurrences"}).
    """
    presentation_metadata: List[Dict[str, Any]] = []
    seen_tuples = set()

    known_columns: Set[Tuple[str, str]] = set()
    columns_by_table: Dict[str, List[str]] = {}
    for table in tables:
        table_name = table.get("name")
        for column in table.get("columns", []):
            column_name = column.get("name")
            if table_name and column_name:
                known_columns.add((table_name, column_name))
                columns_by_table.setdefault(table_name, []).append(column_name)
    known_measures: Set[str] = {m.get("name") for m in measures if m.get("name")}
    unresolved_occurrences: Dict[Tuple[str, str, Optional[str]], int] = {}

    sections = layout.get("sections", [])
    if not isinstance(sections, list):
        return [], []

    for section in sections:
        if not isinstance(section, dict):
            continue
        page = section.get("displayName", "Unknown")
        if not page or not isinstance(page, str):
            page = "Unknown"

        visual_containers = section.get("visualContainers", [])
        if not isinstance(visual_containers, list):
            continue

        for visual in visual_containers:
            if not isinstance(visual, dict):
                continue

            # 1. Parse config JSON (which can be a string or already a dict)
            config_val = visual.get("config")
            config_dict = {}
            if isinstance(config_val, dict):
                config_dict = config_val
            elif isinstance(config_val, str) and config_val.strip():
                try:
                    config_dict = json.loads(config_val)
                except Exception as exc:
                    logger.warning("Malformed JSON in visual container config: %s", exc)
                    continue

            # 2. Extract visual type (first letter capitalized, rest unchanged)
            visual_type = "Unknown"
            single_visual = config_dict.get("singleVisual", {})
            if isinstance(single_visual, dict):
                v_type = single_visual.get("visualType")
                if isinstance(v_type, str) and v_type.strip():
                    v_type_strip = v_type.strip()
                    visual_type = v_type_strip[0].upper() + v_type_strip[1:] if v_type_strip else "Unknown"

            # 3. Extract referenced fields (measures + columns) using the
            #    linear recursive traversal helper. Computed unconditionally,
            #    before the title check below, since unresolved-reference
            #    detection needs every visual's fields regardless of
            #    whether it has a title.
            field_refs = extract_field_references(visual)

            for field_name, field_kind, table_name in field_refs:
                if field_kind in ("measure", "unknown"):
                    if field_name in known_measures:
                        continue
                    key = (field_name, "measure", None)
                else:
                    # A column reference with no resolvable table is
                    # never trustworthy enough to flag — it can't be
                    # distinguished from a same-named column that's
                    # simply unresolved rather than actually stale.
                    if table_name is None or (table_name, field_name) in known_columns:
                        continue
                    key = (field_name, "column", table_name)

                unresolved_occurrences[key] = unresolved_occurrences.get(key, 0) + 1

            if not field_refs:
                continue

            # 4. Extract title using title priority order
            # Priority:
            # 1) singleVisual.vcObjects.title
            # 2) singleVisual.objects.title
            # 3) Fallback text/value fields
            title = None
            if isinstance(single_visual, dict):
                for title_key in ("vcObjects", "objects"):
                    key_val = single_visual.get(title_key)
                    if isinstance(key_val, dict):
                        title_section = key_val.get("title")
                        if isinstance(title_section, list) and title_section:
                            properties = title_section[0].get("properties")
                            if isinstance(properties, dict):
                                text_prop = properties.get("text")
                                if isinstance(text_prop, dict):
                                    expr = text_prop.get("expr")
                                    if isinstance(expr, dict) and "Literal" in expr:
                                        literal = expr["Literal"]
                                        if isinstance(literal, dict) and "Value" in literal:
                                            title = literal["Value"]
                                    if not title and "value" in text_prop:
                                        title = text_prop["value"]

                    if title is not None:
                        if isinstance(title, str):
                            title = title.strip("'").strip()
                            if title:
                                break
                        else:
                            title = None

            # Skip visual if title is missing or empty
            if not title:
                continue

            # A visual's title describes what the visual as a whole shows,
            # not any single field in isolation. For a card/KPI bound to
            # exactly one field, the title genuinely is a synonym for that
            # field (e.g. a card titled "R1" bound only to "Revenue"). But
            # a chart combining a measure with a dimension (e.g. a bar
            # chart titled "Total Sales by Channel" showing measure
            # "Total Sales" broken down by column "Channel") has a title
            # describing their combination — attributing it to either
            # field individually is wrong (e.g. "Channel" would wrongly
            # inherit "Total Sales by Channel" as an alias). Only
            # single-field visuals are unambiguous enough to trust.
            if len(field_refs) != 1:
                continue

            # 5. Populate and deduplicate tuples
            for field_name, field_kind, table_name in field_refs:
                # Deduplicate identical (field, kind, table, title, page, visual_type) tuples
                tpl = (field_name, field_kind, table_name, title, page, visual_type)
                if tpl not in seen_tuples:
                    seen_tuples.add(tpl)
                    presentation_metadata.append({
                        "field": field_name,
                        "field_type": field_kind,
                        "table": table_name,
                        "title": title,
                        "page": page,
                        "visual_type": visual_type,
                    })

    unresolved_field_references = []
    for (f, k, tbl), count in unresolved_occurrences.items():
        candidates = (columns_by_table.get(tbl, []) if tbl else []) if k == "column" else list(known_measures)
        unresolved_field_references.append({
            "field": f,
            "field_type": k,
            "table": tbl,
            "occurrences": count,
            "resolved_target_field": best_matching_field(f, candidates),
        })
    return presentation_metadata, unresolved_field_references

def best_matching_field(stale_name: str, candidates: List[str]) -> Optional[str]:
    """Find the one current field name a stale report reference most
    plausibly used to be, by name similarity alone.

    A stale reference's table is known, but never which specific
    column within it the name used to refer to — Power BI queries
    reference fields purely by name string, with no stable ID
    preserved in the report layout across a rename. Name similarity
    (one name is a prefix of the other, e.g. "Ch" / "Channel") is the
    only signal available without asking a person.

    Deliberately conservative: only returns a match when exactly one
    candidate qualifies. Zero matches means nothing looks related;
    more than one means the name alone can't tell them apart — in both
    cases, guessing would be worse than saying nothing, so ``None`` is
    returned and the caller shows no suggestion at all rather than
    broadcasting an ambiguous one to multiple fields.
    """
    def _norm(s: str) -> str:
        return s.lower().replace(" ", "").replace("_", "")

    stale_norm = _norm(stale_name)
    matches = []
    for candidate in candidates:
        candidate_norm = _norm(candidate)
        if not candidate_norm or candidate_norm == stale_norm:
            continue
        shorter, longer = sorted((candidate_norm, stale_norm), key=len)
        if len(shorter) >= 2 and longer.startswith(shorter):
            matches.append(candidate)

    return matches[0] if len(matches) == 1 else None


def summarize_report_aliases(
    presentation_metadata: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Report titles/captions -> (field_aliases, ambiguous_report_aliases).

    A caption claimed by more than one field is excluded from every field
    and reported as ambiguous instead of guessed."""
    aliases_by_field: Dict[Tuple[str, str, Optional[str]], List[str]] = {}

    def _add_alias(field_name: str, field_kind: str, table_name: Optional[str], title: str) -> None:
        key = (field_name, field_kind, table_name if field_kind == "column" else None)
        if key not in aliases_by_field:
            aliases_by_field[key] = []
        if title and title.lower() != field_name.lower():
            if title not in aliases_by_field[key]:
                aliases_by_field[key].append(title)

    for item in presentation_metadata:
        f = item["field"]
        k = item["field_type"]
        tbl = item.get("table")
        t = item["title"]
        if k == "unknown":
            # No adjacent Measure/Column node to type this reference.
            # It can safely feed the measure bucket (measure names are
            # globally unique in valid TMSL, so no table info is ever
            # needed there). It cannot safely feed the column bucket:
            # columns are only unique per-table, and an untyped
            # reference never resolves a table either — attributing it
            # to a column risks misattributing across same-named
            # columns in different tables, so it's skipped entirely
            # for columns rather than guessed.
            _add_alias(f, "measure", None, t)
        else:
            _add_alias(f, k, tbl, t)

    # A title/caption is only trustworthy as a synonym for ONE field.
    # Two different visuals can coincidentally (or via copy/paste,
    # or a stale rename) attach the same caption to two DIFFERENT
    # fields -- if both kept it, Cortex Analyst would have no way
    # to tell which field a business user querying that term
    # actually meant. Rather than let an ambiguous term silently
    # attach to whichever field happened to claim it, it's
    # stripped from every field that claims it (same "don't guess"
    # philosophy as _best_matching_field above) and reported
    # separately so a person can see what was excluded and why.
    alias_display: Dict[str, str] = {}
    alias_lower_to_keys: Dict[str, Set[Tuple[str, str, Optional[str]]]] = {}
    for field_key, aliases in aliases_by_field.items():
        for alias in aliases:
            alias_lower = alias.casefold()
            alias_display.setdefault(alias_lower, alias)
            alias_lower_to_keys.setdefault(alias_lower, set()).add(field_key)

    ambiguous_lowers = {
        alias_lower
        for alias_lower, keys in alias_lower_to_keys.items()
        if len(keys) > 1
    }

    ambiguous_report_aliases: List[Dict[str, Any]] = []
    if ambiguous_lowers:
        for field_key in aliases_by_field:
            aliases_by_field[field_key] = [
                alias
                for alias in aliases_by_field[field_key]
                if alias.casefold() not in ambiguous_lowers
            ]
        for alias_lower in sorted(ambiguous_lowers):
            conflicting_keys = sorted(
                alias_lower_to_keys[alias_lower],
                key=lambda k: (k[0], k[1], k[2] or ""),
            )
            ambiguous_report_aliases.append({
                "alias": alias_display[alias_lower],
                "fields": [
                    {"field": f, "field_type": k, "table": tbl}
                    for (f, k, tbl) in conflicting_keys
                ],
            })
        logger.info(
            "Excluded %d report-layer alias(es) claimed by more than one "
            "field from synonyms, to avoid ambiguous business-term "
            "resolution: %s",
            len(ambiguous_lowers),
            [a["alias"] for a in ambiguous_report_aliases],
        )

    field_aliases = [
        {"field": f, "field_type": k, "table": tbl, "aliases": aliases}
        for (f, k, tbl), aliases in aliases_by_field.items()
        if aliases
    ]
    return field_aliases, ambiguous_report_aliases
