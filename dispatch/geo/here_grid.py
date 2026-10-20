"""Пробки по сетке ~330 м (клетка 334 × 313 м): доля загруженности клетки от средней по городу в тот же час (10:00…21:00), как у районов
config.ZONES, но мелко. Посчитано по зондам HERE за 13 будних дней 2018 года (Traffic4cast 2019,
research/traffic/scripts/t4c_accumulate.py → build_here_grid.py). Данные HERE — только для обучения и
некоммерческих исследований; сетка (data/traffic/here_grid.npz) лежит в репозитории для такого использования. Без
файла (или без numpy) load() даёт None, и вызывающий код остаётся на районах config.zone_of.

Поездка из a в b — прямая a→b, точки на ней через каждые ~300 м; доля поездки — среднее долей клеток с весом клетки.
Доля клетки уже сглажена по коридору ~650 м с весом машино-минут (прямая заменяет маршрут по соседним улицам), вес
клетки — корень из машино-минут её окрестности: ширина коридора и вес выбраны по ошибке против долей, посчитанных по
настоящим маршрутам OSRM (research/traffic/scripts/eval_here_grid_routes.py). Точки прямой вне сетки берут долю
района config.zone_of с весом обычной клетки. Итог обрезается до [0,3; 3]. Направление поездки не учитывается: в
данных одна скорость на клетку для всех направлений. path_shares — доля по известной линии маршрута; в плане не
используется (линии строятся после решения), им проверяли, насколько прямая близка к маршруту.

Путь к файлу: аргумент path, иначе переменная окружения DISPATCH_HERE_GRID (off, 0 или пусто — выключить), иначе
data/traffic/here_grid.npz."""
import importlib.util
import math
import os

from .. import config

DEFAULT_PATH = os.path.join(config.DATA_DIR, 'traffic', 'here_grid.npz')
STEP_M = 300.0
CLIP = (0.3, 3.0)
OUTSIDE_WEIGHT = 1.0                      # вес точки вне сетки: обычная клетка с дорогами (медиана весов = 1)
CHUNK = 400_000                           # точек прямых за один проход (память ~ CHUNK × 12 × 4 байт)
M_LAT = 111320.0

_cache = {}


def grid_path(path=None):
    """Путь к сетке или None, если выключено."""
    if path is None:
        path = os.environ.get('DISPATCH_HERE_GRID', DEFAULT_PATH)
    return None if path.strip().lower() in ('', '0', 'off', 'none') else path


def load(path=None):
    """HereGrid или None (нет файла, выключено, нет numpy). Загружается один раз на путь и время изменения файла."""
    path = grid_path(path)
    if not path or not os.path.exists(path):
        return None
    if importlib.util.find_spec('numpy') is None:
        return None
    key = (os.path.abspath(path), os.path.getmtime(path))
    if key not in _cache:
        _cache.clear()
        _cache[key] = HereGrid(path)
    return _cache[key]


def available(path=None):
    return load(path) is not None


