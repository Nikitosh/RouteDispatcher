"""Пакеты данных участков для сервера страницы (dispatch/api.py), которому не нужен OSRM.

Для каждого плана — папка <site>/api/<участок>/:
- matrix_<car|foot|bike>.json.gz (все файлы сжаты) — пары между точками дня (офис, дома бригад, заявки, новые точки событий) из кэша и
  между ними и сеткой точек с шагом GRID_STEP в радиусе GRID_RADIUS от точек дня (считает OSRM при сборке). Новую
  точку — клик по карте, точку разворота — сервер считает через ближайшие известные (geo/nearby.py);
- geometry.json.gz — линии по улицам между точками дня, какие есть в кэше;
- houses.json.gz — данные о домах заявок (коэффициент сложности новых заявок по адресу);
- matrix_pt.json.gz — общественный транспорт по расписаниям (tools/transit.py) между точками дня и от сетки к ним, если
  сеть собрана.
"""
import gzip
import json
import math
import os

from ..config import CACHE_DIR
from ..geo.geometry import RouteGeometry
from ..geo.houses import HouseIndex
from ..geo.matrices import MatrixBuilder, _key
from ..geo.osrm import OsrmClient, profile_of
from ..model.domain import house_address
from ..tools import transit
from .builder import slug

GRID_STEP = 0.5          # км
GRID_RADIUS = 1.2        # км от ближайшей точки дня
PROFILES = ('car', 'foot', 'bike')
M_LAT = 111.32


def _km(a, b):
    return math.hypot((a[0] - b[0]) * M_LAT, (a[1] - b[1]) * M_LAT * math.cos(math.radians(a[0])))


def _dump(obj, path):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def day_points(docs):
    pts = []
    for d in docs:
        pts.append((d['office']['lat'], d['office']['lon']))
        pts += [(b['lat'], b['lon']) for b in d['brigades']]
        pts += [(o['lat'], o['lon']) for o in d['orders']]
        for t in (d.get('event') or {}).get('turned') or ():
            pts.append((t['lat'], t['lon']))
    return list(dict.fromkeys((round(a, 5), round(b, 5)) for a, b in pts))


def grid(points, step=GRID_STEP, radius=GRID_RADIUS):
    lat0, lat1 = min(p[0] for p in points), max(p[0] for p in points)
    lon0, lon1 = min(p[1] for p in points), max(p[1] for p in points)
    dlat = step / M_LAT
    dlon = step / (M_LAT * math.cos(math.radians((lat0 + lat1) / 2)))
    out = []
    lat = lat0 - radius / M_LAT
    while lat <= lat1 + radius / M_LAT:
        lon = lon0 - radius / (M_LAT * 0.56)
        while lon <= lon1 + radius / (M_LAT * 0.56):
            p = (round(lat, 5), round(lon, 5))
            if any(_km(p, q) < radius for q in points):
                out.append(p)
            lon += dlon
        lat += dlat
    return out


def build_bundle(plan_doc, event_docs, out_root, client=None, log=print, cache_dir=None, name=None):
    """name — папка участка в api/ (как slug на странице); по умолчанию — slug названия."""
    docs = [plan_doc] + event_docs
    folder = os.path.join(out_root, 'api', name or slug(plan_doc['meta']['name']))
    os.makedirs(folder, exist_ok=True)
    pts = day_points(docs)
    net = grid(pts)
    client = client or OsrmClient()
    cache_dir = cache_dir or CACHE_DIR
    mb = MatrixBuilder(client=client, cache_dir=cache_dir)
    outs = {}
    for profile in PROFILES:
        mb.profile(profile, pts)                       # пары точек дня — из кэша (или OSRM, если чего-то нет)
        store = mb._store(profile)
        out = {f'{_key(a)}|{_key(b)}': store.get(f'{_key(a)}|{_key(b)}') for a in pts for b in pts if a != b}
        for i in range(0, len(net), 100):
            chunk = net[i:i + 100]
            for j in range(0, len(pts), 100):
                dst = pts[j:j + 100]
                for a, row in zip(chunk, client.table(profile, chunk, dst)):
                    for b, cell in zip(dst, row):
                        out[f'{_key(a)}|{_key(b)}'] = list(cell) if cell else [1e6, 1e6]
                for b, row in zip(dst, client.table(profile, dst, chunk)):
                    for a, cell in zip(chunk, row):
                        out[f'{_key(b)}|{_key(a)}'] = list(cell) if cell else [1e6, 1e6]
        _dump(out, os.path.join(folder, f'matrix_{profile}.json.gz'))
        outs[profile] = out
    if transit.available():                            # общественный транспорт по расписаниям: точки дня и сетка
        mb.build(pts, 0)                               # пары точек дня — те же, что в плане (кэш cache/matrix_pt.json)
        day = mb._store('pt')
        allp = pts + net
        got = transit.times(allp, walk=transit.OsrmWalk(client), hourly=True)
        nd = len(pts)
        out = {}
        for i in range(len(allp)):
            for j in range(len(allp)):
                if i == j or (i >= nd and j >= nd):
                    continue
                k = f'{_key(allp[i])}|{_key(allp[j])}'
                c = outs['car'].get(k)                 # км — по машинному пути, как в MatrixBuilder.transit
                if i < nd and j < nd and day.get(k) is not None:
                    out[k] = day.get(k)
                elif (i, j) in got and c and c[1] < 1e5:
                    mean, hours = got[i, j]
                    out[k] = [round(mean, 2), c[1], [-1.0 if h is None else round(h, 2) for h in hours]]
                else:
                    out[k] = [-1.0, -1.0]
        _dump(out, os.path.join(folder, 'matrix_pt.json.gz'))
    geo = RouteGeometry(path=os.path.join(cache_dir, 'geometry.json'), online=False)
    modes = {profile_of(b['mode']) for d in docs for b in d['brigades']}
    lines = {}
    for p in modes:
        for a in pts:
            for b in pts:
                k = RouteGeometry._key(p, a, b)
                if a != b and k in geo.store:
                    lines[k] = geo.store.get(k)
    _dump(lines, os.path.join(folder, 'geometry.json.gz'))
    houses = HouseIndex(os.path.join(cache_dir, 'houses.json'))
    _dump({house_address(o['addr']): houses.get(o['addr']) for d in docs for o in d['orders'] if houses.get(o['addr'])},
          os.path.join(folder, 'houses.json.gz'))
    size = sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder)) / 1e6
    log(f"{plan_doc['meta']['name']}: точек дня {len(pts)}, сетка {len(net)}, линий {len(lines)}, {size:.1f} МБ")
    return folder
