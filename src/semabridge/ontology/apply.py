"""Apply ontology metadata to a migrated semantic model (SML or OSI).

Layers, highest precedence first (deterministic):

1. per-element user override (``synonym_overrides`` table: synonyms,
   description, business_rules, data_steward) keyed by (model, table, element);
2. domain ontologies attached to the project, in attachment order;
3. BI source metadata already on the model.

Matching of ontology entries to model elements is explicit-then-exact:
``maps_to`` keys first, then a case-insensitive exact match of the entry's
name / label / synonyms (and a concept's ``physical_table``) against the
element's name / label (/ source table). No fuzzy guessing. Entries that match
nothing, or match more than one element, are reported (never silently dropped).

The resulting description (plus a concise "Business rules: ...; Data steward:
..." suffix) lands on the element's ``description`` and synonyms on its
``synonyms`` list. The Snowflake clause builders emit those as COMMENT /
WITH SYNONYMS and the Cortex Analyst YAML renderer as description / synonyms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

RULES_PREFIX = "Business rules:"
STEWARD_PREFIX = "Data steward:"


@dataclass
class _Annot:
    synonyms: List[str] = field(default_factory=list)
    description: Optional[str] = None
    business_rules: List[str] = field(default_factory=list)
    data_steward: Optional[str] = None
    filter_expression: Optional[str] = None
    derives_from: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)


def _cf(value: Any) -> str:
    return str(value or "").strip().casefold()


def _union(*lists: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for lst in lists:
        for v in lst or []:
            t = " ".join(str(v or "").split())
            if t and t.casefold() not in seen:
                seen.add(t.casefold())
                out.append(t)
    return out


def compose_description(base: Optional[str], rules: List[str], steward: Optional[str]) -> str:
    """Description + concise rules / steward suffix (idempotent)."""
    text = (base or "").strip()
    parts: List[str] = []
    rules = [r.strip().rstrip(".") for r in rules or [] if str(r or "").strip()]
    if rules:
        parts.append(f"{RULES_PREFIX} {'; '.join(rules)}.")
    if steward and str(steward).strip():
        parts.append(f"{STEWARD_PREFIX} {str(steward).strip()}.")
    suffix = " ".join(parts)
    if not suffix:
        return text
    if suffix in text:
        return text
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return f"{text} {suffix}".strip()


class _ModelIndex:
    """Lookup tables over an SML/OSI model's tables, columns and metrics."""

    def __init__(self, model: Any):
        self.model = model
        self.datasets = list(getattr(model, "datasets", []) or [])
        self.metrics = list(getattr(model, "metrics", []) or [])
        self.ds_by_key: Dict[str, List[Any]] = {}
        for ds in self.datasets:
            for key in {_cf(ds.unique_name), _cf(getattr(ds, "label", "")), _cf(getattr(ds, "source_table", ""))}:
                if key:
                    self.ds_by_key.setdefault(key, [])
                    if ds not in self.ds_by_key[key]:
                        self.ds_by_key[key].append(ds)
        self.metric_by_key: Dict[str, List[Any]] = {}
        for m in self.metrics:
            for key in {_cf(m.unique_name), _cf(getattr(m, "label", ""))}:
                if key:
                    self.metric_by_key.setdefault(key, [])
                    if m not in self.metric_by_key[key]:
                        self.metric_by_key[key].append(m)

    @staticmethod
    def columns_matching(ds: Any, key: str) -> List[Any]:
        out = []
        for col in getattr(ds, "columns", []) or []:
            if key in {_cf(col.unique_name), _cf(getattr(col, "label", ""))}:
                out.append(col)
        return out

    def dataset_exact(self, name: str) -> Optional[Any]:
        for ds in self.datasets:
            if _cf(ds.unique_name) == _cf(name):
                return ds
        return None

    def resolve_column_ref(self, ref: str) -> List[Tuple[Any, Any]]:
        """'Table.Column' -> [(ds, col)]; bare 'Column' -> every table's matching column."""
        ref = str(ref or "").strip()
        if "." in ref:
            t, c = ref.split(".", 1)
            out = []
            for ds in self.ds_by_key.get(_cf(t), []):
                out.extend((ds, col) for col in self.columns_matching(ds, _cf(c)))
            return out
        out = []
        for ds in self.datasets:
            out.extend((ds, col) for col in self.columns_matching(ds, _cf(ref)))
        return out

    def resolve_metric_ref(self, ref: str) -> List[Any]:
        ref = str(ref or "").strip()
        if "." in ref and _cf(ref) not in self.metric_by_key:
            t, n = ref.split(".", 1)
            return [m for m in self.metric_by_key.get(_cf(n), []) if _cf(getattr(m, "dataset", "")) == _cf(t)]
        return list(self.metric_by_key.get(_cf(ref), []))


