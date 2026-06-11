import requests

try:
    resp = requests.post("http://127.0.0.1:8001/api/projects/sync/deploy", json={
        "project_id": "test", # Need real ID
        "target": "snowflake"
    })
    print(resp.status_code, resp.text)
except Exception as e:
    print(e)
