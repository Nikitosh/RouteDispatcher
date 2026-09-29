"""Геометрия участков маршрутов по улицам из локального OSRM (машина 5003, велосипед 5002, пешком 5001) → geom.js."""
import json, urllib.request
PORT = {'car': 5003, 'bike': 5002, 'foot': 5001, 'pt': 5001}
d = json.load(open('data.json'))
def leg(mode, a, b):
    url = f"http://localhost:{PORT[mode]}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=full&geometries=geojson"
    g = json.load(urllib.request.urlopen(url, timeout=10))['routes'][0]['geometry']['coordinates']
    return [[round(y, 5), round(x, 5)] for x, y in g]
G = {}
for r in d['plan']:
    pts = [d['office']] + [[d['orders'][s['k']]['lat'], d['orders'][s['k']]['lon']] for s in r['stops']]
    G[r['v']] = [leg(r['mode'], pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
open('geom.js', 'w').write('const G=' + json.dumps(G) + ';\n')
print({v: sum(len(l) for l in g) for v, g in G.items()})
