"""Улучшение лучших известных решений на незакрытых задачах, параллельно с доказательствами (nice 20 — только свободное время).
Ждёт конца продуктовых прогонов (строка «best done» в v3_run.log), затем по кругу: задачи без доказанного оптимума,
сначала с наибольшим разрывом к границе (задачи без границы — в конце), s76 60 с с новыми сидами →
runs/results/<набор>/s76_60s_s<сид>/. Останавливается по файлу v3_ub.stop или после строки «ALL DONE» в логе."""
import glob, json, os, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
DIRS = ['instances_control_v2', 'instances_road_v2', 'instances_gen_v2', 'instances_newctl_v2', 'instances_newroad_v2']
log = lambda: open('v3_run.log').read() if os.path.exists('v3_run.log') else ''
while 'best done' not in log(): time.sleep(20)
def todo():
    rows = []
    for d in DIRS:
        for f in sorted(glob.glob(f'{d}/*.txt')):
            n = os.path.basename(f)[:-4]; p = f'lb/{d}/{n}.json'
            j = json.load(open(p)) if os.path.exists(p) else None
            if j and j.get('proven_optimal'): continue
            if j is None: gap = -1
            elif j.get('best_used') != j.get('lb_used'): gap = 1.0
            else: gap = j['best_km'] / j['lb_km'] - 1 if j.get('lb_km') else 0.5
            if j is None or gap > 0.005: rows.append((gap, d, n, f))
    return sorted(rows, reverse=True)
seed = 21
while True:
    for gap, d, n, f in todo():
        if os.path.exists('v3_ub.stop') or 'ALL DONE' in log(): raise SystemExit
        o = f'runs/results/{d}/s76_60s_s{seed}/{n}.out'; os.makedirs(os.path.dirname(o), exist_ok=True)
        if os.path.exists(o) and os.path.getsize(o): continue
        with open(o, 'w') as fh: subprocess.run(['nice', '-n', '20', 'bin/s76_coopG2', f, '60', str(seed)], stdout=fh)
        print(time.strftime('%H:%M'), d, n, f'разрыв {gap:.3f}', open(o).read().strip().splitlines()[-1], flush=True)
    seed += 1
