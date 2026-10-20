"""Автобусы Московской области по OpenStreetMap: маршруты (отношения route=bus/trolleybus/minibus/share_taxi) с
упорядоченными остановками. Открытого расписания области нет, поэтому рейсы строим по интервалу: тег interval, если
он есть, иначе городской маршрут — каждые URBAN_HEADWAY минут, пригородный — каждые SUBURBAN_HEADWAY (порядок величин
сверен по расписанию городского маршрута Каширы на rasp.yandex.ru: днём каждые 10–15 мин). Время между остановками —
по прямой с поправкой на улицы и средней скоростью автобуса.

Выгрузка OSM — osrm/cfd.osm.pbf (та же, что для карт OSRM; лицензия ODbL). Маршруты сохраняются в
data/transit/mo/routes.json; Москву покрывает GTFS Мосгортранса, поэтому маршруты, большая часть остановок которых
совпадает с московскими остановками с рейсами, Network.load_osm_bus пропускает.
"""
import json
import math
import os
import re

from ..config import ROOT

PBF = os.path.join(ROOT, 'osrm', 'cfd.osm.pbf')
MODES = ('bus', 'trolleybus', 'minibus', 'share_taxi')
MO_BBOX = (54.25, 35.14, 56.97, 40.21)          # Москва и область, грубо: юг, запад, север, восток
URBAN_HEADWAY = 15                              # мин
SUBURBAN_HEADWAY = 45
URBAN_KM = 15                                   # длиннее — пригородный
SPEED = {'urban': 20.0, 'suburban': 30.0}       # км/ч с остановками
DETOUR = 1.3
FIRST, LAST = 6 * 3600, 23 * 3600               # рейсы с конечной


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.32, (a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0])))


def interval_min(text):
    """Тег interval OSM («00:15», «15», «10-20») → минуты или None."""
    if not text:
        return None
    m = re.match(r'^\s*(\d{1,2}):(\d{2})', text)
    if m:
        return int(m[1]) * 60 + int(m[2]) or None
    nums = [int(x) for x in re.findall(r'\d+', text)]
    return sum(nums) / len(nums) if nums else None


def headway(route):
    """(минуты между рейсами, вид: urban | suburban)."""
    st = route['stops']
    length = sum(km(a, b) for a, b in zip(st, st[1:]))
    kind = 'urban' if length <= URBAN_KM else 'suburban'
    tagged = interval_min(route.get('interval'))
    return (tagged or (URBAN_HEADWAY if kind == 'urban' else SUBURBAN_HEADWAY)), kind


def trips(route):
    """Рейсы маршрута: [[(индекс остановки, прибытие, отправление), …], …] — секунды от полуночи."""
    h, kind = headway(route)
    st = route['stops']
    legs = [km(a, b) * DETOUR / SPEED[kind] * 3600 for a, b in zip(st, st[1:])]
    out, t = [], FIRST
    while t <= LAST:
        clock, trip = t, [(0, t, t)]
        for i, sec in enumerate(legs, start=1):
            clock += sec
            trip.append((i, int(clock), int(clock)))
        out.append(trip)
        t += h * 60
    return out


def extract(pbf=PBF, bbox=MO_BBOX):
    """Маршруты из выгрузки OSM: [{ref, name, mode, interval, stops: [[lat, lon, имя], …]}]. Порядок остановок —
    порядок участников отношения с ролью stop (если их меньше, чем platform, — платформы)."""
    import osmium
    routes = {}
    for o in osmium.FileProcessor(pbf, osmium.osm.RELATION):
        t = dict(o.tags)
        if t.get('type') == 'route' and t.get('route') in MODES:
            routes[o.id] = (t, [(m.type, m.ref, m.role) for m in o.members])
    need_n, need_w = set(), set()
    for _, mem in routes.values():
        for typ, ref, role in mem:
            if role.startswith(('stop', 'platform')):
                (need_n if typ == 'n' else need_w if typ == 'w' else set()).add(ref)
    nodes, ways = {}, {}
    for o in osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY).with_locations():
        if o.is_node() and o.id in need_n:
            nodes[o.id] = (o.location.lat, o.location.lon, o.tags.get('name', ''))
        elif o.is_way() and o.id in need_w:
            pts = [(n.lat, n.lon) for n in o.nodes if n.location.valid()]
            if pts:
                ways[o.id] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), o.tags.get('name', ''))
    out = []
    for rid, (t, mem) in sorted(routes.items()):
        stops = ordered_stops(mem, nodes, ways)
        if len(stops) < 2 or not any(bbox[0] <= s[0] <= bbox[2] and bbox[1] <= s[1] <= bbox[3] for s in stops):
            continue
        out.append(dict(osm_id=rid, ref=t.get('ref', ''), name=t.get('name', ''), mode=t['route'],
                        interval=t.get('interval'), stops=stops))
    return out


def ordered_stops(members, nodes, ways):
    def pick(prefix):
        out = []
        for typ, ref, role in members:
            if not role.startswith(prefix):
                continue
            p = nodes.get(ref) if typ == 'n' else ways.get(ref) if typ == 'w' else None
            if p and (not out or (round(p[0], 6), round(p[1], 6)) != (out[-1][0], out[-1][1])):
                out.append([round(p[0], 6), round(p[1], 6), p[2]])
        return out
    st, pl = pick('stop'), pick('platform')
    return st if len(st) >= max(2, len(pl)) else pl


def load(folder, pbf=PBF, log=print):
    """Маршруты из data/transit/mo/routes.json; если файла нет, а выгрузка OSM есть — извлечь и сохранить."""
    path = os.path.join(folder, 'mo', 'routes.json')
    if os.path.exists(path):
        return json.load(open(path, encoding='utf-8'))
    small = os.path.join(folder, 'mo', 'cfd_pt_routes.osm.pbf')     # уже отфильтрованная (osmium tags-filter r/route=…)
    pbf = small if os.path.exists(small) else pbf
    if not os.path.exists(pbf):
        return []
    log(f'Автобусы области: маршруты из {pbf}…')
    routes = extract(pbf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(routes, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    return routes