class HereGrid:
    def __init__(self, path):
        import numpy as np
        with np.load(path, allow_pickle=False) as z:
            share = z['share'].astype(np.float32)                    # (12, n_lat, n_lon)
            self.share = np.ascontiguousarray(np.moveaxis(share, 0, -1))   # (n_lat, n_lon, 12) — быстрее выборка
            self.weight = z['weight'].astype(np.float32) if 'weight' in z else np.ones(share.shape[1:], np.float32)
            self.lat0, self.lon0 = float(z['lat0']), float(z['lon0'])
            self.dlat, self.dlon = float(z['dlat']), float(z['dlon'])
            self.meta = str(z['meta']) if 'meta' in z else ''
        self.n_lat, self.n_lon = self.share.shape[:2]
        if self.share.shape[2] != len(config.TRAFFIC_HOURS):
            raise ValueError(f'{path}: {self.share.shape[2]} часов, а в config.TRAFFIC_HOURS '
                             f'{len(config.TRAFFIC_HOURS)}')

    # ---- точки
    def point_shares(self, lat, lon):
        """Доли по часам (…, 12) и веса (…) для точек; вне сетки — район config.zone_of."""
        import numpy as np
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        i = np.floor((lat - self.lat0) / self.dlat).astype(np.int64)
        j = np.floor((lon - self.lon0) / self.dlon).astype(np.int64)
        inside = (i >= 0) & (i < self.n_lat) & (j >= 0) & (j < self.n_lon)
        ii, jj = np.where(inside, i, 0), np.where(inside, j, 0)
        share = self.share[ii, jj]
        weight = self.weight[ii, jj]
        if not inside.all():
            out = ~inside
            share[out] = zone_shares(lat[out], lon[out])
            weight[out] = OUTSIDE_WEIGHT
        return share, weight

    # ---- поездки
    def arc_shares(self, points_a, points_b=None, step_m=STEP_M):
        """Доли загруженности поездок по часам 10…21: массив (len(a), len(b), 12), float32. points_* — [(lat, lon)];
        points_b=None — те же, что points_a. Доля не зависит от направления поездки (см. research/traffic)."""
        import numpy as np
        A = np.asarray(points_a, float).reshape(-1, 2)
        B = A if points_b is None else np.asarray(points_b, float).reshape(-1, 2)
        na, nb = len(A), len(B)
        out = np.empty((na, nb, self.share.shape[2]), np.float32)
        if not na or not nb:
            return out
        coslat = math.cos(math.radians(float(np.mean(A[:, 0]))))
        dl = B[None, :, 0] - A[:, None, 0]                           # (na, nb)
        dg = B[None, :, 1] - A[:, None, 1]
        k = np.maximum(2, np.ceil(np.hypot(dl * M_LAT, dg * M_LAT * coslat) / step_m).astype(np.int64) + 1)
        rows_per = max(1, int(CHUNK // max(1, k.sum() // na)))
        for r0 in range(0, na, rows_per):
            r1 = min(na, r0 + rows_per)
            K = k[r0:r1].ravel()
            pair = np.repeat(np.arange(K.size), K)
            first = np.cumsum(K) - K
            t = (np.arange(K.sum()) - first[pair]) / (K[pair] - 1)
            ia, ib = pair // nb + r0, pair % nb
            lat = A[ia, 0] + t * (B[ib, 0] - A[ia, 0])
            lon = A[ia, 1] + t * (B[ib, 1] - A[ia, 1])
            share, weight = self.point_shares(lat, lon)
            num = np.add.reduceat(share * weight[:, None], first, axis=0)
            den = np.add.reduceat(weight, first)
            out[r0:r1] = (num / den[:, None]).reshape(r1 - r0, nb, -1)
        return np.clip(out, *CLIP, out=out)

    def path_shares(self, paths, step_m=STEP_M):
        """Доли поездок по линиям маршрутов: paths — [[(lat, lon), …], …] → (len(paths), 12)."""
        import numpy as np
        out = np.empty((len(paths), self.share.shape[2]), np.float32)
        for n, path in enumerate(paths):
            P = np.asarray(path, float).reshape(-1, 2)
            if len(P) > 1:
                seg = np.hypot(np.diff(P[:, 0]) * M_LAT, np.diff(P[:, 1]) * M_LAT * math.cos(math.radians(P[0, 0])))
                s = np.concatenate([[0.0], np.cumsum(seg)])
                q = np.linspace(0.0, s[-1], max(2, int(np.ceil(s[-1] / step_m)) + 1))
                P = np.stack([np.interp(q, s, P[:, 0]), np.interp(q, s, P[:, 1])], 1)
            share, weight = self.point_shares(P[:, 0], P[:, 1])
            out[n] = (share * weight[:, None]).sum(0) / weight.sum()
        return np.clip(out, *CLIP, out=out)


def zone_shares(lat, lon):
    """config.zone_of для массивов точек: (…, 12)."""
    import numpy as np
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    out = np.empty(lat.shape + (len(config.OUTSIDE[1]),), np.float32)
    out[...] = config.OUTSIDE[1]
    done = np.zeros(lat.shape, bool)
    for _, (zlat, zlon), (a, b), share in config.ZONES:
        m = ~done & (((lat - zlat) / a) ** 2 + ((lon - zlon) / b) ** 2 <= 1)
        out[m] = share
        done |= m
    return out


def arc_shares(points_a, points_b=None, path=None, step_m=STEP_M):
    """Доли поездок (len(a), len(b), 12) или None, если сетки нет (тогда — районы config.zone_of)."""
    grid = load(path)
    return None if grid is None else grid.arc_shares(points_a, points_b, step_m)


def hour_weights(spans):
    """Доли часов 10…21 в окнах [a, b] (минуты от 10:00), как в config.window_hours: массив (len(spans), 12)."""
    import numpy as np
    W = np.zeros((len(spans), len(config.TRAFFIC_HOURS)))
    for n, (a, b) in enumerate(spans):
        hours = config.window_hours(a, b)
        np.add.at(W[n], hours, 1.0 / len(hours))
    return W


def traffic_ratios(points_from, points_to, spans, path=None, step_m=STEP_M, profile=None):
    """Коэффициенты пробок поездок i → j (len(from), len(to)) — то же, что config.traffic_ratio(*spans[j], share)
    с долей поездки по сетке вместо среднего по районам концов; None, если сетки нет."""
    grid = load(path)
    if grid is None:
        return None
    import numpy as np
    S = grid.arc_shares(points_from, points_to, step_m)                # (nf, nt, 12)
    T = np.asarray(profile or config.TRAFFIC_HOURS, float)                 # пробки по часам дня (config.day_profile)
    W = hour_weights(spans)                                                 # (nt, 12)
    return (1.0 + np.einsum('jh,ijh->ij', W * (T - 1.0), S)) / np.mean(config.TRAFFIC_HOURS)
