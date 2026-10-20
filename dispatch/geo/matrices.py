"""Матрицы времени (мин) и расстояния (км) между точками по видам транспорта.

Машина, велосипед и пешком — OSRM; пары точек кэшируются в cache/matrix_<профиль>.json, так что для известных адресов
сервер не нужен. Общественный транспорт — по расписаниям (tools/transit.py: автобусы и трамваи из GTFS Мосгортранса,
метро, МЦК и МЦД по схеме Мосметро), среднее по отправлениям 10:00–22:00 с ожиданием; пешком, если так быстрее; пары
кэшируются в cache/matrix_pt.json. Если сеть не собрана (python -m dispatch transit), — оценка без расписаний: до 1,5 км
пешком, дальше 12 мин на подход и ожидание плюс дорога 20 км/ч (45 км/ч после 25 км) по машинному расстоянию.
Затем время умножается на коэффициент пробок, и к каждому переезду в заявку добавляется подход к двери.
"""
import os
from dataclasses import dataclass

from ..config import CACHE_DIR, MODES, TravelSettings
from .osrm import OsrmClient
from .store import JsonStore

UNREACHABLE = 1e6


def _key(p):
    return f'{p[0]:.5f},{p[1]:.5f}'


@dataclass
class TravelMatrices:
    time: dict        # вид транспорта → матрица минут
    dist: dict        # вид транспорта → матрица км
    hourly: bool = False          # Problem поправит машину и общ. транспорт на часы окна заявки
    offset: list = None           # минуты, прибавляемые ко всем выездам из стартовой точки i (перепланирование)
    pt_hours: list = None         # [i][j] — общ. транспорт по часам 10:00, 11:00, … (с подходом к заявке) или None
    day: int = None               # день недели (0 — понедельник) — пробки по профилю дня; None — средние будни
    overhead: dict = None         # вид транспорта → минуты подхода, уже прибавленные к поездкам к заявкам (пробки их не множат)


UNKNOWN = -2.0           # общ. транспорт: пары нет в расписании (оценка по формуле); −1 — по расписанию не доехать


def public_transport(car_km, foot_min, foot_km):
    """(минуты, км) на общественном транспорте по машинному и пешему пути."""
    if foot_km <= 1.5:
        return foot_min, foot_km
    ride = car_km / 20 * 60 if car_km < 25 else 25 / 20 * 60 + (car_km - 25) / 45 * 60
    t = 12 + ride
    return (foot_min, foot_km) if foot_min < t else (t, car_km)


