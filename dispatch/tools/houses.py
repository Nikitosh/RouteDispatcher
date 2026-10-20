"""Данные о домах заявок → cache/houses.json (python -m dispatch houses).

Три открытых источника; что откуда берётся, записано в поле src каждого дома:
- OSM (osrm/cfd.osm.pbf — Москва и область): контур дома, этажность, квартиры, год, подъезды с координатами и
  диапазонами квартир (addr:flats), офисы внутри дома, шлагбаумы и закрытые ворота рядом;
- реестр домов Реформы ЖКХ по Москве на 01.03.2025: этажность, подъезды, лифты, квартиры, год, материал, серия;
- кадастровая оценка Росреестра 2023 по Москве: этаж каждой квартиры. Подъезды по ней выводятся так: квартиры
  дома по возрастанию номера, где этаж падает к первому — начался следующий подъезд.
Порталы Реформы ЖКХ и Росреестра из-за рубежа закрыты, поэтому выгрузки берутся из открытого зеркала на GitHub
(SOURCES) и лежат в data/houses/. По Московской области реестра и кадастра нет — там только OSM.
Нужны пакеты osmium и shapely.
"""
import bz2
import csv
import glob
import io
import json
import math
import os
import re
import urllib.request
import zipfile

from ..config import DATA_DIR, ROOT
from ..files.readers import read_orders
from ..geo.geocoder import Geocoder
from ..geo.houses import PATH as HOUSES_PATH
from ..model.domain import house_address

HOUSES_DIR = os.path.join(DATA_DIR, 'houses')
MIRROR = 'https://raw.githubusercontent.com/PavelSyomin/moscow-housing-vacancy-rate/HEAD/data/'
SOURCES = ['export-reestrmkd-77-20250301.zip'] + [f'ce/living-quarters-part-{i}.csv.bz2' for i in range(1, 9)]
OSM_PBF = os.path.join(ROOT, 'osrm', 'cfd.osm.pbf')
GATE_RADIUS = 100        # м: шлагбаум или закрытые ворота ближе — въезд ограничен
MATCH_RADIUS = 40        # м: точка адреса вне контуров — берём ближайший дом не дальше
RESIDENTIAL = ('apartments', 'residential', 'house', 'detached', 'dormitory', 'terrace')
M_LAT = 111320.0

_TYPES = r'(ул|улица|пр-кт|проспект|пр-д|проезд|ш|шоссе|б-р|бульвар|пер|переулок|наб|набережная|пл|площадь|туп|тупик|аллея|мкр)'


def _street(s):
    s = re.sub(r'\b' + _TYPES + r'\b\.?', ' ', s.lower().replace('ё', 'е'))
    return ' '.join(sorted(w for w in re.split(r'[\s.]+', s) if w))


def key(address):
    """Ключ для сверки адресов разных источников: улица (слова по алфавиту) | дом | корпус | строение."""
    a = house_address(address).lower().replace('ё', 'е')
    m = re.search(r'(?:^|,)\s*([^,]+),\s*д\.?\s*(\d+[а-я]?(?:/\d+)?)\s*(?:,?\s*(?:к|корп)\.?\s*(\d+))?'
                  r'\s*(?:,?\s*(?:стр|с)\.?\s*(\d+))?\s*$', a)
    return m and f'{_street(m[1])}|{m[2]}|{m[3] or ""}|{m[4] or ""}'


def download(log=print):
    os.makedirs(HOUSES_DIR, exist_ok=True)
    for name in SOURCES:
        path = os.path.join(HOUSES_DIR, os.path.basename(name))
        if not os.path.exists(path):
            log(f'Скачиваю {name}')
            urllib.request.urlretrieve(MIRROR + name, path)


def _int(x):
    m = re.match(r'\s*(\d+)', str(x or ''))
    return int(m[1]) if m else None


