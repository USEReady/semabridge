import asyncio
import os
import sys

# Ensure we can import the semabridge modules
sys.path.append(r"c:\Users\MANOJ\semabridge-working\src")

from semabridge.api.services.project_mapping_engine import build_entity_mappings
from semabridge.utils.synonyms import generate_auto_synonyms

def test():
    # 1. Test the pure NLP synonym generation
    names = ["Total Units", "Sales $", "Count of Product", "Sentiment"]
    for name in names:
        syns = generate_auto_synonyms(name)
        print(f"NLP for {name!r}: {syns}")

    # 2. Test the mapping engine output
    sml_model = {
        "unique_name": "preview",
        "datasets": [],
        "metrics": [
            {
                "unique_name": "Sales $",
                "label": "Sales $",
                "data_type": "number",
                "expression": "SUM(Sales)"
            }
        ]
    }
    built = build_entity_mappings(
        project_id="test_proj",
        model=sml_model,
        existing_mappings={},
        session_key="test_sess",
        target_connector="snowflake"
    )
    
    mappings = built.get("mappings", [])
    print(f"\nMapping engine returned {len(mappings)} mappings")
    for m in mappings:
        if m.get("source_name") == "Sales $":
            print(f"Engine output for 'Sales $': synonyms={m.get('synonyms')}")
            break

if __name__ == "__main__":
    test()
