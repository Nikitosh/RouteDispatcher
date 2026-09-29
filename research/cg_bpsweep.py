"""Run cg_bp.py on every instance of a dir whose lb json is not proven optimal (fleet+penalty proven, km gap open).
Usage: cg_bpsweep.py <dir> <tl_per_instance> [min_gap_pct=0.01]"""
import sys, os, glob, json, subprocess
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
d, tl = sys.argv[1], sys.argv[2]; mg = float(sys.argv[3]) if len(sys.argv) > 3 else 0.01
HERE = os.path.dirname(os.path.abspath(__file__))
todo = []
for f in sorted(glob.glob(f'{HERE}/lb/{d}/*.json')):
    name = os.path.basename(f)[:-5]
    if name.startswith('_'): continue
    L = json.load(open(f)); bf = f'{HERE}/best/{d}/{name}.out'
    if not os.path.exists(bf): continue
    I = load_instance(f'{HERE}/{d}/{name}.txt'); R, _ = parse_output(open(bf).read()); R = (R + [[]] * I['V'])[:I['V']]
    c = check(I, R); served = {k for r in R for k in r}; pen = sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served)
    if L.get('lb_used') != c['used'] or not L.get('lb_km'): continue
    gap = (c['km'] / L['lb_km'] - 1) * 100
    if gap > mg: todo.append((gap, name))
print('todo', len(todo), flush=True)
for gap, name in sorted(todo):
    lf = f'{HERE}/runs/results/{d}/cg/{name}.bp.log'
    subprocess.run([f'{HERE}/.venv/bin/python', f'{HERE}/cg_bp.py', f'{HERE}/{d}/{name}.txt', '--tl', tl], stdout=open(lf, 'w'), stderr=subprocess.STDOUT)
    last = [l for l in open(lf) if 'B&P done' in l or 'lb json' in l]
    print(name, f'gap0 {gap:.2f}%', ' | '.join(x.strip() for x in last), flush=True)
