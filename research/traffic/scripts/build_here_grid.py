"""Step 2 of the HERE grid: hourly congestion share of ~300 m cells relative to the city average (10:00-21:00 MSK).

Input: accumulators from t4c_accumulate.py (13 weekdays of Traffic4cast 2019 Moscow, HERE probe data of 2018).
Output: data/traffic/here_grid.npz (derived from HERE data: education / non-commercial research licence) and a small
zone-level check (research/traffic/here_grid_check.json, aggregates only).

Method, the same as t4c_profile.py / config.ZONES:
  per 100 m source cell c (0.001 deg; the source grid is stored rotated, row ~ lon reversed, col ~ lat reversed):
    w_c = volume 10:00-22:00, ff_c = night (01-05) volume-weighted speed, v_c(h) = speed in hour h (>= 3),
    cell used if night bins >= 20, ff >= 10, w > 0 and >= 5 bins in hour h;
  a block B of 3 (lat) x 5 (lon) source cells = 0.003 x 0.005 deg ~ 334 x 313 m:
    f_B(h) = sum_c w_c / v_c(h) / sum_c w_c / ff_c    (travel-time factor vs night free flow)
  city: the same over the whole grid (= "all_grid" in t4c_moscow2018_hourly.json);
  share_B(h) = (f_B(h) - 1) / (f_city(h) - 1).
Sparse cells (empirical Bayes, in congestion units c = f - 1): prior = Gaussian neighbourhood (sigma 1.5 cells
  ~ 0.5 km) of the same sums, if it has too little volume -- a wider one (sigma 5 cells ~ 1.6 km), else the
  config.ZONES share of the cell centre; c = prior + lam (raw - prior), lam = tau2_h / (tau2_h + noise(V)),
  noise(V) = A + B / V fitted on a split of the 13 days into two halves, tau2_h = spread of reliable cells around
  their prior in hour h. share = c / c_city, clipped to [-1, 5] per cell (noise below 0 averages out along an arc;
  the arc result is clipped to [0.3, 3] in dispatch/geo/here_grid.py).
Corridor and arc weight (--corridor, --weight; chosen by eval_here_grid_routes.py): the stored share of a cell is
  the share of its neighbourhood (Gaussian, sigma in cells) weighted by the vehicle-time D = sum w / ff, and the arc
  weight of a cell is that neighbourhood's D (or sqrt D, or 1), divided by its median.
Direction: not modelled -- see the printed in/out check (speed is one value per cell for all directions,
  the heading channel is only the dominant direction of the 5-minute bin).
Usage: build_here_grid.py <accum.npz> <out_grid.npz> [--check out.json] [--days all|even|odd] [--corridor S]
       [--weight D|sqrtD|flat]
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from dispatch.config import OUTSIDE, ZONES  # noqa: E402

LAT0, LON0 = 55.506, 37.358               # south-west corner of the source grid
SRC = 0.001                               # source cell, deg
BL, BO = 3, 5                             # block = 3 source cells in lat x 5 in lon
DLAT, DLON = SRC * BL, SRC * BO
HOURS = list(range(10, 22))
V_MIN = 30                                # probe volume (all days, one hour) below which a cell has no own value
CLIP = (-1.0, 5.0)                        # per cell, outliers only (clipping at 0 biases quiet zones up by ~0.05)


def rot(x):
    """(…, 495 rows = lon reversed, 436 cols = lat reversed) -> (…, lat up, lon east)."""
    return np.ascontiguousarray(np.swapaxes(x[..., ::-1, ::-1], -1, -2)).astype(np.float64)


def load_days(acc, days='all'):
    """vol, sv, cnt (24, 436, 495) for all / even / odd-numbered days of the accumulator file."""
    A = np.load(acc)
    full = [rot(A[k]) for k in ('vol', 'sv', 'cnt')]
    half = [rot(A[k]) for k in ('vol_a', 'sv_a', 'cnt_a')]
    if days == 'all':
        return full, half
    if days == 'even':
        return half, None
    return [f - h for f, h in zip(full, half)], None


def block(x):
    x = np.pad(x, [(0, 0)] * (x.ndim - 2) + [(0, (-x.shape[-2]) % BL), (0, (-x.shape[-1]) % BO)])
    s = x.shape
    return x.reshape(s[:-2] + (s[-2] // BL, BL, s[-1] // BO, BO)).sum((-3, -1))


def source_cells(vol, sv, cnt):
    """Per 100 m cell: w (10-22 volume), ff (night speed), ok[h] (usable in hour 10+h), vh[h] (speed >= 3)."""
    night = slice(1, 5)
    ff = sv[night].sum(0) / np.maximum(vol[night].sum(0), 1e-9)
    w = vol[10:22].sum(0)
    base = (cnt[night].sum(0) >= 20) & (ff >= 10) & (w > 0)
    with np.errstate(invalid='ignore', divide='ignore'):
        vh = sv[10:22] / vol[10:22]
    ok = base[None] & (cnt[10:22] >= 5) & np.isfinite(vh)
    return w, ff, ok, np.maximum(np.where(ok, vh, 1.0), 3.0)


def sums(vol, sv, cnt):
    """Per block and hour 10..21: N = sum w/v, D = sum w/ff, V = probe volume of the used source cells."""
    w, ff, ok, vh = source_cells(vol, sv, cnt)
    N = np.where(ok, w / vh, 0)
    D = np.where(ok, (w / np.where(ff > 0, ff, 1))[None], 0)
    V = np.where(ok, vol[10:22], 0)
    return block(N), block(D), block(V)


def cong(N, D):
    with np.errstate(invalid='ignore', divide='ignore'):
        return N / D - 1


def gauss(x, sigma):
    """Separable Gaussian sum over the last two axes, truncated at 3 sigma, zero outside the grid."""
    if sigma <= 0:
        return x.copy()
    r = int(np.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    out = np.apply_along_axis(lambda v: np.convolve(v, k, 'same'), -1, x)
    return np.apply_along_axis(lambda v: np.convolve(v, k, 'same'), -2, out)


def zone_share(lat, lon):
    out = np.empty(lat.shape + (12,))
    out[:] = OUTSIDE[1]
    done = np.zeros(lat.shape, bool)
    for _, (zlat, zlon), (a, b), share in ZONES:
        m = ~done & (((lat - zlat) / a) ** 2 + ((lon - zlon) / b) ** 2 <= 1)
        out[m] = share
        done |= m
    return np.moveaxis(out, -1, 0)


def fit_noise(halves, V):
    """noise(V) = a + b / V of the congestion c of a block, from two halves of the days (in c units: the noise of a
    speed estimate does not depend on the hour, the share noise does since c_city is 0.1 at 21:00, 0.42 at 18:00)."""
    (Na, Da, Va), (Nb, Db, Vb) = halves
    cA, cB = cong(Na, Da), cong(Nb, Db)
    both = np.isfinite(cA) & np.isfinite(cB) & (np.minimum(Va, Vb) >= 10)
    d2 = (cA - cB)[both] ** 2 / 4          # var(cA - cB) ~ 4 noise(V) of the full data, V = Va + Vb
    Vf = V[both]
    bins = np.quantile(Vf, np.linspace(0, 1, 21))
    xs, ys = [], []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (Vf >= lo) & (Vf < hi)
        q = d2[m]
        q = q[q <= np.quantile(q, 0.98)]                 # robust to a few broken cells
        xs.append(1 / np.median(Vf[m]))
        ys.append(q.mean())
    b, a = np.polyfit(xs, ys, 1)
    good = np.isfinite(cA) & np.isfinite(cB) & (np.minimum(Va, Vb) >= V_MIN)
    return max(a, 1e-4), max(b, 0.0), float(np.corrcoef(cA[good], cB[good])[0, 1])


NOISE_DEFAULT = (0.0070, 4.01)            # fit on all 13 days (used for the half-day variants of the evaluation)


def build(data, half=None, corridor=2.0, weight='sqrtD'):
    vol, sv, cnt = data
    N, D, V = sums(vol, sv, cnt)
    fc = N.sum((1, 2)) / D.sum((1, 2))
    c = cong(N, D)
    nlat, nlon = N.shape[1:]
    LAT, LON = np.meshgrid(LAT0 + (np.arange(nlat) + 0.5) * DLAT, LON0 + (np.arange(nlon) + 0.5) * DLON, indexing='ij')
    if half is not None:
        hA = sums(*half)
        hB = sums(*[f - h for f, h in zip(data, half)])
        NA, NB, split_corr = fit_noise((hA, hB), V)
    else:
        (NA, NB), split_corr = NOISE_DEFAULT, None

    def noise(v):
        return NA + NB / np.maximum(v, 1)

    prior = zone_share(LAT, LON) * (fc[:, None, None] - 1)   # in c units
    src = np.full((12, nlat, nlon), 3, np.uint8)            # 0 own data, 1 near, 2 wide, 3 zone
    for code, sigma in ((2, 5.0), (1, 1.5)):
        gN, gD, gV = gauss(N, sigma), gauss(D, sigma), gauss(V, sigma)
        p = cong(gN, gD)
        ok = (gV >= 5 * V_MIN) & np.isfinite(p)
        prior = np.where(ok, p, prior)
        src = np.where(ok, code, src)
    has = (V >= V_MIN) & np.isfinite(c)
    tau2 = np.zeros(12)
    for k in range(12):                                     # spread of reliable cells around their prior, per hour
        rel = has[k] & (V[k] >= 2000)
        r = (c[k] - prior[k])[rel]
        r = r[np.abs(r) <= np.quantile(np.abs(r), 0.98)]
        tau2[k] = max(float(r.var() - noise(V[k][rel]).mean()), 1e-4)
    lam = np.where(has, tau2[:, None, None] / (tau2[:, None, None] + noise(V)), 0.0)
    cfin = np.where(has, prior + lam * (np.nan_to_num(c) - prior), prior)
    src = np.where(has & (lam >= 0.5), 0, src)
    cell_share = np.clip(cfin / (fc[:, None, None] - 1), *CLIP)
    # corridor: a straight line stands for a route along the roads next to it
    gD = gauss(D, corridor)
    with np.errstate(invalid='ignore', divide='ignore'):
        share = gauss(D * cell_share, corridor) / gD
    share = np.where(gD > 1e-9 * gD.max(), share, cell_share)
    wgt = {'D': gD.mean(0), 'sqrtD': np.sqrt(gD.mean(0)), 'flat': np.ones(gD.shape[1:])}[weight]
    wgt = np.maximum(wgt / np.median(wgt[wgt > 0]), 0.01)   # 1 = a median road cell; empty cells still count a little
    return dict(share=share, weight=wgt, src=src, fc=fc, N=N, D=D, LAT=LAT, LON=LON, noise=(NA, NB), tau2=tau2,
                split_corr=split_corr, cell_share=cell_share)


def zone_check(g):
    N, D, fc, share, wgt, LAT, LON = (g[k] for k in ('N', 'D', 'fc', 'share', 'weight', 'LAT', 'LON'))
    inside, done = [], np.zeros(LAT.shape, bool)
    for name, (zlat, zlon), (a, b), zs in ZONES:
        m = ~done & (((LAT - zlat) / a) ** 2 + ((LON - zlon) / b) ** 2 <= 1)
        inside.append((name, m, zs))
        done |= m
    inside.append((OUTSIDE[0] + ' (в сетке)', ~done, OUTSIDE[1]))
    out = {}
    for name, m, zs in inside:
        agg = (N[:, m].sum(1) / D[:, m].sum(1) - 1) / (fc - 1)
        arc = (share[:, m] * wgt[m]).sum(1) / wgt[m].sum()
        mean = share[:, m].mean(1)
        out[name] = {'cells': int(m.sum()), 'config': list(zs), 'grid_sums': [round(x, 2) for x in agg],
                     'grid_mean_arc_weight': [round(x, 2) for x in arc], 'grid_mean_cells': [round(x, 2) for x in mean]}
        print(f'{name:22s} cells={m.sum():5d}')
        print('   config         ', np.round(zs, 2))
        print('   grid sums      ', np.round(agg, 2))
        print('   grid arc weight', np.round(arc, 2))
        print('   grid cell mean ', np.round(mean, 2))
    return out


def save(g, out, args):
    nlat, nlon = g['share'].shape[1:]
    meta = {
        'source': 'Traffic4cast 2019 (IARAI/HERE), Moscow_2018: HERE probe data, 13 weekdays of 2018 '
                  '(research/traffic/t4c_moscow2018_hourly.json, days listed there)',
        'license': 'HERE sample data for Traffic4cast: education / non-commercial research only; this derived grid '
                   'is for non-commercial use only',
        'method': __doc__.split('Usage:')[0].strip(),
        'params': {'days': args.days, 'corridor_sigma_cells': args.corridor, 'weight': args.weight,
                   'noise': [round(x, 5) for x in g['noise']], 'tau2': [round(x, 5) for x in g['tau2']]},
        'layout': 'share[h, i, j]: hour 10+h MSK, cell i from lat0 northwards, j from lon0 eastwards; '
                  'cell (i, j) covers lat0 + i*dlat .. +dlat, lon0 + j*dlon .. +dlon; weight[i, j]',
        'rotation': 'source h5 arrays are (time, 495, 436, ch) with row = lon (reversed, row 0 = 37.853), '
                    'col = lat (reversed, col 0 = 55.942); un-rotated here',
        'bbox': {'lat': [LAT0, LAT0 + nlat * DLAT], 'lon': [LON0, LON0 + nlon * DLON]},
        'direction': 'not modelled: speed is one value per cell for all directions, heading only the dominant one',
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    np.savez_compressed(out, share=g['share'].astype(np.float16), weight=g['weight'].astype(np.float16),
                        source=g['src'], lat0=LAT0, lon0=LON0, dlat=DLAT, dlon=DLON, hours=np.array(HOURS),
                        city_factor=g['fc'].astype(np.float32), meta=json.dumps(meta, ensure_ascii=False))
    print(out, os.path.getsize(out), 'bytes')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('accum')
    ap.add_argument('out')
    ap.add_argument('--check')
    ap.add_argument('--days', default='all', choices=['all', 'even', 'odd'])
    ap.add_argument('--corridor', type=float, default=2.0)
    ap.add_argument('--weight', default='sqrtD', choices=['D', 'sqrtD', 'flat'])
    args = ap.parse_args()
    data, half = load_days(args.accum, args.days)
    g = build(data, half, args.corridor, args.weight)
    print('city factor 10..21:', np.round(g['fc'], 3))
    print('noise(V) = %.4f + %.2f/V (c units); tau2 by hour' % g['noise'], np.round(g['tau2'], 4))
    src_frac = {k: round(float((g['src'] == c).mean()), 3) for c, k in enumerate(('own', 'near', 'wide', 'zone'))}
    pcts = [round(float(x), 2) for x in np.percentile(g['share'], [5, 25, 50, 75, 95])]
    print('cells by source:', src_frac, ' split-half corr (raw c, per cell-hour):', g['split_corr'])
    print('share percentiles 5/25/50/75/95:', pcts)
    zones = zone_check(g)
    if args.check:
        check = {'city_factor': [round(x, 3) for x in g['fc']],
                 'noise_model': {'a': round(g['noise'][0], 5), 'b': round(g['noise'][1], 3),
                                 'tau2': [round(x, 4) for x in g['tau2']]},
                 'params': {'corridor_sigma_cells': args.corridor, 'weight': args.weight},
                 'cells': {'grid': list(g['share'].shape[1:]), 'source_share': src_frac,
                           'split_half_corr_hourly': g['split_corr'], 'share_percentiles_5_25_50_75_95': pcts},
                 'zones': zones}
        json.dump(check, open(args.check, 'w'), ensure_ascii=False, indent=1)
    save(g, args.out, args)


if __name__ == '__main__':
    main()
