"""Калибровка коэффициента пробок по Яндексу.

1. python -m dispatch calibrate sample <заявки> -o pairs.csv — случайные пары адресов участка, время OSRM без пробок и ссылка на
   маршрут в Яндекс Картах. Колонку «Яндекс, мин» заполняют вручную (в Картах выбрать «Отправление» в будни 14:00)
   или ключом API Матрицы расстояний Яндекса (--apikey, запрос с departure_time на ближайший будний день 14:00).
2. python -m dispatch calibrate fit pairs.csv — коэффициент = медиана отношения Яндекс / OSRM и прямая Яндекс ≈ a · OSRM + b.
"""
import csv
import datetime
import json
import random
import statistics
import urllib.request

from ..config import TravelSettings
from ..files.readers import read_orders
from ..geo.geocoder import Geocoder
from ..geo.matrices import MatrixBuilder


def _weekday_14():
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() >= 5: d += datetime.timedelta(days=1)
    return int(datetime.datetime.combine(d, datetime.time(14, 0)).timestamp())


def _yandex(apikey, a, b, t):
    url = (f'https://api.routing.yandex.net/v2/distancematrix?origins={a[0]},{a[1]}&destinations={b[0]},{b[1]}'
           f'&mode=driving&departure_time={t}&apikey={apikey}')
    r = json.load(urllib.request.urlopen(url, timeout=20))
    e = r['rows'][0]['elements'][0]
    return e['duration']['value'] / 60 if e.get('status') == 'OK' else None


def sample(orders_path, out, n=30, seed=1, apikey=None, log=print):
    orders = read_orders(orders_path).orders
    coords = Geocoder().locate([o.address for o in orders], log=log)
    pts = list(dict.fromkeys(coords[o.address] for o in orders))
    m = MatrixBuilder().build(pts, 0, TravelSettings(traffic={}, overhead={}))      # чистый OSRM, без пробок и подхода
    T, D = m.time, m.dist
    rnd = random.Random(seed); pairs = [(i, j) for i in range(len(pts)) for j in range(len(pts)) if 1.0 <= D['car'][i][j] <= 15]
    rnd.shuffle(pairs); pairs = pairs[:n]; t = _weekday_14()
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['Откуда', 'Куда', 'Км', 'OSRM, мин', 'Яндекс, мин', 'Ссылка'])
        for i, j in pairs:
            a, b = pts[i], pts[j]
            y = ''
            if apikey:
                try: y = f'{_yandex(apikey, a, b, t):.1f}'
                except Exception as e: log(f'Яндекс: {e}'); apikey = None
            w.writerow([f'{a[0]:.6f},{a[1]:.6f}', f'{b[0]:.6f},{b[1]:.6f}', f"{D['car'][i][j]:.2f}", f"{T['car'][i][j]:.1f}", y,
                        f'https://yandex.ru/maps/?rtext={a[0]},{a[1]}~{b[0]},{b[1]}&rtt=auto'])
    return out, len(pairs)


def fit(path):
    rows = [r for r in csv.DictReader(open(path, encoding='utf-8-sig'), delimiter=';') if r.get('Яндекс, мин', '').strip()]
    if len(rows) < 5: raise ValueError(f'в {path} заполнено меньше 5 строк «Яндекс, мин»')
    x = [float(r['OSRM, мин'].replace(',', '.')) for r in rows]; y = [float(r['Яндекс, мин'].replace(',', '.')) for r in rows]
    ratio = statistics.median(b / a for a, b in zip(x, y))
    mx, my = statistics.mean(x), statistics.mean(y)
    a = sum((p - mx) * (q - my) for p, q in zip(x, y)) / sum((p - mx) ** 2 for p in x); b = my - a * mx
    return dict(n=len(rows), ratio=ratio, slope=a, intercept=b)