class OntologyApplier:
    def __init__(self, model: Any):
        self.model = model
        self.idx = _ModelIndex(model)
        # element id -> annotation layers (list ordered high -> low precedence)
        self.table_layers: Dict[int, List[_Annot]] = {}
        self.column_layers: Dict[int, List[_Annot]] = {}
        self.metric_layers: Dict[int, List[_Annot]] = {}
        self._objs: Dict[int, Any] = {}
        self.unmatched: List[str] = []
        self.ambiguous: List[str] = []
        self.matched: List[str] = []
        # Domain metrics whose formula differs from the BI (PBIX) measure they
        # annotate: BI formulas are read-only, so the domain formula is ignored.
        self.formula_overrides_ignored: List[Dict[str, Any]] = []

    # -------------------------------------------------------------- layers
    def _add(self, layers: Dict[int, List[_Annot]], obj: Any, annot: _Annot) -> None:
        self._objs[id(obj)] = obj
        layers.setdefault(id(obj), []).append(annot)

    # ---------------------------------------------------- user overrides
    def add_user_overrides(
        self,
        overrides: Dict[Tuple[str, str, str], Dict[str, Any]],
        model_names: Iterable[str],
    ) -> None:
        names = {_cf(n) for n in model_names if _cf(n)}
        for (model_key, table_key, elem_key), data in sorted(overrides.items()):
            if model_key not in names:
                continue
            annot = _Annot(
                synonyms=list(data.get("synonyms") or []),
                description=(data.get("description") or None),
                business_rules=list(data.get("business_rules") or []),
                data_steward=(data.get("data_steward") or None),
                sources=["user_override"],
            )
            label = f"user override {table_key}.{elem_key}"
            ds = self.idx.dataset_exact(table_key)
            target = None
            kind = None
            if ds is not None:
                if elem_key in ("", "*", "__table__"):
                    target, kind = ds, "table"
                else:
                    cols = self.idx.columns_matching(ds, elem_key)
                    if cols:
                        target, kind = cols[0], "column"
            if target is None:
                ms = [m for m in self.idx.metric_by_key.get(elem_key, []) if _cf(getattr(m, "dataset", "")) == table_key]
                if not ms:
                    ms = self.idx.metric_by_key.get(elem_key, [])
                if len(ms) == 1:
                    target, kind = ms[0], "metric"
                elif len(ms) > 1:
                    self.ambiguous.append(f"{label}: matches {len(ms)} metrics; not applied")
                    continue
            if target is None:
                self.unmatched.append(f"{label}: no matching table/column/metric in this model")
                continue
            layers = {"table": self.table_layers, "column": self.column_layers, "metric": self.metric_layers}[kind]
            self._add(layers, target, annot)
            self.matched.append(label)

    # ---------------------------------------------------- domain ontology
    @staticmethod
    def _effective_rules_and_filter(
        dom: Any,
        item: Any,
    ) -> Tuple[List[str], Optional[str]]:
        rules = list(getattr(item, "business_rules", None) or [])
        filters: List[str] = []
        inline_f = (getattr(item, "filter_expression", None) or "").strip()
        if inline_f:
            filters.append(inline_f)
            rule_txt = f"Filter: {inline_f}"
            if rule_txt.casefold() not in {r.casefold() for r in rules}:
                rules.append(rule_txt)
        item_key = _cf(getattr(item, "name", ""))
        for pol in getattr(dom, "policies", None) or []:
            applies = [_cf(x) for x in (getattr(pol, "applies_to", None) or [])]
            if not applies or "*" in applies or item_key in applies:
                pf = (getattr(pol, "filter_expression", None) or "").strip()
                if pf and pf.casefold() not in {f.casefold() for f in filters}:
                    filters.append(pf)
                pol_desc = (getattr(pol, "description", None) or pol.name).strip()
                pol_rule = f"{pol_desc} (SQL: {pf})" if pf else pol_desc
                if pol_rule and pol_rule.casefold() not in {r.casefold() for r in rules}:
                    rules.append(pol_rule)
        combined_filter = " AND ".join(filters) if filters else None
        return rules, combined_filter

    def add_domain(self, dom: Any) -> None:
        dname = dom.domain
        for concept in dom.concepts:
            if getattr(concept, "stale", False):
                continue
            label = f"domain '{dname}' concept '{concept.name}'"
            c_rules, c_filter = self._effective_rules_and_filter(dom, concept)
            annot = _Annot(
                synonyms=list(concept.synonyms),
                description=concept.description,
                business_rules=c_rules,
                data_steward=concept.data_steward,
                filter_expression=c_filter,
                sources=[f"domain:{dname}"],
            )
            datasets = self._match_concept(concept)
            matched_columns: List[Tuple[Any, Any]] = []
            if not datasets:
                # A concept may annotate a single column via maps_to 'Table.Column'.
                for ref in concept.maps_to:
                    if "." in ref:
                        matched_columns.extend(self.idx.resolve_column_ref(ref))
            if len(datasets) > 1:
                self.ambiguous.append(
                    f"{label}: matches several tables ({', '.join(d.unique_name for d in datasets)}); "
                    "add 'maps_to' to choose one"
                )
                continue
            if datasets:
                ds = datasets[0]
                self._add(self.table_layers, ds, annot)
                self.matched.append(f"{label} -> table {ds.unique_name}")
                for attr in concept.attributes:
                    if getattr(attr, "stale", False):
                        continue
                    a_label = f"{label} attribute '{attr.name}'"
                    cols = self._match_attribute(ds, attr)
                    if len(cols) == 1:
                        self._add(
                            self.column_layers,
                            cols[0],
                            _Annot(synonyms=list(attr.synonyms), description=attr.description, sources=[f"domain:{dname}"]),
                        )
                        self.matched.append(f"{a_label} -> column {ds.unique_name}.{cols[0].unique_name}")
                    elif len(cols) > 1:
                        self.ambiguous.append(f"{a_label}: matches several columns of {ds.unique_name}")
                    else:
                        self.unmatched.append(f"{a_label}: no column of table '{ds.unique_name}' matches")
            elif len({id(c) for _, c in matched_columns}) == 1:
                ds, col = matched_columns[0]
                self._add(self.column_layers, col, annot)
                self.matched.append(f"{label} -> column {ds.unique_name}.{col.unique_name}")
            elif matched_columns:
                self.ambiguous.append(f"{label}: maps_to matches several columns")
            else:
                self.unmatched.append(f"{label}: no table in this model matches (maps_to / name / synonyms)")

        for metric in dom.metrics:
            if getattr(metric, "stale", False):
                continue
            label = f"domain '{dname}' metric '{metric.name}'"
            m_rules, m_filter = self._effective_rules_and_filter(dom, metric)
            cands = self._match_metric(metric)
            if len(cands) == 1:
                self._add(
                    self.metric_layers,
                    cands[0],
                    _Annot(
                        synonyms=list(metric.synonyms),
                        description=metric.description,
                        business_rules=m_rules,
                        data_steward=metric.data_steward,
                        filter_expression=m_filter,
                        derives_from=list(getattr(metric, "derives_from", None) or []),
                        sources=[f"domain:{dname}"],
                    ),
                )
                self.matched.append(f"{label} -> metric {cands[0].unique_name}")
                self._check_formula_override(dname, metric, cands[0])
            elif len(cands) > 1:
                self.ambiguous.append(
                    f"{label}: matches several metrics ({', '.join(m.unique_name for m in cands)}); add 'maps_to'"
                )
            else:
                self.unmatched.append(f"{label}: no metric in this model matches (maps_to / name / label / synonyms)")

    def _check_formula_override(self, dname: str, metric: Any, target: Any) -> None:
        """Record a finding when a domain metric carries a formula that differs from
        the BI measure it matched (the BI formula always wins; never silently)."""
        from semabridge.ontology.federated_loader import normalize_formula_for_compare as _norm

        dom_formulas = {
            _norm(f) for f in (getattr(metric, "sql_expression", None), getattr(metric, "dax_expression", None)) if _norm(f)
        }
        if not dom_formulas:
            return
        bi_raw = [getattr(target, "sql_expression", None), getattr(target, "expression", None)]
        agg = getattr(getattr(target, "aggregation", None), "value", getattr(target, "aggregation", None))
        src_col = getattr(target, "source_column", None)
        if agg and src_col:
            bi_raw.append(f"{agg}({src_col})")
        bi_formulas = {_norm(f) for f in bi_raw if _norm(f)}
        if not bi_formulas or dom_formulas & bi_formulas:
            return
        bi_display = next((str(f) for f in bi_raw if str(f or "").strip()), "")
        dom_display = getattr(metric, "sql_expression", None) or getattr(metric, "dax_expression", None)
        self.formula_overrides_ignored.append({
            "metric": metric.name,
            "domain": dname,
            "model_metric": target.unique_name,
            "domain_formula": dom_display,
            "bi_formula": bi_display,
            "message": (
                f"Domain '{dname}' metric '{metric.name}' defines formula {dom_display!r} but the BI measure "
                f"'{target.unique_name}' uses {bi_display!r}. BI (PBIX) formulas are read-only: the domain "
                "formula is ignored — rename the domain metric or remove its formula."
            ),
        })

    def _match_concept(self, concept: Any) -> List[Any]:
        if concept.maps_to:
            found: List[Any] = []
            for ref in concept.maps_to:
                if "." in ref:
                    continue
                for ds in self.idx.ds_by_key.get(_cf(ref), []):
                    if ds not in found:
                        found.append(ds)
            if found or any("." not in r for r in concept.maps_to):
                return found
        for keys in (
            [concept.name],
            [concept.physical_table, concept.label],
            list(concept.synonyms),
        ):
            found = []
            for key in keys:
                for ds in self.idx.ds_by_key.get(_cf(key), []) if key else []:
                    if ds not in found:
                        found.append(ds)
            if found:
                return found
        return []

    def _match_attribute(self, ds: Any, attr: Any) -> List[Any]:
        if attr.maps_to:
            found: List[Any] = []
            for ref in attr.maps_to:
                col_name = ref.split(".", 1)[1] if "." in ref else ref
                for col in self.idx.columns_matching(ds, _cf(col_name)):
                    if col not in found:
                        found.append(col)
            return found
        for keys in ([attr.name], list(attr.synonyms)):
            found = []
            for key in keys:
                for col in self.idx.columns_matching(ds, _cf(key)):
                    if col not in found:
                        found.append(col)
            if found:
                return found
        return []

    def _match_metric(self, metric: Any) -> List[Any]:
        def _narrow(cands: List[Any]) -> List[Any]:
            if len(cands) > 1 and metric.concept:
                narrowed = [m for m in cands if _cf(getattr(m, "dataset", "")) == _cf(metric.concept)]
                return narrowed or cands
            return cands

        if metric.maps_to:
            found: List[Any] = []
            for ref in metric.maps_to:
                for m in self.idx.resolve_metric_ref(ref):
                    if m not in found:
                        found.append(m)
            return _narrow(found)
        for keys in ([metric.name], [metric.label], list(metric.synonyms)):
            found = []
            for key in keys:
                for m in self.idx.metric_by_key.get(_cf(key), []) if key else []:
                    if m not in found:
                        found.append(m)
            if found:
                return _narrow(found)
        return []

    # ---------------------------------------------------------- resolve
    @staticmethod
    def _resolve(obj: Any, layers: List[_Annot]) -> Dict[str, Any]:
        description = next((a.description for a in layers if a.description and a.description.strip()), None)
        rules = next((a.business_rules for a in layers if a.business_rules), [])
        steward = next((a.data_steward for a in layers if a.data_steward and a.data_steward.strip()), None)
        filter_expr = next((a.filter_expression for a in layers if a.filter_expression and a.filter_expression.strip()), None)
        derives_from = _union(*[a.derives_from for a in layers], list(getattr(obj, "derives_from", None) or []))
        bi_desc = str(getattr(obj, "description", "") or "")
        base = description.strip() if description else bi_desc
        final_desc = compose_description(base, rules, steward)
        synonyms = _union(*[a.synonyms for a in layers], list(getattr(obj, "synonyms", None) or []))
        return {
            "description": final_desc,
            "synonyms": synonyms,
            "business_rules": rules,
            "data_steward": steward,
            "filter_expression": filter_expr,
            "derives_from": derives_from,
            "sources": [s for a in layers for s in a.sources],
        }

    def _element_name(self, kind: str, obj: Any) -> str:
        name = str(getattr(obj, "unique_name", "") or getattr(obj, "label", "") or "")
        if kind == "columns":
            for ds in self.idx.datasets:
                if any(c is obj for c in (getattr(ds, "columns", None) or [])):
                    return f"{ds.unique_name}.{name}"
        return name

    def apply(self) -> Dict[str, int]:
        counts = {"tables": 0, "columns": 0, "metrics": 0}
        # What the ontology actually changed on the model (vs. the BI source),
        # so a COPY-mode dry run can show description / synonym changes too.
        self.changes: Dict[str, List[Dict[str, Any]]] = {"descriptions": [], "synonyms": []}
        for kind, layers_map in (("tables", self.table_layers), ("columns", self.column_layers), ("metrics", self.metric_layers)):
            for oid, layers in layers_map.items():
                obj = self._objs[oid]
                old_desc = str(getattr(obj, "description", "") or "")
                old_syns = list(getattr(obj, "synonyms", None) or [])
                resolved = self._resolve(obj, layers)
                if (resolved["description"] or "") != old_desc:
                    self.changes["descriptions"].append({
                        "kind": kind, "name": self._element_name(kind, obj),
                        "old": old_desc, "new": resolved["description"] or "",
                    })
                if hasattr(obj, "synonyms") and {" ".join(str(s).split()).casefold() for s in resolved["synonyms"]} != {" ".join(str(s).split()).casefold() for s in old_syns}:
                    self.changes["synonyms"].append({
                        "kind": kind, "name": self._element_name(kind, obj),
                        "old": old_syns, "new": list(resolved["synonyms"]),
                    })
                _set(obj, "description", resolved["description"])
                if resolved.get("data_steward") and hasattr(obj, "business_owner") and not getattr(obj, "business_owner", None):
                    _set(obj, "business_owner", resolved["data_steward"])
                if resolved.get("filter_expression") and hasattr(obj, "filter_expression"):
                    _set(obj, "filter_expression", resolved["filter_expression"])
                if resolved.get("derives_from") and hasattr(obj, "derives_from"):
                    _set(obj, "derives_from", resolved["derives_from"])
                if hasattr(obj, "synonyms"):
                    _set(obj, "synonyms", resolved["synonyms"])
                    sources = getattr(obj, "synonym_sources", None)
                    if isinstance(sources, dict):
                        for a in layers:
                            tag = "manual_override" if a.sources == ["user_override"] else "ontology_domain"
                            for syn in a.synonyms:
                                sources.setdefault(syn, tag)
                counts[kind] += 1
        self._propagate_to_attributes()
        return counts

    def _propagate_to_attributes(self) -> None:
        """SML dimension attributes mirror their backing column's description."""
        touched = {oid for oid in self.column_layers}
        if not touched:
            return
        by_ds = {_cf(ds.unique_name): ds for ds in self.idx.datasets}
        for dim in getattr(self.model, "dimensions", []) or []:
            for attr in getattr(dim, "attributes", []) or []:
                ds = by_ds.get(_cf(getattr(attr, "dataset", "")))
                if ds is None:
                    continue
                col_name = getattr(attr, "dataset_column", None) or getattr(attr, "source_column", None) or attr.unique_name
                for col in self.idx.columns_matching(ds, _cf(col_name)):
                    if id(col) in touched and getattr(col, "description", None):
                        _set(attr, "description", col.description)


