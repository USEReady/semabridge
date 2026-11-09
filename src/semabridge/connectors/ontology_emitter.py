"""Snowflake Ontology & Knowledge-Graph Emitter.

Compiles OSI models into:
1. Relational Knowledge-Graph tables on Snowflake:
   - _SEMABRIDGE_ONTOLOGY.ONTOLOGY_CONCEPTS
   - _SEMABRIDGE_ONTOLOGY.ONTOLOGY_RELATIONSHIPS
   - _SEMABRIDGE_ONTOLOGY.ONTOLOGY_METRIC_BINDINGS
2. Snowflake Cortex Search Services for semantic concept and metric search:
   - <MODEL>_CONCEPT_SEARCH
   - <MODEL>_METRIC_SEARCH

This bridges existing BI models into an active enterprise ontology layer,
providing Cortex Agents and external MCP agents with grounded reasoning
and zero-hallucination semantic search.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel, OSIRelationship

logger = logging.getLogger("semabridge.connectors.ontology_emitter")


def _escape_sql_str(val: str | None) -> str:
    if val is None:
        return "NULL"
    from semabridge.connectors.synonym_clause import escape_sql_string_literal

    return f"'{escape_sql_string_literal(val)}'"


def _format_sql_array(items: List[str] | None) -> str:
    if not items:
        return "ARRAY_CONSTRUCT()"
    elements = ", ".join(_escape_sql_str(item) for item in items if item and item.strip())
    return f"ARRAY_CONSTRUCT({elements})" if elements else "ARRAY_CONSTRUCT()"


def _sanitize_identifier(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return sanitized.upper()


class OntologyEmitter:
    """Emits Snowflake DDL for Knowledge-Graph metadata tables and Cortex Search Services."""

    def __init__(
        self,
        target_database: str = "PROD_DWH",
        target_schema: str = "ANALYTICS",
        ontology_schema: str = "_SEMABRIDGE_ONTOLOGY",
        warehouse: str = "COMPUTE_WH",
        target_lag: str = "1 hour",
    ):
        self.target_database = _sanitize_identifier(target_database)
        self.target_schema = _sanitize_identifier(target_schema)
        self.ontology_schema = _sanitize_identifier(ontology_schema)
        self.warehouse = _sanitize_identifier(warehouse)
        self.target_lag = target_lag

    def emit_ontology_ddl(
        self,
        model: OSIModel,
        ontology_overrides: Optional[Dict[tuple[str, str, str], Dict[str, Any]]] = None,
    ) -> str:
        """Generate complete SQL script creating ontology tables, inserting concepts, and building Cortex Search."""
        overrides = ontology_overrides or {}
        model_name = _sanitize_identifier(model.unique_name or "SEMANTIC_MODEL")
        # Override keys are casefolded (model, table, element) names as stored by
        # the synonyms API; accept the model's raw name as well as the sanitized one.
        overrides = {
            **overrides,
            **{
                (model_name.casefold(), k[1], k[2]): v
                for k, v in overrides.items()
                if k[0] == str(model.unique_name or "").casefold()
            },
        }
        lines: List[str] = []

        # 1. Header & Schema Setup
        lines.append(f"-- =============================================================================")
        lines.append(f"-- SemaBridge Enterprise Ontology Layer for: {model_name}")
        lines.append(f"-- Database: {self.target_database} | Schema: {self.ontology_schema}")
        lines.append(f"-- =============================================================================")
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {self.target_database}.{self.ontology_schema};")
        lines.append("")

        # 2. Table Definitions
        lines.append(f"-- 1. Concepts Table (Entities, Business Terms, Dimensions & Attributes)")
        lines.append(f"""CREATE TABLE IF NOT EXISTS {self.target_database}.{self.ontology_schema}.ONTOLOGY_CONCEPTS (
    CONCEPT_ID VARCHAR(255) PRIMARY KEY,
    CONCEPT_NAME VARCHAR(255) NOT NULL,
    CONCEPT_TYPE VARCHAR(50) NOT NULL, -- 'ENTITY', 'DIMENSION', 'ATTRIBUTE'
    DOMAIN VARCHAR(100),
    DESCRIPTION TEXT,
    SYNONYMS ARRAY,
    BUSINESS_RULES ARRAY,
    FILTER_EXPRESSION TEXT,
    DATA_STEWARD VARCHAR(255),
    SOURCE_DATASET VARCHAR(255),
    PHYSICAL_TABLE VARCHAR(255),
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);""")
        lines.append("")

        lines.append(f"-- 2. Relationships Table (Concept Links, Joins & Metric Lineage)")
        lines.append(f"""CREATE TABLE IF NOT EXISTS {self.target_database}.{self.ontology_schema}.ONTOLOGY_RELATIONSHIPS (
    RELATIONSHIP_ID VARCHAR(255) PRIMARY KEY,
    FROM_CONCEPT VARCHAR(255) NOT NULL,
    TO_CONCEPT VARCHAR(255) NOT NULL,
    RELATIONSHIP_TYPE VARCHAR(50) DEFAULT 'JOIN', -- 'JOIN', 'DERIVES_FROM', 'IS_A', 'APPLIES_TO', 'GOVERNED_BY'
    CARDINALITY VARCHAR(50),
    JOIN_EXPRESSION TEXT,
    PREDICATE_LABEL VARCHAR(255),
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);""")
        lines.append("")

        lines.append(f"-- 3. Metric Bindings Table (Calculations, Lineage, Policies & Ownership)")
        lines.append(f"""CREATE TABLE IF NOT EXISTS {self.target_database}.{self.ontology_schema}.ONTOLOGY_METRIC_BINDINGS (
    METRIC_NAME VARCHAR(255) PRIMARY KEY,
    CONCEPT_ID VARCHAR(255) NOT NULL,
    DESCRIPTION TEXT,
    CALCULATION_EXPRESSION TEXT,
    AGGREGATION_TYPE VARCHAR(50),
    SYNONYMS ARRAY,
    BUSINESS_RULES ARRAY,
    FILTER_EXPRESSION TEXT,
    DERIVES_FROM ARRAY,
    DATA_STEWARD VARCHAR(255),
    SEMANTIC_VIEW_NAME VARCHAR(255),
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);""")
        lines.append("")

        # 3. Insert / Merge Concepts (Datasets & Attributes)
        lines.append(f"-- 4. Populate / Refresh Concepts (Entities & Attributes)")
        for ds in model.datasets:
            ds_name = ds.unique_name
            ds_key = (model_name.casefold(), ds_name.casefold(), ds_name.casefold())
            ds_override = overrides.get(ds_key, {})

            desc = ds_override.get("description") or ds.description or f"Business entity {ds.label or ds_name}"
            steward = ds_override.get("data_steward")
            syns = list(ds_override.get("synonyms") or getattr(ds, "synonyms", None) or [])
            rules = ds_override.get("business_rules") or []
            filter_expr = ds_override.get("filter_expression") or getattr(ds, "filter_expression", None)
            phys_table = ds.source_table or ds_name

            lines.append(f"""MERGE INTO {self.target_database}.{self.ontology_schema}.ONTOLOGY_CONCEPTS AS target
