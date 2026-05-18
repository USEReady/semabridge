"""
TMDL to OSI Converter.

Converts Fabric Model Definitions (TMDL JSON) into the OSI (Open Semantic Interchange)
canonical intermediate representation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semabridge.utils.synonyms import merge_synonyms, generate_auto_synonyms

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

logger = get_logger(__name__)


class TMDLToOSIConverter(BaseConverter):
    """
    Transforms Fabric TMDL JSON into OSI Model.
    """

    AUTO_HIDDEN_TABLE_PREFIXES = (
        "LocalDateTable_",
        "DateTableTemplate_",
    )

    def to_osi(self, source_data: Dict[str, Any]) -> OSIModel:
        """
        Convert TMDL dictionary to OSIModel object.

        Args:
            source_data: Dictionary containing:
                - tmdl: Decoded model.bim JSON
                - workspace_id: Fabric workspace ID
                - dataset_id: Fabric dataset ID (unique_name for OSIModel)

        Returns:
            OSIModel object
        
        Raises:
            ConversionError: If transformation fails.
        """
        try:
            tmsl_json = source_data.get("tmdl", {})
            workspace_id = source_data.get("workspace_id")
            dataset_id = source_data.get("dataset_id")

            if not tmsl_json or not dataset_id:
                raise ConversionError(
                    "Missing 'tmdl' or 'dataset_id' in source_data",
                    source_format="tmdl",
                    target_format="osi"
                )

            model_obj = tmsl_json.get("model", {})
            # Prioritize display_name passed from source_data, then the model name, then default.
            display_name = source_data.get("display_name") or model_obj.get("name") or "FabricModel"
            self._dump_measure_audit(model_obj, dataset_id, phase="tmdl_to_osi_pre")

            # Guard: connector-type keywords used as model names produce misleading view names
            # (e.g. a dataset named "fabric" would generate a "fabric_SEMANTIC" view).
            # Fall back to the dataset_id when the display name collides with a reserved keyword.
            _RESERVED_MODEL_NAMES = frozenset({"fabric", "snowflake", "pbix", "databricks", "model"})
            resolved_unique_name = display_name
            if str(resolved_unique_name).strip().lower() in _RESERVED_MODEL_NAMES:
                logger.warning(
                    "Fabric dataset display name '%s' collides with a reserved connector keyword. "
                    "Using dataset_id '%s' as the model unique_name to avoid ambiguous Snowflake view names. "
                    "Rename the Fabric dataset or set 'project_name' in your project config to override.",
                    resolved_unique_name,
                    dataset_id,
                )
                resolved_unique_name = dataset_id

            # Use display name as unique_name to ensure Snowflake views use display names, not GUIDs.
            # GUID (dataset_id) is still preserved in metadata for traceability.
            osi_model = OSIModel(
                unique_name=resolved_unique_name,
                label=display_name,
                description=model_obj.get("description", ""),
                source_platform="fabric",
                metadata={"workspace_id": workspace_id, "dataset_id": dataset_id}
            )

            # Process Datasets (Tables)
            if "tables" in model_obj:
                for table in model_obj["tables"]:
                    t_name = table.get("name", "")
                    if self._is_auto_hidden_table_name(t_name):
                        logger.info(
                            "Skipping hidden auto-date table during OSI conversion: %s",
                            t_name or "<unnamed>",
                        )
                        continue
                    dataset = self._parse_dataset(table)
                    osi_model.datasets.append(dataset)

                    # Create corresponding Dimension for each Dataset
                    # In TMDL/Power BI, every table is potentially a dimension
                    dim = self._create_dimension_from_dataset(dataset)
                    if dim:
                        osi_model.dimensions.append(dim)

                    # Process Measures (Metrics)
                    for measure in self._iter_table_measures_with_stable_names(table):
                        metric = self._parse_metric(measure, dataset.unique_name)
                        osi_model.metrics.append(metric)

            # Process Relationships
            # Build resilient lookup maps so relationship endpoint table names
            # can still resolve when Fabric emits qualified identifiers.
            valid_dataset_map = {
                str(ds.unique_name).casefold(): ds.unique_name for ds in osi_model.datasets
            }
            compact_dataset_map = {
                re.sub(r"[^a-z0-9]", "", str(ds.unique_name).casefold()): ds.unique_name
                for ds in osi_model.datasets
            }

            def _resolve_dataset_name(raw_name: str) -> str | None:
                candidate = str(raw_name or "").strip()
                if not candidate:
                    return None

                # 1. Direct case-insensitive match.
                direct = valid_dataset_map.get(candidate.casefold())
                if direct:
                    return direct

                # 2. Unqualified suffix match (schema.table -> table).
                parts = [p.strip(" []'\"`") for p in re.split(r"[./]", candidate) if p.strip(" []'\"`")]
                if parts:
                    suffix = valid_dataset_map.get(parts[-1].casefold())
                    if suffix:
                        return suffix

                # 3. Compact normalization (DIM CUSTOMER == dim_customer).
                compact_key = re.sub(r"[^a-z0-9]", "", candidate.casefold())
                if compact_key:
                    compact = compact_dataset_map.get(compact_key)
                    if compact:
                        return compact
                    if parts:
                        compact_suffix = compact_dataset_map.get(
                            re.sub(r"[^a-z0-9]", "", parts[-1].casefold())
                        )
                        if compact_suffix:
                            return compact_suffix
                return None
            
            if "relationships" in model_obj:
                for rel in model_obj["relationships"]:
                    osi_rel = self._parse_relationship(rel)
                    if osi_rel:
                        from_dataset = _resolve_dataset_name(osi_rel.from_dataset)
                        to_dataset = _resolve_dataset_name(osi_rel.to_dataset)
                        # Only add relationship if both referenced tables exist
                        if from_dataset and to_dataset:
                            osi_rel.from_dataset = from_dataset
                            osi_rel.to_dataset = to_dataset
                            osi_model.relationships.append(osi_rel)
                        else:
                            # Skip only relationships that reference truly missing datasets.
                            logger.info(
                                f"Skipping relationship '{osi_rel.unique_name}': "
                                f"references unavailable table(s) "
                                f"({osi_rel.from_dataset} -> {osi_rel.to_dataset})"
                            )

            # Supplement: always run RelationshipDetector to add inferred
            # relationships that the Fabric model omits (e.g. CUSTOMER->INDUSTRY,
            # BU->EXECUTIVE).  Results are merged with dedup so that explicit raw
            # relationships are never overwritten.
            if osi_model.datasets:
                try:
                    # Supplement: infer Fabric relationships not captured in TMDL.
                    # Uses two targeted strategies for Fabric column naming conventions:
                    #  A) _KEY / _ID / _FK suffix -> match base to a table name -> find that table's PK
                    #     e.g. BU.Executive_id -> Executive.ID
                    #  B) Exact shared column name in exactly one other table (must also have FK suffix)
                    #     e.g. Fact.YearPeriod -> Calendar.YearPeriod

                    FK_SUFFIXES = ("_KEY", "_ID", "_FK", "_CODE", " KEY", " ID")

                    def _n(name: str) -> str:
                        return name.replace(" ", "_").upper()

                    # Build lookup: norm_table_name -> dataset_name
                    table_name_map = {ds.unique_name.replace(" ", "_").upper(): ds.unique_name for ds in osi_model.datasets}

                    # Build lookup: norm_col_name -> [(ds_name, original_col)]
                    col_index: dict = {}
                    for ds in osi_model.datasets:
                        for c in ds.columns:
                            col_index.setdefault(_n(c.unique_name), []).append((ds.unique_name, c.unique_name))

                    existing_sigs = {
                        (r.from_dataset.casefold(), _n(r.from_columns[0] if r.from_columns else ""),
                         r.to_dataset.casefold(), _n(r.to_columns[0] if r.to_columns else ""))
                        for r in osi_model.relationships
                    }

                    # Only infer from datasets that are known fact/bridge tables:
                    # (a) already a 'from_table' in existing explicit relationships, OR
                    # (b) has >= 2 FK-suffix columns (wide/fact-like table).
                    # This prevents dimension tables from generating false reverse joins.
                    known_from_tables = {r.from_dataset.casefold() for r in osi_model.relationships}
                    _fk_sfx = ("_KEY", "_ID", "_FK", "_CODE")
                    def _fk_count(ds):
                        return sum(1 for c in ds.columns if any(_n(c.unique_name).endswith(s) for s in _fk_sfx))

                    added = 0
                    for from_ds in osi_model.datasets:
                        if not (from_ds.unique_name.casefold() in known_from_tables or _fk_count(from_ds) >= 2):
                            continue
                        for col in from_ds.columns:
                            col_n = _n(col.unique_name)
                            matched = False

                            # Strategy A: FK suffix -> derive target table name, find its PK
                            for suffix in ("_KEY", "_ID", "_FK", "_CODE"):
                                if not col_n.endswith(suffix):
                                    continue
                                base = col_n[:-len(suffix)].rstrip("_")
                                if not base:
                                    continue
                                target_ds_name = table_name_map.get(base)
                                if not target_ds_name or target_ds_name == from_ds.unique_name:
                                    continue
                                target_ds = next((d for d in osi_model.datasets if d.unique_name == target_ds_name), None)
                                if not target_ds:
                                    continue
                                # Find PK of target table: is_key col, then ID, then first col
                                pk_col = next((c for c in target_ds.columns if c.is_key), None)
                                if not pk_col:
                                    pk_col = next((c for c in target_ds.columns if _n(c.unique_name) in ("ID", base + "_ID")), None)
                                if not pk_col:
                                    continue
                                sig = (from_ds.unique_name.casefold(), col_n, target_ds_name.casefold(), _n(pk_col.unique_name))
                                if sig not in existing_sigs:
                                    fdk = valid_dataset_map.get(from_ds.unique_name.casefold())
                                    tdk = valid_dataset_map.get(target_ds_name.casefold())
                                    if fdk and tdk:
                                        osi_model.relationships.append(OSIRelationship(
                                            unique_name=generate_relationship_name(fdk, col.unique_name, tdk, pk_col.unique_name),
                                            from_dataset=fdk, from_columns=[col.unique_name],
                                            to_dataset=tdk, to_columns=[pk_col.unique_name],
                                            cardinality=OSICardinality.MANY_TO_ONE,
                                            cross_filter_direction=OSICrossFilterDirection.SINGLE,
                                            is_active=True,
                                        ))
                                        existing_sigs.add(sig)
                                        added += 1
                                        matched = True
                                break

                            if matched:
                                continue

                            # Strategy B: shared column name in exactly 1 other table,
                            # but ONLY if this column also has a FK-like suffix
                            # (prevents false matches on generic names like 'Name', 'State')
                            has_fk_suffix = any(col_n.endswith(s) for s in ("_KEY", "_ID", "_FK", "_CODE", "KEY", "ID"))
                            if not has_fk_suffix:
                                continue
                            others = [(ds_name, orig) for ds_name, orig in col_index.get(col_n, []) if ds_name != from_ds.unique_name]
                            if len(others) != 1:
                                continue
                            to_ds_name, to_col_orig = others[0]
                            sig = (from_ds.unique_name.casefold(), col_n, to_ds_name.casefold(), _n(to_col_orig))
                            if sig not in existing_sigs:
                                fdk = valid_dataset_map.get(from_ds.unique_name.casefold())
                                tdk = valid_dataset_map.get(to_ds_name.casefold())
                                if fdk and tdk:
                                    osi_model.relationships.append(OSIRelationship(
                                        unique_name=generate_relationship_name(fdk, col.unique_name, tdk, to_col_orig),
                                        from_dataset=fdk, from_columns=[col.unique_name],
                                        to_dataset=tdk, to_columns=[to_col_orig],
                                        cardinality=OSICardinality.MANY_TO_ONE,
                                        cross_filter_direction=OSICrossFilterDirection.SINGLE,
                                        is_active=True,
                                    ))
                                    existing_sigs.add(sig)
                                    added += 1

                    if added:
                        logger.info(
                            "Supplemented %d inferred relationship(s) (total: %d)",
                            added, len(osi_model.relationships),
                        )
                except Exception as exc:
                    logger.warning("Relationship inference supplement failed: %s", exc)

            return osi_model

        except Exception as e:
            logger.error(f"TMDL to OSI conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert TMDL: {e}",
                source_format="tmdl",
                target_format="osi",
                details={"error": str(e)}
            )

    def from_osi(self, osi_model: OSIModel) -> Any:
        # Implementing simple TMDL generation or raising NotImplemented
        # Ideally, we should use a separate OSIToTMSLConverter or TmslGenerator
        raise NotImplementedError("OSI to TMDL conversion is handled by TmslGenerator")

    def _create_dimension_from_dataset(self, dataset: OSIDataset) -> Optional[OSIDimension]:
        """Create an implicit dimension from a dataset."""
        if dataset.is_hidden:
            return None
            
        attributes = []
        for col in dataset.columns:
            # Skip hidden columns or potential measures (metrics usually come separately, but columns might be hidden)
            if col.is_hidden:
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

    def _parse_dataset(self, table_def: Dict[str, Any]) -> OSIDataset:
        """Parse a TMDL table into OSIDataset."""
        name = table_def["name"]
        
        columns = []
        if "columns" in table_def:
            for col in table_def["columns"]:
                columns.append(self._parse_column(col, name))
        
        # Source table logic
        source_table = name
        if name == "Table":
            source_table = "DEVICE_INVENTORY"  # Legacy heuristic from PRD/Test environment

        return OSIDataset(
            unique_name=name,
            label=name,
            description=table_def.get("description"),
            is_hidden=table_def.get("isHidden", False),
            columns=columns,
            source_table=source_table
        )

    @classmethod
    def _is_auto_hidden_table_name(cls, table_name: str) -> bool:
        return any(
            str(table_name or "").startswith(prefix)
            for prefix in cls.AUTO_HIDDEN_TABLE_PREFIXES
        )

    def _parse_column(self, col_def: Dict[str, Any], table_name: str) -> OSIColumn:
        """Parse a TMDL column into OSIColumn with Cortex AI metadata."""
        tmsl_type = col_def.get("dataType", "string")
        col_name = col_def.get("name", "")
        format_string = col_def.get("formatString")

        business_type = self._business_rule_type(table_name, col_name)

        type_map = {
            # canonical lowercase TMDL names
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
            # Fabric short-hand aliases not present in standard TMDL
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

        # Ambiguous TMDL types that need business-rule or inference refinement.
        # Explicit types (bool, Currency, Date, Time, int64, etc.) should be
        # trusted as-is; business rules are only for resolving ambiguity.
        _ambiguous_tmsl_types = frozenset({"string", "String", "dateTime", "DateTime"})

        # Layer 1: hard business rules â€” only for ambiguous TMDL types.
        if tmsl_type in _ambiguous_tmsl_types and business_type is not None:
            mapped_type = business_type
        else:
            # Layer 2: Fabric metadata â€” trust explicit type declarations.
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

        # â”€â”€ Cortex AI metadata â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Merge user-defined synonyms with auto-generated heuristics
        user_synonyms = col_def.get("synonyms") or []
        if not isinstance(user_synonyms, list):
            user_synonyms = []
        
        auto_synonyms = generate_auto_synonyms(col_name)
        synonyms = merge_synonyms(user_synonyms, auto_synonyms)
        
        if user_synonyms:
            logger.debug("Column '%s': loaded %d user-defined synonyms from TMDL", col_name, len(user_synonyms))

        # is_enum heuristic: TMDL dataCategory == "Category" or boolean type
        is_enum = (
            col_def.get("dataCategory", "").lower() == "category"
            or mapped_type == OSIDataType.BOOLEAN
        )

        # Fabric may emit calculated column expressions as a list of lines.
        # OSIColumn.source_expression expects a string.
        source_expr = col_def.get("sourceColumn") or col_def.get("expression") or ""
        if isinstance(source_expr, list):
            source_expr = "\n".join(str(x) for x in source_expr)
        elif source_expr is None:
            source_expr = ""
        else:
            source_expr = str(source_expr)

        return OSIColumn(
            unique_name=col_name,
            label=col_name,
            data_type=mapped_type,
            description=col_def.get("description"),
            is_hidden=col_def.get("isHidden", False),
            format_string=format_string,
            is_key=is_key,
            source_expression=source_expr,
            synonyms=synonyms,
            is_enum=is_enum,
        )

    @staticmethod
    def _business_rule_type(
        table_name: str,
        col_name: str,
    ) -> Optional[OSIDataType]:
        """Business-rule layer for known Salesforce semantic fields."""
        t = (table_name or "").upper()
        c = (col_name or "").strip().upper()
        c_norm = re.sub(r"[^A-Z0-9]", "", c)

        if c_norm == "FIRMNESSOFFIRSTDELIVERYDATE":
            return OSIDataType.STRING
        if c == "DELETED":
            return OSIDataType.BOOLEAN
        if "MODSTAMP" in c_norm:
            return OSIDataType.DATETIME
        if "VOLUME" in c_norm or "AMOUNT" in c_norm:
            return OSIDataType.FLOAT
        if "DATE" in c_norm and not t.endswith("FIELDHISTORY"):
            return OSIDataType.DATE

        return None

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
        """Parse a TMDL measure into OSIMetric with Cortex AI metadata."""
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
            dax = f"[{name}]"

        # access_modifier heuristic:
        # - Names prefixed with "_" or ending with " Helper" / " Base" â†’ private_access
        # - Hidden measures â†’ private_access
        # - All others â†’ public_access
        is_helper = (
            name.startswith("_")
            or any(name.lower().endswith(suffix) for suffix in (" helper", " base", " temp", " internal"))
            or measure_def.get("isHidden", False)
        )
        access_modifier = "private_access" if is_helper else "public_access"

        # Extract and merge synonyms from TMDL measure definition
        user_synonyms = measure_def.get("synonyms") or []
        if not isinstance(user_synonyms, list):
            user_synonyms = []
            
        auto_synonyms = generate_auto_synonyms(display_name)
        synonyms = merge_synonyms(user_synonyms, auto_synonyms)
        
        if user_synonyms:
            logger.debug("Metric '%s': loaded %d user-defined synonyms from TMDL", name, len(user_synonyms))

        return OSIMetric(
            unique_name=name,
            label=display_name,
            dataset=dataset_name,
            expression=dax,
            aggregation=OSIAggregationType.NONE,  # Raw DAX implies explicit calc
            description=measure_def.get("description"),
            format_string=measure_def.get("formatString"),
            is_hidden=measure_def.get("isHidden", False),
            access_modifier=access_modifier,
            synonyms=synonyms,
        )

    @staticmethod
    def _iter_table_measures(table_def: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return any measure definitions attached to a TMDL table."""
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
                is_project_measures = table_name.strip().casefold() == "project measures"
                if not (is_project_measures or duplicate_name_groups or duplicate_display_groups):
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
                # Sanitize the base name before appending the numeric suffix so
                # special characters (e.g. single quotes from TMDL artifacts) in
                # raw_name don't produce invalid Snowflake identifiers like
                # "COST_THIRD_PARTY_4'_2".  The original name is preserved in
                # displayName for human-readable labels.
                safe_base = re.sub(r"[^A-Za-z0-9_ ]", "", raw_name).strip()
                cloned["name"] = f"{safe_base}_{idx}"
                cloned.setdefault("displayName", raw_name)
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
        """Read the raw DAX expression from a TMDL measure definition."""
        for key in ("expression", "formula", "dax", "value"):
            if key not in measure_def:
                continue
            expr = measure_def.get(key)
            if expr is not None:
                return expr
        return ""

    def _parse_relationship(self, rel_def: Dict[str, Any]) -> Optional[OSIRelationship]:
        """Parse TMDL relationship."""
        try:
             def _parse_endpoint(value: Any) -> tuple[Optional[str], Optional[str]]:
                 text = str(value or "").strip()
                 if not text:
                     return None, None
                 # Match forms like: 'TABLE'[COLUMN], "TABLE"[COLUMN], TABLE[COLUMN]
                 m = re.match(r"^\s*['\"]?([^'\"]+)['\"]?\[([^\]]+)\]\s*$", text)
                 if not m:
                     return None, None
                 table_name = _normalize_rel_identifier(m.group(1))
                 column_name = _normalize_rel_identifier(m.group(2))
                 if not (table_name and column_name):
                     return None, None
                 return table_name, column_name

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

             # Some Fabric/TMDL relationship payloads encode endpoints inside
             # fromColumn/toColumn (e.g., "'FACT'[CUSTOMER_KEY]") and may omit
             # explicit fromTable/toTable keys. Recover table+column from those forms.
             parsed_from_table, parsed_from_col = _parse_endpoint(from_column)
             if parsed_from_col:
                 from_column = parsed_from_col
                 if not from_table and parsed_from_table:
                     from_table = parsed_from_table

             parsed_to_table, parsed_to_col = _parse_endpoint(to_column)
             if parsed_to_col:
                 to_column = parsed_to_col
                 if not to_table and parsed_to_table:
                     to_table = parsed_to_table

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
             
             card_map = {
                 "manytoone": OSICardinality.MANY_TO_ONE,
                 "onetoone": OSICardinality.ONE_TO_ONE,
                 "onetomany": OSICardinality.ONE_TO_MANY,
                 "manytomany": OSICardinality.MANY_TO_MANY
             }
             raw_card = rel_def.get("cardinality", "ManyToOne").lower()
             
             cf_map = {
                 "single": OSICrossFilterDirection.SINGLE,
                 "both": OSICrossFilterDirection.BOTH
             }
             raw_cf = rel_def.get("crossFilteringBehavior", "Single").lower()

             return OSIRelationship(
                 unique_name=name,
                 from_dataset=from_table,
                 from_columns=[from_column],
                 to_dataset=to_table,
                 to_columns=[to_column],
                 cardinality=card_map.get(raw_card, OSICardinality.MANY_TO_ONE),
                 cross_filter_direction=cf_map.get(raw_cf, OSICrossFilterDirection.SINGLE),
                 is_active=rel_def.get("isActive", True)
             )
        except Exception as e:
            logger.warning(f"Failed to parse relationship '{rel_def.get('name')}': {e}")
            return None


class TMSLToOSIConverter(TMDLToOSIConverter):
    """Backward-compatible alias for legacy TMSL naming."""


