import urllib.request
import urllib.parse
from dotenv import load_dotenv
load_dotenv()
from semabridge.auth.tokens import create_access_token
import json

token = create_access_token({'sub': '1'})
url = 'http://127.0.0.1:8001/api/discovery/snowflake/databases/SEMABRIDGE/schemas?identity_id=fec91843-1e76-4e17-aff7-c26a77a3f937'
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})

try:
    res = urllib.request.urlopen(req)
    print(res.status, res.read().decode())
except Exception as e:
    print(e.code, e.read().decode())
