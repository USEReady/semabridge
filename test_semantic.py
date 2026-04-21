import requests
import json
import logging

logging.basicConfig(level=logging.INFO)

file1 = """
semantic_model:
  tables:
    - name: "sales"
      columns:
        - name: "id"
          type: "int"
        - name: "amount"
          type: "float"
        - name: "tax"
          type: "float"
      metrics:
        - name: "total_revenue"
          expr: "SUM(amount) + SUM(tax)"
"""

file2 = """
semantic_model:
  tables:
    - name: "sales"
      columns:
        - name: "id"
          type: "integer"
        - name: "amount"
          type: "float"
        - name: "tax"
          type: "float"
      metrics:
        - name: "total_revenue"
          expr: "SUM(amount + tax)"
"""

if __name__ == "__main__":
    url_compare = "http://127.0.0.1:8001/api/comparator/compare"
    res = requests.post(url_compare, json={
        "file1_name": "v1.yaml",
        "file1_content": file1,
        "file2_name": "v2.yaml",
        "file2_content": file2
    })
    
    data = res.json()
    logging.info(f"Compare Result Status: {res.status_code}")
    
    # Extract the modified metric 'total_revenue'
    mod_metrics = [m for m in data.get("metrics", []) if m.get("_diff_status") == "modified"]
    
    if mod_metrics:
        m = mod_metrics[0]
        logging.info(f"Modified metric found: {m}")
        
        # Now test the semantic logic using our google api key
        url_sem = "http://127.0.0.1:8001/api/comparator/compare-semantic"
        sem_res = requests.post(url_sem, json={
            "metric1_definition": m["_old_value"],
            "metric2_definition": m["definition"]
        })
        
        sem_data = sem_res.json()
        logging.info(f"Semantic Check Result: {json.dumps(sem_data, indent=2)}")
    else:
        logging.warning("No modified metrics found in test.")
