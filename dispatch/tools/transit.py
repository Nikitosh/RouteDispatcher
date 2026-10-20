"""Сеть общественного транспорта для матриц «Общ. транспорт» (python -m dispatch transit).

Источники (data/transit/, не в git):
- gtfs/latest.zip — GTFS Мосгортранса (Mobility Database, mdb-3226): автобусы и трамваи по настоящему расписанию;
- metro/schema.json — схема Мосметро (prodapp.mosmetro.ru/api/schema/v1.0): станции метро, МЦК и МЦД, время перегонов
  и пересадок. Поезда строим сами: по линии от конечной до конечной с интервалом по часу дня (HEADWAY);
- yandex/ — расписания МЦД и электричек из Яндекс Расписаний (tools/rasp.py), если загружены: тогда поезда МЦД — по
  настоящему расписанию, а не по интервалу, плюс электрички вне МЦД;
- mo/routes.json — автобусы области из OpenStreetMap (tools/osm_bus.py), рейсы по интервалу.

Сеть — «связи» (остановка → следующая остановка одного рейса, отправление, прибытие) и пешие переходы между
остановками до FOOT_RADIUS. Время от точки до точки считает solver/bin/transit (алгоритм CSA, Connection Scan) для
отправлений каждые STEP минут с 10:00 до 21:40: среднее за день и по часам, с ожиданием транспорта. Подход от точки к
остановкам — по улицам (OsrmWalk), если OSRM доступен. Если пешком быстрее — пешком (geo/matrices.py).
"""
import csv
import io
import json
import math
import os
import re
import subprocess
import tempfile
import zipfile
from collections import defaultdict

import numpy as np

from ..config import ROOT, SOLVER_BIN

DIR = os.path.join(ROOT, 'data', 'transit')
DAY_FROM, DAY_TO = 8 * 3600, 24 * 3600          # связи с отправлением в этом промежутке
WALK = 1.25                                      # м/с: 4,5 км/ч
DETOUR = 1.25                                    # улицы длиннее прямой
ACCESS_RADIUS = 1200                             # м: от точки до остановки пешком
FOOT_RADIUS = 300                                # м: пересадка между остановками пешком
METRO_IN = 120                                   # с: от входа до платформы и от платформы до выхода
RAIL_IN = 60                                     # с: на платформу электрички и с неё
DUPLICATE = 150                                  # м: остановка OSM совпадает с остановкой GTFS
STEP = 20                                        # мин между отправлениями при расчёте матрицы
MAX_RIDE = 3 * 3600                              # с: дальше не ищем
# интервал движения, мин, по часу отправления (будни); вне таблицы — последний известный
HEADWAY = {
    'metro': {6: 3, 7: 2, 10: 2.5, 17: 2, 20: 3, 22: 4, 23: 6},
    'МЦК': {6: 6, 7: 5, 10: 6, 17: 5, 20: 7, 22: 10},
    'МЦД': {6: 10, 7: 6, 10: 10, 17: 6, 20: 10, 22: 15},
}
M_LAT = 111320.0


def _sec(text):
    h, m, s = text.split(':')
    return int(h) * 3600 + int(m) * 60 + int(s)


def _xy(lat, lon):
    return lat * M_LAT, lon * M_LAT * math.cos(math.radians(55.75))


