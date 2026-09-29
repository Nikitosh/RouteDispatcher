"""Клиент OSRM: матрицы (table) и линии маршрутов (route) по профилям car, bike, foot.
Адреса серверов — переменные OSRM_CAR, OSRM_BIKE, OSRM_FOOT (по умолчанию localhost:5003/5002/5001)."""
import json
import os
import time
import urllib.request

DEFAULT_URLS = {'car': 'http://localhost:5003', 'bike': 'http://localhost:5002', 'foot': 'http://localhost:5001'}


class OsrmError(Exception):
    pass


def profile_of(mode):
    """Общественный транспорт считаем по машинным дорогам."""
    return 'car' if mode == 'pt' else mode


class OsrmClient:
    def __init__(self, urls=None, timeout=90, retries=3):
        self.urls = urls or {p: os.environ.get(f'OSRM_{p.upper()}', u) for p, u in DEFAULT_URLS.items()}
        self.timeout, self.retries = timeout, retries

    def _get(self, url):
        last = None
        for attempt in range(self.retries):
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'dispatch/1.0'}), timeout=self.timeout))
                if r.get('code') == 'Ok':
                    return r
                last = r.get('code')
            except OSError as e:
                last = e
            time.sleep(1 + 2 * attempt)
        raise OsrmError(f'OSRM недоступен ({url.split("/")[2]}): {last}')

    def table(self, profile, sources, destinations):
        """(минуты, км) для всех пар sources × destinations; недостижимые пары — None."""
        pts = list(dict.fromkeys(sources + destinations))
        idx = {p: i for i, p in enumerate(pts)}
        coords = ';'.join(f'{p[1]:.6f},{p[0]:.6f}' for p in pts)
        url = (f'{self.urls[profile]}/table/v1/driving/{coords}?sources={";".join(str(idx[p]) for p in sources)}'
               f'&destinations={";".join(str(idx[p]) for p in destinations)}&annotations=duration,distance')
        r = self._get(url)
        return [[None if r['durations'][i][j] is None else (r['durations'][i][j] / 60, r['distances'][i][j] / 1000)
                 for j in range(len(destinations))] for i in range(len(sources))]

    def route(self, profile, a, b):
        """Линия по улицам от a до b: [[lat, lon], ...]."""
        url = f'{self.urls[profile]}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=full&geometries=geojson'
        r = self._get(url)
        return [[round(y, 5), round(x, 5)] for x, y in r['routes'][0]['geometry']['coordinates']]
