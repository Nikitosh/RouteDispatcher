"""Пакетный запуск доказательств с жёстким лимитом на задачу (cg_batch.py ждёт зависшую задачу бесконечно).
python3 v3_cg.py <cg|cgc> <P> <лимит, с> <список задач или папка ...>  — cg: cg_master.py --tl, cgc: cgc_master.py --tl.
Задачи, у которых уже есть доказанный оптимум в lb/, пропускаются. По таймауту процесс снимается вместе с детьми."""
import glob, json, os, signal, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); PY = os.path.join(HERE, '.venv/bin/python')
mode, P, TL = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
insts = []
for a in sys.argv[4:]:
    insts += sorted(glob.glob(os.path.join(a, '*.txt'))) if os.path.isdir(a) else [l.strip() for l in open(a) if l.strip()]
def done(p):
    d, n = os.path.dirname(p), os.path.basename(p)[:-4]; f = os.path.join(HERE, 'lb', os.path.basename(d), n + '.json')
    return os.path.exists(f) and json.load(open(f)).get('proven_optimal')
def run(p):
    if done(p): return p, 'уже доказано', 0
    d, n = os.path.basename(os.path.dirname(p)), os.path.basename(p)[:-4]
    os.makedirs(os.path.join(HERE, 'results', d, mode), exist_ok=True)
    script = 'cg_master.py' if mode == 'cg' else 'cgc_master.py'
    args = [PY, os.path.join(HERE, script), os.path.join(HERE, p), '--tl', str(int(TL * 0.9))]
    t0 = time.time()
    with open(os.path.join(HERE, 'results', d, mode, n + '.log'), 'w') as f:
        pr = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, cwd=HERE, start_new_session=True)
        try:
            pr.wait(timeout=TL); st = f'код {pr.returncode}'
        except subprocess.TimeoutExpired:
            os.killpg(pr.pid, signal.SIGKILL); pr.wait(); st = 'снят по таймауту'
    return p, st, time.time() - t0
with ThreadPoolExecutor(P) as ex:
    for p, st, t in ex.map(run, insts):
        print(f'{time.strftime("%H:%M")} {p}: {st}, {t:.0f} с, доказано={bool(done(p))}', flush=True)