USING (SELECT 
    {_escape_sql_str(ds_name)} AS CONCEPT_ID,
    {_escape_sql_str(ds.label or ds_name)} AS CONCEPT_NAME,
    'ENTITY' AS CONCEPT_TYPE,
    {_escape_sql_str(model_name)} AS DOMAIN,
    {_escape_sql_str(desc)} AS DESCRIPTION,
    {_format_sql_array(syns)} AS SYNONYMS,
    {_format_sql_array(rules)} AS BUSINESS_RULES,
    {_escape_sql_str(filter_expr)} AS FILTER_EXPRESSION,
    {_escape_sql_str(steward)} AS DATA_STEWARD,
    {_escape_sql_str(ds_name)} AS SOURCE_DATASET,
    {_escape_sql_str(phys_table)} AS PHYSICAL_TABLE
) AS src
ON target.CONCEPT_ID = src.CONCEPT_ID
WHEN MATCHED THEN UPDATE SET
    CONCEPT_NAME = src.CONCEPT_NAME,
    DESCRIPTION = src.DESCRIPTION,
    SYNONYMS = src.SYNONYMS,
    BUSINESS_RULES = src.BUSINESS_RULES,
    FILTER_EXPRESSION = src.FILTER_EXPRESSION,
    DATA_STEWARD = src.DATA_STEWARD,
    PHYSICAL_TABLE = src.PHYSICAL_TABLE,
    UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    CONCEPT_ID, CONCEPT_NAME, CONCEPT_TYPE, DOMAIN, DESCRIPTION, 
    SYNONYMS, BUSINESS_RULES, FILTER_EXPRESSION, DATA_STEWARD, SOURCE_DATASET, PHYSICAL_TABLE, UPDATED_AT
) VALUES (
    src.CONCEPT_ID, src.CONCEPT_NAME, src.CONCEPT_TYPE, src.DOMAIN, src.DESCRIPTION,
    src.SYNONYMS, src.BUSINESS_RULES, src.FILTER_EXPRESSION, src.DATA_STEWARD, src.SOURCE_DATASET, src.PHYSICAL_TABLE, CURRENT_TIMESTAMP()
);""")

            # Emit ATTRIBUTE rows for each column in the dataset
            for col in getattr(ds, "columns", None) or []:
                col_name = col.unique_name
                col_id = f"{ds_name}.{col_name}"
                col_key = (model_name.casefold(), ds_name.casefold(), col_name.casefold())
                col_override = overrides.get(col_key, {})

                col_desc = (
                    col_override.get("description")
                    or col.description
                    or f"Attribute {col.label or col_name} of {ds.label or ds_name}"
                )
                col_steward = col_override.get("data_steward") or steward
                col_syns = list(
                    dict.fromkeys(
                        list(col_override.get("synonyms") or [])
                        + list(getattr(col, "synonyms", None) or [])
                    )
                )
                col_rules = col_override.get("business_rules") or []
                col_phys = f"{phys_table}.{col.source_column or col_name}"

                lines.append(f"""MERGE INTO {self.target_database}.{self.ontology_schema}.ONTOLOGY_CONCEPTS AS target
