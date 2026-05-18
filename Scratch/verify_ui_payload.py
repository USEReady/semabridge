import requests
import json

def verify_synonym_payload():
    """
    Simulates a UI Dry Run request and verifies the synonym generation logic in the response.
    Note: Requires backend to be running on http://127.0.0.1:8001
    """
    base_url = "http://127.0.0.1:8001"
    
    # Payload similar to what UI sends during Project Creation / Dry Run
    payload = {
        "source_type": "pbix",
        "target_type": "snowflake",
        "output_format": "sml",
        "pbix_path": "Sample.pbix",
        "selected_models": ["Sales"],
        "auto_relationships": True,
        "generate_descriptions": True
    }
    
    print(f"Triggering Dry Run on {base_url}...")
    
    try:
        # Step 1: Trigger Dry Run (Assuming /api/projects/preview/dry-run exists)
        # Note: If this fails, it might be because a real projectId is needed
        response = requests.post(f"{base_url}/api/projects/preview/dry-run", json=payload, timeout=30)
        
        if response.status_code != 200:
            print(f"Failed with status {response.status_code}: {response.text}")
            return
            
        data = response.json()
        print("Dry Run Successful! Analyzing results...")
        
        # Step 2: Verify Synonyms in SML structure
        # Expected structure: data['models'][0]['datasets'][0]['columns'][0]['synonyms']
        models = data.get('models', [])
        if not models:
            print("No models found in response.")
            return
            
        for model in models:
            print(f"\nModel: {model.get('unique_name')}")
            for dataset in model.get('datasets', []):
                print(f"  Dataset: {dataset.get('unique_name')}")
                for column in dataset.get('columns', []):
                    syns = column.get('synonyms', [])
                    print(f"    Column: {column.get('unique_name')} -> Synonyms: {syns}")
                    
                for metric in model.get('metrics', []):
                    syns = metric.get('synonyms', [])
                    print(f"    Metric: {metric.get('unique_name')} -> Synonyms: {syns}")

    except Exception as e:
        print(f"Error connecting to backend: {e}")
        print("Make sure to run '.\dev.ps1 run' before running this script.")

if __name__ == "__main__":
    verify_synonym_payload()
