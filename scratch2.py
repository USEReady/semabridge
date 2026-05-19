import urllib.request
from dotenv import load_dotenv
load_dotenv()
from semabridge.auth.tokens import create_access_token
import json

token = create_access_token({'sub': '1'})
req = urllib.request.Request('http://127.0.0.1:8001/api/accounts?connector_type=SNOWFLAKE', headers={'Authorization': 'Bearer ' + token})
res = urllib.request.urlopen(req)
print(res.status, res.read().decode())