USING (SELECT 
    {_escape_sql_str(col_id)} AS CONCEPT_ID,
    {_escape_sql_str(col.label or col_name)} AS CONCEPT_NAME,
    'ATTRIBUTE' AS CONCEPT_TYPE,
    {_escape_sql_str(model_name)} AS DOMAIN,
    {_escape_sql_str(col_desc)} AS DESCRIPTION,
    {_format_sql_array(col_syns)} AS SYNONYMS,
    {_format_sql_array(col_rules)} AS BUSINESS_RULES,
    NULL AS FILTER_EXPRESSION,
    {_escape_sql_str(col_steward)} AS DATA_STEWARD,
    {_escape_sql_str(ds_name)} AS SOURCE_DATASET,
    {_escape_sql_str(col_phys)} AS PHYSICAL_TABLE
) AS src
ON target.CONCEPT_ID = src.CONCEPT_ID
WHEN MATCHED THEN UPDATE SET
    CONCEPT_NAME = src.CONCEPT_NAME,
    DESCRIPTION = src.DESCRIPTION,
    SYNONYMS = src.SYNONYMS,
    BUSINESS_RULES = src.BUSINESS_RULES,
    DATA_STEWARD = src.DATA_STEWARD,
    PHYSICAL_TABLE = src.PHYSICAL_TABLE,
    UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    CONCEPT_ID, CONCEPT_NAME, CONCEPT_TYPE, DOMAIN, DESCRIPTION, 
    SYNONYMS, BUSINESS_RULES, FILTER_EXPRESSION, DATA_STEWARD, SOURCE_DATASET, PHYSICAL_TABLE, UPDATED_AT
) VALUES (
    src.CONCEPT_ID, src.CONCEPT_NAME, src.CONCEPT_TYPE, src.DOMAIN, src.DESCRIPTION,
    src.SYNONYMS, src.BUSINESS_RULES, src.FILTER_EXPRESSION, src.DATA_STEWARD, src.SOURCE_DATASET, src.PHYSICAL_TABLE, CURRENT_TIMESTAMP()
);""")

        lines.append("")

        # 4. Insert / Merge Relationships
        lines.append(f"-- 5. Populate / Refresh Relationships")
        for rel in model.relationships:
            rel_id = rel.unique_name
            from_c = rel.from_dataset
            to_c = rel.to_dataset
            card = rel.cardinality.value if hasattr(rel.cardinality, "value") else str(rel.cardinality)
            from_cols = ", ".join(rel.from_columns)
            to_cols = ", ".join(rel.to_columns)
            join_expr = f"{from_c}.({from_cols}) = {to_c}.({to_cols})"
            predicate = f"{from_c} related to {to_c}"

            lines.append(f"""MERGE INTO {self.target_database}.{self.ontology_schema}.ONTOLOGY_RELATIONSHIPS AS target
