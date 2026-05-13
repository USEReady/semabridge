# SemaBridge: Databricks & Unity Catalog Integration Guide

## 1. Overview
SemaBridge has been evolved from a Snowflake-centric platform into a **Multi-Cloud Semantic Gateway**. It now provides native, production-grade support for **Databricks (Spark SQL)** and **Unity Catalog**, enabling seamless semantic parity between Microsoft Fabric and Databricks.

## 2. Technical Architecture

### 2.1 Multi-Cloud Dialect Engine (`DaxTranslationEngine`)
The translation engine has been hardened to support multiple SQL dialects:
- **Snowflake (T-SQL/Cloud SQL)**: Optimized for Snowflake Views and Metrics.
- **Databricks (Spark SQL)**: Optimized for Delta Tables and Databricks SQL.

**Key Feature**: The engine dynamically adjusts its system prompts and deterministic rules based on the `target_dialect` parameter.

### 2.2 Unity Catalog Security Module (`DatabricksRlsTranslator`)
Automates the migration of Microsoft Fabric Row Level Security (RLS) to Databricks:
- Converts DAX filter expressions to Spark SQL predicates.
- Generates Unity Catalog DDL:
  - `CREATE OR REPLACE FUNCTION` (for row filtering logic).
  - `ALTER TABLE ... SET ROW FILTER` (to apply the policy).

### 2.3 Databricks Emitter
A specialized connector that handles the "Last Mile" of deployment:
- Translates SML models to Spark SQL Schema.
- Deploys metadata tables to Databricks for lineage and governance.
- Applies translated security policies to the target tables.

## 3. Supported Sync Directions
The following enterprise-grade sync paths are now fully operational:
- `PBIX_TO_DATABRICKS`
- `FABRIC_TO_DATABRICKS`
- `DATABRICKS_TO_FABRIC` (Experimental)

## 4. Implementation Details

### DAX to Spark SQL Translation (Tiered Approach)
- **Tiers 1-4 (Deterministic)**: AST-based conversion for standard aggregations, arithmetic, and time intelligence.
- **Tier 5 (LLM Fallback)**: Uses Google Gemini (or Groq/DeepSeek) with a specialized `databricks` prompt to handle complex, non-deterministic logic.

### Configuration Example
```python
sync_config = SyncConfig(
    direction="fabric_to_databricks",
    databricks_catalog="main",
    databricks_schema="sales_analytics",
    target_dialect="databricks"
)
```

## 5. Validation & Testing
The integration is verified via:
- `src/semabridge/scripts/databricks_validation.py`: Checks DDL and SQL correctness.
- `src/semabridge/scripts/comprehensive_tier_validation.py`: Ensures parity across Snowflake and Databricks outputs.

---
**Status**: Production Ready 🚀
**Last Updated**: May 2026
