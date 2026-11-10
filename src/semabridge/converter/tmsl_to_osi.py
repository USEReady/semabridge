"""
TMSL to OSI Converter.

Converts Fabric Model Definitions (TMSL JSON) into the OSI (Open Semantic Interchange)
canonical intermediate representation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDimension,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name
from semabridge.utils.synonyms import (
    load_synonym_overrides,
    lookup_synonym_override,
    merge_synonyms_with_sources,
)
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.connectors.business_field_type_hints import infer_business_field_type

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared TMSL semantics helpers (also used by the legacy tmsl_to_sml path)
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on"}


def join_tmsl_expression(value: Any) -> str:
    """TMSL/model.bim may store expressions as a list of lines."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    return str(value)


def _norm_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")


_CARDINALITY_PAIR = {
    ("many", "one"): OSICardinality.MANY_TO_ONE,
    ("one", "one"): OSICardinality.ONE_TO_ONE,
    ("one", "many"): OSICardinality.ONE_TO_MANY,
    ("many", "many"): OSICardinality.MANY_TO_MANY,
}

_CARDINALITY_STRING = {
    "manytoone": OSICardinality.MANY_TO_ONE,
    "m:1": OSICardinality.MANY_TO_ONE,
    "*:1": OSICardinality.MANY_TO_ONE,
    "onetoone": OSICardinality.ONE_TO_ONE,
    "1:1": OSICardinality.ONE_TO_ONE,
    "onetomany": OSICardinality.ONE_TO_MANY,
    "1:m": OSICardinality.ONE_TO_MANY,
    "1:*": OSICardinality.ONE_TO_MANY,
    "manytomany": OSICardinality.MANY_TO_MANY,
    "m:m": OSICardinality.MANY_TO_MANY,
    "*:*": OSICardinality.MANY_TO_MANY,
}


