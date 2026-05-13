# SemaBridge Master Documentation: The Multi-Cloud Semantic Gateway

## 1. Executive Summary
SemaBridge is an enterprise-grade semantic orchestration platform designed to bridge the gap between fragmented BI ecosystems (Microsoft Fabric/Power BI) and modern cloud data warehouses (Snowflake, Databricks). It enables "Write Once, Run Anywhere" semantic logic by decoupling business definitions from underlying SQL dialects.

## 2. Core Architecture Philosophy

### 2.1 OSI (Open Semantic Interchange)
The canonical intermediate format that abstracts source-specific metadata (TMSL, PBIX) into a platform-agnostic schema.

### 2.2 SML (Semantic Modeling Language)
The final enriched representation used for deployment. SML contains:
- **Logical Tables & Columns**: Normalized data structures.
- **Semantic Metrics**: Translated business logic (DAX → SQL).
- **Security Policies**: Abstracted RLS/CLS rules.

## 3. The Translation Engine (Tiers 1-5)
The heart of SemaBridge is a **Multi-Dialect DAX-to-SQL Engine** that follows a tiered safety strategy:

- **Tier 1 (Deterministic)**: Direct aggregations (SUM, AVG) mapped via AST.
- **Tier 2 (Arithmetic)**: Measures with operators (+, -, *, /) and DIVIDE.
- **Tier 3 (Time Intelligence)**: TOTALYTD, SAMEPERIODLASTYEAR converted to window functions.
- **Tier 4 (Advanced AST)**: CALCULATE with complex filter contexts and iterators (SUMX).
- **Tier 5 (LLM Fallback)**: Gemini/Groq-powered translation for opaque or extremely complex DAX patterns.

## 4. Multi-Cloud Connectors

### 4.1 Extractors (Source)
- **Fabric/PBIX Extractor**: Uses TMSL (Tabular Model Scripting Language) to pull model definitions.
- **Snowflake Extractor**: Reverse-engineers SML models from Snowflake Views and Metadata.

### 4.2 Emitters (Target)
- **Snowflake Emitter**: Deploys SQL Views and Row Access Policies (RAP).
- **Databricks Emitter**: Deploys Spark SQL and Unity Catalog Row Filters/Masks.

## 5. Enterprise Security & Governance

### 5.1 Security Migration
- **Snowflake**: Automates the creation of Row Access Policies (RAP) from Fabric RLS.
- **Databricks**: Automates Unity Catalog Row Filters using Spark SQL predicates.

### 5.2 Governance Modules
- **PII Detector**: Scans semantic models for sensitive data.
- **Anomaly Detector**: Monitors sync patterns for unexpected schema changes.
- **Lineage Tracker**: Provides end-to-end visibility from Fabric measures to Snowflake/Databricks tables.

## 6. NLP (Natural Language Processing)
The `NLToSQL` module allows users to query their data in plain English. It utilizes the SML model as "Semantic Context" to ground LLM responses, ensuring high accuracy and dialect-correct SQL generation for both Snowflake and Databricks.

## 7. Operational Workflows

### 7.1 Sync Pipeline
1. **Extract**: Pull source model (TMSL).
2. **Convert**: TMSL → OSI.
3. **Translate**: DAX → Target SQL (Dialect-aware).
4. **Enrich**: Apply security and metadata.
5. **Snapshot**: Version the model in the internal state store.
6. **Deploy**: Emit to target platform (DDL/DML).

### 7.2 Versioning & Rollback
Every sync creates a point-in-time **Snapshot**. SemaBridge supports one-click rollbacks to previous versions, ensuring high availability and disaster recovery for semantic logic.

---
**Technical Specification**: v2.5 (Multi-Cloud Hardened)
**Maintainer**: SemaBridge Engineering Team
**Last Updated**: May 2026
