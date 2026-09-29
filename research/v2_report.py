"""Сводка бенчмарка модели продукта: продуктовый решатель (s76, 3 с, 3 сида) против лучших известных и точных границ."""
import glob, json, os, statistics as st
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
def score(I, path):
    if not os.path.exists(path): return None
    R, _ = parse_output(open(path).read()); R = (R + [[]] * I['V'])[:I['V']]; c = check(I, R)
    if not c['ok']: return None
    served = {k for r in R for k in r}
    return (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served), c['used'], c['km'])
print('# Бенчмарк модели продукта (OSRM × 1,3 для машины, подход 5/3 мин, «только машина»)\n')
print('| Набор | Задач | Бригады = нижней границе (доказан минимум) | Продукт: лишних бригад к лучшему | Продукт: км к лучшему при равных бригадах | Лучшее: км к нижней границе | Решение доказанно оптимально |')
print('|---|---|---|---|---|---|---|')
for d in ['instances_control_v2', 'instances_road_v2', 'instances_gen_v2', 'instances_newctl_v2', 'instances_newroad_v2']:
    rows = []
    for f in sorted(glob.glob(f'{d}/*.txt')):
        n = os.path.basename(f)[:-4]; I = load_instance(f)
        lb = json.load(open(f'lb/{d}/{n}.json')) if os.path.exists(f'lb/{d}/{n}.json') else {}
        best = score(I, f'best/{d}/{n}.out'); prods = [p for p in (score(I, f'runs/results/{d}/prod3_s{s}/{n}.out') for s in (1, 2, 3)) if p]
        if not best or not prods: continue
        rows.append(dict(n=n, best=best, prods=prods, lb_used=lb.get('lb_used'), lb_km=lb.get('lb_km'), opt=lb.get('proven_optimal', False)))
    k = len(rows)
    fleet_opt = sum(1 for r in rows if r['lb_used'] is not None and r['best'][1] == r['lb_used'] and r['best'][0] == 0)
    extra = st.mean(p[1] - r['best'][1] + (p[0] - r['best'][0]) / 20 for r in rows for p in r['prods'])
    gaps = [100 * (p[2] / r['best'][2] - 1) for r in rows for p in r['prods'] if p[:2] == r['best'][:2]]
    lbg = [100 * (r['best'][2] / r['lb_km'] - 1) for r in rows if r['lb_km'] and r['best'][1] == r['lb_used']]
    print(f"| {d} | {k} | {fleet_opt} из {k} | {extra:+.2f} в среднем на прогон | {st.mean(gaps):+.1f}% ({len(gaps)} прогонов) | "
          f"{(st.mean(lbg) if lbg else float('nan')):.1f}% (медиана {(st.median(lbg) if lbg else float('nan')):.1f}%) | {sum(r['opt'] for r in rows)} из {k} |")
print('\nПо задачам: `best/<набор>/_meta.json`, границы: `lb/<набор>/`.')
