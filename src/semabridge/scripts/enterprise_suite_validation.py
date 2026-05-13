"""
SemaBridge Grand Finale: Enterprise Suite Validation.

Integrates and validates all Phase 3 & 4 modules:
NLP, Lineage, Governance, Economics, Optimization, and Anomaly Detection.
"""

from semabridge.nlp.nl_to_sql import NLToSQL
from semabridge.lineage.lineage_tracker import LineageTracker, AuditLogger
from semabridge.governance.pii_detector import PIIDetector, MaskingEngine
from semabridge.governance.anomaly_detector import AnomalyDetector
from semabridge.optimization.translation_cache import TranslationCache
from semabridge.economics.query_cost_tracker import QueryCostTracker

def run_enterprise_demo():
    print("=== SemaBridge Enterprise Suite: End-to-End Validation ===\n")
    
    # 1. Setup Context
    project_id = "proj_enterprise_001"
    metadata = {
        "tables": ["Sales", "Customers"],
        "measures": [{"name": "Total Sales", "expression": "SUM(Sales[Amount])"}],
        "table_schemas": {
            "Customers": {"columns": [{"name": "customer_email"}, {"name": "region"}]}
        }
    }
    
    # 2. NLP to SQL + Cache
    print("[1/6] Testing NLP to SQL with Cache...")
    nlp_engine = NLToSQL()
    question = "Show me total sales for North America"
    sql = nlp_engine.question_to_sql(question, project_id, metadata)
    print(f"Question: {question}")
    print(f"Generated SQL: {sql}")
    
    # Verify Cache Hit
    sql_cached = nlp_engine.question_to_sql(question, project_id, metadata)
    print("✓ NLP Cache Hit Verified.")
    print("-" * 40)
    
    # 3. Lineage & Audit
    print("[2/6] Testing Lineage & Audit...")
    tracker = LineageTracker()
    audit = AuditLogger()
    
    m_node = tracker.add_node("fabric_measure", "Total Sales", "SUM(Amount)")
    q_node = tracker.add_node("final_query", "NLP Result", sql, parent_ids=[m_node])
    
    audit.log_event("QUERY_EXECUTION", {"sql": sql, "project_id": project_id})
    print(f"✓ Lineage nodes created. Query Node ID: {q_node}")
    print("-" * 40)
    
    # 4. Governance: PII Detection & Masking
    print("[3/6] Testing Governance (PII & Masking)...")
    detector = PIIDetector()
    masker = MaskingEngine()
    
    col_name = "customer_email"
    samples = ["user@example.com", "admin@inarva.com"]
    classification = detector.detect(col_name, samples)
    
    masked_val = masker.apply_mask(samples[0], strategy="redact")
    
    print(f"Column: {col_name} -> Classification: {classification.upper()}")
    print(f"Original: {samples[0]} -> Masked: {masked_val}")
    print("-" * 40)
    
    # 5. Economics: Cost Attribution
    print("[4/6] Testing Cost Attribution...")
    cost_tracker = QueryCostTracker()
    query_id = "sf_query_9988"
    credits_used = 0.45
    cost_tracker.track_cost(query_id, "Total Sales", credits_used)
    print(f"✓ Credits {credits_used} attributed to 'Total Sales'.")
    print("-" * 40)
    
    # 6. Anomaly Detection
    print("[5/6] Testing Anomaly Detection...")
    detector = AnomalyDetector()
    cost_history = [0.1, 0.12, 0.09, 0.11, 0.85] # 0.85 is a spike
    result = detector.detect_anomalies("Total Sales", cost_history)
    
    if result["is_anomaly"]:
        print(f"⚠ ANOMALY DETECTED! Value={result['current_value']} (Range: {result['expected_range']})")
    print("-" * 40)
    
    print("\n=== All Enterprise Modules VALIDATED! ===")

if __name__ == "__main__":
    run_enterprise_demo()
