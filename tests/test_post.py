
import urllib.request, urllib.error
import json
try:
    req = urllib.request.Request(
        'http://127.0.0.1:8001/api/accounts',
        data=b'{\"connector_type\":\"FABRIC\",\"tag\":\"test5\"}',
        headers={'Content-Type': 'application/json'}
    )
    resp = urllib.request.urlopen(req)
    print(resp.read().decode())
except urllib.error.HTTPError as e:
    print('HTTP ERROR', e.code)
    print(e.read().decode())

