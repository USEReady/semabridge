# SemaBridge Product Overview

> **Semantic Integration and Transformation Platform**

SemaBridge is a CLI tool for **bi-directional semantic model synchronization** between **Snowflake** and **Microsoft Fabric (Power BI)** with built-in Git-like version control.

---

## Business Value

| Value Driver | Description |
|-------------|-------------|
| **Single Source of Truth** | Unified semantic definitions across all platforms |
| **Zero Variance** | Same metrics, same answers, across all departments |
| **AI Grounding** | Validated metadata for LLM context (reduces hallucinations) |
| **Full Auditability** | Versioned metric definitions with rollback capability |
| **60% Faster** | Reduces semantic layer implementation from ~1,200 hours |

---

## Key Features

### 1. Bi-Directional Sync

| Direction | Flow | Use Case |
|-----------|------|----------|
| **Forward Sync** | Snowflake â†’ Fabric | Deploy Snowflake data models to Power BI |
| **Reverse Sync** | Fabric â†’ Snowflake | Export Fabric models to Snowflake Semantic Views |

### 2. Version Control (Git-like)

- **Snapshots**: DuckDB-based immutable snapshots
- **History**: View all versions of a semantic model
- **Rollback**: Revert to any previous version
- **Diff Engine**: Entity-level change comparison

### 3. Semantic Modeling Language (SML)

Intermediate representation decoupling source/target formats:
- Datasets, Columns, Metrics, Relationships, Dimensions
- DAX â†’ SQL translation (3-tier with graceful degradation)
- YAML-based for human readability

### 4. Cortex Analyst Integration

Auto-generates Snowflake Cortex Analyst YAML for natural language querying.

---

## Supported Platforms

| Platform | Extract | Deploy |
|----------|---------|--------|
| **Snowflake** | âœ… INFORMATION_SCHEMA | âœ… Semantic Views, Cortex YAML |
| **Microsoft Fabric** | âœ… REST API (getDefinition) | âœ… model.bim via REST API |

---

## Use Cases

1. **Data Mesh Governance**: Centralize semantic definitions across federated data products
2. **BI Migration**: Migrate Power BI models to Snowflake or vice versa
3. **Multi-Cloud Analytics**: Maintain consistent metrics across cloud platforms
4. **AI/ML Feature Stores**: Provide grounded context for LLM applications
5. **Compliance & Audit**: Track all metric definition changes with rollback

---

## Quick Start

```powershell
# Validate connections
uv run main.py validate

# Sync from Fabric to Snowflake (using semabridge.yaml)
uv run main.py semantic-sync

# View history
uv run main.py list-projects
uv run main.py history -d <project-id>

# Rollback to previous version
uv run main.py rollback -d <project-id> --tag v1.0
```

---

## Configuration

Configured via environment variables (`.env`) and `semabridge.yaml`:

```yaml
source:
  type: fabric
  dataset_id: "1cb616cc-52b7-4268-b458-b092d64c84a6"

target:
  type: snowflake
  deploy: true

model_name: "Customer Profitability"
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Metric Consistency | Zero variance between departmental reports |
| AI Grounding | 98% reduction in high/critical false positives |
| Implementation Time | 60% reduction vs manual semantic layer build |
| Audit Compliance | Full traceability of all definition changes |

