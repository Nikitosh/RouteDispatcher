"""Вклад гибридов в лучшие известные: по папке сравнивает лучшее из всех «одиночных» источников
(bench_*.json, runs/results/<d>/<не hy>, runs/hy_runs/<d>/s1*_*) с лучшим, включая гибридные (runs/results/<d>/hy, hy_runs p*/s40/polish).
python3 hy_gain.py <dir ...>"""
import glob, os, sys
import best
from validate import load_instance, parse_output
import hy_sp
for d in sys.argv[1:]:
    C = best.collect(d); tot = dict(n=0, fleet=0, km=0.0, pen=0)
    rows = []
    for p in sorted(glob.glob(f'{d}/*.txt')):
        inst = os.path.basename(p)[:-4]; I = load_instance(p); V = I['V']
        single, hyb = [], []
        for r, tag in C.get(inst, []):
            if tag == 'best': continue   # best/ — производная от всех источников (включая hy)
            (hyb if tag.startswith('hy/') else single).append(r)
        for f in glob.glob(f'runs/hy_runs/{d}/*/{inst}.out'):
            r, _ = parse_output(open(f).read()); tag = f.split('/')[2]
            (single if tag.startswith('s1') else hyb).append(r)
        sc = lambda L: min([s for s in (hy_sp.score(I, (r + [[]] * V)[:V]) for r in L if r) if s] or [None], key=lambda x: x or (1e9,))
        s0, s1 = sc(single), sc(hyb)
        if not s0: continue
        b = min(x for x in (s0, s1) if x)
        if s1 and s1 < s0:
            tot['n'] += 1
            if s1[0] < s0[0]: tot['pen'] += s0[0] - s1[0]
            elif s1[1] < s0[1]: tot['fleet'] += s0[1] - s1[1]
            else: tot['km'] += s0[2] - s1[2]
            rows.append((inst, s0, s1))
    print(f"== {d}: гибрид лучше одиночных на {tot['n']} задачах; -штраф {tot['pen']}, -бригад {tot['fleet']}, -км {tot['km']:.1f} (при равном парке)")
    for r in rows: print('  ', *r)
