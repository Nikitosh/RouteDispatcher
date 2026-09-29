"""cgc: table old lb (runs/results/cgc_tmp/lb_backup) vs current lb for the far-home targets."""
import json, os
T = {'instances_road': 'yugovostok_s1 yugovostok_s2 yugovostok_s3 yugovostok_s4 yugovostok_s6 yugovostok_s8 yugovostok_s9'.split(),
     'instances_control_road': ['control_yugovostok_mix1_inf'],
     'instances_gen_road': 'allday_yugovostok_2 allday_yugovostok_5 allday_yugovostok_8 large_yugovostok_2 large_yugovostok_8 peak_yugovostok_2 real_yugovostok_2 real_yugovostok_5 real_yugovostok_8 scarce_yugovostok_2 scarce_yugovostok_8 tight_yugovostok_2 tight_yugovostok_5 walk_yugovostok_8'.split()}
print(f"{'instance':32} {'best':>9} {'old lb':>9} {'gap%':>6} {'new lb':>9} {'gap%':>6}  cgc root / nodes")
for d, L in T.items():
    meta = json.load(open(f'best/{d}/_meta.json'))
    for i in L:
        b = meta[i]['km']; o = json.load(open(f'runs/results/cgc_tmp/lb_backup/{d}/{i}.json')); n = json.load(open(f'lb/{d}/{i}.json'))
        c = n.get('cgc', {})
        print(f"{i:32} {b:9.2f} {o['lb_km']:9.2f} {100*(b/o['lb_km']-1):6.1f} {n['lb_km']:9.2f} {100*(b/n['lb_km']-1):6.1f}  {c.get('root','')} / {c.get('nodes','')}{' PROVEN' if n.get('proven_optimal') else ''}")
