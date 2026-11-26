"""Phase 3.7: the metric-view writer.

Turns the planner's plan (``databricks_view_plan``) into the exact text
Databricks executes, per planned view:

    CREATE VIEW `catalog`.`schema`.`view` WITH METRICS LANGUAGE YAML AS $$
    version: 1.1
    ...
    $$

A pure, deterministic text generator:

* no Databricks calls, no decisions -- names and expressions are copied from
  the plan exactly; nothing is renamed, re-normalised or rewritten;
* every check fails closed with a structured ``WriterError`` (illegal or
  duplicate names, references outside the view, ``$$`` in the body, limits,
  a stale plan, a table with no location);
* YAML is produced by a serializer (never by concatenation), the join key
  ``on`` is always quoted (YAML 1.1 reads a bare ``on`` as boolean true), and
  the emitted YAML is parsed back and compared with what was intended;
* the same plan and inputs give byte-identical output.

Syntax follows the Databricks YAML 1.1 reference (docs.databricks.com
uc-semantics/metric-views/yaml-reference): ``fields`` (``dimensions`` is
accepted as a synonym), nested many-to-one joins under their parent with
conditions in local aliases, ``cardinality: one_to_many`` (Runtime 18.1+),
``display_name`` / ``synonyms`` (up to 10, each <= 255) and ``format``.
Details the 3.5 baseline probe confirms live are kept as constants here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

WRITER_REVISION = "1"
INCOMPLETE_WARNING = ("INCOMPLETE – one or more source tables are empty placeholders. "
                      "Results are not verified for business use.")
YAML_VERSION = "1.1"
FIELDS_KEY = "fields"             # documented canonical key ("dimensions" is accepted as a synonym)
MAX_NAME = 255
MAX_LABEL = 255
MAX_SYNONYMS = 10
MAX_COMMENT = 4096                # conservative; the platform limit is confirmed by the 3.5 baseline probe

_ISO_SYMBOLS = {"€": "EUR", "£": "GBP", "₹": "INR"}
# A bare "$" / "¥" is ambiguous (USD, CAD, AUD, ... / JPY, CNY): only an explicit
# locale tag ([$$-409], [$¥-ja-JP], ...) identifies the currency.
_LOCALE_CURRENCY = {
    ("$", "409"): "USD", ("$", "en-us"): "USD", ("$", "1009"): "CAD", ("$", "en-ca"): "CAD",
    ("$", "c09"): "AUD", ("$", "en-au"): "AUD", ("$", "1409"): "NZD", ("$", "en-nz"): "NZD",
    ("¥", "411"): "JPY", ("¥", "ja-jp"): "JPY", ("¥", "804"): "CNY", ("¥", "zh-cn"): "CNY",
    ("€", ""): "EUR", ("£", ""): "GBP",
}


class WriterError(ValueError):
    """The plan cannot be written (fail closed); ``code`` says why."""

    def __init__(self, code: str, message: str, view: Optional[str] = None) -> None:
        super().__init__(f"{code}: {message}" + (f" (view {view})" if view else ""))
        self.code, self.message, self.view = code, message, view


@dataclass
class RenderedView:
    view: str
    fqn: str
    yaml: str
    create_sql: str
    body_sha256: str
    tables: List[str] = field(default_factory=list)          # model tables the view reads
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"view": self.view, "fqn": self.fqn, "yaml": self.yaml, "create_sql": self.create_sql,
                "body_sha256": self.body_sha256, "tables": list(self.tables), "warnings": list(self.warnings)}


# --- probe-only source substitution (3.5) ----------------------------------------------

_PROBE_KEY = object()


class ProbeSources:
    """Inline queries (``SELECT ... FROM VALUES ...``) standing in for tables -- ONLY for
    the 3.5 probe executor's trusted, code-defined fixtures. Construction needs the
    module-private key; projects, configs and the screen have no way to pass one, and
    anything written with it is marked as a probe artifact (never deployable)."""

    def __init__(self, queries: Mapping[str, str], *, key: object) -> None:
        if key is not _PROBE_KEY:
            raise PermissionError("probe source substitution is reserved for the 3.5 probe executor")
        self.queries = dict(queries)


# --- helpers ----------------------------------------------------------------------------

def quote_identifier(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def quote_three_part(catalog: str, schema: str, name: str) -> str:
    return ".".join(quote_identifier(p) for p in (catalog, schema, name))


_SIMPLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def table_reference(catalog: str, schema: str, name: str) -> str:
    """A three-part table name for a YAML ``source``: as documented (plain dotted),
    with backticks only on a part that needs them (spaces, symbols, periods)."""
    return ".".join(p if _SIMPLE.fullmatch(p) else quote_identifier(p) for p in (catalog, schema, name))


def translate_format(fmt: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """A Power BI format string -> a Databricks ``format`` spec, only when it can be
    expressed exactly. Returns (spec or None, warning or None); the original stays in
    the plan either way."""
    if fmt is None or not str(fmt).strip():
        return None, None
    raw = str(fmt).strip()
    named = {
        "general number": (None, None),
        "fixed": ({"type": "number", "decimal_places": {"type": "exact", "places": 2},
                   "hide_group_separator": True}, None),
        "standard": ({"type": "number", "decimal_places": {"type": "exact", "places": 2}}, None),
        "percent": ({"type": "percentage", "decimal_places": {"type": "exact", "places": 2}}, None),
    }
    if raw.lower() in named:
        return named[raw.lower()]
    section = raw.split(";")[0].strip()
    note = "negative/zero format sections are not carried" if ";" in raw else None

    if section in ("yyyy-mm-dd", "yyyy-MM-dd"):
        return {"type": "date", "date_format": "year_month_day"}, note

    currency: Optional[str] = None
    symbol: Optional[str] = None
    m = re.match(r"^\[\$([^\-\]]*)-([0-9A-Za-z\-]+)\]", section)
    if m:
        symbol, locale = m.group(1), m.group(2).lower()
        currency = _LOCALE_CURRENCY.get((symbol, locale))
        section = section[m.end():].strip()
        if currency is None:
            return None, f"format {raw!r} omitted: currency {symbol!r} with locale {m.group(2)!r} is not mapped"
    else:
        lead = re.match(r'^(?:\\?(\$)|"?([€£₹¥])"?)\s*', section)
        if lead:
            symbol = lead.group(1) or lead.group(2)
            section = section[lead.end():].strip()
            currency = _ISO_SYMBOLS.get(symbol)
            if currency is None:
                return None, f"format {raw!r} omitted: currency symbol {symbol!r} is ambiguous (no locale tag)"

    pct = section.endswith("%")
    body = section[:-1].strip() if pct else section
    if not re.fullmatch(r"[#0,]*0(?:\.0+)?|[#0,]*#(?:\.0+)?", body or "x"):
        return None, f"format {raw!r} omitted: pattern not expressible exactly"
    places = len(body.split(".", 1)[1]) if "." in body else 0
    if places > 10:
        return None, f"format {raw!r} omitted: more than 10 decimal places"
    grouping = "," in body.split(".", 1)[0]
    decimals = {"type": "exact", "places": places}
    if pct:
        if currency:
            return None, f"format {raw!r} omitted: currency and percent together"
        spec: Dict[str, Any] = {"type": "percentage", "decimal_places": decimals}
    elif currency:
        spec = {"type": "currency", "currency_code": currency, "decimal_places": decimals}
    else:
        spec = {"type": "number", "decimal_places": decimals}
    if not grouping:
        spec["hide_group_separator"] = True
    return spec, note


def _comment(*parts: Optional[str]) -> Optional[str]:
    text = "\n\n".join(p.strip() for p in parts if p and str(p).strip())
    return text or None


# --- rendering ---------------------------------------------------------------------------

class _ViewWriter:
    def __init__(self, view: Dict[str, Any], locations: Dict[str, Tuple[str, str, str]],
                 probe: Optional[ProbeSources], naming: Any) -> None:
        self.v, self.locations, self.probe, self.naming = view, locations, probe, naming
        self.warnings: List[str] = []
        self.name = str(view.get("name") or "")

    def fail(self, code: str, message: str) -> None:
        raise WriterError(code, message, self.name)

    def source_of(self, dataset: str) -> str:
        if self.probe is not None:
            if dataset not in self.probe.queries:
                self.fail("probe_source_missing", f"no inline query for table {dataset}")
            return self.probe.queries[dataset]
        loc = self.locations.get(dataset)
        if not loc:
            self.fail("table_location_missing", f"table {dataset} has no location (bound or proposed)")
        return table_reference(*loc)

    def check_name(self, name: str, what: str, generated: bool = True) -> None:
        """The planner's own rule (planner/names.check_name): a generated name must
        follow the naming policy, a preserved one must be a legal identifier.
        Validation only -- the name is never re-normalised or changed."""
        from semabridge.target_backends.databricks.planner.names import check_name

        problem = check_name(name, generated) if name else "empty name"
        if problem or len(name) > MAX_NAME:
            self.fail("illegal_name", f"{what}: {problem or 'name too long'}")

    def check_refs(self, expr: str, allowed: set, what: str) -> None:
        from semabridge.target_backends.databricks.planner.sqltext import reference_paths

        if not str(expr or "").strip():
            self.fail("empty_expression", f"{what} has no expression")
        for path, column in reference_paths(expr):
            if path.lower() not in allowed:
                self.fail("unknown_reference", f"{what} reads {path}.{column}, which is not part of this view")

    def labels(self, entry: Dict[str, Any], what: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        display = str(entry.get("display_name") or "")
        if display:
            if len(display) > MAX_LABEL:
                self.fail("label_too_long", f"{what} display name is longer than {MAX_LABEL} characters")
            out["display_name"] = display
        synonyms = [str(s) for s in entry.get("synonyms") or [] if str(s)]
        if synonyms:
            if len(synonyms) > MAX_SYNONYMS or any(len(s) > MAX_LABEL for s in synonyms):
                self.fail("synonyms_over_limit", f"{what} has more than {MAX_SYNONYMS} synonyms or one over {MAX_LABEL}")
            out["synonyms"] = synonyms
        return out

    def fmt(self, entry: Dict[str, Any], what: str) -> Optional[Dict[str, Any]]:
        spec, warning = translate_format(entry.get("source_format"))
        if warning:
            self.warnings.append(f"{what}: {warning}")
        return spec

    def joins(self) -> Tuple[List[Dict[str, Any]], set, Dict[str, str]]:
        joins = list(self.v.get("joins") or [])
        by_parent: Dict[str, List[Dict[str, Any]]] = {}
        full_names = {str(j.get("name")) for j in joins}
        for j in joins:
            parent = str(j.get("parent") or "source")
            if parent != "source" and parent not in full_names:
                self.fail("join_parent_missing", f"join {j.get('name')} has no parent {parent} in this view")
            by_parent.setdefault(parent, []).append(j)
        aliases: Dict[str, str] = {}

        def build(parent: str) -> List[Dict[str, Any]]:
            out = []
            for j in by_parent.get(parent, []):
                alias = str(j.get("alias") or "")
                on_local = str(j.get("on_local") or "")
                if not alias or not on_local:
                    self.fail("plan_predates_writer", "the plan has no local join conditions; run the dry run again")
                self.check_name(alias, "join")
                low = alias.lower()
                if low == "source" or low in aliases:
                    self.fail("duplicate_join_alias", f"join alias {alias} is used twice")
                aliases[low] = str(j.get("name"))
                self.check_refs(on_local, {str(j.get("parent_alias") or "source").lower(), low},
                                f"join {alias} condition")
                entry: Dict[str, Any] = {"name": alias, "source": self.source_of(str(j.get("dataset"))), "on": on_local}
                if str(j.get("cardinality") or "many_to_one") == "one_to_many":
                    entry["cardinality"] = "one_to_many"
                children = build(str(j.get("name")))
                if children:
                    if entry.get("cardinality") == "one_to_many":
                        self.fail("one_to_many_has_children", f"one-to-many join {alias} cannot have nested joins")
                    entry["joins"] = children
                out.append(entry)
            return out

        built = build("source")
        if sum(1 for _ in aliases) != len(joins):
            self.fail("join_tree_incomplete", "some joins are not reachable from the source")
        return built, {"source"} | {str(j.get("name")).lower() for j in joins}, aliases

    def render(self, model_label: str) -> Tuple[Dict[str, Any], List[str]]:
        self.check_name(self.name, "view", generated=self.name.endswith("_metrics"))
        tables = [str(self.v.get("source_dataset"))] + [str(j.get("dataset")) for j in self.v.get("joins") or []]
        joins, paths, aliases = self.joins()
        doc: Dict[str, Any] = {}
        provenance = f"Created by SemaBridge from model {model_label}"
        incomplete = str(self.v.get("readiness") or "real") != "real" or any(
            str(j.get("readiness") or "real") != "real" for j in self.v.get("joins") or [])
        # 3.8: a view over empty placeholder tables says so in its approved definition
        # (part of the bundle, so it is previewed and hashed -- never added at deploy).
        doc["comment"] = f"{INCOMPLETE_WARNING} {provenance}" if incomplete else provenance
        doc["source"] = self.source_of(str(self.v.get("source_dataset")))
        if joins:
            doc["joins"] = joins

        seen: Dict[str, str] = {}

        def unique(name: str, what: str) -> None:
            low = name.lower()
            if low in seen:
                self.fail("duplicate_name", f"{what} {name} has the same name as {seen[low]}")
            if low == "source" or low in aliases:
                self.fail("name_is_join_alias", f"{what} {name} is also a join alias")
            seen[low] = f"{what} {name}"

        fields_out = []
        for f in self.v.get("fields") or []:
            name = str(f.get("name") or "")
            self.check_name(name, "field", bool(f.get("name_generated", True)))
            unique(name, "field")
            self.check_refs(str(f.get("expr") or ""), paths, f"field {name}")
            entry: Dict[str, Any] = {"name": name, "expr": str(f.get("expr"))}
            entry.update(self.labels(f, f"field {name}"))
            comment = _comment(f.get("description"))
            if comment:
                entry["comment"] = comment
            spec = self.fmt(f, f"field {name}")
            if spec:
                entry["format"] = spec
            fields_out.append(entry)

        measures_out = []
        for m in self.v.get("measures") or []:
            name = str(m.get("name") or "")
            self.check_name(name, "measure", bool(m.get("name_generated", True)))
            unique(name, "measure")
            self.check_refs(str(m.get("expr") or ""), paths, f"measure {name}")
            entry = {"name": name, "expr": str(m.get("expr"))}
            entry.update(self.labels(m, f"measure {name}"))
            comment = _comment(m.get("description"), m.get("comment"))
            if comment:
                entry["comment"] = comment
            spec = self.fmt(m, f"measure {name}")
            if spec:
                entry["format"] = spec
            measures_out.append(entry)

        if fields_out:
            doc[FIELDS_KEY] = fields_out
        if measures_out:
            doc["measures"] = measures_out
        if not fields_out and not measures_out:
            self.fail("empty_view", "the view has no fields and no measures")
        for text in _all_strings(doc):
            if len(text) > MAX_COMMENT:
                self.fail("text_too_long", f"a text value is longer than {MAX_COMMENT} characters")
        return doc, tables


def _all_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_strings(v)


def dump_yaml(doc: Dict[str, Any]) -> str:
    """``version: 1.1`` first (the documented form), then the document from a
    serializer with fixed settings: block style, insertion order, Unicode kept,
    no line wrapping. PyYAML quotes a key such as ``on`` that YAML 1.1 would read
    as a boolean."""
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10 ** 9)
    return f"version: {YAML_VERSION}\n{body}"


def _round_trip(text: str, doc: Dict[str, Any], view: str) -> None:
    loaded = yaml.safe_load(text)
    # A bare `on` key would read back as boolean True and fail this comparison.
    if not isinstance(loaded, dict) or str(loaded.pop("version", "")) != YAML_VERSION or loaded != doc:
        raise WriterError("round_trip_mismatch", "the emitted YAML does not read back as intended", view)


def table_locations(plan: Dict[str, Any]) -> Dict[str, Tuple[str, str, str]]:
    """{model table: (catalog, schema, name)} from the plan's ``tables`` (bound and
    proposed alike -- a proposed location is a location for the text, never a
    verified binding)."""
    out: Dict[str, Tuple[str, str, str]] = {}
    for t in plan.get("tables") or []:
        if t.get("catalog") and t.get("schema") and t.get("name"):
            out[str(t["dataset"])] = (str(t["catalog"]), str(t["schema"]), str(t["name"]))
    return out


def render_plan(plan: Dict[str, Any], *, catalog: Optional[str], schema: Optional[str], model_label: str,
                probe: Optional[ProbeSources] = None) -> List[RenderedView]:
    """Every planned view's definition, in plan order. Raises WriterError (fail closed)."""
    from semabridge.target_backends.databricks.planner.names import NAMING

    meta = plan.get("meta") or {}
    if meta.get("status") == "stale":
        raise WriterError("plan_stale", "the plan is out of date; re-plan or run the dry run again")
    if plan.get("platform") != "databricks":
        raise WriterError("not_a_databricks_plan", "the input is not a Databricks metric-view plan")
    if not catalog or not schema:
        raise WriterError("output_location_missing", "the project has no output catalog/schema configured")
    if probe is not None and not isinstance(probe, ProbeSources):
        raise PermissionError("probe source substitution is reserved for the 3.5 probe executor")
    locations = table_locations(plan)
    out: List[RenderedView] = []
    names = set()
    for v in plan.get("views") or []:
        w = _ViewWriter(v, locations, probe, NAMING)
        doc, tables = w.render(model_label)
        if w.name.lower() in names:
            raise WriterError("duplicate_view", "two views have the same name", w.name)
        names.add(w.name.lower())
        text = dump_yaml(doc)
        if "$$" in text:
            raise WriterError("dollar_quote_in_body", "a value contains '$$', which would end the definition", w.name)
        _round_trip(text, doc, w.name)
        fqn = quote_three_part(catalog, schema, w.name)
        out.append(RenderedView(
            view=w.name, fqn=fqn, yaml=text,
            create_sql=f"CREATE VIEW {fqn} WITH METRICS LANGUAGE YAML AS $$\n{text}$$",
            body_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            tables=tables, warnings=w.warnings,
        ))
    return out
