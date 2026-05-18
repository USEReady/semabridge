# Phase 3: Semantic Runtime & Enterprise Semantics Specification

## Overview
Transform Semabridge from a one-way sync tool into an enterprise semantic platform.

## Key Modules

### 1. RLS Translation Engine (`src/semabridge/rls/`)
- `rls_analyzer.py`: Parse Fabric RLS from TMSL.
- `rls_translator.py`: Generate Snowflake Row Access Policies (RAC) and Dynamic Data Masking (DDM).
- `rls_validator.py`: Verify enforcement.

### 2. Bidirectional Sync Engine (`src/semabridge/bidirectional/`)
- `snowflake_model_monitor.py`: Detect Snowflake-side changes.
- `reverse_translator.py`: SQL Metric -> DAX Measure conversion.
- `conflict_resolver.py`: Merge strategies (Fabric vs Snowflake).

### 3. Semantic Runtime Engine (`src/semabridge/runtime/`)
- `semantic_metadata_cache.py`: High-performance metadata lookups.
- `semantic_query_proxy.py`: Query rewriting to enforce constraints.

### 4. Cortex AI Integration (`src/semabridge/cortex_ai/`)
- `cortex_semantic_adapter.py`: Export models to Cortex AI format.
- `semantic_query_generator.py`: NL -> Semantic SQL.

### 5. Enterprise Audit & Lineage (`src/semabridge/governance/`)
- `lineage_tracker.py`: Fabric -> Snowflake measure lineage.
- `data_classifier.py`: PII detection and classification.
- `masking_engine.py`: Apply DDM based on classification.

## Implementation Guidelines
- Focus on correctness and clean code.
- Implement incrementally starting with RLS (3.1).
- Target >90% test coverage.