def _rel_get(rel_def: Dict[str, Any], *names: str) -> Any:
    lowered = {str(k).lower(): v for k, v in rel_def.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def tmsl_relationship_cardinality(rel_def: Dict[str, Any]) -> OSICardinality:
    """Resolve a TMSL relationship's cardinality.

    Real TMSL / model.bim / Fabric definitions express cardinality as two
    per-end properties, ``fromCardinality`` (default "many") and
    ``toCardinality`` (default "one"). Some internal producers (the pbixray
    fallback, older fixtures) emit a combined ``cardinality`` string instead.
    Both forms are honoured; the per-end form wins when present.
    """
    from_card = _rel_get(rel_def, "fromCardinality", "from_cardinality")
    to_card = _rel_get(rel_def, "toCardinality", "to_cardinality")
    combined = _rel_get(rel_def, "cardinality")
    if from_card is not None or to_card is not None or combined is None:
        pair = (
            _norm_token(from_card) if from_card is not None else "many",
            _norm_token(to_card) if to_card is not None else "one",
        )
        if pair in _CARDINALITY_PAIR:
            return _CARDINALITY_PAIR[pair]
        if combined is None:
            return OSICardinality.MANY_TO_ONE
    raw = str(combined).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return _CARDINALITY_STRING.get(raw, OSICardinality.MANY_TO_ONE)


def tmsl_relationship_cross_filter(rel_def: Dict[str, Any]) -> OSICrossFilterDirection:
    """Map TMSL crossFilteringBehavior (oneDirection|bothDirections|automatic)."""
    raw = _norm_token(_rel_get(rel_def, "crossFilteringBehavior", "cross_filtering_behavior") or "oneDirection")
    if raw in {"both", "bothdirections", "2"}:
        return OSICrossFilterDirection.BOTH
    # "automatic" resolves to single-direction for regular relationships.
    return OSICrossFilterDirection.SINGLE


def tmsl_relationship_is_active(rel_def: Dict[str, Any]) -> bool:
    value = _rel_get(rel_def, "isActive", "is_active")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no"}
    return bool(value)


def classify_tmsl_table(table_def: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Return (dataset_kind, defining DAX) for a TMSL table.

    Kinds: 'calculation_group', 'field_parameter', 'calculated_table', 'table'.
    """
    partitions = table_def.get("partitions") or []
    if table_def.get("calculationGroup"):
        return "calculation_group", None
    calc_expr: Optional[str] = None
    is_calculated = False
    for part in partitions:
        if not isinstance(part, dict):
            continue
        source = part.get("source") or {}
        if not isinstance(source, dict):
            source = {}
        p_type = _norm_token(source.get("type") or part.get("source_type") or part.get("type"))
        if p_type == "calculationgroup":
            return "calculation_group", None
        if p_type == "calculated":
            is_calculated = True
            calc_expr = join_tmsl_expression(source.get("expression") or part.get("expression")) or calc_expr
    if not is_calculated:
        return "table", None

    def _has_param_metadata(obj: Dict[str, Any]) -> bool:
        for prop in obj.get("extendedProperties") or []:
            if isinstance(prop, dict) and str(prop.get("name") or "") == "ParameterMetadata":
                return True
        return False

    if _has_param_metadata(table_def) or any(
        isinstance(c, dict) and _has_param_metadata(c) for c in table_def.get("columns") or []
    ) or re.search(r"\bNAMEOF\s*\(", calc_expr or "", re.IGNORECASE):
        return "field_parameter", calc_expr
    return "calculated_table", calc_expr


def infer_relationships_enabled(explicit: Optional[bool] = None) -> bool:
    """Name-based relationship inference is OFF unless explicitly enabled
    (options.infer_relationships / SEMABRIDGE_INFER_RELATIONSHIPS=1)."""
    if isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str):
        return explicit.strip().lower() in _TRUTHY
    import os

    return str(os.getenv("SEMABRIDGE_INFER_RELATIONSHIPS", "")).strip().lower() in _TRUTHY


INFERRED_RELATIONSHIP_REASON = (
    "inferred, not in source — added by name-based relationship inference "
    "(options.infer_relationships is enabled); verify it before relying on it"
)


class TMSLToOSIConverter(BaseConverter):
    """
    Transforms Fabric TMSL JSON into OSI Model.
    """

    AUTO_HIDDEN_TABLE_PREFIXES = (
        "LocalDateTable_",
        "DateTableTemplate_",
        "ClusterMappingTable",
        "ClusterMapping",
    )

    def __init__(
        self,
        drop_ledger: Optional[DropLedger] = None,
        infer_relationships: Optional[bool] = None,
    ) -> None:
        # None -> resolved per run from source_data / environment (default OFF).
        self._infer_relationships_opt = infer_relationships
        self._excluded_tables: Dict[str, Tuple[str, str, bool]] = {}
        self._linguistic_terms: Dict[Tuple[str, Optional[str]], Tuple[List[str], List[str]]] = {}
        self._synonym_overrides: Dict[tuple[str, str, str], List[str]] = {}
        self._synonym_model_names: List[str] = []
        self._field_alias_lookup: Optional[Dict[Tuple[str, str, Optional[str]], List[str]]] = None
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()
        # Full path to the source .pbix file this conversion run is processing,
        # stamped onto every OSIColumn/OSIMetric produced (see to_osi()). None
        # for Fabric sources, which have no local file. A fresh converter
        # instance is created per model run (see core/engine/conversion/pbix.py
        # and fabric.py), so this never leaks across files in a multi-PBIX
        # project's parallel per-file runs.
        self._source_file: Optional[str] = None

    def to_osi(self, source_data: Dict[str, Any]) -> OSIModel:
        """
        Convert TMSL dictionary to OSIModel object.

        Args:
            source_data: Dictionary containing:
                - tmsl: Decoded model.bim JSON
                - workspace_id: Fabric workspace ID
                - dataset_id: Fabric dataset ID (unique_name for OSIModel)

        Returns:
            OSIModel object
        
        Raises:
            ConversionError: If transformation fails.
        """
        try:
            tmsl_json = source_data.get("tmsl", {})
            workspace_id = source_data.get("workspace_id")
            dataset_id = source_data.get("dataset_id")
            self._source_file = source_data.get("source_file")

            if not tmsl_json or not dataset_id:
                raise ConversionError(
                    "Missing 'tmsl' or 'dataset_id' in source_data",
                    source_format="tmsl",
                    target_format="osi"
                )

            model_obj = tmsl_json.get("model", {})
            model_label = model_obj.get("name") or "FabricModel"
            # Explicit connector display names control semantic view names; otherwise
            # keep the historical dataset_id unique_name and model-name label.
            display_name = source_data.get("display_name") or model_label
            project_id = source_data.get("project_id") or dataset_id
            explicit_overrides = source_data.get("synonym_overrides")
            self._synonym_overrides = (
                explicit_overrides
                if isinstance(explicit_overrides, dict)
                else load_synonym_overrides(project_id)
            )
            self._dump_measure_audit(model_obj, dataset_id, phase="tmsl_to_osi_pre")

            # Initialize field aliases if present in source_data (for PBIX)
            field_aliases = source_data.get("field_aliases")
            if field_aliases is not None:
                self._field_alias_lookup = {}
                for item in field_aliases:
                    if isinstance(item, dict):
                        f_name = item.get("field")
                        f_type = item.get("field_type")
                        table = item.get("table")
                        aliases = item.get("aliases")
                        if (
                            isinstance(f_name, str)
                            and isinstance(aliases, list)
                            and f_type in ("measure", "column")
                        ):
                            table_key = table.lower() if isinstance(table, str) and table.strip() else None
                            key = (f_name.lower(), f_type, table_key if f_type == "column" else None)
                            self._field_alias_lookup[key] = list(aliases)
            else:
                self._field_alias_lookup = None

            # Guard: connector-type keywords used as model names produce misleading view names
            # (e.g. a dataset named "fabric" would generate a "fabric_SEMANTIC" view).
            # Fall back to the dataset_id when the display name collides with a reserved keyword.
            _RESERVED_MODEL_NAMES = frozenset({"fabric", "snowflake", "pbix", "databricks", "model"})
            resolved_unique_name = source_data.get("display_name") or dataset_id or display_name
            if str(resolved_unique_name).strip().lower() in _RESERVED_MODEL_NAMES:
                logger.warning(
                    "Fabric dataset display name '%s' collides with a reserved connector keyword. "
                    "Using dataset_id '%s' as the model unique_name to avoid ambiguous Snowflake view names. "
                    "Rename the Fabric dataset or set 'project_name' in your project config to override.",
                    resolved_unique_name,
                    dataset_id,
                )
                resolved_unique_name = dataset_id

            self._synonym_model_names = [
                str(name).strip()
                for name in (resolved_unique_name, display_name, dataset_id, model_obj.get("name"))
                if str(name or "").strip()
            ]

            # Use display name as unique_name to ensure Snowflake views use display names, not GUIDs.
            # GUID (dataset_id) is still preserved in metadata for traceability.
            workspace_name = source_data.get("workspace_name") or ""
            osi_model = OSIModel(
                unique_name=resolved_unique_name,
                label=display_name,
                description=model_obj.get("description", ""),
                source_platform="fabric",
                metadata={"workspace_id": workspace_id, "dataset_id": dataset_id},
                platform_metadata={
                    "fabric": {
                        "workspace_id": workspace_id,
                        "workspace_name": workspace_name,
                        "dataset_id": dataset_id,
                    }
                }
            )

            self._excluded_tables = {}
            self._linguistic_terms = self._collect_linguistic_terms(model_obj)
            infer_rels = infer_relationships_enabled(
                self._infer_relationships_opt
                if self._infer_relationships_opt is not None
                else source_data.get("infer_relationships")
            )

            # Process Datasets (Tables)
            if "tables" in model_obj:
                for table in model_obj["tables"]:
                    t_name = table.get("name", "")
                    if self._is_auto_hidden_table_name(t_name):
                        if str(t_name).startswith(("ClusterMapping", "ClusterMappingTable")):
                            reason = (
                                "Power BI auto-generated clustering mapping table "
                                "(ClusterMappingTable) — excluded from the semantic model by design."
                            )
                        else:
                            reason = (
                                "Auto-generated Power BI/Fabric date table "
                                "(LocalDateTable_/DateTableTemplate_) — excluded from "
                                "the semantic model by design."
                            )
                        logger.info(
                            "Skipping auxiliary table during OSI conversion: %s (%s)",
                            t_name or "<unnamed>",
                            reason,
                        )
                        self.drop_ledger.record(
                            "table", t_name or "<unnamed>", DropStage.EXTRACTION,
                            reason,
                            by_design=True,
                        )
                        self._excluded_tables[str(t_name).casefold()] = (t_name, reason, True)
                        continue

                    kind, table_expr = classify_tmsl_table(table)
                    if kind == "calculation_group":
                        self._record_calculation_group(table, osi_model)
                        continue

                    dataset = self._parse_dataset(table, kind=kind, expression=table_expr)
                    osi_model.datasets.append(dataset)
                    self._record_non_physical_dataset(dataset)
                    self._record_sort_by_columns(dataset)

                    # Create corresponding Dimension for each Dataset
                    # In TMSL/Power BI, every table is potentially a dimension
                    hierarchies = self._parse_hierarchies(table, dataset)
                    dim = self._create_dimension_from_dataset(dataset)
                    if dim:
                        dim.hierarchies = hierarchies
                        osi_model.dimensions.append(dim)

                    # Process Measures (Metrics)
                    for measure in self._iter_table_measures_with_stable_names(table):
                        metric = self._parse_metric(measure, dataset.unique_name)
                        osi_model.metrics.append(metric)

            self._dedupe_metric_names_across_model(osi_model)

            # Process Relationships
            # Build case-insensitive map of valid datasets so relationship
            # endpoints can be canonicalized before filtering.
            valid_dataset_map = {
                str(ds.unique_name).casefold(): ds.unique_name for ds in osi_model.datasets
            }

            if "relationships" in model_obj:
                for rel in model_obj["relationships"] or []:
                    if not isinstance(rel, dict):
                        continue
                    osi_rel = self._parse_relationship(rel)
                    if not osi_rel:
                        self.drop_ledger.record(
                            "relationship",
                            self._describe_raw_relationship(rel),
                            DropStage.EXTRACTION,
                            "relationship could not be parsed (missing or malformed endpoints) — dropped",
                            detail=json.dumps(rel, default=str)[:500],
                            by_design=False,
                        )
                        continue
                    from_dataset = valid_dataset_map.get(str(osi_rel.from_dataset).casefold())
                    to_dataset = valid_dataset_map.get(str(osi_rel.to_dataset).casefold())
                    # Only add relationship if both referenced tables exist
                    if from_dataset and to_dataset:
                        osi_rel.from_dataset = from_dataset
                        osi_rel.to_dataset = to_dataset
                        osi_model.relationships.append(osi_rel)
                    else:
                        self._record_relationship_to_missing_table(
                            osi_rel,
                            [
                                name
                                for name, resolved in (
                                    (osi_rel.from_dataset, from_dataset),
                                    (osi_rel.to_dataset, to_dataset),
                                )
                                if not resolved
                            ],
                        )

            # Optional fallback: infer relationships when source metadata
            # exposes none. OFF by default — inferred joins are semantics the
            # source model never declared.
            if not osi_model.relationships and osi_model.datasets and infer_rels:
                self._infer_fallback_relationships(osi_model, valid_dataset_map)

            # Determine fact tables: every table on the many side of a relationship
            fact_datasets: Set[str] = set()
            for r in osi_model.relationships:
                card = getattr(r, "cardinality", None)
                card_val = str(getattr(card, "value", card) or "").lower()
                if "one_to_many" in card_val or "one-to-many" in card_val:
                    if r.to_dataset:
                        fact_datasets.add(r.to_dataset)
                else:
                    if r.from_dataset:
                        fact_datasets.add(r.from_dataset)

            # Build set of columns already covered by DAX measures with the same aggregation
            covered_measure_keys: Set[Tuple[str, str]] = set()
            for m in osi_model.metrics:
                m_agg = getattr(m, "aggregation", None)
                agg_str = str(getattr(m_agg, "value", m_agg) or "").upper()
                if agg_str == "NONE":
                    agg_str = ""
                expr = getattr(m, "expression", "") or ""
                m_match = re.search(r'^(SUM|AVG|AVERAGE|MIN|MAX|COUNT)\s*\(\s*(?:[\'"][^\'"]+[\'"]\[|\[)?([^\s\]]+)\]?\s*\)$', expr, re.IGNORECASE)
                if m_match:
                    if not agg_str:
                        agg_fn = m_match.group(1).upper()
                        agg_str = "AVG" if agg_fn == "AVERAGE" else agg_fn
                    col_ref = m_match.group(2).upper()
                    covered_measure_keys.add((col_ref, agg_str))
                    if m.dataset:
                        covered_measure_keys.add((m.dataset.upper(), col_ref, agg_str))
                elif agg_str and m.source_column:
                    covered_measure_keys.add((m.source_column.upper(), agg_str))
                    if m.dataset:
                        covered_measure_keys.add((m.dataset.upper(), m.source_column.upper(), agg_str))

            # Populate implicit metrics for visible numeric columns on fact tables
            for dataset in osi_model.datasets:
                is_fact = dataset.unique_name in fact_datasets or getattr(dataset, "is_fact", False)
                for metric in self._create_metrics_from_aggregation_columns(dataset, is_fact=is_fact, covered=covered_measure_keys):
                    osi_model.metrics.append(metric)

            # Model-level objects Snowflake semantic views cannot represent.
            self._record_roles(model_obj, osi_model)
            self._record_perspectives(model_obj, osi_model)

            return osi_model

        except Exception as e:
            logger.error(f"TMSL to OSI conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert TMSL: {e}",
                source_format="tmsl",
                target_format="osi",
                details={"error": str(e)}
            )

    # ------------------------------------------------------------------
    # Source-fidelity helpers: carry or ledger every source construct
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate(text: Any, limit: int = 300) -> str:
        flat = " ".join(str(text or "").split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    @staticmethod
    def _describe_raw_relationship(rel: Dict[str, Any]) -> str:
        def g(*keys: str) -> str:
            for k in keys:
                if rel.get(k):
                    return str(rel.get(k))
            return "?"

        return (
            f"{g('fromTable', 'from_table')}[{g('fromColumn', 'from_column')}] -> "
            f"{g('toTable', 'to_table')}[{g('toColumn', 'to_column')}]"
        )

    def _record_non_physical_dataset(self, dataset: OSIDataset) -> None:
        kind = dataset.dataset_kind
        if kind == "table":
            return
        expr = self._truncate(dataset.expression or "", 500)
        if kind == "field_parameter":
            reason = (
                "field parameter — Power BI report-time field switcher defined in DAX; "
                "Snowflake semantic views have no field parameters, so it is not migrated"
            )
        else:
            reason = (
                "calculated table — not materialized in Snowflake; "
                "materialize it as a table/view to include it"
            )
        logger.info("Dataset '%s' is a %s: %s", dataset.unique_name, kind, reason)
        self.drop_ledger.record(
            "table",
            dataset.unique_name,
            DropStage.EXTRACTION,
            reason,
            dataset=dataset.unique_name,
            detail=f"DAX: {expr}" if expr else None,
            by_design=True,
        )

    def _record_calculation_group(self, table: Dict[str, Any], osi_model: OSIModel) -> None:
        name = str(table.get("name") or "<unnamed>")
        cg = table.get("calculationGroup") or {}
        items = [i for i in (cg.get("calculationItems") or []) if isinstance(i, dict)]
        item_names = [str(i.get("name") or "") for i in items]
        reason = (
            "calculation group — Snowflake semantic views have no calculation groups; "
            f"{len(items)} calculation item(s) not migrated"
            + (f" ({', '.join(item_names)})" if item_names else "")
        )
        detail = "; ".join(
            f"{i.get('name')}: {self._truncate(join_tmsl_expression(i.get('expression')), 200)}"
            for i in items
        )
        logger.info("Calculation group '%s' not migrated: %s", name, reason)
        self.drop_ledger.record(
            "table", name, DropStage.EXTRACTION, reason,
            dataset=name, detail=detail or None, by_design=True,
        )
        self._excluded_tables[name.casefold()] = (name, reason, True)
        osi_model.metadata.setdefault("calculation_groups", []).append(
            {
                "name": name,
                "precedence": cg.get("precedence"),
                "description": table.get("description"),
                "items": [
                    {
                        "name": i.get("name"),
                        "expression": join_tmsl_expression(i.get("expression")),
                        "ordinal": i.get("ordinal"),
                        "format_string_expression": join_tmsl_expression(
                            (i.get("formatStringDefinition") or {}).get("expression")
                        )
                        or None,
                    }
                    for i in items
                ],
            }
        )
        for measure in self._iter_table_measures(table):
            m_name = str(measure.get("name") or "<unnamed>")
            self.drop_ledger.record(
                "metric", m_name, DropStage.EXTRACTION,
                f"measure is defined on calculation group table '{name}', which is not migrated",
                dataset=name,
                detail=self._truncate(join_tmsl_expression(self._extract_measure_expression(measure)), 300),
                by_design=False,
            )

    def _record_sort_by_columns(self, dataset: OSIDataset) -> None:
        for col in dataset.columns:
            if not col.sort_by_column:
                continue
            self.drop_ledger.record(
                "column_property",
                f"{col.unique_name} sortByColumn",
                DropStage.EXTRACTION,
                f"sort order of '{col.unique_name}' by '{col.sort_by_column}' not carried — "
                "Snowflake semantic views have no sort-by-column; order by "
                f"'{col.sort_by_column}' explicitly in queries",
                dataset=dataset.unique_name,
                by_design=True,
            )

    def _parse_hierarchies(self, table: Dict[str, Any], dataset: OSIDataset) -> List[OSIHierarchy]:
        hierarchies: List[OSIHierarchy] = []
        for h in table.get("hierarchies") or []:
            if not isinstance(h, dict) or not str(h.get("name") or "").strip():
                continue
            h_name = str(h["name"]).strip()
            levels_raw = [lv for lv in (h.get("levels") or []) if isinstance(lv, dict)]
            levels_raw.sort(key=lambda lv: (lv.get("ordinal") is None, lv.get("ordinal") or 0))
            levels: List[OSILevel] = []
            for lv in levels_raw:
                col = str(lv.get("column") or "").strip()
                lv_name = str(lv.get("name") or col).strip()
                if not col or not lv_name:
                    continue
                levels.append(OSILevel(unique_name=lv_name, attribute=col))
            hierarchy = OSIHierarchy(
                unique_name=h_name,
                levels=levels,
                description=h.get("description"),
                is_hidden=bool(h.get("isHidden", False)),
                folder=h.get("displayFolder") or None,
            )
            hierarchies.append(hierarchy)
            path = " > ".join(f"{lv.unique_name} [{lv.attribute}]" for lv in levels) or "no levels"
            self.drop_ledger.record(
                "hierarchy",
                h_name,
                DropStage.EXTRACTION,
                f"hierarchy '{h_name}' ({path}) not migrated as a drill path — Snowflake semantic "
                "views have no hierarchy object; its level columns are still emitted as "
                "individual dimensions",
                dataset=dataset.unique_name,
                by_design=True,
            )
        return hierarchies

    def _record_relationship_to_missing_table(
        self, osi_rel: OSIRelationship, missing: List[str]
    ) -> None:
        excluded = [self._excluded_tables.get(str(m).casefold()) for m in missing]
        by_design = bool(missing) and all(e is not None and e[2] for e in excluded)
        parts = []
        for name, info in zip(missing, excluded):
            if info is not None:
                parts.append(f"'{name}' (excluded: {info[1]})")
            else:
                parts.append(f"'{name}' (not found in the source model)")
        reason = (
            "relationship references table(s) not in the semantic model — dropped: "
            + ", ".join(parts)
        )
        logger.info("Dropping relationship %s: %s", osi_rel.unique_name, reason)
        self.drop_ledger.record(
            "relationship",
            f"{osi_rel.from_dataset}[{osi_rel.from_columns[0]}] -> "
            f"{osi_rel.to_dataset}[{osi_rel.to_columns[0]}]",
            DropStage.EXTRACTION,
            reason,
            by_design=by_design,
        )

    def _infer_fallback_relationships(
        self, osi_model: OSIModel, valid_dataset_map: Dict[str, str]
    ) -> None:
        try:
            from semabridge.connectors.relationship_detector import RelationshipDetector

            tables_meta: Dict[str, Dict[str, Any]] = {
                ds.unique_name: {"row_count": 0} for ds in osi_model.datasets
            }
            columns_meta: Dict[str, List[Dict[str, Any]]] = {
                ds.unique_name: [
                    {
                        "name": c.unique_name,
                        "data_type": (
                            c.data_type.value if hasattr(c.data_type, "value") else str(c.data_type)
                        ),
                    }
                    for c in ds.columns
                ]
                for ds in osi_model.datasets
            }
            pks_meta: Dict[str, List[str]] = {
                ds.unique_name: [c.unique_name for c in ds.columns if c.is_key]
                for ds in osi_model.datasets
            }
            inferred_rels = RelationshipDetector(tables_meta, columns_meta, pks_meta).detect_all()
            seen_signatures: set = set()
            added = 0
            for rel in inferred_rels or []:
                sig = (
                    str(rel.get("from_table", "")).casefold(),
                    str(rel.get("from_column", "")).casefold(),
                    str(rel.get("to_table", "")).casefold(),
                    str(rel.get("to_column", "")).casefold(),
                )
                if sig in seen_signatures:
                    continue
                from_ds = valid_dataset_map.get(str(rel.get("from_table", "")).casefold())
                to_ds = valid_dataset_map.get(str(rel.get("to_table", "")).casefold())
                from_col = str(rel.get("from_column", "")).strip()
                to_col = str(rel.get("to_column", "")).strip()
                if not (from_ds and to_ds and from_col and to_col):
                    continue
                osi_rel = OSIRelationship(
                    unique_name=generate_relationship_name(from_ds, from_col, to_ds, to_col),
                    from_dataset=from_ds,
                    from_columns=[from_col],
                    to_dataset=to_ds,
                    to_columns=[to_col],
                    cardinality=OSICardinality.MANY_TO_ONE,
                    cross_filter_direction=OSICrossFilterDirection.SINGLE,
                    is_active=True,
                    is_inferred=True,
                )
                osi_model.relationships.append(osi_rel)
                seen_signatures.add(sig)
                added += 1
                self.drop_ledger.record(
                    "relationship",
                    f"{from_ds}[{from_col}] -> {to_ds}[{to_col}]",
                    DropStage.EXTRACTION,
                    INFERRED_RELATIONSHIP_REASON,
                    by_design=True,
                )
            logger.info("Inferred %s fallback relationship(s) from column names", added)
        except Exception as exc:
            logger.warning("Relationship inference fallback failed: %s", exc)

    def _record_roles(self, model_obj: Dict[str, Any], osi_model: OSIModel) -> None:
        roles = [r for r in (model_obj.get("roles") or []) if isinstance(r, dict)]
        carried: List[Dict[str, Any]] = []
        for role in roles:
            r_name = str(role.get("name") or "<unnamed>")
            filters: List[str] = []
            ols: List[str] = []
            table_perms = []
            for tp in role.get("tablePermissions") or []:
                if not isinstance(tp, dict):
                    continue
                t_name = str(tp.get("name") or tp.get("table") or "?")
                f_expr = join_tmsl_expression(tp.get("filterExpression")).strip()
                if f_expr:
                    filters.append(f"{t_name}: {f_expr}")
                if str(tp.get("metadataPermission") or "").lower() == "none":
                    ols.append(f"table '{t_name}' hidden")
                col_perms = []
                for cp in tp.get("columnPermissions") or []:
                    if not isinstance(cp, dict):
                        continue
                    col_perms.append(
                        {"column": cp.get("name"), "metadata_permission": cp.get("metadataPermission")}
                    )
                    if str(cp.get("metadataPermission") or "").lower() == "none":
                        ols.append(f"column '{t_name}'[{cp.get('name')}] hidden")
                table_perms.append(
                    {
                        "table": t_name,
                        "filter_expression": f_expr or None,
                        "metadata_permission": tp.get("metadataPermission"),
                        "column_permissions": col_perms,
                    }
                )
            carried.append(
                {
                    "name": r_name,
                    "model_permission": role.get("modelPermission"),
                    "description": role.get("description"),
                    "table_permissions": table_perms,
                }
            )
            filter_text = "; ".join(filters) if filters else "(no row filters defined)"
            reason = (
                f"row-level security role '{r_name}' not migrated — Snowflake semantic views do "
                "not carry RLS; implement with a Snowflake row access policy. "
                f"Filter: {filter_text}"
            )
            if ols:
                reason += (
                    ". Object-level security not migrated: " + ", ".join(ols)
                    + " — implement with Snowflake masking policies/grants"
                )
            logger.warning("SECURITY: %s", reason)
            # Deliberately NOT by_design: an unmigrated security role means the
            # deployed view exposes rows/columns the source model restricted —
            # it must surface as an actionable item, never be de-emphasized.
            self.drop_ledger.record(
                "security_role", r_name, DropStage.EXTRACTION, reason, by_design=False,
            )
        if carried:
            osi_model.metadata["security_roles"] = carried

    def _record_perspectives(self, model_obj: Dict[str, Any], osi_model: OSIModel) -> None:
        carried: List[Dict[str, Any]] = []
        for p in model_obj.get("perspectives") or []:
            if not isinstance(p, dict):
                continue
            p_name = str(p.get("name") or "<unnamed>")
            tables = [
                {
                    "name": t.get("name"),
                    "columns": [c.get("name") for c in t.get("columns") or [] if isinstance(c, dict)],
                    "measures": [m.get("name") for m in t.get("measures") or [] if isinstance(m, dict)],
                    "hierarchies": [h.get("name") for h in t.get("hierarchies") or [] if isinstance(h, dict)],
                }
                for t in p.get("tables") or []
                if isinstance(t, dict)
            ]
            carried.append({"name": p_name, "description": p.get("description"), "tables": tables})
            self.drop_ledger.record(
                "perspective",
                p_name,
                DropStage.EXTRACTION,
                f"perspective '{p_name}' not migrated — Snowflake semantic views have no "
                "perspectives; all migrated objects are exposed in a single view",
                detail=(
                    f"{len(tables)} table(s): " + ", ".join(str(t["name"]) for t in tables)
                    if tables else None
                ),
                by_design=True,
            )
        if carried:
            osi_model.metadata["perspectives"] = carried

    def _record_kpi(
        self, measure_def: Dict[str, Any], metric_name: str, dataset_name: str
    ) -> Optional[Dict[str, str]]:
        kpi = measure_def.get("kpi")
        if not isinstance(kpi, dict) or not kpi:
            return None
        key_map = {
            "targetExpression": "target_expression",
            "statusExpression": "status_expression",
            "trendExpression": "trend_expression",
            "targetFormatString": "target_format_string",
            "targetDescription": "target_description",
            "statusGraphic": "status_graphic",
            "statusDescription": "status_description",
            "trendGraphic": "trend_graphic",
            "trendDescription": "trend_description",
            "description": "description",
        }
        carried: Dict[str, str] = {}
        for src_key, dst_key in key_map.items():
            value = join_tmsl_expression(kpi.get(src_key)).strip()
            if value:
                carried[dst_key] = value
        parts = [
            f"{label}: {self._truncate(carried[key], 150)}"
            for label, key in (
                ("Target", "target_expression"),
                ("Status", "status_expression"),
                ("Trend", "trend_expression"),
            )
            if key in carried
        ]
        self.drop_ledger.record(
            "kpi",
            metric_name,
            DropStage.EXTRACTION,
            f"KPI on measure '{metric_name}' not migrated — Snowflake semantic views have no KPI "
            "object; the base measure is migrated"
            + (". " + "; ".join(parts) if parts else ""),
            dataset=dataset_name,
            by_design=True,
        )
        return carried

    def _collect_linguistic_terms(
        self, model_obj: Dict[str, Any]
    ) -> Dict[Tuple[str, Optional[str]], Tuple[List[str], List[str]]]:
        """Read Q&A linguistic-schema synonyms (cultures[].linguisticMetadata).

        Returns {(table_cf, property_cf|None): (authored_terms, generated_terms)}.
        Terms in state 'Suggested' (unaccepted suggestions, e.g. from visual
        renames — already covered by report aliases) or 'Deleted' are ignored.
        """
        out: Dict[Tuple[str, Optional[str]], Tuple[List[str], List[str]]] = {}
        for culture in model_obj.get("cultures") or []:
            if not isinstance(culture, dict):
                continue
            lm = culture.get("linguisticMetadata")
            if not isinstance(lm, dict):
                continue
            content = lm.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except Exception:
                    logger.debug("Unparseable linguistic metadata for culture %s", culture.get("name"))
                    continue
            if not isinstance(content, dict):
                continue
            for entity in (content.get("Entities") or {}).values():
                if not isinstance(entity, dict):
                    continue
                binding = (entity.get("Definition") or {}).get("Binding") or {}
                table = binding.get("ConceptualEntity")
                if not table:
                    continue
                prop = binding.get("ConceptualProperty")
                key = (str(table).casefold(), str(prop).casefold() if prop else None)
                authored, generated = out.setdefault(key, ([], []))
                for term_entry in entity.get("Terms") or []:
                    if not isinstance(term_entry, dict):
                        continue
                    for term, meta in term_entry.items():
                        state = (
                            str(meta.get("State") or "").strip().lower()
                            if isinstance(meta, dict)
                            else ""
                        )
                        if state in {"suggested", "deleted"}:
                            continue
                        text = str(term or "").strip()
                        if not text:
                            continue
                        target = generated if state == "generated" else authored
                        if text not in target:
                            target.append(text)
        return out

    def _linguistic_synonyms_for(self, table_name: str, field_name: str) -> Tuple[List[str], List[str]]:
        found = self._linguistic_terms.get((str(table_name).casefold(), str(field_name).casefold()))
        return (list(found[0]), list(found[1])) if found else ([], [])

    def _dedupe_metric_names_across_model(self, osi_model: OSIModel) -> None:
        """Make metric names unique across the whole model, deterministically.

        Two tables may each define e.g. 'Total Sales' (or names that collapse
        to the same Snowflake identifier). The first occurrence in source
        order keeps its name; later ones become '<name> (<table>)' (+ ' 2',
        ' 3' if still taken). Every rename is ledgered.
        """
        from semabridge.utils.identifiers import IdentifierSanitizer

        sanitizer = IdentifierSanitizer()

        def key(name: str) -> str:
            try:
                return sanitizer.sanitize_column(name).casefold()
            except Exception:
                return self._normalize_measure_name_key(name)

        groups: Dict[str, List[OSIMetric]] = {}
        for metric in osi_model.metrics:
            groups.setdefault(key(metric.unique_name), []).append(metric)
        taken = set(groups.keys())
        for metrics in groups.values():
            if len(metrics) < 2:
                continue
            datasets = sorted({m.dataset for m in metrics})
            originals = sorted({m.unique_name for m in metrics})
            for metric in metrics[1:]:
                base = f"{metric.unique_name} ({metric.dataset})"
                candidate, n = base, 2
                while key(candidate) in taken:
                    candidate = f"{base} {n}"
                    n += 1
                taken.add(key(candidate))
                old_name = metric.unique_name
                metric.unique_name = candidate
                if not metric.original_name:
                    metric.original_name = old_name
                self.drop_ledger.record(
                    "metric",
                    old_name,
                    DropStage.EXTRACTION,
                    f"renamed to '{candidate}' — metric name "
                    f"{' / '.join(repr(o) for o in originals)} is defined more than once "
                    f"(tables: {', '.join(datasets)}); Snowflake semantic view metric names "
                    "must be unique",
                    dataset=metric.dataset,
                    by_design=True,
                )
                logger.info(
                    "Renamed duplicate metric '%s' on '%s' to '%s'", old_name, metric.dataset, candidate
                )

    def from_osi(self, osi_model: OSIModel) -> Any:
        # Implementing simple TMSL generation or raising NotImplemented
        # Ideally, we should use a separate OSIToTMSLConverter or TmslGenerator
        raise NotImplementedError("OSI to TMSL conversion is handled by TmslGenerator")

    def _create_dimension_from_dataset(self, dataset: OSIDataset) -> Optional[OSIDimension]:
        """Create an implicit dimension from a dataset."""
        if dataset.is_hidden:
            return None
            
        attributes = []
        for col in dataset.columns:
            # Skip hidden columns or potential measures (metrics usually come separately, but columns might be hidden)
            if col.is_hidden or getattr(col, "is_measure_candidate", False):
                continue
                
            attr = OSIAttribute(
                unique_name=col.unique_name,
                label=col.label,
                dataset=dataset.unique_name,
                source_column=col.unique_name,
                is_hidden=col.is_hidden
            )
            attributes.append(attr)
            
        if not attributes:
            return None
            
        return OSIDimension(
            unique_name=dataset.unique_name,
            label=dataset.label,
            description=dataset.description,
            dataset=dataset.unique_name,
            attributes=attributes,
            is_hidden=dataset.is_hidden
        )

    def _create_metrics_from_aggregation_columns(
        self,
        dataset: OSIDataset,
        is_fact: bool = False,
        covered: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[OSIMetric]:
        """Create OSI metrics for Fabric columns with summarizeBy aggregation."""
        if not is_fact or getattr(dataset, "is_hidden", False):
            return []

        metrics: List[OSIMetric] = []
        seen: set[str] = set()
        covered_set = covered or set()

        for col in dataset.columns:
            if col.is_hidden or getattr(col, "is_key", False):
                continue

            dtype = getattr(col, "data_type", None)
            dtype_val = getattr(dtype, "value", dtype) if dtype is not None else ""
            if dtype_val not in (
                OSIDataType.INTEGER.value,
                OSIDataType.DECIMAL.value,
                OSIDataType.FLOAT.value,
                "integer", "decimal", "float", "double", "int64"
            ):
                continue

            aggregation = getattr(col, "default_aggregation", None)
            if aggregation is None:
                continue

            agg_str = str(getattr(aggregation, "value", aggregation) or "").upper()
            col_key = (dataset.unique_name.upper(), col.unique_name.upper(), agg_str)
            col_key_bare = (col.unique_name.upper(), agg_str)
            if col_key in covered_set or col_key_bare in covered_set:
                continue

            metric_name = col.unique_name
            metric_key = metric_name.casefold()
            if metric_key in seen:
                continue
            seen.add(metric_key)
            metrics.append(
                OSIMetric(
                    unique_name=metric_name,
                    label=col.label or metric_name,
                    dataset=dataset.unique_name,
                    source_column=col.unique_name,
                    expression=None,
                    aggregation=aggregation,
                    description=col.description,
                    format_string=col.format_string,
                    is_hidden=False,
                    access_modifier="public_access",
                    synonyms=list(getattr(col, "synonyms", []) or []),
                    source_file=getattr(col, "source_file", None),
                )
            )
        return metrics

    def _parse_dataset(
        self,
        table_def: Dict[str, Any],
        kind: Optional[str] = None,
        expression: Optional[str] = None,
    ) -> OSIDataset:
        """Parse a TMSL table into OSIDataset."""
        name = table_def["name"]
        if kind is None:
            kind, expression = classify_tmsl_table(table_def)

        columns = []
        if "columns" in table_def:
            for col in table_def["columns"]:
                columns.append(self._parse_column(col, name))

        source_table = name

        return OSIDataset(
            unique_name=name,
            label=name,
            description=table_def.get("description"),
            is_hidden=table_def.get("isHidden", False),
            columns=columns,
            source_table=source_table,
            dataset_kind=kind,
            expression=expression or None,
        )

    @classmethod
    def _is_auto_hidden_table_name(cls, table_name: str) -> bool:
        return any(
            str(table_name or "").startswith(prefix)
            for prefix in cls.AUTO_HIDDEN_TABLE_PREFIXES
        )

    def _parse_column(self, col_def: Dict[str, Any], table_name: str) -> OSIColumn:
        """Parse a TMSL column into OSIColumn with Cortex AI metadata."""
        tmsl_type = col_def.get("dataType", "string")
        col_name = col_def.get("name", "")
        format_string = col_def.get("formatString")

        business_type = self._business_rule_type(table_name, col_name)

        type_map = {
            # canonical lowercase TMSL names
            "int64": OSIDataType.INTEGER,
            "double": OSIDataType.FLOAT,
            "decimal": OSIDataType.DECIMAL,
            "boolean": OSIDataType.BOOLEAN,
            "dateTime": OSIDataType.DATETIME,
            "string": OSIDataType.STRING,
            "binary": OSIDataType.BINARY,
            # Fabric alternative (capitalised) type aliases
            "Int64": OSIDataType.INTEGER,
            "Double": OSIDataType.FLOAT,
            "Decimal": OSIDataType.DECIMAL,
            "Boolean": OSIDataType.BOOLEAN,
            "DateTime": OSIDataType.DATETIME,
            "String": OSIDataType.STRING,
            "Binary": OSIDataType.BINARY,
            # Fabric short-hand aliases not present in standard TMSL
            "time": OSIDataType.TIME,
            "Time": OSIDataType.TIME,
            "date": OSIDataType.DATE,
            "Date": OSIDataType.DATE,
            "bool": OSIDataType.BOOLEAN,
            "Bool": OSIDataType.BOOLEAN,
            # Currency maps to DECIMAL (closest OSI equivalent)
            "currency": OSIDataType.DECIMAL,
            "Currency": OSIDataType.DECIMAL,
        }

        # Ambiguous TMSL types that need business-rule or inference refinement.
        # Explicit types (bool, Currency, Date, Time, int64, etc.) should be
        # trusted as-is; business rules are only for resolving ambiguity.
        _ambiguous_tmsl_types = frozenset({"string", "String", "dateTime", "DateTime"})

        # Layer 1: hard business rules — only for ambiguous TMSL types.
        if tmsl_type in _ambiguous_tmsl_types and business_type is not None:
            mapped_type = business_type
        else:
            # Layer 2: Fabric metadata — trust explicit type declarations.
            mapped_type = type_map.get(tmsl_type, OSIDataType.STRING)
            if tmsl_type in ("dateTime", "DateTime"):
                mapped_type = self._infer_datetime_column_type(col_name, format_string)
            # Layer 3: fallback inference for string/unclear fields.
            if mapped_type == OSIDataType.STRING and tmsl_type in _ambiguous_tmsl_types:
                mapped_type = self._infer_string_column_type(col_name, format_string)


        # Determine if key (heuristic on name pattern)
        is_key = False
        upper_name = col_name.upper()
        if upper_name.endswith("ID") or upper_name.endswith("KEY") or upper_name.startswith("PK_") or upper_name.startswith("FK_"):
            is_key = True

        # ── Cortex AI metadata ──────────────────────────────────────────────
        user_synonyms = col_def.get("synonyms") or []
        if not isinstance(user_synonyms, list):
            user_synonyms = []
        if user_synonyms:
            logger.debug(
                "Column '%s': loaded %d user-defined synonyms from TMSL",
                col_name,
                len(user_synonyms),
            )
        ling_authored, ling_generated = self._linguistic_synonyms_for(table_name, col_name)
        existing_synonyms, synonym_sources = merge_synonyms_with_sources(
            ui_overrides=lookup_synonym_override(
                self._synonym_overrides,
                self._synonym_model_names,
                table_name,
                col_name,
            ),
            user_defined=list(user_synonyms) + ling_authored,
            auto_generated=ling_generated or None,
        )
        # Columns have no separate display-name heuristic distinct from
        # col_name (unlike measures), so the same name is used for both
        # lookup keys.
        synonyms, has_report_alias, matched_aliases = self._resolve_report_aliases(
            col_name, col_name, existing_synonyms, field_type="column", table_name=table_name
        )
        for alias in matched_aliases:
            synonym_sources[alias] = "report_alias"

        # is_enum heuristic: TMSL dataCategory == "Category" or boolean type
        is_enum = (
            col_def.get("dataCategory", "").lower() == "category"
            or mapped_type == OSIDataType.BOOLEAN
        )

        summarize_by = str(col_def.get("summarizeBy") or "").strip().lower()
        default_aggregation = self._map_summarize_by(summarize_by)
        is_measure_candidate = default_aggregation is not None

        # Physical source column and calculated-column DAX are carried in
        # separate fields. ``source_expression`` keeps its historical combined
        # value (sourceColumn, else DAX) for existing downstream consumers.
        col_type = _norm_token(col_def.get("type"))
        raw_source_column = join_tmsl_expression(col_def.get("sourceColumn")).strip()
        dax_expr = join_tmsl_expression(col_def.get("expression")).strip()
        is_calculated = col_type == "calculated" or (bool(dax_expr) and not raw_source_column)
        source_expr = raw_source_column or dax_expr or ""

        sort_by = col_def.get("sortByColumn")
        if isinstance(sort_by, dict):
            sort_by = sort_by.get("name") or sort_by.get("column")
        sort_by = str(sort_by).strip() if sort_by else None

        return OSIColumn(
            unique_name=col_name,
            label=col_name,
            data_type=mapped_type,
            description=col_def.get("description"),
            is_hidden=col_def.get("isHidden", False),
            is_measure_candidate=is_measure_candidate,
            default_aggregation=default_aggregation,
            format_string=format_string,
            is_key=is_key,
            source_expression=source_expr,
            synonyms=synonyms,
            synonym_sources=synonym_sources,
            has_report_alias=has_report_alias,
            source_file=self._source_file,
            is_enum=is_enum,
            folder=(str(col_def.get("displayFolder")).strip() or None) if col_def.get("displayFolder") else None,
            source_column=None if is_calculated else (raw_source_column or None),
            is_calculated=is_calculated,
            expression=(dax_expr or None) if is_calculated else None,
            data_category=(str(col_def.get("dataCategory")).strip() or None) if col_def.get("dataCategory") else None,
            sort_by_column=sort_by or None,
        )

    def _resolve_report_aliases(
        self,
        name: str,
        display_name: str,
        existing_synonyms: List[str],
        field_type: str,
        table_name: Optional[str] = None,
    ) -> Tuple[List[str], bool, List[str]]:
        """Merge report-layer visual-title aliases into a field's synonym list.

        Shared by _parse_metric and _parse_column. Matching is an exact,
        case-insensitive lookup against the field's own name or display
        name — no fuzzy/substring matching (see test_exact_matching_only /
        test_column_exact_matching_only).

        Measures are matched by (name, "measure") alone — measure names are
        globally unique in valid TMSL, so no table scoping is needed or
        attempted. Columns are matched by (name, "column", table) — column
        names are only unique within their own table, so a same-named
        column in a different table must not be able to match. There is
        deliberately no unscoped fallback for columns whose table couldn't
        be resolved at extraction time: two different same-named columns
        that both fail to resolve a table would otherwise be merged into
        the same lookup slot and cross-contaminate each other's aliases.
        An unresolvable-table column simply gets no report alias
        (has_report_alias=False) rather than a guess.

        Returns:
            (merged_synonyms, has_report_alias, matched_aliases) where
            matched_aliases is the list of report-alias strings that were
            actually attributed to this field — including ones that were
            already present via another synonym source, so callers can
            upgrade that entry's provenance tag to "report_alias" (a
            confirmed genuine alias is strictly more informative than a
            coincidental auto-generated/TMSL-authored match).
        """
        lookup = self._field_alias_lookup
        if lookup is None:
            return list(existing_synonyms), False, []

        report_aliases: List[str] = []
        for key_candidate in (name, display_name):
            if not key_candidate:
                continue
            key_name = key_candidate.lower()
            if field_type == "column":
                match = lookup.get((key_name, "column", (table_name or "").lower() or None))
            else:
                match = lookup.get((key_name, field_type, None))
            if match:
                report_aliases = match
                break

        unique_name_lower = name.lower()
        label_lower = display_name.lower()

        merged: List[str] = []
        seen: set = set()

        # Preserves discovery order: existing synonyms first
        for syn in existing_synonyms:
            if isinstance(syn, str) and syn.strip():
                s_strip = syn.strip()
                if s_strip not in seen:
                    seen.add(s_strip)
                    merged.append(s_strip)

        # Append matched layout report aliases next
        matched_aliases: List[str] = []
        for alias in report_aliases:
            if isinstance(alias, str) and alias.strip():
                a_strip = alias.strip()
                a_lower = a_strip.lower()
                if a_lower != unique_name_lower and a_lower != label_lower:
                    if a_strip not in seen:
                        seen.add(a_strip)
                        merged.append(a_strip)
                    matched_aliases.append(a_strip)

        return merged, bool(report_aliases), matched_aliases

    @staticmethod
    def _map_summarize_by(summarize_by: Any) -> Optional[OSIAggregationType]:
        """Map Fabric summarizeBy to OSI aggregation; none/blank means dimension."""
        normalized = str(summarize_by or "").strip().lower()
        if not normalized or normalized in ("none", "default"):
            return None
        int_map = {
            "0": None, "1": None, "2": OSIAggregationType.SUM, "3": OSIAggregationType.MIN,
            "4": OSIAggregationType.MAX, "5": OSIAggregationType.COUNT, "6": OSIAggregationType.AVG,
            "7": OSIAggregationType.COUNT_DISTINCT,
        }
        if normalized in int_map:
            return int_map[normalized]
        mapping = {
            "sum": OSIAggregationType.SUM,
            "average": OSIAggregationType.AVG,
            "avg": OSIAggregationType.AVG,
            "count": OSIAggregationType.COUNT,
            "distinctcount": OSIAggregationType.COUNT_DISTINCT,
            "countdistinct": OSIAggregationType.COUNT_DISTINCT,
            "min": OSIAggregationType.MIN,
            "minimum": OSIAggregationType.MIN,
            "max": OSIAggregationType.MAX,
            "maximum": OSIAggregationType.MAX,
        }
        return mapping.get(normalized, OSIAggregationType.SUM)

    _BUSINESS_FIELD_TYPE_TO_OSI = {
        "BOOLEAN": OSIDataType.BOOLEAN,
        "TIMESTAMP": OSIDataType.DATETIME,
        "FLOAT": OSIDataType.FLOAT,
        "DATE": OSIDataType.DATE,
    }

    @classmethod
    def _business_rule_type(
        cls,
        table_name: str,
        col_name: str,
    ) -> Optional[OSIDataType]:
        """Business-rule layer for common CRM-style naming conventions."""
        hint = infer_business_field_type(table_name, col_name)
        return cls._BUSINESS_FIELD_TYPE_TO_OSI.get(hint) if hint else None

    @staticmethod
    def _infer_string_column_type(
        col_name: str,
        format_string: Optional[str],
    ) -> OSIDataType:
        """Infer stronger type hints for Fabric string columns."""
        n = (col_name or "").lower()
        tokens = {t for t in re.split(r"[^a-z0-9]+", n.replace("_", " ")) if t}
        fmt = (format_string or "").strip().upper()

        # Keep identifier-like business keys as strings even when they contain "number".
        if "id" in tokens or n.endswith("_id"):
            return OSIDataType.STRING
        if "number" in tokens and any(t in tokens for t in ("job", "model", "ticket", "project", "opportunity", "record")):
            return OSIDataType.STRING

        # Descriptive text columns (names, groups, descriptions, categories) must remain STRING
        if any(t in tokens for t in ("name", "group", "desc", "description", "category", "channel", "type", "region", "status", "comment", "title", "text", "segment", "country", "city", "state", "vp")):
            return OSIDataType.STRING

        # Format-driven inference is strongest for Fabric text columns.
        if "%" in fmt:
            return OSIDataType.FLOAT
        if any(ch in fmt for ch in ("#", "0")):
            return OSIDataType.FLOAT

        # Name-based numeric hints for common forecasting fields.
        if "%" in col_name or "percent" in n:
            return OSIDataType.FLOAT
        if any(k in tokens for k in ("amount", "price", "cost", "revenue", "rate", "score", "size", "qty", "quantity", "mw", "mwdc", "forecast", "target", "actual", "variance", "lead", "time")):
            return OSIDataType.FLOAT

        # Date and timestamp hints for string-typed columns.
        if "date" in tokens:
            return OSIDataType.DATE
        if any(k in tokens for k in ("timestamp", "datetime", "ts")):
            return OSIDataType.DATETIME

        # Integer-like counters, but avoid generic "number" ambiguity.
        if any(k in tokens for k in ("count", "num", "year", "month", "day")):
            return OSIDataType.INTEGER

        return OSIDataType.STRING

    @staticmethod
    def _infer_datetime_column_type(
        col_name: str,
        format_string: Optional[str],
    ) -> OSIDataType:
        """Disambiguate Fabric dateTime between DATE and DATETIME."""
        n = (col_name or "").lower()
        fmt = (format_string or "").lower()

        # Explicit time markers mean true datetime.
        if any(token in fmt for token in ("hh", "h:", "am/pm", "ss", "mm:ss")):
            return OSIDataType.DATETIME
        if any(token in n for token in ("timestamp", "datetime", "created at", "updated at")):
            return OSIDataType.DATETIME

        # Date-oriented naming/format should remain DATE in Snowflake.
        if "short date" in fmt or "long date" in fmt or "date" in n:
            return OSIDataType.DATE

        # Default for Fabric dateTime is datetime.
        return OSIDataType.DATETIME

    def _parse_metric(self, measure_def: Dict[str, Any], dataset_name: str) -> OSIMetric:
        """Parse a TMSL measure into OSIMetric with Cortex AI metadata."""
        dax = self._extract_measure_expression(measure_def)
        if isinstance(dax, list):
            dax = "\n".join(dax)

        name = measure_def["name"]
        display_name = self._measure_display_name(measure_def) or name

        if not str(dax).strip():
            logger.warning(
                "Measure '%s' in dataset '%s' has no expression; retaining it with a placeholder",
                name,
                dataset_name,
            )
            dax = "BLANK()"

        # access_modifier heuristic:
        # - Names prefixed with "_" or ending with " Helper" / " Base" → private_access
        # - Hidden measures → private_access
        # - All others → public_access
        is_helper = (
            name.startswith("_")
            or any(name.lower().endswith(suffix) for suffix in (" helper", " base", " temp", " internal"))
            or measure_def.get("isHidden", False)
        )
        access_modifier = "private_access" if is_helper else "public_access"

        user_synonyms = measure_def.get("synonyms") or []
        if not isinstance(user_synonyms, list):
            user_synonyms = []
        if user_synonyms:
            logger.debug(
                "Measure '%s': loaded %d user-defined synonyms from TMSL",
                name,
                len(user_synonyms),
            )
        ling_authored, ling_generated = self._linguistic_synonyms_for(dataset_name, name)
        if not (ling_authored or ling_generated) and display_name != name:
            ling_authored, ling_generated = self._linguistic_synonyms_for(dataset_name, display_name)
        existing_synonyms, synonym_sources = merge_synonyms_with_sources(
            ui_overrides=lookup_synonym_override(
                self._synonym_overrides,
                self._synonym_model_names,
                dataset_name,
                name,
            ),
            user_defined=list(user_synonyms) + ling_authored,
            auto_generated=ling_generated or None,
        )

        synonyms, has_report_alias, matched_aliases = self._resolve_report_aliases(
            name, display_name, existing_synonyms, field_type="measure"
        )
        for alias in matched_aliases:
            synonym_sources[alias] = "report_alias"
        if has_report_alias:
            logger.debug(
                "Applied %s report aliases to metric %s",
                len(matched_aliases),
                name,
            )

        return OSIMetric(
            unique_name=name,
            original_name=name,
            label=display_name,
            dataset=dataset_name,
            expression=dax,
            aggregation=OSIAggregationType.NONE,  # Raw DAX implies explicit calc
            description=measure_def.get("description"),
            format_string=measure_def.get("formatString"),
            is_hidden=measure_def.get("isHidden", False),
            access_modifier=access_modifier,
            synonyms=synonyms,
            synonym_sources=synonym_sources,
            has_report_alias=has_report_alias,
            source_file=self._source_file,
            folder=(str(measure_def.get("displayFolder")).strip() or None)
            if measure_def.get("displayFolder")
            else None,
            kpi=self._record_kpi(measure_def, name, dataset_name),
        )

    @staticmethod
    def _iter_table_measures(table_def: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return any measure definitions attached to a TMSL table."""
        raw_measures = table_def.get("measures")
        if raw_measures is None:
            raw_measures = table_def.get("metrics")

        if raw_measures is None:
            return []
        if isinstance(raw_measures, dict):
            raw_measures = list(raw_measures.values())
        if not isinstance(raw_measures, list):
            logger.warning(
                "Skipping measures on table '%s': expected list, got %s",
                table_def.get("name", "<unnamed>"),
                type(raw_measures).__name__,
            )
            return []

        measures: List[Dict[str, Any]] = []
        for measure in raw_measures:
            if isinstance(measure, dict):
                measures.append(measure)
            else:
                logger.warning(
                    "Skipping malformed measure entry on table '%s': %r",
                    table_def.get("name", "<unnamed>"),
                    measure,
                )
        return measures

    @staticmethod
    def _normalize_measure_name_key(name: str) -> str:
        """Normalize measure names for robust duplicate detection."""
        normalized = unicodedata.normalize("NFKC", str(name or ""))
        normalized = " ".join(normalized.split())
        return normalized.casefold()

    @staticmethod
    def _measure_display_name(measure_def: Dict[str, Any]) -> str:
        """Return user-visible measure display name when available."""
        return str(
            measure_def.get("displayName")
            or measure_def.get("caption")
            or measure_def.get("label")
            or measure_def.get("name")
            or ""
        ).strip()

    def _dump_measure_audit(self, model_obj: Dict[str, Any], dataset_id: str, phase: str) -> None:
        """Write strict raw measure audit before conversion for duplicate tracing."""
        try:
            tables = model_obj.get("tables") or []
            audit_tables: List[Dict[str, Any]] = []
            for table in tables:
                table_name = str(table.get("name") or "")
                measures = self._iter_table_measures(table)
                if not measures:
                    continue

                name_totals: Dict[str, int] = {}
                display_totals: Dict[str, int] = {}
                for m in measures:
                    raw_name = str(m.get("name") or "").strip()
                    display_name = self._measure_display_name(m)
                    nk = self._normalize_measure_name_key(raw_name)
                    dk = self._normalize_measure_name_key(display_name)
                    if nk:
                        name_totals[nk] = name_totals.get(nk, 0) + 1
                    if dk:
                        display_totals[dk] = display_totals.get(dk, 0) + 1

                duplicate_name_groups = [k for k, v in name_totals.items() if v > 1]
                duplicate_display_groups = [k for k, v in display_totals.items() if v > 1]
                is_measure_table = not (table.get("columns") or []) or all(c.get("isHidden") for c in table.get("columns", []))
                if not (is_measure_table or duplicate_name_groups or duplicate_display_groups):
                    continue

                rows: List[Dict[str, Any]] = []
                for idx, m in enumerate(measures, start=1):
                    raw_name = str(m.get("name") or "").strip()
                    display_name = self._measure_display_name(m)
                    expr = self._extract_measure_expression(m)
                    expr_norm = " ".join(str(expr or "").split())
                    expr_hash = hashlib.sha1(expr_norm.encode("utf-8")).hexdigest()[:16]
                    rows.append(
                        {
                            "index": idx,
                            "name": raw_name,
                            "display_name": display_name,
                            "name_key": self._normalize_measure_name_key(raw_name),
                            "display_key": self._normalize_measure_name_key(display_name),
                            "is_hidden": bool(m.get("isHidden", False)),
                            "expression_hash": expr_hash,
                        }
                    )

                audit_tables.append(
                    {
                        "table": table_name,
                        "measure_count": len(measures),
                        "duplicate_name_groups": duplicate_name_groups,
                        "duplicate_display_groups": duplicate_display_groups,
                        "measures": rows,
                    }
                )

            if not audit_tables:
                return

            safe_dataset = re.sub(r"[^A-Za-z0-9_.-]", "_", str(dataset_id or "model"))
            safe_dataset = re.sub(r"_+", "_", safe_dataset).strip("._") or "model"
            out_dir = Path("output") / "debug" / safe_dataset
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{phase}_measure_audit.json"
            payload = {
                "dataset_id": dataset_id,
                "phase": phase,
                "tables": audit_tables,
            }
            out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            logger.info("Wrote measure audit artifact: %s", out_file)
        except Exception as exc:
            logger.warning("Failed writing measure audit artifact: %s", exc)

    def _iter_table_measures_with_stable_names(self, table_def: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return measures with deterministic numbering for duplicate names.

        Fabric can surface visually duplicate measure names. Preserve all of
        them by assigning stable ``_1``, ``_2`` suffixes when duplicates are
        detected, so downstream sync keeps both entries instead of collapsing.
        """
        measures = self._iter_table_measures(table_def)
        if not measures:
            return measures

        name_totals: Dict[str, int] = {}
        display_totals: Dict[str, int] = {}
        for measure in measures:
            raw_name = str(measure.get("name") or "").strip()
            key = self._normalize_measure_name_key(raw_name)
            if key:
                name_totals[key] = name_totals.get(key, 0) + 1
            display_name = self._measure_display_name(measure)
            display_key = self._normalize_measure_name_key(display_name)
            if display_key:
                display_totals[display_key] = display_totals.get(display_key, 0) + 1

        name_seen: Dict[str, int] = {}
        output: List[Dict[str, Any]] = []
        for measure in measures:
            cloned = dict(measure)
            raw_name = str(cloned.get("name") or "").strip()
            key = self._normalize_measure_name_key(raw_name)
            total = name_totals.get(key, 0)
            if total > 1 and raw_name:
                idx = name_seen.get(key, 0) + 1
                name_seen[key] = idx
                cloned["name"] = f"{raw_name}_{idx}"
                cloned.setdefault("displayName", raw_name)
                self.drop_ledger.record(
                    "metric",
                    raw_name,
                    DropStage.EXTRACTION,
                    f"renamed to '{cloned['name']}' — measure name '{raw_name}' is defined "
                    f"{total} times on table '{table_def.get('name', '<unnamed>')}'; "
                    "Snowflake semantic view metric names must be unique",
                    dataset=str(table_def.get("name") or "") or None,
                    detail=f"occurrence {idx} of {total}",
                    by_design=True,
                )
            output.append(cloned)

        display_dup_count = sum(1 for c in display_totals.values() if c > 1)
        if display_dup_count:
            logger.info(
                "Detected %d duplicate measure display-name group(s) on table '%s'",
                display_dup_count,
                table_def.get("name", "<unnamed>"),
            )

        return output

    @staticmethod
    def _extract_measure_expression(measure_def: Dict[str, Any]) -> Any:
        """Read the raw DAX expression from a TMSL measure definition."""
        for key in ("expression", "formula", "dax", "value"):
            if key not in measure_def:
                continue
            expr = measure_def.get(key)
            if expr is not None:
                return expr
        return ""

    def _parse_relationship(self, rel_def: Dict[str, Any]) -> Optional[OSIRelationship]:
        """Parse TMSL relationship."""
        try:
             def _normalize_rel_identifier(value: str) -> str:
                 text = str(value or "").strip()
                 if (text.startswith("[") and text.endswith("]")) and len(text) >= 2:
                     text = text[1:-1].strip()
                 if (text.startswith("'") and text.endswith("'")) and len(text) >= 2:
                     text = text[1:-1].strip()
                 if (text.startswith('"') and text.endswith('"')) and len(text) >= 2:
                     text = text[1:-1].strip()
                 return text

             def _get_rel_field(*names: str) -> Optional[str]:
                 for key in names:
                     for actual_key, value in rel_def.items():
                         if actual_key.lower() == key.lower() and value not in (None, ""):
                             return _normalize_rel_identifier(str(value))
                 return None

             from_table = _get_rel_field("fromTable", "from_table", "sourceTable")
             from_column = _get_rel_field("fromColumn", "from_column", "sourceColumn")
             to_table = _get_rel_field("toTable", "to_table", "targetTable")
             to_column = _get_rel_field("toColumn", "to_column", "targetColumn")
             if not (from_table and from_column and to_table and to_column):
                 raise ValueError("Relationship endpoints missing required fields")

             # Always derive canonical names from endpoints because Fabric can
             # emit GUID/system relationship names that violate OSI naming rules.
             name = generate_relationship_name(
                 from_table,
                 from_column,
                 to_table,
                 to_column,
             )
             
             return OSIRelationship(
                 unique_name=name,
                 from_dataset=from_table,
                 from_columns=[from_column],
                 to_dataset=to_table,
                 to_columns=[to_column],
                 cardinality=tmsl_relationship_cardinality(rel_def),
                 cross_filter_direction=tmsl_relationship_cross_filter(rel_def),
                 is_active=tmsl_relationship_is_active(rel_def),
             )
        except Exception as e:
            logger.warning(f"Failed to parse relationship '{rel_def.get('name')}': {e}")
            return None
