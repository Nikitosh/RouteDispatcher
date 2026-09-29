"""Ансамбль + разбиение: для каждой задачи папки берём K лучших различных решений из всех источников,
генерируем столбцы разбиений пар (s40_hy COLS, SLACK км), решаем CP-SAT по пулу, сохраняем в runs/results/<dir>/hy/.
python3 hy_spall.py <instdir> [tl=20] [K=30] [SLACK=20] [filter]"""
import os, subprocess, sys, tempfile
import glob
import hy_sp
d = sys.argv[1]; tl = float(sys.argv[2]) if len(sys.argv) > 2 else 20; K = int(sys.argv[3]) if len(sys.argv) > 3 else 30
slack = sys.argv[4] if len(sys.argv) > 4 else '20'; flt = sys.argv[5] if len(sys.argv) > 5 else ''
SCR = '/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/d8f727be-c46e-4a0a-ac5e-5e8ed1e794e5/scratchpad/sp'
os.makedirs(SCR, exist_ok=True)
tot = [0, 0]
for p in sorted(glob.glob(f'{d}/*.txt')):
    if flt not in p: continue
    inst = os.path.basename(p)[:-4]
    I = hy_sp.load_instance(p)
    sols = hy_sp.solutions(d, inst); sols = [(hy_sp.score(I, (r + [[]] * I['V'])[:I['V']]), r) for r in sols]
    sols = sorted([x for x in sols if x[0]], key=lambda x: x[0])
    seen = set(); files = []
    for sc, r in sols:
        key = tuple(tuple(x) for x in r)
        if key in seen: continue
        seen.add(key); f = f'{SCR}/{inst}_{len(files)}.out'
        open(f, 'w').write(''.join(f"ROUTE {v} {' '.join(map(str, x))}\n" for v, x in enumerate(r))); files.append(f)
        if len(files) >= K: break
    cf = f'{SCR}/{inst}.cols'
    subprocess.run(['bin/s40_hy', p, '60', '1'], env=dict(os.environ, INIT=','.join(files), COLS=cf, SLACK=slack), capture_output=True)
    b0, res, n = hy_sp.solve(d, inst, tl, [cf])
    if res and res[0]:
        saved = hy_sp.save(d, inst, *res)
        if b0 and res[0] < b0[0]: tot[0] += 1; tot[1] += b0[0][2] - res[0][2] if res[0][:2] == b0[0][:2] else 0
print('улучшено задач:', tot[0], 'выигрыш км (при равном парке):', round(tot[1], 2))
