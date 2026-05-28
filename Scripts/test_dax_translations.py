#!/usr/bin/env python3
"""
Test all DAX translations without calling OpenAI again.
Uses cache and validation rules.
"""

import sys
import os
# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.sml.loader import SMLLoader

def test_all_measures(project_name: str):
    """Test all measures in a project"""
    
    # Load existing SML
    loader = SMLLoader()
    sml = loader.load_by_project(project_name)
    
    # Initialize translator
    sanitizer = IdentifierSanitizer(force_uppercase=True)
    translator = MetricExpressionTranslator(identifier_sanitizer=sanitizer)
    
    # Manually add the validation method to the instance for this script
    import re
    
    def _get_cached_or_translate(self, dax: str, metric_name: str):
        # A simplified version for this test script
        if not hasattr(self, '_llm_cache'):
            self._llm_cache = {}
            cache_file = '.llm_dax_cache.json'
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    self._llm_cache = json.load(f)
        
        cache_key = f"{metric_name}|{dax}"
        return self._llm_cache.get(cache_key)

    def validate_and_test_translation(self, dax: str, metric_name: str, expected_patterns: dict = None) -> dict:
        result = {
            "metric_name": metric_name,
            "dax": dax,
            "translated_sql": None,
            "is_valid": False,
            "issues": [],
            "confidence": 0.0
        }
        
        sql = _get_cached_or_translate(self, dax, metric_name)
        if not sql:
            result["issues"].append("Translation not found in cache")
            return result
        
        result["translated_sql"] = sql
        sql_upper = sql.upper()
        
        forbidden = ["SELECT", "FROM", "JOIN", "WITH", "SUBQUERY"]
        for f in forbidden:
            if f in sql_upper:
                result["issues"].append(f"Contains forbidden keyword: {f}")
        
        if re.search(r'(SUM|AVG|COUNT)\(.*(SUM|AVG|COUNT)\(', sql, re.IGNORECASE):
            result["issues"].append("Contains nested aggregates")
        
        if "YTD" in metric_name.upper() or "TOTALYTD" in dax.upper():
            if "CURRENT_DATE" in sql_upper:
                result["issues"].append("YTD measure uses CURRENT_DATE, should use MAX_DATE")
            elif "MAX_DATE" not in sql_upper:
                result["issues"].append("YTD measure missing MAX_DATE anchor")
        
        if "CALCULATE" in dax.upper() and "CASE WHEN" not in sql_upper and "=" in dax:
            result["issues"].append("CALCULATE filter not converted to CASE WHEN")
        
        if "OVER" in sql_upper or "PARTITION BY" in sql_upper:
            result["issues"].append("Contains window function")
        
        if "DIVIDE" in dax.upper() and ("COALESCE" not in sql_upper or "NULLIF" not in sql_upper):
            result["issues"].append("DIVIDE not using COALESCE/NULLIF pattern")
        
        result["is_valid"] = len(result["issues"]) == 0
        result["confidence"] = 0.9 if result["is_valid"] else 0.3
        
        return result

    # Bind the method to the translator instance
    import types
    translator.validate_and_test_translation = types.MethodType(validate_and_test_translation, translator)

    results = []
    
    # Ensure translator has pre-fetched translations if needed (or loaded cache)
    # This is a simplified stand-in
    if not hasattr(translator, '_openai_prefetch_done'):
         translator.prefetch_openai_metric_translations(metrics=sml.metrics, table_alias="FACT", dataset_col_lookup={})


    for metric in sml.metrics:
        dax = getattr(metric, 'expression', None) or ""
        if not dax:
            continue
        
        validation = translator.validate_and_test_translation(
            dax=dax,
            metric_name=metric.unique_name
        )
        
        results.append(validation)
        
        status = "✅ PASS" if validation["is_valid"] else "❌ FAIL"
        print(f"{status} - {metric.unique_name}")
        if validation["issues"]:
            for issue in validation["issues"]:
                print(f"     ⚠️ {issue}")
        
        sql_display = (validation['translated_sql'][:100] + '...') if validation['translated_sql'] else 'N/A'
        print(f"     SQL: {sql_display}")
        print()
    
    total = len(results)
    passed = sum(1 for r in results if r["is_valid"])
    print(f"\n{'='*50}")
    print(f"SUMMARY: {passed}/{total} measures passed validation")
    print(f"{'='*50}")
    
    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="Project name")
    args = parser.parse_args()
    
    # Assumes running from workspace root
    test_all_measures(args.project)
