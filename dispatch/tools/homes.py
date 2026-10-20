"""Дома бригад для демо (python -m dispatch homes): рабочий день начинается из дома, а не из офиса.

Дом — настоящий жилой дом из OpenStreetMap (data/osm/addr.geojsonseq, см. tools/districts.prepare_osm) не дальше
RADIUS от места, где бригада работает: для участков организаторов — центр её заявок контрольного дня в её основном
районе, для сгенерированных участков — одна из заявок участка. Выбор зависит только от имени бригады, поэтому при
повторном запуске дома те же. Адрес пишется в колонку «Старт» файла бригад, координаты — в кэш геокодинга.
"""
import json
import math
import os
import random
import zlib
from collections import Counter

from ..files.readers import read_brigades, read_orders
from ..files.tables import read_table
from ..geo.geocoder import Geocoder
from .demo_data import DEMO_DIR, FAR_TOWNS, REGIONS, write_brigades
from .districts import OSM, _centroid, house, street
from .sources import organizers_file

RADIUS = 1500            # м
KIND = ('apartments', 'residential', 'house')
M_LAT = 111320.0


def _dist(a, b):
    return math.hypot((a[0] - b[0]) * M_LAT, (a[1] - b[1]) * M_LAT * math.cos(math.radians(a[0])))


def residential(anchors):
    """Жилые дома OSM с адресом не дальше RADIUS от какой-нибудь точки anchors: [(lat, lon, улица и дом, город)]."""
    lat0, lat1 = min(a[0] for a in anchors) - .02, max(a[0] for a in anchors) + .02
    lon0, lon1 = min(a[1] for a in anchors) - .03, max(a[1] for a in anchors) + .03
    out = []
    for line in open(os.path.join(OSM, 'addr.geojsonseq')):
        f = json.loads(line.lstrip('\x1e'))
        t = f['properties']
        if t.get('building') not in KIND or 'addr:street' not in t:
            continue
        c = _centroid(f['geometry'])
        if not c or not (lat0 < c[0] < lat1 and lon0 < c[1] < lon1) or not any(_dist(c, a) < RADIUS for a in anchors):
            continue
        out.append((round(c[0], 7), round(c[1], 7), f"{street(t['addr:street'])}, д. {house(t['addr:housenumber'])}", t.get('addr:city')))
    return out


def anchors(region):
    """{бригада: (точка, рядом с которой её дом; город, если не Москва)}."""
    orders = read_orders(os.path.join(DEMO_DIR, f'{region} заявки.xlsx')).orders
    coords = Geocoder(online=False).locate([o.house for o in orders], log=lambda *_: None)
    brigades = read_brigades(os.path.join(DEMO_DIR, f'{region} бригады.xlsx'))
    out = {}
    if region in REGIONS:                                   # участок организаторов: где бригада работала 17.08
        rows = read_table(organizers_file(region, 'Контрольное'))
        col = rows[0].index('Бригада')
        day = [r for r in rows[1:] if r[0]]
        ranked = [name for name, _ in Counter(r[col] for r in day if r[col]).most_common()]
        for n, name in enumerate(ranked):
            mine = [o for r, o in zip(day, orders) if r[col] == name]
            district = Counter(o.district for o in mine).most_common(1)[0][0]
            pts = [coords[o.house] for o in mine if o.district == district]
            out[f'Бригада {n + 1}'] = ((sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)),
                                       district if district in FAR_TOWNS else None)
    for b in brigades:
        if b.name not in out:
            o = orders[zlib.crc32(f'{region}|{b.name}'.encode()) % len(orders)]
            out[b.name] = (coords[o.house], None)
    return out, brigades


def assign(region, log=print):
    points, brigades = anchors(region)
    houses = residential([p for p, _ in points.values()])
    geocoder = Geocoder(online=False)
    rows = []
    for b in brigades:
        point, town = points[b.name]
        near = sorted((h for h in houses if _dist(h, point) < RADIUS), key=lambda h: h[2])
        if not near:
            raise ValueError(f'{region}, {b.name}: нет жилых домов OSM ближе {RADIUS} м')
        lat, lon, street_house, city = random.Random(f'{region}|{b.name}|home').choice(near)
        city = city or town
        address = f"{'Город Москва' if not city or city == 'Москва' else city}, {street_house}"
        geocoder.remember(address, lat, lon, 'osm')
        rows.append((b.name, b.mode, b.skills, address))
    geocoder.save()
    path = os.path.join(DEMO_DIR, f'{region} бригады.xlsx')
    write_brigades(path, rows)
    return path, rows
