"""Линии маршрутов по улицам. Кэш cache/geometry.json (Google polyline); без OSRM показываются линии из кэша,
а для отсутствующих участков — прямой отрезок."""
import os
from concurrent.futures import ThreadPoolExecutor

from ..config import CACHE_DIR
from . import polyline
from .osrm import OsrmClient, OsrmError, profile_of
from .store import JsonStore


class RouteGeometry:
    def __init__(self, client=None, path=None, online=True):
        self.client = client or OsrmClient(timeout=5, retries=1)
        self.store = JsonStore(path or os.path.join(CACHE_DIR, 'geometry.json'))
        self.online = online

    @staticmethod
    def _key(profile, a, b):
        return f'{profile}|{a[0]:.5f},{a[1]:.5f}|{b[0]:.5f},{b[1]:.5f}'

    def leg(self, mode, a, b):
        """Линия участка a → b или None, если её нет в кэше и OSRM недоступен."""
        if (round(a[0], 5), round(a[1], 5)) == (round(b[0], 5), round(b[1], 5)):
            return [list(a), list(b)]
        profile = profile_of(mode)
        key = self._key(profile, a, b)
        if key in self.store:
            v = self.store.get(key)
            return polyline.decode(v) if isinstance(v, str) else v
        if not self.online:
            return None
        try:
            line = self.client.route(profile, a, b)
        except OsrmError:
            self.online = False               # не ждать таймаута на каждом участке
            return None
        self.store.put(key, polyline.encode(line))
        return line

    def route(self, mode, points):
        """Линии по участкам маршрута; None, если хотя бы одного участка нет."""
        legs = [self.leg(mode, a, b) for a, b in zip(points, points[1:])]
        return None if any(g is None for g in legs) else legs

    def prefetch(self, points, modes, workers=8):
        """Линии для всех упорядоченных пар точек — чтобы страница и презентация работали без OSRM."""
        todo = [(p, a, b) for p in sorted({profile_of(m) for m in modes}) for a in points for b in points
                if a != b and self._key(p, a, b) not in self.store]
        with ThreadPoolExecutor(workers) as ex:
            got = list(ex.map(lambda t: self.leg(*t), todo))
        self.save()
        return len(todo), sum(g is None for g in got)

    def save(self):
        self.store.save()
