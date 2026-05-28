#!/usr/bin/env python3
"""
Standalone measure translation debugging script.

Tests topological dependency-aware translation and structured result formats
on sample Fabric DAX measures.
"""

import sys
from types import SimpleNamespace
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.utils.logger import get_logger

logger = get_logger("scripts.test_measure_translation")

def run_debug_translation():
    # 1. Setup mock SML metrics representing the Fabric model
    metrics = [
        SimpleNamespace(unique_name="Amount", expression="SUM([Value])", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Actual", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Actual\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Plan", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Plan\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var Plan", expression="[Actual]-[Plan]", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var Plan %", expression="DIVIDE([Var Plan],[Plan], BLANK())", sql_expression=None, dataset="Fact"),
        # Add some designed unsupported metrics to test visible failure handling
        SimpleNamespace(unique_name="Var LE3 %", expression="SUMX(FILTER(Fact, [Value] > 100), CALCULATE([Amount]))", sql_expression=None, dataset="Fact"),
    ]

    print("--- Starting Measure Translation Audit ---\n")
    
    # 2. Instantiate translator
    translator = DAXTranslator()
    
    # 3. Print topological sorting logs
    print("Building dependency graph for 6 measures")
    graph = translator.build_dependency_graph(metrics)
    order = translator.get_translation_order(graph)
    print(f"Translation order: {' -> '.join(order)}\n")
    
    # 4. Resolve dependencies
    translator.translate_with_dependencies(metrics, "Fact", "Fact")
    
    # 5. Run structured translation validation
    success_count = 0
    total_count = len(metrics)
    
    for metric in metrics:
        translation_ctx = {
            "table_alias": "Fact",
            "dataset_name": "Fact",
            "metrics_context": metrics
        }
        res = translator.translate_measure(metric.expression, metric.unique_name, translation_ctx)
        if res["success"]:
            success_count += 1
            print(f"Translated {metric.unique_name} successfully")
            print(f"  SQL: {res['sql']}")
            print(f"  Tier: {res['tier']}\n")
        else:
            print(f"Failed {metric.unique_name}: {res['error']}\n")
            
    print(f"SUMMARY: {success_count}/{total_count} measures translated successfully")

if __name__ == "__main__":
    run_debug_translation()
