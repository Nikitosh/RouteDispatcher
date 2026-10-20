"""Hourly congestion factor for Moscow from Traffic4cast 2019 (HERE probe data, year 2018).

Grid: 495x436 cells of 0.001 deg, Moscow is stored rotated (row ~ lon, col ~ lat), bounds
from iarai/MeTS-10 data_helpers.T4C_BBOXES (lat 55.506-55.942, lon 37.358-37.853).
Channels: 0 volume, 1 speed (0..255, linear), 2 heading. 288 five-minute bins in UTC (shifted to MSK = UTC+3 below).

factor(h) = sum_c w_c / v_c(h)  /  sum_c w_c / ff_c
  w_c  = cell's probe volume over 10:00-22:00 (fixed weight = how often trips cross the cell)
  ff_c = volume-weighted mean speed of the cell in the night hours 01-04 (free flow)
  v_c(h) = volume-weighted mean speed of the cell in hour h
i.e. the travel-time ratio of a "typical trip" made of the cells, the same idea as TomTom's
travel time vs free-flow travel time.
"""
import glob
import json
import os
import sys

import h5py
import numpy as np

DATA = sys.argv[1]
OUT = sys.argv[2]

LAT_MIN, LON_MIN = 55.506, 37.358
NR, NC = 495, 436
rows, cols = np.mgrid[0:NR, 0:NC]
lon = LON_MIN + (NR - 1 - rows + 0.5) * 0.001
lat = LAT_MIN + (NC - 1 - cols + 0.5) * 0.001

KREMLIN = (55.7520, 37.6175)


def ell(lat0, lon0, a_lat, a_lon):
    return ((lat - lat0) / a_lat) ** 2 + ((lon - lon0) / a_lon) ** 2 <= 1


# Approximate ring roads by ellipses (good to ~0.5-1 km; enough for zone averages).
in_sadovoe = ell(55.7535, 37.6200, 0.0195, 0.0355)
in_ttk = ell(55.7505, 37.6170, 0.0450, 0.0870)
in_mkad = ell(55.7420, 37.6055, 0.1690, 0.2365)
zones = {
    "center_inside_garden_ring": in_sadovoe,
    "garden_ring_to_ttk": in_ttk & ~in_sadovoe,
    "ttk_to_mkad": in_mkad & ~in_ttk,
    "outside_mkad_in_grid": ~in_mkad,
    "all_grid": np.ones_like(in_mkad),
    "inside_mkad": in_mkad,
}

files = sorted(glob.glob(os.path.join(DATA, "*_100m_bins.h5")))
VOL = np.zeros((24, NR, NC))
SV = np.zeros((24, NR, NC))
CNT = np.zeros((24, NR, NC))
zero_speed = np.zeros(24)
obs = np.zeros(24)
for fn in files:
    a = h5py.File(fn, "r")["array"][:]
    vol = a[..., 0].astype(np.float32)
    spd = a[..., 1].astype(np.float32)
    valid = (vol > 0) & (spd > 0)
    for h in range(24):
        sl = slice(h * 12, (h + 1) * 12)
        v, s, ok = vol[sl], spd[sl], valid[sl]
        VOL[h] += np.where(ok, v, 0).sum(0)
        SV[h] += np.where(ok, v * s, 0).sum(0)
        CNT[h] += ok.sum(0)
        zero_speed[h] += ((v > 0) & (s == 0)).sum()
        obs[h] += (v > 0).sum()
    print(os.path.basename(fn), flush=True)

# Bins are UTC (volume minimum at 00-01 UTC = 03-04 MSK, peaks at 05-06 / 14-15 UTC);
# convert to Moscow local time (UTC+3): local hour hl <- UTC bin (hl - 3) % 24.
perm = [(hl - 3) % 24 for hl in range(24)]
VOL, SV, CNT, zero_speed, obs = VOL[perm], SV[perm], CNT[perm], zero_speed[perm], obs[perm]
with np.errstate(invalid="ignore", divide="ignore"):
    vh = SV / VOL  # (24, NR, NC)
night = slice(1, 5)
ff = SV[night].sum(0) / np.maximum(VOL[night].sum(0), 1e-9)
night_cnt = CNT[night].sum(0)
w = VOL[10:22].sum(0)
base = (night_cnt >= 20) & (ff >= 10) & (w > 0)

result = {}
for zn, zm in zones.items():
    cells = base & zm
    fac = []
    for h in range(24):
        ok = cells & (CNT[h] >= 5) & np.isfinite(vh[h])
        v = np.maximum(vh[h][ok], 3.0)
        fac.append(float((w[ok] / v).sum() / (w[ok] / ff[ok]).sum()))
    result[zn] = {
        "n_cells": int(cells.sum()),
        "factor": [round(x, 3) for x in fac],
        "congestion_pct": [round((x - 1) * 100, 1) for x in fac],
    }

vol_profile = VOL.sum((1, 2))
out = {
    "source": "Traffic4cast 2019 (IARAI/HERE), Moscow_2018.zip, HERE probe-vehicle data for 2018",
    "source_url": "https://here-studentsampledata-traffic4cast.s3.eu-west-2.amazonaws.com/2019/Moscow_2018.zip",
    "access": "public S3 object, no registration; 11.0 GB zip, single days fetched via HTTP range requests",
    "license": "HERE sample data for Traffic4cast: educational / non-commercial research use only, no redistribution of the raw data",
    "days": [os.path.basename(f)[:8] for f in files],
    "grid_bbox": {"lat": [55.506, 55.942], "lon": [37.358, 37.853], "rotated": True, "source": "github.com/iarai/MeTS-10 data_pipeline/data_helpers.py"},
    "method": __doc__.strip(),
    "zones_note": "ring roads approximated by ellipses: Garden Ring (55.7535,37.62; 0.0195x0.0355 deg), TTK (55.7505,37.617; 0.045x0.087), MKAD (55.742,37.6055; 0.169x0.2365). 'outside_mkad_in_grid' = only the margins of the 495x436 grid outside MKAD.",
    "volume_share_by_hour": [round(float(x / vol_profile.sum()), 4) for x in vol_profile],
    "speed0_share_of_observations_by_hour": [round(float(z / max(o, 1)), 3) for z, o in zip(zero_speed, obs)],
    "zones": result,
}
json.dump(out, open(OUT, "w"), indent=1)
for zn, r in result.items():
    print(f"{zn:28s} n={r['n_cells']:6d}  10..22:", r["factor"][10:23])
print("all 0..23:", result["inside_mkad"]["congestion_pct"])
print("vol share:", out["volume_share_by_hour"])
print("speed0 share:", out["speed0_share_of_observations_by_hour"])