def reforma(keys):
    """{ключ: поля дома} из реестра Реформы ЖКХ для нужных ключей."""
    z = zipfile.ZipFile(os.path.join(HOUSES_DIR, 'export-reestrmkd-77-20250301.zip'))
    name = next(n for n in z.namelist() if n.endswith('.csv'))
    out = {}
    for r in csv.DictReader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'), delimiter=';'):
        k = f"{_street(r['formalname_street'] or '')}|{(r['house_number'] or '').lower()}{(r['letter'] or '').lower()}|" \
            f"{r['block'] or ''}|{r['building'] or ''}"
        if k in keys and k not in out:
            levels, lifts = _int(r['floor_count_max']), _int(r['elevators_count'])
            if lifts == 0 and (levels or 0) >= 6:              # ноль лифтов у высокого дома — поле не заполнено
                lifts = None
            out[k] = dict(levels=levels, n_entrances=_int(r['entrance_count']) or None,
                          flats=_int(r['living_quarters_count']) or None, lifts=lifts, year=_int(r['built_year']),
                          material=None if r['wall_material'] in ('', 'Не заполнено') else r['wall_material'],
                          series=r['project_type'] or None)
    return out


def cadastre(keys):
    """{ключ: (этажи квартир, первые квартиры подъездов)} по кадастровой оценке."""
    flats = {}
    for path in sorted(glob.glob(os.path.join(HOUSES_DIR, 'living-quarters-part-*.csv.bz2'))):
        for r in csv.DictReader(io.TextIOWrapper(bz2.open(path), encoding='utf-8')):
            m = re.search(r'^(.*?),\s*кв\.\s*(\d+)$', r['Адрес'])
            fl = _int(r['Этаж (для помещения)'])
            if not m or fl is None:
                continue
            k = key(m[1])
            if k in keys:
                flats.setdefault((k, r['КН родителя']), {})[int(m[2])] = fl
    out = {}
    for (k, _), floors in sorted(flats.items(), key=lambda x: -len(x[1])):
        if k in out:
            continue
        apts = sorted(floors)
        starts = [apts[0]] + [a for p, a in zip(apts, apts[1:]) if floors[a] < floors[p] - 1]
        out[k] = (floors, starts)
    return out


def _flats(text):
    out = []
    for part in re.split(r'[;,]', text or ''):
        m = re.match(r'\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*$', part)
        if m:
            out.append([int(m[1]), int(m[2] or m[1])])
    return out


def osm(points, path=OSM_PBF, log=print):
    """{адрес: поля дома} из OSM для точек {адрес: (lat, lon)}."""
    import osmium
    from shapely.geometry import Point, Polygon
    from shapely.strtree import STRtree
    cell = lambda lat, lon: (int(lat / .0025), int(lon / .004))
    grid = {}
    for p in points.values():
        grid.setdefault(cell(*p), []).append(p)

    def near(lat, lon, r):
        c = cell(lat, lon)
        return any(_dist(p, (lat, lon)) < r for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in grid.get((c[0] + dx, c[1] + dy), ()))

    entrances, gates, offices, buildings = {}, [], [], []

    class Nodes(osmium.SimpleHandler):
        def node(self, n):
            t = n.tags
            if 'entrance' not in t and 'barrier' not in t and 'office' not in t:
                return
            lat, lon = n.location.lat, n.location.lon
            if not near(lat, lon, 300):
                return
            if 'entrance' in t:
                entrances[n.id] = dict(ref=t.get('ref'), lat=round(lat, 6), lon=round(lon, 6), flats=_flats(t.get('addr:flats')))
            elif t.get('barrier') == 'lift_gate' or (t.get('barrier') in ('gate', 'swing_gate', 'sliding_gate')
                                                     and t.get('access') in ('private', 'no')):
                gates.append((lat, lon))
            elif 'office' in t:
                offices.append(Point(lon, lat))

    class Ways(osmium.SimpleHandler):
        def way(self, w):
            t = w.tags
            if 'building' not in t:
                return
            try:
                coords = [(n.lat, n.lon) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) < 4 or not near(*coords[0], 300):
                return
            buildings.append(dict(poly=Polygon([(lon, lat) for lat, lon in coords]), tags=dict(t),
                                  ents=list(dict.fromkeys(n.ref for n in w.nodes if n.ref in entrances))))

    log(f'OSM: {path}')
    Nodes().apply_file(path)
    Ways().apply_file(path, locations=True)
    tree = STRtree([b['poly'] for b in buildings])
    office_tree = STRtree(offices) if offices else None
    out = {}
    for addr, (lat, lon) in points.items():
        pt = Point(lon, lat)
        hit = [i for i in tree.query(pt) if buildings[i]['poly'].contains(pt)]
        if not hit and buildings:
            i = int(tree.nearest(pt))
            if buildings[i]['poly'].exterior.distance(pt) * M_LAT < MATCH_RADIUS:
                hit = [i]
        if not hit:
            continue
        b = buildings[max(hit, key=lambda i: buildings[i]['poly'].area)]
        t = b['tags']
        inside = office_tree is not None and any(b['poly'].contains(offices[j]) for j in office_tree.query(b['poly']))
        out[addr] = dict(levels=_int(t.get('building:levels')), flats=_int(t.get('building:flats')),
                         year=_int(t.get('start_date')), entrances=[entrances[e] for e in b['ents']],
                         office=t.get('building') in ('office', 'commercial') or 'office' in t,
                         office_inside=inside and t.get('building') not in RESIDENTIAL,
                         gated=any(_dist(g, (lat, lon)) < GATE_RADIUS for g in gates))
    return out