USING (SELECT 
    {_escape_sql_str(rel_id)} AS RELATIONSHIP_ID,
    {_escape_sql_str(from_c)} AS FROM_CONCEPT,
    {_escape_sql_str(to_c)} AS TO_CONCEPT,
    'JOIN' AS RELATIONSHIP_TYPE,
    {_escape_sql_str(card)} AS CARDINALITY,
    {_escape_sql_str(join_expr)} AS JOIN_EXPRESSION,
    {_escape_sql_str(predicate)} AS PREDICATE_LABEL
) AS src
ON target.RELATIONSHIP_ID = src.RELATIONSHIP_ID
WHEN MATCHED THEN UPDATE SET
    RELATIONSHIP_TYPE = src.RELATIONSHIP_TYPE,
    CARDINALITY = src.CARDINALITY,
    JOIN_EXPRESSION = src.JOIN_EXPRESSION,
    PREDICATE_LABEL = src.PREDICATE_LABEL,
    UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    RELATIONSHIP_ID, FROM_CONCEPT, TO_CONCEPT, RELATIONSHIP_TYPE, CARDINALITY, JOIN_EXPRESSION, PREDICATE_LABEL, UPDATED_AT
) VALUES (
    src.RELATIONSHIP_ID, src.FROM_CONCEPT, src.TO_CONCEPT, src.RELATIONSHIP_TYPE, src.CARDINALITY, src.JOIN_EXPRESSION, src.PREDICATE_LABEL, CURRENT_TIMESTAMP()
);""")

        # Emit metric-to-metric DERIVES_FROM lineage relationships if present
        for m in model.metrics:
            m_name = m.unique_name
            m_key = (model_name.casefold(), m.dataset.casefold(), m_name.casefold())
            m_override = overrides.get(m_key, {})
            derives = list(
                dict.fromkeys(
                    list(m_override.get("derives_from") or [])
                    + list(getattr(m, "derives_from", None) or [])
                )
            )
            for dep in derives:
                dep_rel_id = f"DERIVES_{_sanitize_identifier(m_name)}__{_sanitize_identifier(dep)}"
                predicate = f"{m_name} derives from {dep}"
                lines.append(f"""MERGE INTO {self.target_database}.{self.ontology_schema}.ONTOLOGY_RELATIONSHIPS AS target
USING (SELECT 
    {_escape_sql_str(dep_rel_id)} AS RELATIONSHIP_ID,
    {_escape_sql_str(m_name)} AS FROM_CONCEPT,
    {_escape_sql_str(dep)} AS TO_CONCEPT,
    'DERIVES_FROM' AS RELATIONSHIP_TYPE,
    'N:1' AS CARDINALITY,
    NULL AS JOIN_EXPRESSION,
    {_escape_sql_str(predicate)} AS PREDICATE_LABEL
) AS src
ON target.RELATIONSHIP_ID = src.RELATIONSHIP_ID
WHEN MATCHED THEN UPDATE SET
    RELATIONSHIP_TYPE = src.RELATIONSHIP_TYPE,
    PREDICATE_LABEL = src.PREDICATE_LABEL,
    UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    RELATIONSHIP_ID, FROM_CONCEPT, TO_CONCEPT, RELATIONSHIP_TYPE, CARDINALITY, JOIN_EXPRESSION, PREDICATE_LABEL, UPDATED_AT
) VALUES (
    src.RELATIONSHIP_ID, src.FROM_CONCEPT, src.TO_CONCEPT, src.RELATIONSHIP_TYPE, src.CARDINALITY, src.JOIN_EXPRESSION, src.PREDICATE_LABEL, CURRENT_TIMESTAMP()
);""")

        lines.append("")

        # 5. Insert / Merge Metrics
        lines.append(f"-- 6. Populate / Refresh Metric Bindings")
        sem_view = f"{self.target_database}.{self.target_schema}.{model_name}_SEMANTIC"
        for m in model.metrics:
            m_name = m.unique_name
            m_key = (model_name.casefold(), m.dataset.casefold(), m_name.casefold())
            m_override = overrides.get(m_key, {})

            desc = m_override.get("description") or m.description or f"Metric {m.label or m_name}"
            steward = m_override.get("data_steward") or m.business_owner
            syns = list(dict.fromkeys(list(m_override.get("synonyms") or []) + list(m.synonyms or [])))
            rules = m_override.get("business_rules") or []
            filter_expr = m_override.get("filter_expression") or getattr(m, "filter_expression", None)
            derives = list(
                dict.fromkeys(
                    list(m_override.get("derives_from") or [])
                    + list(getattr(m, "derives_from", None) or [])
                )
            )
            calc = m.expression or m.source_column or ""
            agg = m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation)

            lines.append(f"""MERGE INTO {self.target_database}.{self.ontology_schema}.ONTOLOGY_METRIC_BINDINGS AS target
