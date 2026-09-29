"""Полировка лучших известных: для каждой задачи K лучших различных решений из всех источников (best.py + hy_runs)
-> s40_hy INIT=они на T с -> runs/hy_runs/<dir>/polish_t<T>_s<seed>/; если лучше best-known, то runs/results/<dir>/hy/.
python3 hy_polish.py <dir> <T> <seed> [K=3] [filter]; WORKERS=2"""
import glob, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
import hy_sp
from validate import parse_output
d, T, sd = sys.argv[1], sys.argv[2], sys.argv[3]; K = int(sys.argv[4]) if len(sys.argv) > 4 else 3
flt = sys.argv[5] if len(sys.argv) > 5 else ''
SCR = '/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/d8f727be-c46e-4a0a-ac5e-5e8ed1e794e5/scratchpad/pol'
os.makedirs(SCR, exist_ok=True); W = int(os.environ.get('WORKERS', 2))
def job(p):
    inst = os.path.basename(p)[:-4]; I = hy_sp.load_instance(p)
    out = f'runs/hy_runs/{d}/polish_t{T}_s{sd}/{inst}.out'
    if os.path.exists(out): return None
    sols = [(hy_sp.score(I, (r + [[]] * I['V'])[:I['V']]), r) for r in hy_sp.solutions(d, inst)]
    sols = sorted([x for x in sols if x[0]], key=lambda x: x[0])
    if not sols: return None
    files, seen = [], set()
    for sc, r in sols:
        key = tuple(tuple(x) for x in r)
        if key in seen: continue
        seen.add(key); f = f'{SCR}/{inst}_{sd}_{len(files)}.out'
        open(f, 'w').write(''.join(f"ROUTE {v} {' '.join(map(str, x))}\n" for v, x in enumerate(r))); files.append(f)
        if len(files) >= K: break
    r = subprocess.run(['bin/s40_hy', p, T, sd], env=dict(os.environ, INIT=','.join(files)), capture_output=True, text=True).stdout
    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, 'w').write(r)
    rr, _ = parse_output(r); sc = hy_sp.score(I, (rr + [[]] * I['V'])[:I['V']])
    if sc and sc < sols[0][0]:
        hy_sp.save(d, inst, sc, (rr + [[]] * I['V'])[:I['V']], 'hy_polish')
        return (inst, sols[0][0], sc)
    return (inst, sols[0][0], sc, 'no')
insts = [p for p in sorted(glob.glob(f'{d}/*.txt')) if flt in p]
with ThreadPoolExecutor(W) as ex:
    for x in ex.map(job, insts):
        if x: print(*x, flush=True)
