import urllib.request
for path in ['/api/dashboard/stats', '/api/umpires', '/api/managers', '/api/players']:
    with urllib.request.urlopen('http://127.0.0.1:8001' + path) as response:
        print(path, response.read().decode())
