"""Матрицы времени (мин) и расстояния (км) между точками по видам транспорта.

Машина, велосипед и пешком — OSRM; пары точек кэшируются в cache/matrix_<профиль>.json, так что для известных адресов
сервер не нужен. Общественный транспорт — оценка без расписаний: до 1,5 км пешком, дальше 12 мин на подход и ожидание
плюс дорога со скоростью 20 км/ч (45 км/ч после 25 км) по машинному расстоянию.
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


def public_transport(car_km, foot_min, foot_km):
    """(минуты, км) на общественном транспорте по машинному и пешему пути."""
    if foot_km <= 1.5:
        return foot_min, foot_km
    ride = car_km / 20 * 60 if car_km < 25 else 25 / 20 * 60 + (car_km - 25) / 45 * 60
    t = 12 + ride
    return (foot_min, foot_km) if foot_min < t else (t, car_km)


class MatrixBuilder:
    def __init__(self, client=None, cache_dir=None, block=100):
        self.client = client or OsrmClient()
        self.cache_dir = cache_dir or CACHE_DIR
        self.block = block
        self._stores = {}

    def _store(self, profile):
        if profile not in self._stores:
            self._stores[profile] = JsonStore(os.path.join(self.cache_dir, f'matrix_{profile}.json'))
        return self._stores[profile]

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
        n = len(points)
        tp = [[0.0] * n for _ in range(n)]
        dp = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j:
                    tp[i][j], dp[i][j] = public_transport(dc[i][j], tf[i][j], df[i][j])
        T = {'car': tc, 'pt': tp, 'bike': tb, 'foot': tf}
        D = {'car': dc, 'pt': dp, 'bike': db, 'foot': df}
        for m in MODES:
            k, extra = settings.traffic.get(m, 1.0), settings.overhead.get(m, 0.0)
            if k != 1.0 or extra:
                T[m] = [[(k * t + (extra if j >= n_starts else 0.0)) if i != j else 0.0 for j, t in enumerate(row)]
                        for i, row in enumerate(T[m])]
        return TravelMatrices(T, D)
