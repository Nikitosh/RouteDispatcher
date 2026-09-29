"""Прогон набора решателей x сидов на папке задач, сохраняет вывод в runs/hy_runs/<папка>/<solver>_t<tl>_s<seed>/<inst>.out
python3 hy_runs.py <instdir> <tl> <seeds: 1,2,3> <solvers: s10_sa,s12_alns,pyvrp> [filter] ; WORKERS=2"""
import glob, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
d, tl, seeds, solvers = sys.argv[1], sys.argv[2], sys.argv[3].split(','), sys.argv[4].split(',')
flt = sys.argv[5] if len(sys.argv) > 5 else ''
EXT = {'ortools': 'ortools_ref.py', 'pyvrp': 'pyvrp_ref.py', 'vroom': 'vroom_ref.py'}
W = int(os.environ.get('WORKERS', 2))
insts = [p for p in sorted(glob.glob(f'{d}/*.txt')) if flt in p]
jobs = []
for s in solvers:
    for sd in seeds:
        for p in insts:
            out = f'runs/hy_runs/{os.path.basename(d.rstrip("/"))}/{s}_t{tl}_s{sd}/{os.path.basename(p)[:-4]}.out'
            if not os.path.exists(out): jobs.append((s, p, sd, out))
def run(j):
    s, p, sd, out = j
    cmd = ['.venv/bin/python', EXT[s], p, tl, sd] if s in EXT else [f'bin/{s}', p, tl, sd]
    try: r = subprocess.run(cmd, capture_output=True, text=True, timeout=float(tl) * 5 + 60).stdout
    except Exception as e: print('FAIL', j, e); return
    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, 'w').write(r)
with ThreadPoolExecutor(W) as ex: list(ex.map(run, jobs))
print('done', len(jobs))
