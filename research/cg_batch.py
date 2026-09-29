"""Run cg_master.py over an instance dir with P parallel workers. Usage: cg_batch.py <dir> [P=2] [extra args...]"""
import sys, os, glob, subprocess, concurrent.futures as cf
d = sys.argv[1]; P = int(sys.argv[2]) if len(sys.argv) > 2 else 2; extra = sys.argv[3:]
HERE = os.path.dirname(os.path.abspath(__file__))
insts = sorted(glob.glob(os.path.join(HERE, d, '*.txt')))
only = os.environ.get('CG_ONLY')
if only: insts = [p for p in insts if any(o in p for o in only.split(','))]
os.makedirs(os.path.join(HERE, 'results', d, 'cg'), exist_ok=True)
if os.environ.get('CG_SKIP_DONE'): insts = [p for p in insts if not os.path.exists(os.path.join(HERE, 'lb', d, os.path.basename(p)[:-4] + '.json'))]
def run(p):
    name = os.path.basename(p)[:-4]; lf = os.path.join(HERE, 'results', d, 'cg', name + '.log')
    if os.environ.get('CG_CLAIM'):
        try: os.close(os.open(os.path.join(HERE, 'results', d, 'cg', name + '.claim'), os.O_CREAT | os.O_EXCL))
        except FileExistsError: return name, -1, 'claimed by another worker'
        if os.path.exists(os.path.join(HERE, 'lb', d, name + '.json')): return name, -1, 'done'
    with open(lf, 'w') as f:
        env = {**os.environ, 'CG_WORKERS': os.environ.get('CG_WORKERS', str(max(1, (os.cpu_count() or 8) // P)))}   # потоки CP-SAT и cg_price на задачу
        r = subprocess.run([os.path.join(HERE, '.venv/bin/python'), os.path.join(HERE, 'cg_master.py'), p] + extra, stdout=f, stderr=subprocess.STDOUT, env=env)
    last = [l for l in open(lf) if 'RESULT' in l]
    return name, r.returncode, (last[-1].strip()[:300] if last else open(lf).read()[-500:])
with cf.ThreadPoolExecutor(P) as ex:
    for name, rc, msg in ex.map(run, insts): print(name, rc, msg, flush=True)
