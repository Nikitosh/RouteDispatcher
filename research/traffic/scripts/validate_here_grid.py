"""Compare traffic ratios of car trips: HERE grid (dispatch/geo/here_grid.py) vs 4 zones (config.zone_of, the end
points' mean) vs the TomTom city profile alone, on the points of a plan (brigade starts + orders).
Also checks the straight-line approximation against the plan's real route geometry (OSRM polylines).
Usage: validate_here_grid.py out/Восток/plan.json [out/Югоцентр/plan.json ...]
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from dispatch import config  # noqa: E402
from dispatch.geo import here_grid  # noqa: E402

grid = here_grid.load()
assert grid is not None, 'no data/traffic/here_grid.npz'
T = np.asarray(config.TRAFFIC_HOURS)
ZN = {name: n for n, (name, *_) in enumerate(config.ZONES)}
ZN[config.OUTSIDE[0]] = len(config.ZONES)


def zone_idx(p):
    return ZN[config.zone_of(*p)[0]]


def ratio_from_share(W, S):
    return (1 + np.einsum('jh,ijh->ij', W * (T - 1), S)) / T.mean()


def through_center(a, b, n=50):
    t = np.linspace(0, 1, n)[:, None]
    p = a[None] + t * (b - a)[None]
    (zlat, zlon), (ra, rb) = config.ZONES[0][1], config.ZONES[0][2]
    return bool((((p[:, 0] - zlat) / ra) ** 2 + ((p[:, 1] - zlon) / rb) ** 2 <= 1).any())


def resample(poly, step_m=300.0):
    poly = np.asarray(poly, float)
    if len(poly) < 2:
        return poly
    d = np.hypot(np.diff(poly[:, 0]) * here_grid.M_LAT, np.diff(poly[:, 1]) * here_grid.M_LAT * 0.563)
    s = np.concatenate([[0], np.cumsum(d)])
    q = np.linspace(0, s[-1], max(2, int(np.ceil(s[-1] / step_m)) + 1))
    return np.stack([np.interp(q, s, poly[:, 0]), np.interp(q, s, poly[:, 1])], 1)


def pct(x):
    return ' '.join(f'{v:+.1%}' for v in np.percentile(x, [5, 25, 50, 75, 95]))


for path in sys.argv[1:]:
    P = json.load(open(path))
    starts = sorted({(b['lat'], b['lon']) for b in P['brigades']})
    orders = P['orders']
    pts = np.array(starts + [(o['lat'], o['lon']) for o in orders])
    ns = len(starts)
    spans = [(o['a'], max(o['a'], o['latest'])) for o in orders]
    W = here_grid.hour_weights(spans)
    dst = pts[ns:]
    S_grid = grid.arc_shares(pts, dst)
    zs = here_grid.zone_shares(pts[:, 0], pts[:, 1])
    S_zone = (zs[:, None] + zs[None, ns:]) / 2
    R_grid = ratio_from_share(W, S_grid)
    R_zone = ratio_from_share(W, S_zone)
    R_tt = ratio_from_share(W, np.ones_like(S_zone))
    mask = np.ones(R_grid.shape, bool)
    mask[np.arange(ns, len(pts)), np.arange(len(orders))] = False
    d = (R_grid / R_zone - 1)[mask]
    print(f'\n=== {P["meta"]["name"]}: {ns} starts + {len(orders)} orders, {mask.sum()} arcs')
    zc = np.bincount([zone_idx(p) for p in pts], minlength=len(ZN))
    print('   points by zone', dict(zip(ZN, zc.tolist())))
    print(f'   ratio mean: grid {R_grid[mask].mean():.3f}  zones {R_zone[mask].mean():.3f}  '
          f'TomTom city {R_tt[mask].mean():.3f}')
    print(f'   grid/zones - 1: mean {d.mean():+.1%}  sd {d.std():.1%}  p5..p95 {pct(d)}  '
          f'|d|>10%: {(np.abs(d) > .1).mean():.0%}'
          f'  |d|>20%: {(np.abs(d) > .2).mean():.0%}')
    dt = (R_grid / R_tt - 1)[mask]
    print(f'   grid/TomTom - 1: p5..p95 {pct(dt)};  zones/TomTom - 1: p5..p95 {pct((R_zone / R_tt - 1)[mask])}')
    print(f'   corr(grid, zones) over arcs: {np.corrcoef(R_grid[mask], R_zone[mask])[0, 1]:.2f}')
    # classes
    cls = {}
    for i in range(len(pts)):
        for j in range(len(orders)):
            if not mask[i, j]:
                continue
            a, b = pts[i], dst[j]
            za, zb = zone_idx(a), zone_idx(b)
            if through_center(a, b):
                c = 'через/в центре (Садовое)'
            elif za == zb:
                c = 'в одном районе: ' + list(ZN)[za]
            else:
                c = 'между районами'
            cls.setdefault(c, []).append((i, j))
    for c, ij in sorted(cls.items()):
        ii, jj = np.array(ij).T
        dd = R_grid[ii, jj] / R_zone[ii, jj] - 1
        print(f'   {c:34s} n={len(ij):5d}  grid/zones-1 mean {dd.mean():+.1%} p5..p95 {pct(dd)}')
    # examples: biggest differences
    order = np.argsort(np.where(mask, R_grid / R_zone, 1).ravel())
    print('   examples (from -> to, window, ratio grid / zones / TomTom):')
    for flat in list(order[:3]) + list(order[-3:]):
        i, j = divmod(int(flat), len(orders))
        name = lambda p: config.zone_of(*p)[0]  # noqa: E731
        print(f'     {tuple(np.round(pts[i], 4))} [{name(pts[i])}] -> {tuple(np.round(dst[j], 4))} [{name(dst[j])}] '
              f'window {spans[j][0] // 60 + 10}:00..{spans[j][1] / 60 + 10:.1f}h  '
              f'{R_grid[i, j]:.2f} / {R_zone[i, j]:.2f} / {R_tt[i, j]:.2f}')
    # straight line vs the real route geometry, on the plan's legs
    st = {(b['v']): (b['lat'], b['lon']) for b in P['brigades']}
    k_line, k_route, k_zone = [], [], []
    for r in P['plan']['routes']:
        prev = st[r['v']]
        for leg, s in zip(r['geometry'], r['stops']):
            o = orders[s['k']]
            to = (o['lat'], o['lon'])
            w = here_grid.hour_weights([spans[s['k']]])
            sl = grid.arc_shares([prev], [to])
            rs = resample(leg)
            sh, wt = grid.point_shares(rs[:, 0], rs[:, 1])
            sr = np.clip((sh * wt[:, None]).sum(0) / wt.sum(), *here_grid.CLIP)[None, None]
            z = here_grid.zone_shares(np.array([prev[0], to[0]]), np.array([prev[1], to[1]])).mean(0)[None, None]
            k_line.append(ratio_from_share(w, sl)[0, 0])
            k_route.append(ratio_from_share(w, sr)[0, 0])
            k_zone.append(ratio_from_share(w, z)[0, 0])
            prev = to
    kl, kr, kz = map(np.array, (k_line, k_route, k_zone))
    print(f'   plan legs n={len(kl)}: straight line vs real route: corr {np.corrcoef(kl, kr)[0, 1]:.2f}, '
          f'line/route-1 p5..p95 {pct(kl / kr - 1)};  zones vs route: corr {np.corrcoef(kz, kr)[0, 1]:.2f}, '
          f'zones/route-1 p5..p95 {pct(kz / kr - 1)}')