def _set(obj: Any, name: str, value: Any) -> None:
    try:
        setattr(obj, name, value)
    except Exception as exc:  # pragma: no cover - defensive (frozen models)
        logger.debug("Could not set %s on %r: %s", name, obj, exc)


def apply_ontology_to_model(
    model: Any,
    *,
    domains: Optional[List[Any]] = None,
    overrides: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
    model_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Apply user overrides + attached domain ontologies to ``model`` in place.

    Returns a report: {"applied": {...}, "matched": [...], "unmatched": [...],
    "ambiguous": [...], "warnings": [...], "domains": [...]}.
    """
    applier = OntologyApplier(model)
    names = list(model_names or [])
    names.extend([getattr(model, "unique_name", ""), getattr(model, "label", "")])
    if overrides:
        applier.add_user_overrides(overrides, names)
    for dom in domains or []:
        applier.add_domain(dom)
    counts = applier.apply()
    warnings = [f"Unmatched {u}" for u in applier.unmatched] + [f"Ambiguous {a}" for a in applier.ambiguous]
    warnings += [f"Formula ignored: {f['message']}" for f in applier.formula_overrides_ignored]
    for w in warnings:
        logger.warning("Ontology: %s", w)
    return {
        "applied": counts,
        "matched": applier.matched,
        "unmatched": applier.unmatched,
        "ambiguous": applier.ambiguous,
        "warnings": warnings,
        "formula_overrides_ignored": list(applier.formula_overrides_ignored),
        "changes": applier.changes,
        "domains": [getattr(d, "domain", str(d)) for d in (domains or [])],
    }


CONFLICT_MODES = ("priority", "namespace")


def _norm_resolution(resolution: Optional[str]) -> Optional[str]:
    mode = str(resolution or "").strip().lower()
    return mode if mode in CONFLICT_MODES else None


def _formula_conflicts(domains: List[Any]) -> List[Dict[str, Any]]:
    """Cross-domain metric formula conflicts (ERROR collisions) among ``domains``."""
    if len(domains or []) < 2:
        return []
    from semabridge.ontology.federated_loader import FederatedOntologyLoader

    fed = FederatedOntologyLoader.merge_domains(list(domains))
    out: List[Dict[str, Any]] = []
    for col in fed.collisions:
        if col.item_type == "metric" and col.severity == "ERROR" and len(col.conflicting_domains or []) > 1:
            out.append({
                "metric": col.item_name,
                "domains": list(col.conflicting_domains or []),
                "expressions": dict(col.expressions or {}),
                "message": col.message,
                "resolution_options": list(CONFLICT_MODES),
            })
    return out


def _detect_formula_conflicts(
    domains: List[Any],
    resolution: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return blocking metric-formula conflicts across attached domains unless explicitly resolved."""
    if _norm_resolution(resolution):
        return []
    return _formula_conflicts(domains)


def _namespace_prefix(domain_name: str) -> str:
    import re as _re

    return _re.sub(r"[^0-9A-Za-z]+", "_", str(domain_name or "")).strip("_") or "domain"


def order_domains_by_priority(domains: List[Any], priority: Optional[Iterable[str]]) -> List[Any]:
    """Domains named (by display name or id) in ``priority`` first, in that order;
    the rest keep their attachment order."""
    keys = [_cf(p) for p in (priority or []) if _cf(p)]
    if not keys:
        return list(domains or [])
    rank = {}
    for i, k in enumerate(keys):
        rank.setdefault(k, i)

    def _rank(d: Any) -> int:
        cands = [rank[k] for k in (_cf(getattr(d, "domain", "")), _cf(getattr(d, "id", ""))) if k in rank]
        return min(cands) if cands else len(keys)

    indexed = list(enumerate(domains or []))
    indexed.sort(key=lambda t: (_rank(t[1]), t[0]))
    return [d for _, d in indexed]


def resolve_domain_conflicts(
    domains: List[Any],
    resolution: Optional[str],
) -> Tuple[List[Any], Dict[str, Any]]:
    """Apply the chosen cross-domain conflict resolution and return (domains, report).

    * ``priority``: ``domains`` are in precedence order; for each conflicting
      metric only the highest-precedence domain's definition is kept, the
      others are dropped from the (copied) lower-precedence domains.
    * ``namespace``: every conflicting definition is kept, renamed
      ``<Domain>_<metric>`` (label and synonyms prefixed too) so none of them
      silently claims the shared name.

    The input domains are never mutated.
    """
    mode = _norm_resolution(resolution)
    conflicts = _formula_conflicts(domains)
    report: Dict[str, Any] = {"mode": mode, "conflicts": len(conflicts), "resolved": []}
    if not mode or not conflicts:
        return list(domains or []), report

    conflict_keys = {_cf(c["metric"]) for c in conflicts}
    order = [getattr(d, "domain", "") for d in domains]
    out: List[Any] = []
    winners: Dict[str, str] = {}
    for dom in domains:
        new_metrics = []
        changed = False
        for m in dom.metrics:
            key = _cf(m.name)
            if key not in conflict_keys or not (m.sql_expression or m.dax_expression):
                new_metrics.append(m)
                continue
            if mode == "priority":
                if key not in winners:
                    winners[key] = dom.domain
                    new_metrics.append(m)
                else:
                    changed = True
            else:  # namespace
                prefix = _namespace_prefix(dom.domain)
                new_metrics.append(m.model_copy(update={
                    "name": f"{prefix}_{m.name}",
                    "label": f"{dom.domain} {m.label or m.name}",
                    "synonyms": [f"{dom.domain} {s}" for s in (m.synonyms or [])],
                }))
                changed = True
        out.append(dom.model_copy(update={"metrics": new_metrics}) if changed else dom)

    for c in conflicts:
        key = _cf(c["metric"])
        doms = sorted(c["domains"], key=lambda n: order.index(n) if n in order else len(order))
        if mode == "priority":
            winner = winners.get(key) or (doms[0] if doms else None)
            report["resolved"].append({
                "metric": c["metric"],
                "mode": mode,
                "winner": winner,
                "overridden": [d for d in doms if d != winner],
                "message": f"'{c['metric']}': using the definition from '{winner}' (domain priority).",
            })
        else:
            names = [f"{_namespace_prefix(d)}_{c['metric']}" for d in doms]
            report["resolved"].append({
                "metric": c["metric"],
                "mode": mode,
                "namespaced": dict(zip(doms, names)),
                "message": f"'{c['metric']}': kept per domain as {', '.join(names)}.",
            })
    return out, report


def apply_project_ontology(
    project_id: Optional[str],
    model: Any,
    *,
    extra_models: Optional[List[Any]] = None,
    model_names: Optional[Iterable[str]] = None,
    store: Any = None,
    overrides: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
    conflict_resolution: Optional[str] = None,
    domain_priority: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Load the project's overrides + attached domains and apply them to ``model``
    (and, identically, to every model in ``extra_models``, e.g. the OSI model).

    ``conflict_resolution`` ('priority' | 'namespace') falls back to the choice
    saved for the project; ``domain_priority`` (names/ids) reorders the attached
    domains before 'priority' picks the winning definition.
    """
    if store is None:
        from semabridge.ontology.store import ontology_store as store  # noqa: N811
    if overrides is None:
        from semabridge.utils.synonyms import load_ontology_overrides

        overrides = load_ontology_overrides(project_id)
    domains = store.get_project_domains(project_id) if project_id else []
    attach_info = store.describe_project_domains(project_id) if project_id else {"missing_domain_ids": []}
    if _norm_resolution(conflict_resolution) is None and project_id and hasattr(store, "get_conflict_resolution"):
        conflict_resolution = store.get_conflict_resolution(project_id)
    domains = order_domains_by_priority(domains, domain_priority)
    domains, resolution_report = resolve_domain_conflicts(domains, conflict_resolution)
    names = list(model_names or [])
    report = apply_ontology_to_model(model, domains=domains, overrides=overrides, model_names=names)
    for extra in extra_models or []:
        if extra is None or extra is model:
            continue
        try:
            apply_ontology_to_model(extra, domains=domains, overrides=overrides, model_names=names)
        except Exception as exc:
            logger.warning("Ontology apply to secondary model failed: %s", exc)
    for missing in attach_info.get("missing_domain_ids", []):
        report["warnings"].append(f"Attached domain id '{missing}' no longer exists")
    blockers = _detect_formula_conflicts(domains, resolution=conflict_resolution)
    report["blocking_conflicts"] = blockers
    report["conflict_resolution"] = resolution_report
    for res in resolution_report.get("resolved", []):
        report["warnings"].append(f"Resolved: {res['message']}")
    for blk in blockers:
        report["warnings"].append(
            f"BLOCKER: {blk['message']} Choose a namespace or domain priority order before deploying."
        )
    report["project_id"] = project_id
    return report


def _attach_project_verified_queries(project_id: Optional[str], *models: Any) -> int:
    pid = str(project_id or "").strip()
    if not pid:
        return 0
    try:
        from semabridge.ai.verified_queries_service import list_verified_queries_for_project

        vqs = list_verified_queries_for_project(pid)
    except Exception as exc:
        logger.debug("Could not load verified queries for project %s: %s", pid, exc)
        return 0
    for mdl in models:
        if mdl is not None and hasattr(mdl, "verified_queries"):
            mdl.verified_queries = list(vqs)
    return len(vqs)


def apply_ontology_for_run(engine: Any, context: Any) -> None:
    """ExecutionEngine hook: apply the ontology & verified queries to context.sml_model / osi_model."""
    payload = getattr(context, "config_payload", None) or {}
    effective_project_id = (
        payload.get("binding_project_id")
        or payload.get("delta_baseline_project_id")
        or getattr(context, "project_id", None)
    )
    resolution = payload.get("ontology_conflict_resolution")
    priority = payload.get("ontology_domain_priority") or payload.get("ontology_priority")
    if isinstance(priority, str):
        priority = [p.strip() for p in priority.split(",") if p.strip()]
    if not isinstance(priority, list):
        priority = None
    try:
        sml = context.sml_model
        osi = getattr(context, "osi_model", None)
        names = [
            getattr(sml, "unique_name", None), getattr(sml, "label", None),
            getattr(osi, "unique_name", None), getattr(osi, "label", None),
            getattr(context, "resolved_pbix_display_name", None),
            getattr(context, "semantic_view_name_override", None),
        ]
        report = apply_project_ontology(
            effective_project_id, sml,
            extra_models=[osi], model_names=[n for n in names if n],
            conflict_resolution=resolution,
            domain_priority=priority,
        )
        vq_count = _attach_project_verified_queries(effective_project_id, sml, osi)
        report["verified_queries_count"] = vq_count
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Ontology application failed (continuing without it): %s", exc)
        report = {"warnings": [f"Ontology application failed: {exc}"], "blocking_conflicts": []}
    try:
        context.ontology_report = report
    except Exception as exc:
        logger.warning("Could not attach the ontology report to the run context: %s", exc)
    summary = getattr(engine, "_summary", None)
    if summary is not None:
        try:
            summary.ontology_report = report
        except Exception as exc:
            logger.warning("Could not attach the ontology report to the run summary (dry run will not show it): %s", exc)
    blockers = report.get("blocking_conflicts") or []
    if blockers and not getattr(context, "dry_run", False):
        from semabridge.core.engine.exceptions import ConversionError

        details = "; ".join(b.get("message", b.get("metric", "")) for b in blockers)
        raise ConversionError(
            f"Conflicting metric definitions across attached domains block deployment ({details}). "
            "Resolve the conflict or set a namespace / priority choice."
        )
