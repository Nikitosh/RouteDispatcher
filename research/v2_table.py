"""Таблица по задачам для SOLUTION.md: лучшее известное решение, нижние границы, статус, продукт (s76 3 с, 3 сида).
python3 v2_table.py <набор> [...]"""
import glob, json, os, statistics as st, sys
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
def score(I, path):
    if not os.path.exists(path): return None
    R, _ = parse_output(open(path).read()); R = (R + [[]] * I['V'])[:I['V']]; c = check(I, R)
    if not c['ok']: return None
    served = {k for r in R for k in r}
    return (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served), c['used'], c['km'])
f1 = lambda x: f'{x:.1f}'.replace('.', ',')
for d in sys.argv[1:]:
    print(f'\n**{d}**\n\n| Задача | Лучшее: бригад / км | Граница: бригад / км | Статус | Продукт 3 с: бригад | км, медиана / худший |')
    print('|---|---|---|---|---|---|')
    for f in sorted(glob.glob(f'{d}/*.txt')):
        n = os.path.basename(f)[:-4]; I = load_instance(f)
        b = score(I, f'best/{d}/{n}.out'); j = json.load(open(f'lb/{d}/{n}.json')) if os.path.exists(f'lb/{d}/{n}.json') else {}
        P = [p for p in (score(I, f'runs/results/{d}/prod3_s{s}/{n}.out') for s in (1, 2, 3)) if p]
        if not b: continue
        lu, lk = j.get('lb_used'), j.get('lb_km')
        if j.get('proven_optimal'): stat = 'оптимум'
        elif lu is not None and lu == b[1] and lk: stat = f'парк доказан, км ≤ {100 * (b[2] / lk - 1):.1f}%'.replace('.', ',')
        elif lu is not None: stat = f'парк: граница {lu}'
        else: stat = 'границы нет'
        pen = f' (штраф {b[0]})' if b[0] else ''
        fleets = sorted({p[1] for p in P}); same = [p[2] for p in P if p[:2] == b[:2]]
        print(f"| {n} | {b[1]} / {f1(b[2])}{pen} | {lu if lu is not None else '—'} / {f1(lk) if lk else '—'} | {stat} | "
              f"{', '.join(map(str, fleets))} | {(f1(st.median(same)) + ' / ' + f1(max(same))) if same else '—'} |")
