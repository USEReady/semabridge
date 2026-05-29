#!/usr/bin/env python3
"""
Standalone debugging test harness for DAX -> SQL translation.
"""
import sys
from types import SimpleNamespace
from semabridge.converter.dax_translator import DAXTranslator, TranslationError

def test_14_measures():
    # Define mock SML metrics representing the Fabric model with the 14 measures
    metrics = [
        SimpleNamespace(unique_name="Amount", expression="TOTALYTD(SUM([Value]), 'Date'[Date])*.3", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Actual", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Actual\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="LE1", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Latest Estimate 1\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="LE2", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Latest Estimate 2\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="LE3", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Latest Estimate 3\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Plan", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Plan\")", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var LE1", expression="[Actual]-[LE1]", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var LE2", expression="[Actual]-[LE2]", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var LE3", expression="[Actual]-[LE3]", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var Plan", expression="[Actual]-[Plan]", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var LE1 %", expression="DIVIDE([Var LE1],[LE1], BLANK())", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var LE2 %", expression="DIVIDE([Var LE2],[LE2], BLANK())", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var LE3 %", expression="DIVIDE([Var LE3],[LE3], BLANK())", sql_expression=None, dataset="Fact"),
        SimpleNamespace(unique_name="Var Plan %", expression="DIVIDE([Var Plan],[Plan], BLANK())", sql_expression=None, dataset="Fact"),
    ]

    translator = DAXTranslator()
    
    # Translate all measures in order using dependency sorting
    translator.translate_measures_in_order(metrics, "Fact", "Fact")
    
    success_count = 0
    total_count = len(metrics)
    
    for metric in metrics:
        print(f"🔄 Translating measure '{metric.unique_name}'".encode('utf-8', 'ignore').decode('ascii', 'ignore'))
        try:
            context = {
                "table_alias": "Fact",
                "dataset_name": "Fact",
                "metrics_context": metrics
            }
            # Verify and print
            sql = translator.translate(metric.expression, metric.unique_name, context)
            if sql:
                success_count += 1
                print(f"✅ Translated '{metric.unique_name}'".encode('utf-8', 'ignore').decode('ascii', 'ignore'))
                print(f"   SQL: {sql}\n")
        except Exception as e:
            print(f"❌ Failed '{metric.unique_name}': {e}\n".encode('utf-8', 'ignore').decode('ascii', 'ignore'))
            
    print(f"RESULT: {success_count}/{total_count} measures translated successfully")
    if success_count == total_count:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    test_14_measures()
