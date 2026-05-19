import urllib.request
import urllib.parse
from dotenv import load_dotenv
load_dotenv()
from semabridge.auth.tokens import create_access_token
import json

token = create_access_token({'sub': '3'})
url = 'http://127.0.0.1:8001/api/discovery/snowflake/warehouses?identity_id=2ea2a1bb-5c84-4808-ac35-a6e01d327a4a'
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})

try:
    res = urllib.request.urlopen(req)
    print(res.status, res.read().decode())
except Exception as e:
    print(e.code, e.read().decode())
