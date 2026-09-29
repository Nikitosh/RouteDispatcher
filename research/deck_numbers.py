"""Цифры для презентации по рабочему набору (88 задач в модели продукта): контрольный день (24), реальные дни 28–29.09
(40), сгенерированные участки (24).

Наш план — готовые прогоны продуктового решателя s76 (3 с, сиды 1–3, runs/results/<набор>/prod3_s<сид>/); переписанный
solver/bin/plan от него статистически не отличается. Базовый вариант ТЗ считается здесь: solver/bin/baseline,
ответы в runs/results/<набор>/baseline/. Лучшие известные решения — best/, нижние границы — lb/. Каждый план
проверяется заново (validate.py). Запуск из research/: .venv/bin/python deck_numbers.py > deck_report.md"""
import glob
import json
import os
import statistics as st
import subprocess

from validate import check, load_instance, parse_output

SETS = {'instances_control_v2': 'контрольный день', 'instances_days_v2': 'реальные дни 28–29.09',
        'instances_newctl_v2': 'сгенерированные участки'}
SEEDS = (1, 2, 3)
PEN = {1: 100, 2: 50, 3: 20}
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'solver', 'bin', 'baseline')


def evaluate(I, R):
    R = (R + [[]] * I['V'])[:I['V']]
    c = check(I, R)
    assert c['ok'], c['errs'][:3]
    served = {k for r in R for k in r}
    pen = sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served)
    return dict(pen=pen, unserved=c['unserved'], used=c['used'], km=c['km'])


def baseline_routes(d, name, task):
    path = f'runs/results/{d}/baseline/{name}.json'
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = subprocess.run([BASELINE, task], capture_output=True, text=True, check=True).stdout
        open(path, 'w').write(out)
    return json.load(open(path))['routes']


def load_rows():
    rows = []
    for d in SETS:
        meta = json.load(open(f'best/{d}/_meta.json'))
        for task in sorted(glob.glob(f'{d}/*.txt')):
            name = os.path.basename(task)[:-4]
            if name not in meta:
                continue
            I = load_instance(task)
            lb = json.load(open(f'lb/{d}/{name}.json'))
            best = evaluate(I, parse_output(open(f'best/{d}/{name}.out').read())[0])
            plans = [evaluate(I, parse_output(open(f'runs/results/{d}/prod3_s{s}/{name}.out').read())[0]) for s in SEEDS]
            base = evaluate(I, baseline_routes(d, name, task))
            rows.append(dict(set=d, name=name, N=I['N'], V=I['V'], lb=lb, best=best, plans=plans, base=base))
    return rows


# Штраф и число бригад равны нижним границам: лучше по (штраф, бригады) нельзя.
def fleet_minimal(p, lb):
    return p['pen'] == lb['lb_pen'] and p['used'] == lb['lb_used']


def optimal(p, lb):
    return fleet_minimal(p, lb) and lb.get('lb_km') is not None and lb.get('km_K') == p['used'] and p['km'] <= lb['lb_km'] * (1 + 1e-4) + 1e-3


