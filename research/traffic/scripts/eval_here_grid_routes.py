"""Choose the HERE grid's corridor/weight and measure its error against a "route truth".

Truth: for real car routes (OSRM polylines from cache/geometry.json) the congestion share computed directly from the
100 m source cells the route passes (every 50 m, weight = free-flow time 1/ff), on the ODD-numbered days.
Estimates: grid variants built on the EVEN-numbered days (build_here_grid.py --days even), straight line between
the route's ends (dispatch/geo/here_grid.py); 4 zones (config.zone_of, ends' mean); TomTom only (share = 1).
Error in ratio units: (TRAFFIC_HOURS[h] - 1) / mean(TRAFFIC_HOURS) * (share_est - share_true), i.e. the error of
the hour-h traffic coefficient relative to the daily mean.
Usage: eval_here_grid_routes.py <accum.npz> <geometry.json> <workdir> [n_routes]

Result (5135 cached car routes, median 7.1 km; RMSE over hours 10..21 / at 18h / corr at 18h):
  truth vs truth on the other half (noise floor)  0.134 / 0.137 / 0.91
  4 zones (ends' mean)                            0.231 / 0.280 / 0.42
  TomTom only                                     0.252 / 0.311 / -
  grid, straight line, sigma 0 (raw 300 m cells)  0.262 / 0.338 / 0.44
  grid, straight line, sigma 2 sqrtD  (chosen)    0.218 / 0.269 / 0.54
  grid, along the real route, sigma 2 sqrtD       0.195 / 0.229 / 0.70
  trips < 3 km: zones 0.309, grid line 0.303 -- no gain on short trips.
"""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, HERE)
from build_here_grid import load_days, source_cells, LAT0, LON0, SRC  # noqa: E402
from dispatch import config  # noqa: E402
from dispatch.geo import here_grid  # noqa: E402

ACC, GEOM, WORK = sys.argv[1:4]
NR = int(sys.argv[4]) if len(sys.argv) > 4 else 6000
os.makedirs(WORK, exist_ok=True)
T = np.asarray(config.TRAFFIC_HOURS)
SCALE = (T - 1) / T.mean()


def decode(s, prec=5):
    idx = lat = lon = 0
    out = []
    while idx < len(s):
        for which in (0, 1):
            shift = res = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                res |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if which == 0:
                lat += d
            else:
                lon += d
        out.append((lat / 10 ** prec, lon / 10 ** prec))
    return np.array(out)


def resample(poly, step):
    d = np.hypot(np.diff(poly[:, 0]) * 111320, np.diff(poly[:, 1]) * 111320 * 0.563)
    s = np.concatenate([[0], np.cumsum(d)])
    q = np.linspace(0, s[-1], max(2, int(np.ceil(s[-1] / step)) + 1))
    return np.stack([np.interp(q, s, poly[:, 0]), np.interp(q, s, poly[:, 1])], 1), s[-1]


# ---- routes
rng = np.random.default_rng(1)
G = json.load(open(GEOM))
keys = [k for k, v in G.items() if k.startswith('car|') and isinstance(v, str)]
rng.shuffle(keys)
bb = (LAT0 + 0.01, LAT0 + 0.436 - 0.01, LON0 + 0.01, LON0 + 0.495 - 0.01)
routes = []
for k in keys:
    _, a, b = k.split('|')
    a, b = tuple(map(float, a.split(','))), tuple(map(float, b.split(',')))
    if not all(bb[0] < p[0] < bb[1] and bb[2] < p[1] < bb[3] for p in (a, b)):
        continue
    pts, length = resample(decode(G[k]), 50.0)
    if length < 500 or len(pts) < 3:
        continue
    routes.append((a, b, pts, length))
    if len(routes) >= NR:
        break
del G

