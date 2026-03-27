
import urllib.request, urllib.error
req = urllib.request.Request(
    'http://127.0.0.1:8001/api/accounts', 
    data=b'{\"connector_type\":\"FABRIC\",\"tag\":\"newtest99\",\"identity_email\":\"foo@bar\"}', 
    headers={'Content-Type': 'application/json'}, 
    method='POST'
)
try:
    print(urllib.request.urlopen(req).read().decode())
except urllib.error.HTTPError as e:
    print('STATUS:', e.code)
    print(e.read().decode())

