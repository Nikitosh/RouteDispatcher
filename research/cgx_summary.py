"""cgx: table of target instances: old lb (before cgx), current lb, best, gap, proven flag."""
import json, os
OLD = {'allday_vostok_3': 185.0523, 'allday_vostok_6': 199.829, 'allday_vostok_9': 137.3034, 'allday_yugocentr_4': 69.2569,
       'large_vostok_0': 236.4043, 'large_vostok_3': 232.0627, 'large_vostok_6': 269.0359, 'large_vostok_9': 219.5655,
       'large_yugocentr_1': 258.2366, 'large_yugocentr_7': 267.6551, 'peak_vostok_0': 168.4805, 'peak_vostok_3': 221.1154,
       'real_vostok_9': 192.3916, 'tight_vostok_3': 157.7733, 'vostok_s0': 184.1496, 'yugocentr_s2': 207.4876,
       'yugocentr_s4': 207.6144, 'control_yugovostok_pt_inf': 336.6884}
SETS = {'instances_gen_road': [k for k in OLD if not k.startswith(('vostok_s', 'yugocentr_s', 'control'))],
        'instances_road': ['vostok_s0', 'yugocentr_s2', 'yugocentr_s4'], 'instances_control_road': ['control_yugovostok_pt_inf']}
for d, L in SETS.items():
    meta = json.load(open(f'best/{d}/_meta.json'))
    for n in L:
        lb = json.load(open(f'lb/{d}/{n}.json')); m = meta[n]; cur = lb.get('lb_km') or 0
        print(f"{n:28s} best {m['used']:2d} {m['km']:9.3f} | old lb {OLD[n]:9.3f} ({(m['km']/OLD[n]-1)*100:6.2f}%) | new lb {cur:9.3f} ({(m['km']/cur-1)*100:6.2f}%) K={lb.get('km_K')} proven={lb.get('proven_optimal')} {'cgx' if 'cgx' in lb else ''}")
