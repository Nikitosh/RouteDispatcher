"""Непрерывное доведение лучших решений и доказательств на 178 задачах модели продукта (с 29.09, после v3_run.sh).
Круг r (сид 30 + r):
  1. улучшение: s76 120 с на всём процессоре, до UB_N задач — сначала незакрытый парк и штраф, затем наибольший разрыв км;
  2. best.py; cg_master (лимит 15 мин, 6 потоков) там, где лучшее решение изменилось;
  3. cgc_master (лимит 45 мин, 6 потоков) на CGC_N задачах с доказанным парком и наименьшим разрывом км;
  4. best.py, cg_finalize, v2_report.md, v2_tables.md.
Останавливается по файлу v4.stop (проверка между задачами). Лог: v4_loop.log."""
import glob, json, os, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
DIRS = ['instances_control_v2', 'instances_road_v2', 'instances_gen_v2', 'instances_newctl_v2', 'instances_newroad_v2']
UB_N, CGC_N = 40, 12


def log(*a): print(time.strftime('%H:%M'), *a, flush=True)
def stop(): return os.path.exists('v4.stop')


def state():
    """{задача: (категория, разрыв, (штраф, бригады, км))} для незакрытых."""
    out = {}
    for d in DIRS:
        meta = json.load(open(f'best/{d}/_meta.json'))
        for f in sorted(glob.glob(f'{d}/*.txt')):
            n = os.path.basename(f)[:-4]; p = f'lb/{d}/{n}.json'; m = meta[n]
            j = json.load(open(p)) if os.path.exists(p) else {}
            if j.get('proven_optimal'): continue
            key = (m['pen'], m['used'], m['km'])
            if m['pen'] > j.get('lb_pen', 0): out[f] = ('штраф', 1.0, key)
            elif m['used'] > j.get('lb_used', 0): out[f] = ('парк', 1.0, key)
            else: out[f] = ('км', m['km'] / j['lb_km'] - 1 if j.get('lb_km') else 0.5, key)
    return out


def best_all():
    subprocess.run(['python3', 'best.py'] + DIRS, capture_output=True)


def run_cg(mode, P, tl, files, tag):
    if not files: return
    lst = f'v4_{tag}.txt'; open(lst, 'w').write('\n'.join(files) + '\n')
    subprocess.run(['python3', 'v3_cg.py', mode, str(P), str(tl), lst], stdout=open('v4_cg.log', 'a'), stderr=subprocess.STDOUT)


r = 0
while not stop():
    seed = 30 + r; st = state()
    log(f'круг {r}: незакрыто {len(st)} (парк {sum(v[0] == "парк" for v in st.values())}, штраф {sum(v[0] == "штраф" for v in st.values())}, км {sum(v[0] == "км" for v in st.values())})')
    order = sorted(st, key=lambda f: (st[f][0] == 'км', -st[f][1]))[:UB_N]
    for f in order:
        if stop(): break
        d, n = f.split('/')[0], os.path.basename(f)[:-4]; o = f'runs/results/{d}/s76_120s_s{seed}/{n}.out'
        os.makedirs(os.path.dirname(o), exist_ok=True)
        if os.path.exists(o) and os.path.getsize(o): continue
        with open(o + '.tmp', 'w') as fh: subprocess.run(['bin/s76_coopG2', f, '120', str(seed)], stdout=fh)
        os.replace(o + '.tmp', o)
    best_all(); st2 = state()
    changed = [f for f in st if f not in st2 or st2[f][2] < st[f][2]]
    log(f'улучшение: лучшее решение изменилось в {len(changed)} задачах: ' + ', '.join(os.path.basename(f)[:-4] for f in changed))
    if stop(): break
    run_cg('cg', 6, 900, [f for f in changed if f in st2], f'cg_r{r}')
    best_all(); st3 = state()
    km = sorted([f for f in st3 if st3[f][0] == 'км'], key=lambda f: st3[f][1])[:CGC_N]
    if not stop(): run_cg('cgc', 6, 2700, km, f'cgc_r{r}')
    best_all(); subprocess.run(['.venv/bin/python', 'cg_finalize.py'] + DIRS, capture_output=True)
    subprocess.run('python3 v2_report.py > v2_report.md; python3 v2_table.py instances_control_v2 instances_newctl_v2 > v2_tables.md', shell=True)
    opt = sum(1 for d in DIRS for p in glob.glob(f'lb/{d}/*.json') if json.load(open(p)).get('proven_optimal'))
    log(f'круг {r} готов: доказанных оптимумов {opt} из 178, незакрыто {len(state())}')
    r += 1
log('остановлен')
