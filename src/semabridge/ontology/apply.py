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
            elif len(cands) > 1:
                self.ambiguous.append(
                    f"{label}: matches several metrics ({', '.join(m.unique_name for m in cands)}); add 'maps_to'"
                )
            else:
                self.unmatched.append(f"{label}: no metric in this model matches (maps_to / name / label / synonyms)")

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

    def apply(self) -> Dict[str, int]:
        counts = {"tables": 0, "columns": 0, "metrics": 0}
        for kind, layers_map in (("tables", self.table_layers), ("columns", self.column_layers), ("metrics", self.metric_layers)):
            for oid, layers in layers_map.items():
                obj = self._objs[oid]
                resolved = self._resolve(obj, layers)
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
    for w in warnings:
        logger.warning("Ontology: %s", w)
    return {
        "applied": counts,
        "matched": applier.matched,
        "unmatched": applier.unmatched,
        "ambiguous": applier.ambiguous,
        "warnings": warnings,
        "domains": [getattr(d, "domain", str(d)) for d in (domains or [])],
    }


def apply_project_ontology(
    project_id: Optional[str],
    model: Any,
    *,
    extra_models: Optional[List[Any]] = None,
    model_names: Optional[Iterable[str]] = None,
    store: Any = None,
    overrides: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Load the project's overrides + attached domains and apply them to ``model``
    (and, identically, to every model in ``extra_models``, e.g. the OSI model)."""
    if store is None:
        from semabridge.ontology.store import ontology_store as store  # noqa: N811
    if overrides is None:
        from semabridge.utils.synonyms import load_ontology_overrides

        overrides = load_ontology_overrides(project_id)
    domains = store.get_project_domains(project_id) if project_id else []
    attach_info = store.describe_project_domains(project_id) if project_id else {"missing_domain_ids": []}
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
    report["project_id"] = project_id
    return report


def apply_ontology_for_run(engine: Any, context: Any) -> None:
    """ExecutionEngine hook: apply the ontology to context.sml_model / osi_model.

    Never fails the run; problems land in ``ontology_report.warnings`` of the
    run context and run summary.
    """
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
            getattr(context, "project_id", None), sml,
            extra_models=[osi], model_names=[n for n in names if n],
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Ontology application failed (continuing without it): %s", exc)
        report = {"warnings": [f"Ontology application failed: {exc}"]}
    try:
        context.ontology_report = report
    except Exception:
        pass
    summary = getattr(engine, "_summary", None)
    if summary is not None:
        try:
            summary.ontology_report = report
        except Exception:
            pass
