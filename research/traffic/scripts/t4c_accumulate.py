"""Step 1 of the HERE grid: sum the 13 Traffic4cast 2019 Moscow days into hourly per-cell accumulators.

Output (npz, derived from HERE data -> keep out of git, e.g. research/traffic/raw/):
  vol[24, NR, NC], sv[24, NR, NC], cnt[24, NR, NC]      all directions, MSK hours
  vol_d[24, 4, NR, NC], sv_d[...], cnt_d[...]          split by the bin's dominant heading
                                                       (1 = NE, 85 = SE, 170 = SW, 255 = NW -> 0..3)
  vol_a, sv_a, cnt_a                                    the same for even-numbered days (split-half check)
  vol, speed as in t4c_profile.py: only bins with volume > 0 and speed > 0; sv = sum(volume * speed).
Usage: t4c_accumulate.py <dir with *_100m_bins.h5> <out.npz>
"""
import glob
import os
import sys

import h5py
import numpy as np

DATA, OUT = sys.argv[1], sys.argv[2]
NR, NC = 495, 436
HEAD = {1: 0, 85: 1, 170: 2, 255: 3}

files = sorted(glob.glob(os.path.join(DATA, "*_100m_bins.h5")))
vol = np.zeros((24, NR, NC))
sv = np.zeros((24, NR, NC))
cnt = np.zeros((24, NR, NC), np.int32)
vol_d = np.zeros((24, 4, NR, NC), np.float32)
sv_d = np.zeros((24, 4, NR, NC), np.float32)
cnt_d = np.zeros((24, 4, NR, NC), np.int32)
vol_a = np.zeros((24, NR, NC))                             # half A (even-numbered days) for split-half checks
sv_a = np.zeros((24, NR, NC))
cnt_a = np.zeros((24, NR, NC), np.int32)
for di, fn in enumerate(files):
    a = h5py.File(fn, "r")["array"][:]
    for hu in range(24):
        sl = a[hu * 12:(hu + 1) * 12]
        v = sl[..., 0].astype(np.float32)
        s = sl[..., 1].astype(np.float32)
        hd = sl[..., 2]
        ok = (v > 0) & (s > 0)
        h = (hu + 3) % 24                                  # UTC -> MSK
        vv = np.where(ok, v, 0)
        vol[h] += vv.sum(0)
        sv[h] += (vv * s).sum(0)
        cnt[h] += ok.sum(0)
        if di % 2 == 0:
            vol_a[h] += vv.sum(0)
            sv_a[h] += (vv * s).sum(0)
            cnt_a[h] += ok.sum(0)
        for code, k in HEAD.items():
            m = ok & (hd == code)
            vm = np.where(m, v, 0)
            vol_d[h, k] += vm.sum(0)
            sv_d[h, k] += (vm * s).sum(0)
            cnt_d[h, k] += m.sum(0)
    print(os.path.basename(fn), flush=True)

np.savez_compressed(OUT, vol=vol.astype(np.float32), sv=sv.astype(np.float32), cnt=cnt, vol_d=vol_d, sv_d=sv_d,
                    cnt_d=cnt_d, vol_a=vol_a.astype(np.float32), sv_a=sv_a.astype(np.float32),
                    cnt_a=cnt_a, days=np.array([os.path.basename(f)[:8] for f in files]))
