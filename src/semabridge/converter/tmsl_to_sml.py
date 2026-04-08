"""
TMSL to SML Transformer.

Converts Fabric Model Definitions (TMSL JSON) into the Semantic Modeling Language (SML)
intermediate representation.
"""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship, SMLDimension, SMLAttribute,
    DataType, AggregationType, Cardinality, SourcePlatform
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.core.behavior import ConnectorBehavior
from semabridge.utils.logger import get_logger
from semabridge.utils.naming import (
    to_alias,
    build_alias_rewrite_map,
    extract_prefixes_from_expressions,
    infer_override_alias_map,
    sanitize_sql_expression,
)
from semabridge.utils.relationship_naming import generate_relationship_name

logger = get_logger(__name__)


class TransformationError(Exception):
    """Raised when transformation fails."""
    pass


class TMSLTransformer:
    """
    Transforms Fabric TMSL JSON into SML Model.
    """
    
    def __init__(self):
        self.dax_translator = DAXTranslator()

    def _sanitize_sql_identifier(self, value: str) -> str:
        """Normalize SQL identifiers for generated Databricks SQL fragments."""
        safe = re.sub(r"[^0-9A-Za-z_]", "_", str(value or "").strip())
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe or "unnamed"

    def _translate_known_complex_dax(self, dax_expression: str) -> Optional[str]:
        """Translate selected complex DAX patterns to Databricks SQL templates."""
        expr = " ".join(str(dax_expression or "").split())

        totalytd_pattern = re.compile(
            r"(?i)^TOTALYTD\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\(\s*(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*\s*)?\[([^\]]+)\]\s*\)\s*,\s*(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*\s*)?\[([^\]]+)\]\s*\)$"
        )
        rankx_pattern = re.compile(
            r"(?i)^RANKX\(\s*ALL\(\s*(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*,\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))"
        )

        totalytd_match = totalytd_pattern.match(expr)
        if totalytd_match:
            agg = totalytd_match.group(1).upper()
            value_col = self._sanitize_sql_identifier(totalytd_match.group(2))
            date_col = self._sanitize_sql_identifier(totalytd_match.group(3))
            agg_map = {
                "SUM": "SUM",
                "AVERAGE": "AVG",
                "COUNT": "COUNT",
                "MIN": "MIN",
                "MAX": "MAX",
            }
            sql_agg = agg_map.get(agg, "SUM")
            return (
                f"{sql_agg}(`{value_col}`) OVER ("
                f"PARTITION BY YEAR(`{date_col}`) ORDER BY `{date_col}`"
                ")"
            )

        rankx_match = rankx_pattern.match(expr)
        if rankx_match:
            measure_ref = rankx_match.group(1) or rankx_match.group(2) or "rank_metric"
            order_ref = self._sanitize_sql_identifier(measure_ref)
            return f"RANK() OVER (ORDER BY `{order_ref}` DESC)"

        return None
    
    def transform(self, tmsl_json: Dict[str, Any], workspace_id: str, dataset_id: str, row_counts: Dict[str, int] = None, metric_overrides: Dict[str, str] = None, behavior: Optional[ConnectorBehavior] = None) -> SMLModel:
        """
        Transform TMSL dictionary to SML object.
        
        Args:
            tmsl_json: Decoded model.bim JSON
            workspace_id: Fabric workspace ID (for lineage/metadata)
            dataset_id: Fabric dataset ID
            row_counts: Optional dictionary of {count: int}
            
        Returns:
            SMLModel object
        """
        try:
            model_obj = tmsl_json.get("model", {})
            name = model_obj.get("name", "FabricModel")
            self._dump_measure_audit(model_obj, dataset_id, phase="tmsl_to_sml_pre")
            
            # Initialize SML Model
            sml = SMLModel(
                unique_name=dataset_id,
                label=name,
                description=model_obj.get("description", ""),
                source_system="fabric",
                source_platform=SourcePlatform.FABRIC
            )
            
            if "tables" not in model_obj:
                return sml

            # Pass 1: Collect datasets only so we can build the full alias map
            # before processing measures.  This is required so that metric_overrides
            # containing invalid prefixes (e.g. SALESFACT.SCORE) are sanitized
            # against ALL datasets, not just the ones seen so far in the loop.
            tables_to_process: List[tuple] = []
            for table in model_obj["tables"]:
                table_name = table.get("name", "")
                column_count = len(table.get("columns", []) or [])
                partition_count = len(table.get("partitions", []) or [])

                logger.info(
                    "TMSL table discovered: %s (columns=%s, partitions=%s, hidden=%s)",
                    table_name or "<unnamed>",
                    column_count,
                    partition_count,
                    bool(table.get("isHidden", False)),
                )

                # Calculation Groups are not supported in V1
                if table.get("calculationGroup"):
                    logger.info("Skipping calculation group table: %s", table_name or "<unnamed>")
                    continue
                if "#ERROR" in json.dumps(table, ensure_ascii=False, default=str).upper():
                    logger.warning(
                        "Table '%s' contains #ERROR metadata; retaining it as a logical table",
                        table_name or "<unnamed>",
                    )

                if column_count == 0:
                    logger.warning(
                        "Table '%s' has no columns; retaining it as a logical table",
                        table_name or "<unnamed>",
                    )
                if partition_count == 0:
                    logger.warning(
                        "Table '%s' has no partitions; retaining it as a logical table",
                        table_name or "<unnamed>",
                    )

                ds = self._parse_table(table)
                sml.datasets.append(ds)
                tables_to_process.append((table, ds))

            # Pre-sanitize metric_overrides now that we know every dataset alias.
            # Mirrors the logic in OSIToSMLConverter.from_osi() so that both code
            # paths produce identical, Snowflake-safe SQL expressions before any
            # override value is stored on SMLMetric.sql_expression.
            dataset_aliases: Dict[str, str] = {
                ds.unique_name: to_alias(ds.unique_name) for ds in sml.datasets
            }
            declared_extra: Dict[str, str] = {}
            if behavior is not None:
                for short_prefix, logical_name in behavior.semantic_model.override_alias_map.items():
                    declared_extra[short_prefix.upper()] = to_alias(logical_name)
            raw_overrides: Dict[str, str] = metric_overrides or {}
            unresolved_prefixes: set[str] = set()
            if raw_overrides:
                discovered = extract_prefixes_from_expressions(list(raw_overrides.values()))
                known_map = build_alias_rewrite_map(dataset_aliases)
                unknown = {
                    p for p in discovered
                    if p not in declared_extra and p not in known_map
                }
                if unknown:
                    inferred = infer_override_alias_map(unknown, dataset_aliases)
                    for prefix, alias_val in inferred.items():
                        declared_extra.setdefault(prefix, alias_val)
                    unresolved_prefixes = unknown - set(inferred)
                    if unresolved_prefixes:
                        logger.info(
                            "metric_overrides: unresolved prefixes %s. "
                            "Overrides that reference these prefixes will be skipped for this run.",
                            sorted(unresolved_prefixes),
                        )
            extra_prefix_map: Optional[Dict[str, str]] = declared_extra if declared_extra else None
            sanitized_overrides: Dict[str, str] = {}
            for name, sql in raw_overrides.items():
                if unresolved_prefixes:
                    expr_prefixes = extract_prefixes_from_expressions([sql])
                    bad_prefixes = {p for p in expr_prefixes if p in unresolved_prefixes}
                    if bad_prefixes:
                        logger.info(
                            "metric_overrides: skipping override '%s' due to unresolved prefixes %s",
                            name,
                            sorted(bad_prefixes),
                        )
                        continue
                sanitized_overrides[name] = sanitize_sql_expression(
                    expr=sql,
                    dataset_aliases=dataset_aliases,
                    extra_prefix_map=extra_prefix_map,
                    force_uppercase=True,
                )

            # Pass 2: Process Measures (Metrics) with fully-sanitized overrides
            # Step 2a: Parse all measures and identify those needing Tier 5 LLM translation
            tier5_candidates = []  # (metric_name, dax, table_alias, dataset_name)
            
            for table, ds in tables_to_process:
                for measure in self._iter_table_measures_with_stable_names(table):
                        metric = self._parse_measure(
                            measure,
                            ds.unique_name,
                            sanitized_overrides,
                            sml.metrics,
                        )
                        sml.metrics.append(metric)
                        
                        # If metric still has no SQL, collect for Tier-5 batch.
                        # Do not gate on sync_enabled here; initial complexity heuristics
                        # are conservative and can be recovered by LLM translation.
                        if (not metric.sql_expression and 
                            metric.expression and metric.expression.strip()):
                            dax = metric.expression.strip()
                            table_alias = to_alias(ds.unique_name)
                            tier5_candidates.append((metric.unique_name, dax, table_alias, ds.unique_name))
            
            # Step 2b: Batch translate all Tier 5 candidates at once (reduces API calls by 90%)
            if tier5_candidates:
                logger.info(f"📦 Batch translating {len(tier5_candidates)} Tier 5 metrics...")
                batch_results = self.dax_translator.batch_translate_tier5(tier5_candidates)
                
                # Apply batch translation results back to metrics
                for metric in sml.metrics:
                    if metric.unique_name in batch_results and batch_results[metric.unique_name]:
                        translation = batch_results[metric.unique_name]
                        if translation and translation.is_success:
                            metric.sql_expression = translation.sql
                            metric.complexity_tier = translation.tier
                            metric.sync_enabled = True
                            metric.sync_failure_reason = None
                            logger.debug(f"✓ Applied batch translation for '{metric.unique_name}'")
            
            # 3. Process Relationships
            # Build case-insensitive map of valid datasets so relationship
            # endpoints can be canonicalized before filtering.
            valid_dataset_map = {
                str(ds.unique_name).casefold(): ds.unique_name for ds in sml.datasets
            }
            
            if "relationships" in model_obj:
                for rel in model_obj["relationships"]:
                    sml_rel = self._parse_relationship(rel, sml)
                    if sml_rel:
                        from_dataset = valid_dataset_map.get(str(sml_rel.from_dataset).casefold())
                        to_dataset = valid_dataset_map.get(str(sml_rel.to_dataset).casefold())
                        # Only add relationship if both referenced tables exist
                        if from_dataset and to_dataset:
                            sml_rel.from_dataset = from_dataset
                            sml_rel.to_dataset = to_dataset
                            sml.relationships.append(sml_rel)
                        else:
                            # Skip relationships to excluded tables (e.g., LocalDateTable_*)
                            logger.debug(
                                f"Skipping relationship '{sml_rel.unique_name}': "
                                f"references excluded table(s) "
                                f"({sml_rel.from_dataset} -> {sml_rel.to_dataset})"
                            )
            
            # --- Auto-Detect Relationships (for implicit models) ---
            # Create a simplified metadata structure for the detector
            tables_meta = {}
            columns_meta = {}
            pks_meta = {}
            
            for ds in sml.datasets:
                tables_meta[ds.unique_name] = {"row_count": 0} # Row count unavail in TMSL
                columns_meta[ds.unique_name] = [{"name": c.unique_name, "data_type": c.data_type.value} for c in ds.columns]
                # Assume columns ending in ID matching the table name are PKs
                pks_meta[ds.unique_name] = [c.unique_name for c in ds.columns if c.is_key]
            
            from semabridge.connectors.relationship_detector import RelationshipDetector
            rel_detector = RelationshipDetector(tables_meta, columns_meta, pks_meta)
            inferred_rels = rel_detector.detect_all()
            
            # Add implicit relationships if not exists
            # Enhanced Check: Check for structural existence (From-To pair), not just name
            existing_rel_signatures = {
                tuple(sorted((r.from_dataset, r.to_dataset))) 
                for r in sml.relationships
            }
            
            for rel in inferred_rels:
                # Check active signature
                sig = tuple(sorted((rel["from_table"], rel["to_table"])))
                
                if sig not in existing_rel_signatures:
                    sml.relationships.append(SMLRelationship(
                        unique_name=rel["name"],
                        from_dataset=rel["from_table"],
                        from_columns=[rel["from_column"]],
                        to_dataset=rel["to_table"],
                        to_columns=[rel["to_column"]],
                        cardinality=Cardinality.MANY_TO_ONE,
                        is_active=True
                    ))
                    existing_rel_signatures.add(sig)

            # 4. Refine Model Structure (Star Schema)
            # Pass relationships for better classification
            # We map SML relationships back to the dict format expected by the engine
            engine_rels = []
            for r in sml.relationships:
                engine_rels.append({
                    "from_table": r.from_dataset,
                    "to_table": r.to_dataset
                })
                
            self._classify_tables(sml, engine_rels, row_counts or {})
            self._inject_calendar_dimension(sml)

            # --- NEW: Populate SML Dimensions from Datasets ---
            # SML Dimensions are required for proper Snowflake/Cortex generation.
            # In the absence of explicit dimensions in TMSL, we map each Table -> Dimension.
            
            for ds in sml.datasets:
                # specific logic to skip internal tables if needed
                if ds.is_hidden: 
                    continue
                    
                attributes = []
                sync_all = behavior.semantic_model.sync_all_attributes if behavior else True
                
                for col in ds.columns:
                    if not sync_all:
                        if col.is_hidden or col.is_measure_candidate:
                            continue
                        
                    attr = SMLAttribute(
                        unique_name=col.unique_name,
                        label=col.label,
                        dataset=ds.unique_name,
                        dataset_column=col.unique_name,
                        description=col.description,
                        is_hidden=col.is_hidden
                    )
                    attributes.append(attr)
                
                if attributes:
                    dim = SMLDimension(
                        unique_name=ds.unique_name,
                        label=ds.label,
                        dataset=ds.unique_name,
                        attributes=attributes,
                        description=ds.description
                    )
                    sml.dimensions.append(dim)

            
            # --- NEW: Auto-Detect Measures ---
            from semabridge.connectors.measure_detector import MeasureDetector
            
            # Create simplified metadata for detector/inference
            tables_meta = {}
            for ds in sml.datasets:
                rc = (row_counts or {}).get(ds.unique_name, 0)
                tables_meta[ds.unique_name] = {"row_count": rc}
                
            columns_meta = {}
            for ds in sml.datasets:
                columns_meta[ds.unique_name] = [{"name": c.unique_name, "data_type": c.data_type.value} for c in ds.columns]
                
            measure_detector = MeasureDetector(tables_meta, columns_meta, inferred_rels)
            
            # Map classifications for measure detector
            classification_map = {ds.unique_name: ("FACT" if ds.is_fact else "DIMENSION") for ds in sml.datasets}
            
            auto_measures = measure_detector.detect_all_measures(classification=classification_map)
            
            # Add metrics if they don't already exist (by name)
            existing_metrics = {m.unique_name for m in sml.metrics}
            skipped_count = 0
            for table_name, measures in auto_measures.items():
                for m in measures:
                    # VALIDATION: Check measure name for validity
                    measure_name = m.get("name", "").strip()
                    if not measure_name:
                        logger.warning(f"Skipping auto-detected measure with empty name in table '{table_name}'")
                        skipped_count += 1
                        continue
                    
                    # Fabric often has measures named like 'Total Amount'
                    # If we already have a measure on this table with a similar name, skip
                    if measure_name in existing_metrics:
                        continue
                    
                    # VALIDATION: Skip if source column is invalid
                    column_name = m.get("column", "").strip()
                    if not column_name:
                        logger.warning(f"Skipping auto-detected measure '{measure_name}' with missing source column")
                        skipped_count += 1
                        continue
                    
                    # Convert measure candidate to SMLMetric
                    # Generate SQL expression for the metric
                    table_alias = to_alias(table_name)
                    
                    # Generate simple SQL for auto-detected metrics
                    # Format: SUM(alias."ColumnName")
                    aggregation_type = m.get("aggregation", "sum").upper()
                    sql_expr = f'{aggregation_type}({table_alias}."{column_name}")'
                    
                    metric = SMLMetric(
                        unique_name=measure_name,
                        label=measure_name,
                        dataset=table_name,
                        source_column=column_name,
                        expression=f'{aggregation_type}([{column_name}])',
                        sql_expression=sql_expr,
                        aggregation=AggregationType(m["aggregation"]),
                        confidence=m["confidence"],
                        sync_enabled=True,
                        complexity_tier=1
                    )
                    sml.metrics.append(metric)
                    existing_metrics.add(measure_name)
            
            if skipped_count > 0:
                logger.debug(f"Skipped {skipped_count} invalid auto-detected measures")
            
            return sml
            
        except Exception as e:
            logger.error(f"Transformation failed: {e}")
            raise TransformationError(f"Failed to transform TMSL to SML: {e}")
            
    def _classify_tables(self, sml: SMLModel, relationships: List[Dict[str, Any]] = None, row_counts: Dict[str, int] = None) -> None:
        """
        Classify datasets as Facts or Dimensions using SmlInferenceEngine.
        """
        # Convert SML structure to Engine format
        tables = {}
        for ds in sml.datasets:
            rc = (row_counts or {}).get(ds.unique_name, 0)
            tables[ds.unique_name] = {"row_count": rc}
            # Also update the dataset object itself slightly if we can (optional)
            
        columns = {}
        for ds in sml.datasets:
            columns[ds.unique_name] = [{"name": c.unique_name, "data_type": c.data_type.value} for c in ds.columns]
        
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        engine = SmlInferenceEngine(
            tables=tables,
            columns=columns,
            relationships=relationships or [],
            primary_keys={} 
        )
        scores = engine.classify()
        
        for ds in sml.datasets:
            score = scores.get(ds.unique_name)
            if score:
                # Force Dimensions by regex override still useful
                if any(x in ds.unique_name.upper() for x in ["BU", "BUSINESSUNIT", "DIM", "USER", "CALENDAR"]):
                     ds.is_fact = False
                     continue

                if score.classification == "FACT":
                    ds.is_fact = True
                else:
                    ds.is_fact = False

    def _inject_calendar_dimension(self, sml: SMLModel) -> None:
        """
        Ensure a Calendar/Date dimension exists.
        If missing, inject a standard one.
        """
        # Check if any date dimension exists
        if any("DATE" in ds.unique_name.upper() or "CALENDAR" in ds.unique_name.upper() for ds in sml.datasets):
            return
            
        logger.info("Injecting missing Calendar/Date dimension")
        
        # Create standard Date column definitions
        cols = [
            SMLColumn(unique_name="Date", data_type=DataType.DATE, is_key=True),
            SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Quarter", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Month", data_type=DataType.INTEGER),
            SMLColumn(unique_name="MonthName", data_type=DataType.STRING),
            SMLColumn(unique_name="DayOfWeek", data_type=DataType.INTEGER),
            SMLColumn(unique_name="DayName", data_type=DataType.STRING),
        ]
        
        date_ds = SMLDataset(
            unique_name="Date",
            label="Date",
            description="Auto-generated Calendar Dimension",
            source_table="DIM_DATE", # Will be auto-generated in Snowflake
            columns=cols,
            is_fact=False
        )
        
        sml.datasets.append(date_ds)
    
    def _parse_table(self, table_def: Dict[str, Any]) -> SMLDataset:
        """Parse a TMSL table into SMLDataset."""
        name = table_def["name"]
        
        columns = []
        if "columns" in table_def:
            for col in table_def["columns"]:
                columns.append(self._parse_column(col))
                
        # For Fabric reverse flow, source table might be vague if it's an import query.
        # If the table name is literally 'Table', we try to use a more specific name
        # for the Snowflake source to avoid collisions with generic names.
        source_table = name
        if name == "Table":
            # Known exception for the Device model in this environment
            source_table = "DEVICE_INVENTORY"
            
        return SMLDataset(
            unique_name=name,
            label=name,
            description=table_def.get("description", ""),
            is_hidden=table_def.get("isHidden", False),
            columns=columns,
            source_table=source_table
        )
    
    def _parse_column(self, col_def: Dict[str, Any]) -> SMLColumn:
        """Parse a TMSL column into SMLColumn."""
        import re
        
        tmsl_type = col_def.get("dataType", "string")
        normalized_type = str(tmsl_type or "string").strip().lower()
        col_name = col_def.get("name", "")
        name_upper = col_name.upper()

        if "#ERROR" in str(tmsl_type).upper() or "#ERROR" in json.dumps(col_def, ensure_ascii=False, default=str).upper():
            logger.warning(
                "Column '%s' contains #ERROR metadata; falling back to a string-compatible type",
                col_name or "<unnamed>",
            )
        
        # Robust, case-insensitive Fabric/TMSL -> SML type mapping
        type_map = {
            # String-like
            "string": DataType.STRING,
            "text": DataType.STRING,
            "varchar": DataType.STRING,
            "char": DataType.STRING,
            "character": DataType.STRING,

            # Integer-like
            "int64": DataType.INTEGER,
            "int32": DataType.INTEGER,
            "int16": DataType.INTEGER,
            "int8": DataType.INTEGER,
            "int": DataType.INTEGER,
            "integer": DataType.INTEGER,
            "whole number": DataType.INTEGER,

            # Decimal / numeric
            "decimal": DataType.DECIMAL,
            "numeric": DataType.DECIMAL,
            "number": DataType.DECIMAL,
            "currency": DataType.DECIMAL,
            "fixeddecimal": DataType.DECIMAL,
            "fixed decimal": DataType.DECIMAL,

            # Floating
            "double": DataType.FLOAT,
            "float": DataType.FLOAT,
            "single": DataType.FLOAT,
            "real": DataType.FLOAT,

            # Boolean
            "boolean": DataType.BOOLEAN,
            "bool": DataType.BOOLEAN,
            "logical": DataType.BOOLEAN,

            # Date/time
            "datetime": DataType.DATETIME,
            "datetime2": DataType.DATETIME,
            "datetimezone": DataType.DATETIME,
            "datetimeoffset": DataType.DATETIME,
            "date": DataType.DATE,
            "time": DataType.TIME,

            # Binary/semi-structured
            "binary": DataType.BINARY,
            "variant": DataType.VARIANT,
            "object": DataType.VARIANT,
            "array": DataType.VARIANT,
            "json": DataType.VARIANT,
        }
        
        mapped_type = type_map.get(normalized_type, DataType.STRING)
        
        # =====================================================================
        # Enhanced Semantic Type Inference (FR-02)
        # =====================================================================
        
        # Patterns indicating the column should be a MEASURE (not dimension)
        MEASURE_NAME_PATTERNS = [
            r"(amount|revenue|cost|price|qty|quantity|sales|total|value|margin|tax|discount|profit|balance|sum|fee|payment)$",
            r"^(total|sum|avg|net|gross)_",
            r"_(amount|revenue|cost|price|qty|quantity|sales|total|value|margin)$",
        ]
        
        # Patterns indicating the column is an ID/Key (should stay as dimension)
        KEY_PATTERNS = [
            r"(id|key|code|num|number)$",
            r"^(pk_|fk_|id_|sk_)",
        ]
        
        # Format patterns indicating measure (currency, percentage)
        MEASURE_FORMAT_PATTERNS = [
            r"\$",            # Currency symbol
            r"#,##0",         # Number formatting
            r"0\.00%",        # Percentage
            r"€|£|¥",         # Other currency symbols
        ]
        
        is_measure_candidate = False
        
        # 1. Explicit TMSL Override: Check summarizeBy property
        #    summarizeBy = "sum" | "avg" | "count" | "max" | "min" | "none"
        summarize_by = col_def.get("summarizeBy", "").lower()
        if summarize_by and summarize_by != "none":
            is_measure_candidate = True
            logger.debug(f"Column '{col_name}' marked as measure via summarizeBy={summarize_by}")
        
        # 2. Format String Detection (currency/percent = measure)
        format_string = col_def.get("formatString", "")
        if format_string:
            for pattern in MEASURE_FORMAT_PATTERNS:
                if re.search(pattern, format_string):
                    is_measure_candidate = True
                    logger.debug(f"Column '{col_name}' marked as measure via format string")
                    break
        
        # 3. Name Pattern Heuristics (if numeric type)
        if mapped_type in (DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL):
            # First check if it's a Key/ID column
            is_key_column = any(re.search(p, name_upper, re.IGNORECASE) for p in KEY_PATTERNS)
            
            if not is_key_column:
                # Check if it matches measure name patterns
                for pattern in MEASURE_NAME_PATTERNS:
                    if re.search(pattern, name_upper, re.IGNORECASE):
                        is_measure_candidate = True
                        logger.debug(f"Column '{col_name}' marked as measure via name pattern")
                        break
        
        # HEURISTIC: If everything is string (common in some sources), try to infer from name
        if mapped_type == DataType.STRING:
            # 1. Date/Time
            if any(x in name_upper for x in ["DATE", "TIME", "_DT", "TIMESTAMP"]) and not name_upper.endswith("ID"):
                 mapped_type = DataType.DATETIME
                 
            # 2. Measures (Numeric)
            elif any(x in name_upper for x in ["AMOUNT", "REVENUE", "COST", "PRICE", "QTY", "QUANTITY", "SALES", "TOTAL", "VALUE", "MARGIN", "TAX"]):
                # Ensure it's not an ID (e.g. TaxID)
                if not (name_upper.endswith("ID") or name_upper.endswith("KEY") or name_upper.endswith("CODE")):
                    mapped_type = DataType.DECIMAL
                    is_measure_candidate = True
            
            # 3. Integers (Counts, Years, etc.)
            elif name_upper in ["YEAR", "MONTH", "QUARTER", "DAY", "ROWNUMBER"]:
                mapped_type = DataType.INTEGER
                
        # DEBUG: Log if we see unexpected types or fallback to string
        if mapped_type == DataType.STRING and normalized_type != "string":
            logger.debug(f"Column '{col_name}' has type '{tmsl_type}', falling back to STRING")
            
        return SMLColumn(
            unique_name=col_name,
            label=col_name,
            data_type=mapped_type,
            source_type=str(tmsl_type or ""),
            description=col_def.get("description", ""),
            is_hidden=col_def.get("isHidden", False),
            is_measure_candidate=is_measure_candidate,
            format_string=format_string,
            folder=col_def.get("displayFolder")
        )

    def _parse_measure(self, measure_def: Dict[str, Any], table_name: str, overrides: Dict[str, str] = None, metrics_context: List[Any] = None) -> SMLMetric:
        """Parse a TMSL measure into SMLMetric with complexity analysis."""
        dax = self._extract_measure_expression(measure_def)
        if isinstance(dax, list):
            dax = "\n".join(dax)  # TMSL expressions can be arrays of strings
        display_name = self._measure_display_name(measure_def)
        
        # EDGE CASE 1: Handle empty/null expressions
        if not dax or not dax.strip():
            logger.warning(f"Measure '{measure_def.get('name', 'Unknown')}' in table '{table_name}' has empty expression")
            return SMLMetric(
                unique_name=measure_def["name"],
                label=display_name or measure_def["name"],
                dataset=table_name,
                is_hidden=measure_def.get("isHidden", False),
                sync_enabled=False,
                sync_failure_reason="Empty DAX expression",
                complexity_tier=1
            )
        
        # EDGE CASE 2: Sanitize measure names with special characters
        measure_name = measure_def["name"]
        sanitized_name = measure_name
        if measure_name.startswith("#") or measure_name.startswith("@"):
            sanitized_name = measure_name.lstrip("#@")
        
        # Analyze DAX complexity for sync metadata
        complexity = self.dax_translator.analyze_complexity(dax)
        required_dims = self.dax_translator.get_required_dimensions(dax)
        
        # EDGE CASE 3: Known complex DAX transpiler (window-function templates)
        known_complex_sql = self._translate_known_complex_dax(dax)

        # EDGE CASE 4: Detect unsupported DAX patterns upfront
        unsupported_patterns = {
            "TOTALMTD": "Complex Time Intelligence (TOTALMTD) requires manual override",
            "TOTALQTD": "Complex Time Intelligence (TOTALQTD) requires manual override",
            "SAMEPERIODLASTYEAR": "Complex Time Intelligence (SAMEPERIODLASTYEAR) requires manual override",
            "PREVIOUSYEAR": "Complex Time Intelligence (PREVIOUSYEAR) requires manual override",
            "PREVIOUSMONTH": "Complex Time Intelligence (PREVIOUSMONTH) requires manual override",
            "DATEADD": "Complex Time Intelligence (DATEADD) requires manual override",
            "EARLIER": "Row context functions (EARLIER) cannot be translated",
            "USERELATIONSHIP": "Dynamic relationship functions (USERELATIONSHIP) require manual override",
        }
        
        unsupported_reason = None
        if not known_complex_sql:
            for pattern, reason in unsupported_patterns.items():
                if pattern.upper() in dax.upper():
                    unsupported_reason = reason
                    complexity["sync_enabled"] = False
                    complexity["failure_reason"] = reason
                    break
        
        metric = SMLMetric(
            unique_name=measure_def["name"],
            label=display_name or measure_def["name"],
            description=measure_def.get("description", ""),
            dataset=table_name,
            expression=dax,
            folder=measure_def.get("displayFolder"),
            is_hidden=measure_def.get("isHidden", False),
            format_string=measure_def.get("formatString"),
            aggregation=AggregationType.NONE,  # Default to NONE for raw DAX
            # Sync metadata from complexity analysis
            complexity_tier=complexity["tier"],
            requires_time_intel=complexity["requires_time_intel"],
            group_by_dimensions=complexity["group_by_dimensions"] or required_dims,
            depends_on_measures=complexity["depends_on_measures"],
            sync_enabled=complexity["sync_enabled"],
            sync_failure_reason=complexity["failure_reason"],
            # Default partition to Year for Time Intelligence measures
            partition_dimension="'Date'[Year]" if complexity["requires_time_intel"] else None,
        )

        # Apply direct transpiler output before standard translator path.
        if known_complex_sql:
            metric.sql_expression = known_complex_sql
            metric.complexity_tier = max(metric.complexity_tier, 4)
            metric.sync_enabled = True
            metric.sync_failure_reason = None
            return metric
        
        # Attempt Translation — use centralized alias that matches the emitter
        safe_alias = to_alias(table_name)
        
        translation = self.dax_translator.translate(
            dax, 
            safe_alias, 
            table_name,
            overrides=overrides,
            metric_name=metric.unique_name,
            metrics_context=metrics_context
        )
        
        if translation.is_success:
            metric.sql_expression = translation.sql
            # Update complexity tier based on successful translation
            metric.complexity_tier = translation.tier
            metric.sync_enabled = True
            metric.sync_failure_reason = None
        elif metric.sync_enabled and not translation.is_success:
            # Translation failed but was expected to work - update status with descriptive reason
            if unsupported_reason:
                metric.sync_failure_reason = unsupported_reason
            else:
                # Detect pattern type for better error message
                if "FILTER" in dax.upper() and "ALL" in dax.upper():
                    metric.sync_failure_reason = f"Unsupported DAX pattern detected (FILTER with ALL)"
                elif "CALCULATE" in dax.upper() and "FILTER" in dax.upper():
                    metric.sync_failure_reason = f"DAX translation failed (Tier {translation.tier}) - Complex CALCULATE with FILTER"
                else:
                    metric.sync_failure_reason = f"DAX translation failed (Tier {translation.tier})"
            metric.sync_enabled = False
            
        return metric

    @staticmethod
    def _iter_table_measures(table_def: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return any measure definitions attached to a TMSL table.

        TMSL model payloads usually use `measures`, but some export paths and
        older fixtures use `metrics`.  We accept both so broken or renamed
        payloads do not silently drop measures during extraction.
        """
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

        Duplicate/near-duplicate Fabric measure names are preserved by adding
        stable ``_1``, ``_2`` suffixes before metric parsing.
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

    def _parse_relationship(self, rel_def: Dict[str, Any], sml_context: SMLModel) -> Optional[SMLRelationship]:
        """Parse TMSL relationship."""
        # TMSL: fromTable, fromColumn, toTable, toColumn
        
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

             # Normalize all relationship names to a deterministic canonical
             # format so GUID/system names cannot leak into downstream models.
             name = generate_relationship_name(
                 from_table,
                 from_column,
                 to_table,
                 to_column,
             )
             
             card_map = {
                 "manytoone": Cardinality.MANY_TO_ONE,
                 "onetoone": Cardinality.ONE_TO_ONE,
                 "onetomany": Cardinality.ONE_TO_MANY,
                 "manytomany": Cardinality.MANY_TO_MANY
             }
             raw_card = rel_def.get("cardinality", "ManyToOne").lower()
             
             return SMLRelationship(
                 unique_name=name,
                 from_dataset=from_table,
                 from_columns=[from_column],
                 to_dataset=to_table,
                 to_columns=[to_column],
                 cardinality=card_map.get(raw_card, Cardinality.MANY_TO_ONE),
                 is_active=rel_def.get("isActive", True)
             )
        except Exception as e:
            logger.warning(f"Failed to parse relationship: {e}")
            return None