# ---- truth on odd days (and on even days: the truth's own noise)
def route_truth(days):
    (vol, sv, cnt), _ = load_days(ACC, days)
    w, ff, ok, vh = source_cells(vol, sv, cnt)
    ffs = np.where(ff > 0, ff, np.inf)
    f_city = np.where(ok, w / vh, 0).sum((1, 2)) / np.where(ok, (w / np.where(ff > 0, ff, 1))[None], 0).sum((1, 2))
    out = []
    for a, b, pts, length in routes:
        i = np.floor((pts[:, 0] - LAT0) / SRC).astype(int)
        j = np.floor((pts[:, 1] - LON0) / SRC).astype(int)
        good = ok[:, i, j]                                   # (12, S)
        f = np.where(good, 1 / vh[:, i, j], 0).sum(1) / np.maximum(np.where(good, 1 / ffs[i, j][None], 0).sum(1), 1e-12)
        out.append(((f - 1) / (f_city - 1), good.mean()))
    return out


odd, even = route_truth('odd'), route_truth('even')
keep = [n for n in range(len(routes)) if odd[n][1] >= 0.6 and even[n][1] >= 0.6]
truth = np.array([odd[n][0] for n in keep])
truth_even = np.array([even[n][0] for n in keep])
routes = [routes[n] for n in keep]
lengths = np.array([r[3] for r in routes]) / 1000
print(f'routes: {len(routes)}, length km p10/50/90: {np.percentile(lengths, [10, 50, 90]).round(1)}')

# ---- estimates
A = np.array([r[0] for r in routes])
B = np.array([r[1] for r in routes])
zs = here_grid.zone_shares(np.r_[A[:, 0], B[:, 0]], np.r_[A[:, 1], B[:, 1]])
est = {'истина по чётным дням (шум)': truth_even, '4 районы (концы)': (zs[:len(A)] + zs[len(A):]) / 2,
       'TomTom (доля 1)': np.ones_like(truth)}
variants = [(0.0, 'D'), (0.5, 'D'), (1.0, 'D'), (2.0, 'D'), (1.0, 'sqrtD'), (1.0, 'flat'), (0.0, 'flat'),
            (0.5, 'sqrtD'),
            (3.0, 'D'), (5.0, 'D'), (2.0, 'sqrtD'), (3.0, 'sqrtD'), (4.0, 'sqrtD'), (3.0, 'flat')]
for corr, wgt in variants:
    path = os.path.join(WORK, f'grid_even_c{corr}_{wgt}.npz')
    if not os.path.exists(path):
        subprocess.run([sys.executable, os.path.join(HERE, 'build_here_grid.py'), ACC, path, '--days', 'even',
                        '--corridor', str(corr), '--weight', wgt], check=True, stdout=subprocess.DEVNULL)
    g = here_grid.HereGrid(path)
    line = np.array([g.arc_shares([a], [b])[0, 0] for a, b in zip(A, B)])
    est[f'сетка σ={corr} {wgt}: прямая'] = line
    if (corr, wgt) in ((1.0, 'D'), (2.0, 'D'), (2.0, 'sqrtD'), (3.0, 'sqrtD')):
        rs = []
        for _, _, pts, _ in routes:
            sh, wt = g.point_shares(pts[::6, 0], pts[::6, 1])       # every 300 m along the real route
            rs.append(np.clip((sh * wt[:, None]).sum(0) / wt.sum(), *here_grid.CLIP))
        est[f'сетка σ={corr} {wgt}: по маршруту'] = np.array(rs)

short = lengths < 3
print(f'{"оценка":34s} RMSE(все ч)  RMSE 12ч  RMSE 18ч  corr18  bias   RMSE<3км RMSE≥3км')
for name, e in est.items():
    err = (e - truth) * SCALE
    rm = lambda x: float(np.sqrt(np.mean(x ** 2)))  # noqa: E731
    c18 = np.corrcoef(e[:, 8], truth[:, 8])[0, 1] if e[:, 8].std() > 0 else float('nan')
    print(f'{name:34s} {rm(err):.3f}       {rm(err[:, 2]):.3f}     {rm(err[:, 8]):.3f}     {c18:5.2f}  '
          f'{err.mean():+.3f}'
          f'  {rm(err[short]):.3f}    {rm(err[~short]):.3f}')
print('truth share p5/50/95 at 18h:', np.percentile(truth[:, 8], [5, 50, 95]).round(2))