USING (SELECT 
    {_escape_sql_str(m_name)} AS METRIC_NAME,
    {_escape_sql_str(m.dataset)} AS CONCEPT_ID,
    {_escape_sql_str(desc)} AS DESCRIPTION,
    {_escape_sql_str(calc)} AS CALCULATION_EXPRESSION,
    {_escape_sql_str(agg)} AS AGGREGATION_TYPE,
    {_format_sql_array(syns)} AS SYNONYMS,
    {_format_sql_array(rules)} AS BUSINESS_RULES,
    {_escape_sql_str(filter_expr)} AS FILTER_EXPRESSION,
    {_format_sql_array(derives)} AS DERIVES_FROM,
    {_escape_sql_str(steward)} AS DATA_STEWARD,
    {_escape_sql_str(sem_view)} AS SEMANTIC_VIEW_NAME
) AS src
ON target.METRIC_NAME = src.METRIC_NAME
WHEN MATCHED THEN UPDATE SET
    CONCEPT_ID = src.CONCEPT_ID,
    DESCRIPTION = src.DESCRIPTION,
    CALCULATION_EXPRESSION = src.CALCULATION_EXPRESSION,
    AGGREGATION_TYPE = src.AGGREGATION_TYPE,
    SYNONYMS = src.SYNONYMS,
    BUSINESS_RULES = src.BUSINESS_RULES,
    FILTER_EXPRESSION = src.FILTER_EXPRESSION,
    DERIVES_FROM = src.DERIVES_FROM,
    DATA_STEWARD = src.DATA_STEWARD,
    SEMANTIC_VIEW_NAME = src.SEMANTIC_VIEW_NAME,
    UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    METRIC_NAME, CONCEPT_ID, DESCRIPTION, CALCULATION_EXPRESSION, AGGREGATION_TYPE,
    SYNONYMS, BUSINESS_RULES, FILTER_EXPRESSION, DERIVES_FROM, DATA_STEWARD, SEMANTIC_VIEW_NAME, UPDATED_AT
) VALUES (
    src.METRIC_NAME, src.CONCEPT_ID, src.DESCRIPTION, src.CALCULATION_EXPRESSION, src.AGGREGATION_TYPE,
    src.SYNONYMS, src.BUSINESS_RULES, src.FILTER_EXPRESSION, src.DERIVES_FROM, src.DATA_STEWARD, src.SEMANTIC_VIEW_NAME, CURRENT_TIMESTAMP()
);""")

        lines.append("")

        # 6. Cortex Search Services
        lines.append(f"-- 7. Snowflake Cortex Search Services for Agent Discovery")
        concept_svc_name = f"{model_name}_CONCEPT_SEARCH"
        metric_svc_name = f"{model_name}_METRIC_SEARCH"

        lines.append(f"""CREATE OR REPLACE CORTEX SEARCH SERVICE {self.target_database}.{self.ontology_schema}.{concept_svc_name}
  ON concept_name, synonyms, description
  ATTRIBUTES domain, concept_type, source_dataset, data_steward
  WAREHOUSE = {self.warehouse}
  TARGET_LAG = '{self.target_lag}'
  AS (
    SELECT 
      concept_name,
      ARRAY_TO_STRING(synonyms, ', ') AS synonyms,
      COALESCE(description, concept_name) AS description,
      domain,
      concept_type,
      source_dataset,
      COALESCE(data_steward, 'Unassigned') AS data_steward
    FROM {self.target_database}.{self.ontology_schema}.ONTOLOGY_CONCEPTS
  );""")
        lines.append("")

        lines.append(f"""CREATE OR REPLACE CORTEX SEARCH SERVICE {self.target_database}.{self.ontology_schema}.{metric_svc_name}
  ON metric_name, synonyms, description
  ATTRIBUTES concept_id, semantic_view_name, aggregation_type, data_steward
  WAREHOUSE = {self.warehouse}
  TARGET_LAG = '{self.target_lag}'
  AS (
    SELECT 
      metric_name,
      ARRAY_TO_STRING(synonyms, ', ') AS synonyms,
      COALESCE(description, metric_name) AS description,
      concept_id,
      semantic_view_name,
      aggregation_type,
      COALESCE(data_steward, 'Unassigned') AS data_steward
    FROM {self.target_database}.{self.ontology_schema}.ONTOLOGY_METRIC_BINDINGS
  );""")

        return "\n".join(lines)