def _dist(a, b):
    return math.hypot((a[0] - b[0]) * M_LAT, (a[1] - b[1]) * M_LAT * math.cos(math.radians(a[0])))


def build(order_files, osm_path=OSM_PBF, out=HOUSES_PATH, log=print):
    download(log)
    addrs = sorted({o.house for f in order_files for o in read_orders(f).orders})
    coords = Geocoder(online=False).locate(addrs, log=log)
    keys = {a: key(a) for a in addrs}
    want = {k for k in keys.values() if k}
    log(f'Домов: {len(addrs)}; реестр Реформы ЖКХ и кадастр')
    ref, cad = reforma(want), cadastre(want)
    o = osm(coords, osm_path, log)
    houses = {}
    for a in addrs:
        k = keys[a]
        r, g = ref.get(k) or {}, o.get(a) or {}
        if not r and not g and k not in cad:
            continue
        h, src = {}, {}
        for f in ('levels', 'flats', 'year'):                     # реестр точнее OSM, OSM — запасной
            h[f] = r.get(f) or g.get(f)
            if h[f]:
                src[f] = 'Реформа ЖКХ' if r.get(f) else 'OSM'
        for f in ('n_entrances', 'lifts', 'material', 'series'):  # только в реестре
            h[f] = r.get(f)
            if h[f] is not None:
                src[f] = 'Реформа ЖКХ'
        # пропуск: офисное здание; организации внутри жилого дома (офис на первом этаже) — не повод
        office = g.get('office', False) or (g.get('office_inside', False) and not r)
        h.update(entrances=g.get('entrances', []), office=office, gated=g.get('gated', False),
                 floors=None, starts=None)
        if k in cad:
            floors, h['starts'] = cad[k]
            h['floors'] = {str(apt): fl for apt, fl in floors.items()}
            src['floors'] = 'кадастр'
        h['src'] = src
        houses[a] = h
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(houses, open(out, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    n = len(addrs)
    share = lambda f: sum(1 for h in houses.values() if f(h))
    return dict(path=out, total=n, found=len(houses), levels=share(lambda h: h['levels']), lifts=share(lambda h: h['lifts'] is not None),
                floors=share(lambda h: h['floors']), entrances=share(lambda h: h['entrances']),
                ranges=share(lambda h: any(e['flats'] for e in h['entrances'])), office=share(lambda h: h['office']),
                gated=share(lambda h: h['gated']), year=share(lambda h: h['year']))
