"""Время и расстояние до точек, которых нет в кэше матриц (сервер без OSRM): клик по карте, точка разворота, новый
адрес. Берём ближайшие известные точки a* и b* (заявки дня и сетка вокруг них — site/bundle.py) и считаем
T(a, b) ≈ T(a*, b*) + подход от a к a* и от b* к b по прямой с поправкой на улицы. Чем гуще сетка, тем меньше ошибка:
при шаге 500 м — минута-две."""
import math

from .matrices import _key

M_LAT = 111320.0
DETOUR = 1.3                                      # улицы длиннее прямой
SPEED = {'car': 25.0, 'bike': 14.0, 'foot': 4.5, 'pt': 4.5}  # км/ч на коротких подходах (к соседу — пешком)


def dist_km(a, b):
    return math.hypot((a[0] - b[0]) * M_LAT, (a[1] - b[1]) * M_LAT * math.cos(math.radians(a[0]))) / 1000


class NearbyEstimate:
    """Подмена OsrmClient для MatrixBuilder: стоимость пар через ближайшие известные точки из store."""

    def __init__(self, stores):
        self.stores = stores                       # профиль → JsonStore с парами «lat,lon|lat,lon»
        self._points = {}

    def points(self, profile):
        if profile not in self._points:
            pts = set()
            for k in self.stores[profile].data:
                a, b = k.split('|')
                pts.add(tuple(map(float, a.split(','))))
                pts.add(tuple(map(float, b.split(','))))
            self._points[profile] = sorted(pts)
        return self._points[profile]

    def nearest(self, profile, p):
        return min(self.points(profile), key=lambda q: (q[0] - p[0]) ** 2 + ((q[1] - p[1]) * 0.56) ** 2)

    def table(self, profile, sources, destinations):
        store, v = self.stores[profile], SPEED[profile]
        out = []
        for a in sources:
            na = self.nearest(profile, a)
            row = []
            for b in destinations:
                nb = self.nearest(profile, b)
                da, db = dist_km(a, na) * DETOUR, dist_km(b, nb) * DETOUR
                core = store.get(f'{_key(na)}|{_key(nb)}') if na != nb else [0.0, 0.0]
                if profile == 'pt' and (core is None or core[0] == -2.0):
                    row.append((-2.0, -2.0))              # неизвестно — MatrixBuilder посчитает по формуле
                elif profile == 'pt' and not 0 <= core[0] < 1e5:
                    row.append((-1.0, -1.0))              # ближайшие точки по расписанию не связаны — не доехать
                elif core is None or not 0 <= core[0] < 1e5:
                    km = dist_km(a, b) * DETOUR
                    row.append((km / v * 60, km))
                else:
                    row.append((core[0] + (da + db) / v * 60, core[1] + da + db))
            out.append(row)
        return out