class Grid:
    """Поиск точек в радиусе по квадратной сетке."""

    def __init__(self, coords, cell):
        self.cell, self.coords, self.cells = cell, coords, defaultdict(list)
        for i, (lat, lon) in enumerate(coords):
            x, y = _xy(lat, lon)
            self.cells[int(x // cell), int(y // cell)].append(i)

    def near(self, lat, lon, radius):
        x, y = _xy(lat, lon)
        cx, cy, r = int(x // self.cell), int(y // self.cell), int(radius // self.cell) + 1
        for i in range(cx - r, cx + r + 1):
            for j in range(cy - r, cy + r + 1):
                for k in self.cells.get((i, j), ()):
                    a, b = _xy(*self.coords[k])
                    d = math.hypot(a - x, b - y)
                    if d <= radius:
                        yield k, d


def _headway(kind, hour):
    table = HEADWAY[kind]
    return table[max([h for h in table if h <= hour], default=min(table))]


class Network:
    def __init__(self):
        self.stops = []                  # (lat, lon, имя, вид: bus | metro)
        self.conn = []                   # массивы numpy по частям: [откуда, куда, отправление, прибытие, рейс]
        self.trips = 0
        self.foot = []                   # (a, b, секунды)
        self.served = set()              # остановки GTFS, через которые есть рейсы

    def add_stop(self, lat, lon, name, kind):
        self.stops.append((lat, lon, name, kind))
        return len(self.stops) - 1

    # ---- GTFS Мосгортранса
    def load_gtfs(self, path, weekday='tuesday', log=print):
        z = zipfile.ZipFile(path)
        rd = lambda name: csv.reader(io.TextIOWrapper(z.open(name), encoding='utf-8-sig'))
        r = rd('calendar.txt')
        head = next(r)
        services = {row[head.index('service_id')] for row in r if row[head.index(weekday)] == '1'}
        r = rd('trips.txt')
        head = next(r)
        trips = {}
        for row in r:
            if row[head.index('service_id')] in services:
                trips[row[head.index('trip_id')]] = len(trips)
        r = rd('stops.txt')
        head = next(r)
        stop_idx = {}
        for row in r:
            lat, lon = float(row[head.index('stop_lat')]), float(row[head.index('stop_lon')])
            stop_idx[row[head.index('stop_id')]] = self.add_stop(lat, lon, row[head.index('stop_name')], 'bus')
        log(f'GTFS: рейсов в будни {len(trips)}, остановок {len(stop_idx)}; читаем stop_times…')
        r = rd('stop_times.txt')
        head = next(r)
        it, ia, idp, ist, isq = (head.index(c) for c in ('trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'))
        cols, missing = [[], [], [], [], []], 0
        for row in r:
            t = trips.get(row[it])
            if t is None:
                continue
            if row[ist] not in stop_idx:
                missing += 1
                continue
            cols[0].append(t)
            cols[1].append(int(row[isq]))
            cols[2].append(_sec(row[ia]))
            cols[3].append(_sec(row[idp]))
            cols[4].append(stop_idx[row[ist]])
        trip, seq, arr, dep, stop = (np.array(c, dtype=np.int64) for c in cols)
        order = np.lexsort((seq, trip))
        trip, arr, dep, stop = trip[order], arr[order], dep[order], stop[order]
        same = trip[:-1] == trip[1:]
        c = np.stack([stop[:-1], stop[1:], dep[:-1], arr[1:], trip[:-1] + self.trips], axis=1)[same]
        c = c[(c[:, 2] >= DAY_FROM) & (c[:, 2] < DAY_TO) & (c[:, 3] >= c[:, 2])]
        self.conn.append(c)
        self.served.update(np.unique(c[:, :2]).tolist())
        self.trips += len(trips)
        log(f'GTFS: связей {len(c)} с {DAY_FROM // 3600}:00 до {DAY_TO // 3600}:00; строк с неизвестной остановкой {missing}')

    # ---- метро, МЦК, МЦД по схеме Мосметро
    def load_metro(self, path, timetable=None, log=print):
        """timetable — поезда МЦД из Яндекс Расписаний (mcd_timetable): такие линии строим по расписанию, остальные —
        по интервалу HEADWAY."""
        d = json.load(open(path, encoding='utf-8'))['data']
        lines = {l['id']: l['name']['ru'] for l in d['lines']}
        idx = {}
        for s in d['stations']:
            idx[s['id']] = self.add_stop(s['location']['lat'], s['location']['lon'], s['name']['ru'], 'metro')
        adj, run = defaultdict(set), {}
        for c in d['connections']:
            a, b = c['stationFromId'], c['stationToId']
            if a not in idx or b not in idx:
                continue
            adj[a].add(b)
            run[a, b] = c['pathLength']
            if c.get('bi', True) and not c.get('closedBackward'):
                adj[b].add(a)
                run[b, a] = c['pathLength']
        for t in d['transitions']:
            a, b = t['stationFromId'], t['stationToId']
            if a in idx and b in idx:
                self.foot.append((idx[a], idx[b], t['pathLength']))
                if t.get('bi', True):
                    self.foot.append((idx[b], idx[a], t['pathLength']))
        by_line = defaultdict(list)
        for s in d['stations']:
            by_line[s['lineId']].append(s['id'])
        rows, trips, real = [], 0, []
        names = {s['id']: _norm(s['name']['ru']) for s in d['stations']}
        for line, stations in by_line.items():
            name = lines.get(line, '')
            kind = 'МЦК' if name == 'МЦК' else 'МЦД' if name.startswith('МЦД') else 'metro'
            own = set(stations)
            g = {s: [x for x in adj[s] if x in own] for s in stations}
            by_name = {names[s]: s for s in stations}
            trains = [t for t in (timetable or {}).get(name, []) if t['from'] in by_name and t['to'] in by_name]
            if trains:                                   # МЦД по расписанию Яндекса
                real.append(f'{name} ({len(trains)} поездов)')
                for t in trains:
                    path = _bfs(g, by_name[t['from']], by_name[t['to']])
                    if not path:
                        continue
                    legs = [run[a, b] for a, b in zip(path, path[1:])]
                    scale = t['duration'] / max(1, sum(legs))   # время перегонов схемы → время этого поезда
                    stop = [i for i, s in enumerate(path) if i in (0, len(path) - 1) or _stops_at(t, names[s])]
                    cum = [0.0]
                    for x in legs:
                        cum.append(cum[-1] + x * scale)
                    for i, j in zip(stop, stop[1:]):
                        rows.append((idx[path[i]], idx[path[j]], int(t['dep'] + cum[i]), int(t['dep'] + cum[j]), self.trips + trips))
                    trips += 1
                continue
            paths = _line_paths(g)
            for path in paths:
                legs = [run[a, b] for a, b in zip(path, path[1:])]
                t = 6 * 3600
                while t < DAY_TO:
                    h = _headway(kind, t // 3600) * 60 * len(paths) / (2 if len(paths) > 1 else 1)
                    clock = t
                    for (a, b), sec in zip(zip(path, path[1:]), legs):
                        rows.append((idx[a], idx[b], clock, clock + sec, self.trips + trips))
                        clock += sec                          # время перегона в схеме — со стоянкой
                    trips += 1
                    t += h
        c = np.array(rows, dtype=np.int64)
        c = c[(c[:, 2] >= DAY_FROM) & (c[:, 2] < DAY_TO)]
        self.conn.append(c)
        self.trips += trips
        log(f'Метро, МЦК, МЦД: станций {len(idx)}, поездов {trips}, связей {len(c)}; по расписанию: '
            + (', '.join(real) or 'нет (интервалы HEADWAY)'))

    # ---- автобусы области (OpenStreetMap, рейсы по интервалу — tools/osm_bus.py)
    def load_osm_bus(self, routes, log=print):
        """Маршруты, у которых больше половины остановок в DUPLICATE м от остановок GTFS с рейсами, — это Москва, она
        уже есть по расписанию; их пропускаем."""
        from . import osm_bus
        served = Grid([(self.stops[i][0], self.stops[i][1]) for i in sorted(self.served)], DUPLICATE)
        idx, rows, n, skipped = {}, [], 0, 0
        for route in routes:
            st = route['stops']
            dup = sum(1 for lat, lon, _ in st if next(served.near(lat, lon, DUPLICATE), None) is not None)
            if dup * 2 > len(st):
                skipped += 1
                continue
            ids = []
            for lat, lon, name in st:
                key = (round(lat, 5), round(lon, 5))
                if key not in idx:
                    idx[key] = self.add_stop(lat, lon, name, 'bus')
                ids.append(idx[key])
            for trip in osm_bus.trips(route):
                for (a, _, dep), (b, arr, _) in zip(trip, trip[1:]):
                    rows.append((ids[a], ids[b], dep, arr, self.trips + n))
                n += 1
        if rows:
            c = np.array(rows, dtype=np.int64)
            self.conn.append(c[(c[:, 2] >= DAY_FROM) & (c[:, 2] < DAY_TO)])
        self.trips += n
        log(f'Автобусы области (OSM): маршрутов {len(routes) - skipped} (ещё {skipped} — Москва, есть в GTFS), '
            f'остановок {len(idx)}, рейсов {n}, связей {len(rows)}')

    # ---- электрички вне МЦД (Яндекс Расписания)
    def load_rail(self, trains, coords, log=print):
        """trains — [(uid, [(код станции, прибытие, отправление), …])] из suburban_trains; coords — {код: (lat, lon, имя)}."""
        idx, rows, n = {}, [], 0
        for uid, stops in trains:
            for (a, _, dep), (b, arr, _) in zip(stops, stops[1:]):
                for c in (a, b):
                    if c not in idx:
                        lat, lon, name = coords[c]
                        idx[c] = self.add_stop(lat, lon, name, 'rail')
                rows.append((idx[a], idx[b], dep, arr, self.trips + n))
            n += 1
        if rows:
            c = np.array(rows, dtype=np.int64)
            c = c[(c[:, 2] >= DAY_FROM) & (c[:, 2] < DAY_TO) & (c[:, 3] >= c[:, 2])]
            self.conn.append(c)
        self.trips += n
        log(f'Электрички вне МЦД: станций {len(idx)}, поездов {n}, связей {len(rows)}')

    # ---- пешие пересадки
    def link_stops(self, log=print):
        grid = Grid([(s[0], s[1]) for s in self.stops], FOOT_RADIUS)
        n = 0
        for i, (lat, lon, _, kind) in enumerate(self.stops):
            for j, d in grid.near(lat, lon, FOOT_RADIUS):
                if j != i:
                    extra = METRO_IN if (kind == 'metro') != (self.stops[j][3] == 'metro') else 0
                    extra += RAIL_IN if (kind == 'rail') != (self.stops[j][3] == 'rail') else 0
                    if kind == 'metro' and self.stops[j][3] == 'metro':
                        continue                              # между станциями — только пересадки из схемы
                    self.foot.append((i, j, int(d * DETOUR / WALK) + extra))
                    n += 1
        log(f'Пешие пересадки: {n} пар до {FOOT_RADIUS} м и {len(self.foot) - n} пересадок метро')

    def save(self, folder):
        os.makedirs(folder, exist_ok=True)
        c = np.concatenate(self.conn)
        c = c[np.argsort(c[:, 2], kind='stable')]
        c.astype(np.int32).tofile(os.path.join(folder, 'conn.bin'))
        f = np.array(sorted(set(self.foot)), dtype=np.int32).reshape(-1, 3)
        f.tofile(os.path.join(folder, 'foot.bin'))
        json.dump(dict(stops=self.stops, trips=self.trips, connections=len(c), foot=len(f)),
                  open(os.path.join(folder, 'stops.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        return len(c), len(f)


def _norm(name):
    """Название станции для сравнения схемы Мосметро и Яндекса: «Москва-Рижская», «Бирюлёво-Пасс.» → «рижская», …"""
    n = re.sub(r'\(.*?\)', '', name.lower().replace('ё', 'е')).replace('москва-', '').replace('пасс.', '')
    return re.sub(r'[^а-яa-z0-9]', '', n)


def _stops_at(train, station):
    if train['skip'] is not None:
        return station not in train['skip']
    return station in train['only']


def mcd_timetable(folder=None, schema=None):
    """Поезда МЦД из сохранённых ответов Яндекс Расписаний (search между конечными, tools/rasp.py):
    {«МЦД-2»: [{dep, duration (с), from, to, skip | only}]}. Описание остановок: «везде», «кроме: …» или список;
    сложные («до X везде, кроме …») — как «везде»."""
    folder = folder or os.path.join(DIR, 'yandex')
    schema = schema or os.path.join(DIR, 'metro', 'schema.json')
    if not os.path.isdir(folder) or not os.path.exists(schema):
        return {}
    d = json.load(open(schema, encoding='utf-8'))['data']
    line_of = defaultdict(set)
    lines = {l['id']: l['name']['ru'] for l in d['lines']}
    for s in d['stations']:
        if lines.get(s['lineId'], '').startswith('МЦД'):
            line_of[_norm(s['name']['ru'])].add(lines[s['lineId']])
    out, seen = defaultdict(list), set()
    for path in sorted(os.listdir(folder)):
        if not path.startswith('search_'):
            continue
        r = json.load(open(os.path.join(folder, path), encoding='utf-8'))
        for s in r['response'].get('segments', []):
            a, b = _norm(s['from']['title']), _norm(s['to']['title'])
            common = line_of.get(a, set()) & line_of.get(b, set())
            if not common or (s['thread']['uid'], a) in seen:
                continue
            seen.add((s['thread']['uid'], a))
            h, m = s['departure'][11:16].split(':')
            text = s.get('stops') or 'везде'
            train = dict(dep=int(h) * 3600 + int(m) * 60, duration=s['duration'], from_=a, skip=None, only=None,
                         uid=s['thread']['uid'])
            train['from'], train['to'] = train.pop('from_'), b
            if text == 'везде' or ('везде' in text and not text.startswith('кроме')):
                train['skip'] = set()
            elif text.startswith('кроме:'):
                train['skip'] = {_norm(x) for x in text[6:].split(',')}
            else:
                train['only'] = {_norm(x) for x in text.split(',')}
            out[min(common)].append(train)
    return dict(out)


def _sec_of(stamp):
    """«2026-10-20 10:05:00» или «2026-10-20T10:05:00+03:00» → секунды от полуночи."""
    h, m, s = stamp[11:19].split(':')
    return int(h) * 3600 + int(m) * 60 + int(s)


def _thread_offsets(stops):
    """[(код, название, секунд от отправления с первой станции)] по остановкам thread; через полночь — +сутки."""
    out, start, day, last = [], _sec_of(stops[0]['departure']), 0, None
    for s in stops:
        t = _sec_of(s['departure'] or s['arrival'])
        if last is not None and t + day < last:
            day += 86400
        last = t + day
        out.append((s['station']['code'], s['station']['title'], last - start))
    return out


def _stopping(text, names):
    """Где поезд останавливается по описанию Яндекса: «везде», «кроме: …», список; сложные — везде."""
    if not text or text == 'везде' or ('везде' in text and not text.startswith('кроме')):
        return [True] * len(names)
    if text.startswith('кроме:'):
        skip = {_norm(x) for x in text[6:].split(',')}
        return [n not in skip for n in names]
    only = {_norm(x) for x in text.split(',')}
    return [n in only for n in names]


def suburban_trains(r, skip_uids=()):
    """Поезда электричек по сохранённым ответам Яндекса (tools/rasp.py, SUBURBAN): [(uid, [(код, прибытие, отправление)])].
    Остановки между концами участка и время хода — из сохранённого thread, в котором есть оба конца (время
    масштабируется к ходу этого поезда); если такого нет — поезд идёт от конца до конца без остановок. Поезда МЦД
    (skip_uids) пропускаем: они уже есть по схеме Мосметро. Один поезд на нескольких участках — один рейс."""
    from . import rasp
    threads = []
    for path in sorted(os.listdir(r.folder)) if os.path.isdir(r.folder) else []:
        if path.startswith('thread_'):
            t = json.load(open(os.path.join(r.folder, path), encoding='utf-8'))['response']
            threads.append(_thread_offsets(t['stops']))
    by_uid = defaultdict(dict)                     # uid → {код: (прибытие, отправление)}
    for parts in rasp.SUBURBAN.values():
        for a, b in parts:
            for f, t in ((a, b), (b, a)):
                for s in rasp.search(r, f, t).get('segments', []):
                    uid = s['thread']['uid']
                    if uid in skip_uids:
                        continue
                    dep = _sec_of(s['departure'])
                    line = next((th for th in threads if {f, t} <= {c for c, _, _ in th}), None)
                    if line is None:
                        path = [(f, f, 0.0), (t, t, 1.0)]
                    else:
                        codes = [c for c, _, _ in line]
                        i, j = codes.index(f), codes.index(t)
                        seg = line[i:j + 1] if i < j else line[j:i + 1][::-1]
                        x0, x1 = seg[0][2], seg[-1][2]
                        path = [(c, n, (x - x0) / (x1 - x0) if x1 != x0 else 0.0) for c, n, x in seg]
                    stop = _stopping(s.get('stops'), [_norm(n) for _, n, _ in path])
                    for k, ((c, _, share), on) in enumerate(zip(path, stop)):
                        if on or k in (0, len(path) - 1):
                            t_at = dep + share * s['duration']
                            by_uid[uid][c] = (int(t_at), int(t_at))
    out = []
    for uid, st in by_uid.items():
        seq = sorted(((t, c) for c, (t, _) in st.items()))
        out.append((uid, [(c, t, t) for t, c in seq]))
    return out


def _line_paths(g):
    """Пути поездов по графу станций одной линии: между конечными (станции с одним соседом); кольцо — по кругу в
    обе стороны."""
    ends = [s for s, nb in g.items() if len(nb) == 1]
    if not ends:                                       # кольцо
        start = next(iter(g))
        path, prev = [start], None
        while True:
            nxt = next((x for x in g[path[-1]] if x != prev and (len(path) < 2 or x != path[-2])), None)
            if nxt is None or nxt == start:
                break
            prev = path[-1]
            path.append(nxt)
        loop = path + [start]
        return [loop, loop[::-1]]
    out = []
    for i, a in enumerate(ends):
        for b in ends:
            if a != b:
                p = _bfs(g, a, b)
                if p:
                    out.append(p)
    return out


def _bfs(g, a, b):
    prev, queue = {a: None}, [a]
    for x in queue:
        if x == b:
            break
        for y in g[x]:
            if y not in prev:
                prev[y] = x
                queue.append(y)
    if b not in prev:
        return None
    path = [b]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    return path[::-1]


def build_network(folder=None, log=print):
    folder = folder or DIR
    net = Network()
    net.load_gtfs(os.path.join(folder, 'gtfs', 'latest.zip'), log=log)
    schema = os.path.join(folder, 'metro', 'schema.json')
    mcd = mcd_timetable(os.path.join(folder, 'yandex'), schema)
    net.load_metro(schema, timetable=mcd, log=log)
    from . import rasp
    r = rasp.Rasp(folder=os.path.join(folder, 'yandex'), offline=True)
    trains = suburban_trains(r, skip_uids={t['uid'] for ts in mcd.values() for t in ts})
    if trains:
        net.load_rail(trains, rasp.stations(r), log=log)
    from . import osm_bus
    net.load_osm_bus(osm_bus.load(folder, log=log), log=log)
    net.link_stops(log=log)
    c, f = net.save(os.path.join(folder, 'net'))
    log(f'Сеть: остановок {len(net.stops)}, связей {c}, пеших переходов {f} → {folder}/net')


# ---- матрицы для точек
def available(folder=None):
    folder = folder or DIR
    return os.path.exists(os.path.join(folder, 'net', 'conn.bin')) and os.path.exists(os.path.join(SOLVER_BIN, 'transit'))


class OsrmWalk:
    """Время пешком от точки до остановок по улицам (OSRM, профиль foot); кэш — data/transit/walk.json.
    Если OSRM недоступен — None, и подход считается по прямой."""

    def __init__(self, client=None, path=None):
        from ..geo.osrm import OsrmClient
        from ..geo.store import JsonStore
        self.client, self.dead = client or OsrmClient(), False
        self.store = JsonStore(path or os.path.join(DIR, 'walk.json'))

    def __call__(self, p, targets):
        key = lambda q: f'{p[0]:.5f},{p[1]:.5f}|{q[0]:.5f},{q[1]:.5f}'
        need = [q for q in targets if key(q) not in self.store]
        if need and not self.dead:
            try:
                for i in range(0, len(need), 99):
                    chunk = need[i:i + 99]
                    for q, cell in zip(chunk, self.client.table('foot', [p], chunk)[0]):
                        self.store.put(key(q), cell[0] * 60 if cell else -1)
            except Exception:
                self.dead = True
        out = [self.store.get(key(q)) for q in targets]
        return [x if x is not None and x >= 0 else None for x in out]

    def save(self):
        self.store.save()


def access(points, stops, walk=None):
    """Для каждой точки — остановки в радиусе ACCESS_RADIUS с временем подхода (к метро и электричке — плюс вход).
    walk(точка, [остановки]) → секунды по улицам или None; без него — по прямой с поправкой DETOUR."""
    grid = Grid([(s[0], s[1]) for s in stops], ACCESS_RADIUS)
    out = []
    for lat, lon in points:
        near = list(grid.near(lat, lon, ACCESS_RADIUS))
        street = walk((lat, lon), [(stops[j][0], stops[j][1]) for j, _ in near]) if walk and near else [None] * len(near)
        row = []
        for (j, d), sec in zip(near, street):
            sec = d * DETOUR / WALK if sec is None else sec
            if sec <= ACCESS_RADIUS * DETOUR / WALK:
                row.append((j, int(sec) + {'metro': METRO_IN, 'rail': RAIL_IN}.get(stops[j][3], 0)))
        out.append(row)
    if walk and hasattr(walk, 'save'):
        walk.save()
    return out


def times(points, folder=None, start=10 * 60, end=22 * 60, step=STEP, binary=None, walk=None, hourly=False):
    """{(i, j): среднее время в минутах по отправлениям} между точками (без пешего варианта напрямую). hourly —
    {(i, j): (среднее, [среднее по часам с start; None — в этот час не доехать])}."""
    net = os.path.join(folder or DIR, 'net')
    stops = json.load(open(os.path.join(net, 'stops.json'), encoding='utf-8'))['stops']
    acc = access(points, stops, walk)
    deps = list(range(start * 60, end * 60, step * 60))
    with tempfile.TemporaryDirectory() as tmp:
        q = os.path.join(tmp, 'query.txt')
        with open(q, 'w') as f:
            f.write(f'{len(stops)} {len(points)} {len(deps)} {MAX_RIDE}\n')
            f.write(' '.join(map(str, deps)) + '\n')
            for a in acc:
                f.write(f'{len(a)} ' + ' '.join(f'{s} {t}' for s, t in a) + '\n')
        p = subprocess.run([binary or os.path.join(SOLVER_BIN, 'transit'), os.path.join(net, 'conn.bin'),
                            os.path.join(net, 'foot.bin'), q], capture_output=True, text=True, check=True)
    rows = p.stdout.split('\n')
    n = len(points)
    H = (deps[-1] - deps[0]) // 3600 + 1
    out = {}
    for i in range(n):
        vals = rows[i].split()
        for j in range(n):
            cell = [float(x) for x in vals[j * (H + 1):(j + 1) * (H + 1)]]
            if i != j and cell[0] >= 0:
                out[i, j] = (cell[0], [x if x >= 0 else None for x in cell[1:]]) if hourly else cell[0]
    return out