class MatrixBuilder:
    def __init__(self, client=None, cache_dir=None, block=100, ext='.json', timetable=True, walk_osrm=True, readonly=False):
        self.client = client or OsrmClient()
        self.timetable = timetable           # считать общественный транспорт по расписаниям, если сеть собрана
        self.walk_osrm = walk_osrm           # подход к остановкам — по улицам через OSRM (иначе по прямой)
        self.cache_dir = cache_dir or CACHE_DIR
        self.ext = ext
        self.block = block
        self.readonly = readonly             # не дописывать кэш (сервер: оценки соседями не должны стать опорными точками)
        self._stores = {}

    def _store(self, profile):
        if profile not in self._stores:
            self._stores[profile] = JsonStore(os.path.join(self.cache_dir, f'matrix_{profile}{self.ext}'), readonly=self.readonly)
        return self._stores[profile]

    def transit(self, points, dc, tf, df):
        """Общественный транспорт: кэш → расписания (если сеть собрана) → оценка соседями на сервере → формула."""
        store = self._store('pt')
        pts = [(round(p[0], 5), round(p[1], 5)) for p in points]
        n = len(pts)
        key = lambda i, j: f'{_key(pts[i])}|{_key(pts[j])}'
        missing = [(i, j) for i in range(n) for j in range(n) if i != j and pts[i] != pts[j] and key(i, j) not in store]
        transit = None
        if missing and self.timetable:
            try:
                from ..tools import transit               # нужен numpy; без него и на сервере — без расписаний
            except ImportError:
                transit = None
        if transit is not None and transit.available():
            got = transit.times(pts, walk=transit.OsrmWalk(self.client) if self.walk_osrm else None, hourly=True)
            for i, j in missing:                              # [-1, -1] — по расписанию не доехать: только пешком
                if (i, j) in got:
                    mean, hours = got[i, j]
                    store.put(key(i, j), [mean, dc[i][j], [-1.0 if h is None else h for h in hours]])
                else:
                    store.put(key(i, j), [-1.0, -1.0])
            store.save()
        elif missing and 'pt' in getattr(self.client, 'stores', {}):
            rows = sorted({i for i, _ in missing})
            cols = sorted({j for _, j in missing})
            for i, line in zip(rows, self.client.table('pt', [pts[i] for i in rows], [pts[j] for j in cols])):
                for j, cell in zip(cols, line):
                    if i != j:
                        store.put(key(i, j), list(cell))
        T = [[0.0] * n for _ in range(n)]
        D = [[0.0] * n for _ in range(n)]
        H = [[None] * n for _ in range(n)]                    # по часам 10:00, 11:00, …; None — нет данных
        for i in range(n):
            for j in range(n):
                if i == j or pts[i] == pts[j]:
                    continue
                cell = store.get(key(i, j))
                if cell is None or cell[0] == UNKNOWN:      # расписания нет — оценка по расстоянию
                    T[i][j], D[i][j] = public_transport(dc[i][j], tf[i][j], df[i][j])
                    continue
                if cell[0] < 0:                             # по расписанию не доехать — только пешком
                    T[i][j], D[i][j] = tf[i][j], df[i][j]
                    continue
                if tf[i][j] <= cell[0]:
                    T[i][j], D[i][j] = tf[i][j], df[i][j]        # пешком быстрее
                else:
                    T[i][j], D[i][j] = cell[0], dc[i][j]
                if len(cell) > 2:
                    H[i][j] = [min(tf[i][j], cell[0] if h < 0 else h) for h in cell[2]]
        return T, D, H

    def profile(self, profile, points):
        """Матрицы одного профиля OSRM; недостающие пары запрашиваются блоками."""
        store = self._store(profile)
        pts = [(round(p[0], 5), round(p[1], 5)) for p in points]
        need = [(a, b) for a in pts for b in pts if a != b and f'{_key(a)}|{_key(b)}' not in store]
        if need:
            rows = sorted({a for a, _ in need})
            cols = sorted({b for _, b in need})
            for i in range(0, len(rows), self.block):
                for j in range(0, len(cols), self.block):
                    src, dst = rows[i:i + self.block], cols[j:j + self.block]
                    if all(f'{_key(s)}|{_key(d)}' in store for s in src for d in dst if s != d):
                        continue
                    for s, line in zip(src, self.client.table(profile, src, dst)):
                        for d, cell in zip(dst, line):
                            store.put(f'{_key(s)}|{_key(d)}', list(cell) if cell else [UNREACHABLE, UNREACHABLE])
            store.save()
        n = len(pts)
        T = [[0.0] * n for _ in range(n)]
        D = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if pts[i] != pts[j]:
                    T[i][j], D[i][j] = store.get(f'{_key(pts[i])}|{_key(pts[j])}')
        return T, D

    def build(self, points, n_starts, settings=None):
        """Матрицы для MODES. Точки 0..n_starts-1 — стартовые, надбавка на подход к ним не добавляется."""
        settings = settings or TravelSettings()
        (tc, dc), (tf, df), (tb, db) = (self.profile(p, points) for p in ('car', 'foot', 'bike'))
        tp, dp, hours = self.transit(points, dc, tf, df)
        T = {'car': tc, 'pt': tp, 'bike': tb, 'foot': tf}
        D = {'car': dc, 'pt': dp, 'bike': db, 'foot': df}
        for m in MODES:
            k, extra = settings.traffic.get(m, 1.0), settings.overhead.get(m, 0.0)
            if k != 1.0 or extra:
                T[m] = [[(k * t + (extra if j >= n_starts else 0.0)) if i != j else 0.0 for j, t in enumerate(row)]
                        for i, row in enumerate(T[m])]
        k, extra = settings.traffic.get('pt', 1.0), settings.overhead.get('pt', 0.0)   # по часам — та же поправка
        hours = [[[k * h + (extra if j >= n_starts else 0.0) for h in cell] if cell else None for j, cell in enumerate(row)]
                 for row in hours]
        return TravelMatrices(T, D, hourly=settings.hourly, pt_hours=hours, day=settings.weekday, overhead=dict(settings.overhead))
