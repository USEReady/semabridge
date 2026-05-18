"""
Phase 3: RLS Translation Validation Script.

Simulates the extraction of RLS roles from Fabric TMSL and 
validates the generation of Snowflake Row Access Policies.
"""

from semabridge.rls.rls_analyzer import RlsAnalyzer
from semabridge.rls.rls_translator import RlsTranslator
import json

def test_rls_translation():
    print("=== Phase 3: RLS Translation Validation ===\n")
    
    # 1. Mock Fabric TMSL (Security Roles)
    tmsl_mock = {
        "model": {
            "roles": [
                {
                    "name": "North_America_Sales",
                    "tablePermissions": [
                        {
                            "name": "Sales",
                            "filterExpression": "'Sales'[Region] = \"North America\""
                        }
                    ]
                },
                {
                    "name": "EMEA_Manager",
                    "tablePermissions": [
                        {
                            "name": "Orders",
                            "filterExpression": "'Orders'[Status] = \"Shipped\""
                        }
                    ]
                }
            ]
        }
    }
    
    tmsl_json = json.dumps(tmsl_mock)
    
    # 2. Analyze (Extract Policies)
    analyzer = RlsAnalyzer()
    policies = analyzer.analyze_tmsl(tmsl_json)
    
    print(f"Detected {len(policies)} RLS Policies in TMSL.\n")
    
    # 3. Translate (Generate Snowflake DDL)
    translator = RlsTranslator()
    ddls = translator.translate_to_snowflake(policies)
    
    # 4. Results
    for i, ddl in enumerate(ddls):
        print(f"Policy #{i+1} Role: {policies[i].role}")
        print(f"DAX Filter: {policies[i].filter_expression}")
        print("-" * 20)
        print("Snowflake DDL:")
        print(ddl)
        print("-" * 40)

if __name__ == "__main__":
    test_rls_translation()
