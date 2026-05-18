# SemaBridge Phase 3 & 4 Implementation Prompts

## 1. Natural Language to SQL (NLP)
- Module: `src/semabridge/nlp/`
- Goal: Convert English questions to Semantic SQL using LLM.

## 2. Enterprise Audit & Lineage
- Module: `src/semabridge/lineage/`
- Goal: Traceability from Fabric to Snowflake.

## 3. Data Governance (PII & Masking)
- Module: `src/semabridge/governance/`
- Goal: Auto-PII detection and dynamic masking.

## 4. Cost Attribution
- Module: `src/semabridge/economics/`
- Goal: Track Snowflake credits per measure.

## 5. Performance Optimization (Cache)
- Module: `src/semabridge/optimization/`
- Goal: Redis-based translation caching.

## 6. Anomaly Detection
- Module: `src/semabridge/governance/`
- Goal: Detect unusual query patterns.