def summary(rows, title):
    runs = [(r, p) for r in rows for p in r['plans']]
    n, nr = len(rows), len(runs)
    fleet_lb = sum(fleet_minimal(r['best'], r['lb']) for r in rows)
    fleet_any = sum(any(fleet_minimal(p, r['lb']) for p in r['plans']) for r in rows)
    fleet_runs = sum(fleet_minimal(p, r['lb']) for r, p in runs)
    fleet_all = sum(all(fleet_minimal(p, r['lb']) for p in r['plans']) for r in rows)
    opt_runs = sum(optimal(p, r['lb']) for r, p in runs)
    opt_tasks = sum(any(optimal(p, r['lb']) for p in r['plans']) for r in rows)
    same = [(r, p) for r, p in runs if (p['pen'], p['used']) == (r['best']['pen'], r['best']['used'])]
    km_to_best = st.mean(r['best']['km'] / p['km'] for r, p in same)
    with_lb = [(r, p) for r, p in runs if fleet_minimal(p, r['lb']) and r['lb'].get('lb_km') and r['lb']['km_K'] == p['used']]
    km_to_lb = st.mean(r['lb']['lb_km'] / p['km'] for r, p in with_lb)
    plan_used = st.mean(p['used'] for _, p in runs) * n
    base_used = sum(r['base']['used'] for r in rows)
    plan_un = st.mean(p['unserved'] for _, p in runs) * n
    base_un = sum(r['base']['unserved'] for r in rows)
    orders = sum(r['N'] for r in rows)
    plan_km = st.mean(p['km'] for _, p in runs) * n
    base_km = sum(r['base']['km'] for r in rows)
    print(f'## {title}: {n} задач, {nr} прогонов\n')
    print(f'- Парк лучшего известного решения равен нижней границе (минимум доказан): {fleet_lb} из {n} задач.')
    print(f'- План продукта использует доказанный минимум бригад: {fleet_runs} из {nr} прогонов '
          f'({100 * fleet_runs / nr:.0f}%); хотя бы в одном сиде — {fleet_any} из {n} задач, во всех трёх — {fleet_all}.')
    print(f'- План продукта теоретически оптимален (бригады и км равны нижним границам): {opt_runs} из {nr} прогонов '
          f'({100 * opt_runs / nr:.0f}%); хотя бы в одном сиде — {opt_tasks} из {n} задач ({100 * opt_tasks / n:.0f}%).')
    print(f'- Пробег при том же парке, что у лучшего известного: {100 * km_to_best:.1f}% от лучшего в среднем ({len(same)} прогонов).')
    print(f'- Пробег при доказанном минимуме парка: {100 * km_to_lb:.1f}% от нижней границы км в среднем ({len(with_lb)} прогонов).')
    print(f'- Бригады, сумма по задачам: план {plan_used:.1f} (среднее по сидам), базовый вариант {base_used} '
          f'(в {base_used / plan_used:.2f} раза больше).')
    forced = sum(r['lb']['lb_pen'] > 0 for r in rows)
    print(f'- Невыполненные заявки из {orders}: план {plan_un:.1f}, базовый вариант {base_un}. Задач, где невыполненная '
          f'заявка неизбежна (нижняя граница штрафа > 0): {forced}.')
    print(f'- Пробег, сумма: план {plan_km:.0f} км, базовый вариант {base_km:.0f} км.\n')


def per_task(rows):
    print('## По задачам\n')
    print('| Набор | Задача | Заявок | Бригад доступно | Граница бригад | План: бригады по сидам | План: км (медиана) | '
          'Граница км | Базовый: бригады / невыполнено / км |')
    print('|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        lb, b = r['lb'], r['base']
        kms = st.median(p['km'] for p in r['plans'])
        print(f"| {SETS[r['set']]} | {r['name']} | {r['N']} | {r['V']} | {lb['lb_used']} | "
              f"{' / '.join(str(p['used']) for p in r['plans'])} | {kms:.1f} | "
              f"{lb['lb_km']:.1f} | {b['used']} / {b['unserved']} / {b['km']:.1f} |" if lb.get('lb_km') else
              f"| {SETS[r['set']]} | {r['name']} | {r['N']} | {r['V']} | {lb['lb_used']} | "
              f"{' / '.join(str(p['used']) for p in r['plans'])} | {kms:.1f} | — | {b['used']} / {b['unserved']} / {b['km']:.1f} |")


def main():
    rows = load_rows()
    print('# Цифры для презентации: модель продукта, 88 задач\n')
    print('План — продуктовый решатель, 3 с на 8 потоках, сиды 1–3. Базовый вариант — жадный алгоритм из раздела 2.3 ТЗ.\n')
    summary(rows, 'Все наборы')
    for d, title in SETS.items():
        summary([r for r in rows if r['set'] == d], title.capitalize())
    per_task(rows)


if __name__ == '__main__':
    main()
