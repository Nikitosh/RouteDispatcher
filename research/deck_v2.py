"""Рабочий набор презентации (88 задач) в текущей модели продукта.

Те же заявки, бригады и варианты, что в instances_control_v2 / instances_days_v2 / instances_newctl_v2 (deck_numbers.py):
3 участка контрольного дня 17.08, реальные дни 28–29.09 (Восток ×2, Юго-восток ×2, Югоцентр ×1), 3 сгенерированных
участка; транспорт pt / car / mix1 / mix2 × навыки inf / all. Задачу собирает dispatch.model.problem.Problem так же, как
Planner.load, поэтому модель — продуктовая:
- обед 45 мин (13:00–15:15) при смене от 6 часов, смена 10:00–22:00;
- оборудование как опция клиента (config.equipment_for) и «унесёт» по транспорту (config.CARRY);
- длительность по коэффициенту сложности (cache/houses.json; дома, которых там нет, — норматив × подтип работы);
- общ. транспорт по расписаниям (data/transit/net, кэш matrix_pt), пешком, если по расписанию не доехать;
- пробки по часам окна и дню недели (сетка HERE, иначе районы), подход к двери;
- дома всех бригад — из data/demo/<участок> бригады.xlsx (python -m dispatch homes); у реальных дней — те же бригады,
  что у контрольного дня участка (как в v2_days.py);
- штраф за ожидание аварии wwait = config.WAIT_KM_PER_MIN (решатель plan по умолчанию продукта).
Правило из v2*.py сохранено: требование «только машина» снимается, если заявку с ним не может взять ни одна бригада.

Прогоны: solver/bin/plan 3 с, сиды 1–3 (лучший — по штрафу, бригадам, км + wwait·ожидание); solver/bin/baseline;
solver/bin/fleet_bound 60 с на лучшем плане. Каждый план проверяется dispatch.planning.validate.check_plan.
Всё по очереди, один процесс решателя за раз. Ничего не пишет в cache/ продукта: кэши копируются в
runs/results/deck_v2/cache (DISPATCH_CACHE), сеть Nominatim выключена (Geocoder(online=False)), OSRM — только localhost.

Запуск из корня репозитория:  .venv/bin/python research/deck_v2.py [run|report] [фильтр имени]
  run    — собрать задачи и прогнать решатели (продолжает с места остановки: готовые задачи пропускает);
  report — research/deck_report_v2.md по runs/results/deck_v2/tasks/*.json."""
import copy
import csv
import io
import json
import os
import re
import shutil
import statistics as st
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'research')
OUT = os.path.join(RES, 'runs', 'results', 'deck_v2')
CACHE = os.path.join(OUT, 'cache')
SEEDS = (1, 2, 3)
PLAN_SEC, BOUND_SEC = 3, 60


def prepare_cache():
    os.makedirs(CACHE, exist_ok=True)
    for f in ('geocode.json', 'houses.json', 'matrix_car.json', 'matrix_bike.json', 'matrix_foot.json', 'matrix_pt.json'):
        if not os.path.exists(os.path.join(CACHE, f)):
            shutil.copy(os.path.join(ROOT, 'cache', f), os.path.join(CACHE, f))
    walk = os.path.join(ROOT, 'data', 'transit', 'walk.json')
    if os.path.exists(walk) and not os.path.exists(os.path.join(CACHE, 'walk.json')):
        shutil.copy(walk, os.path.join(CACHE, 'walk.json'))


prepare_cache()
os.environ['DISPATCH_CACHE'] = CACHE
sys.path.insert(0, ROOT)

from dispatch.config import CARRY, PENALTY, SERVICE, WAIT_KM_PER_MIN, TravelSettings, weekday_of  # noqa: E402
from dispatch.files.readers import read_brigades, read_orders  # noqa: E402
from dispatch.files.tables import read_table  # noqa: E402
from dispatch.geo.geocoder import Geocoder  # noqa: E402
from dispatch.geo.houses import HouseIndex  # noqa: E402
from dispatch.geo.matrices import MatrixBuilder  # noqa: E402
from dispatch.model import complexity  # noqa: E402
from dispatch.model.domain import Brigade  # noqa: E402
from dispatch.model.problem import Problem  # noqa: E402
from dispatch.planning.validate import check_plan  # noqa: E402
from dispatch.solve.runner import SolverRunner  # noqa: E402
from dispatch.tools import transit as _transit  # noqa: E402
from dispatch.tools.demo_data import mode_of, needs_car, roster  # noqa: E402
from dispatch.tools.sources import organizers_file  # noqa: E402

assert os.path.dirname(HouseIndex().path) == CACHE


class _Walk(_transit.OsrmWalk):            # подход к остановкам: кэш — копия, а не data/transit/walk.json
    def __init__(self, client=None, path=None):
        super().__init__(client, path or os.path.join(CACHE, 'walk.json'))


_transit.OsrmWalk = _Walk

DEMO = os.path.join(ROOT, 'data', 'demo')
DAYS = os.path.join(ROOT, 'data', 'organizers', 'Реальные дни 28-29.09')
OLD = {'Восток': 'vostok', 'Юго-восток': 'yugovostok', 'Югоцентр': 'yugocentr'}
NEW = {'Север': 'sever', 'Запад': 'zapad', 'Северо-восток': 'severovostok'}
DAY_FILES = [('Восток', '28.09'), ('Восток', '29.09'), ('Юго-восток', '28.09'), ('Юго-восток', '29.09'), ('Югоцентр', '28.09')]
TMODES, SKV = ('pt', 'car', 'mix1', 'mix2'), ('inf', 'all')
SETS = {'instances_control_v2': 'контрольный день', 'instances_days_v2': 'реальные дни 28–29.09',
        'instances_newctl_v2': 'сгенерированные участки'}
BIN = os.path.join(ROOT, 'solver', 'bin')
GEO = Geocoder(online=False)


# ---------------------------------------------------------------- сборка задач
def homes(region):
    return {b.name: b.start_address for b in read_brigades(os.path.join(DEMO, f'{region} бригады.xlsx'))}


def brigade(name, mode, skills, start_address):
    return Brigade(name=name, mode=mode, skills=list(skills), start_address=start_address, carry=CARRY[mode])


def control_rosters(region):
    """Состав контрольного дня (demo_data.roster, транспорт mix — по настоящему имени бригады, как в control_mix.py);
    дома — из файла бригад демо."""
    home = homes(region)
    out = {}
    for tm in TMODES:
        rows = roster(region, tm, geocoder=GEO)
        out[tm] = [brigade(n, m, sk, home[n]) for n, m, sk, _ in rows]
    return out


def generated_rosters(region):
    base = read_brigades(os.path.join(DEMO, f'{region} бригады.xlsx'))
    return {tm: [brigade(b.name, mode_of(f'{region} {b.name}', False, tm), b.skills, b.start_address) for b in base]
            for tm in TMODES}


def day_orders(region, day):
    """Как v2_days.orders_of: отменённые выкинуть, «Транспорт» по needs_car, офис — из синтетики участка."""
    rows = read_table(os.path.join(DAYS, f'{region} {day}.csv'))
    h = rows[0]
    stc, bk, hd = h.index('Статус BK'), h.index('Тип заявки BK'), h.index('Тип заявки HD')
    synth = read_table(organizers_file(region, 'Синтетические'))
    office = next(r[1] for r in synth if r[0].lower().startswith('адрес'))
    keep = [r for r in rows[1:] if r[0] and r[stc] != 'Отменена']
    out = [h + ['Транспорт']] + [r + [''] * (len(h) - len(r)) + ['авто' if needs_car(r[bk], r[hd]) else ''] for r in keep]
    out.append(['Адрес офиса', office])
    src = os.path.join(OUT, 'src')
    os.makedirs(src, exist_ok=True)
    p = os.path.join(src, f'{OLD[region]}_{day}.csv')
    s = io.StringIO()
    csv.writer(s, delimiter=';').writerows(out)
    open(p, 'w', encoding='utf-8').write(s.getvalue())
    return read_orders(p)


def groups():
    """[(набор, префикс имени, файл заявок, составы по транспорту, дата)] — 11 групп по 8 задач."""
    out = []
    for region, tr in OLD.items():
        f = read_orders(os.path.join(DEMO, f'{region} заявки.xlsx'))
        out.append(('instances_control_v2', f'control_{tr}', f, control_rosters(region)))
    for region, day in DAY_FILES:
        f = day_orders(region, day)
        f.date = f.date or f'{day}.2026'
        out.append(('instances_days_v2', f"day_{OLD[region]}_{day.replace('.', '')}", f, control_rosters(region)))
    for region, tr in NEW.items():
        f = read_orders(os.path.join(DEMO, f'{region} заявки.xlsx'))
        out.append(('instances_newctl_v2', f'ctl_{tr}', f, generated_rosters(region)))
    return out


def build_group(f, rosters, houses):
    """Общие для 8 вариантов точки и матрицы (дома одни и те же), длительности по сложности."""
    orders = copy.deepcopy(f.orders)
    any_roster = next(iter(rosters.values()))
    coords = GEO.locate([f.office] + [o.house for o in orders] + [b.start_address for b in any_roster if b.start_address],
                        log=lambda *_: None)
    starts = [coords[f.office]]
    for b in any_roster:
        c = coords[b.start_address] if b.start_address else starts[0]
        if c not in starts:
            starts.append(c)
    for o in orders:
        o.lat, o.lon = coords[o.house]
    complexity.apply(orders, houses)
    known = sum(1 for o in orders if houses.get(o.house))
    travel = TravelSettings(weekday=weekday_of(f.date))
    mats = MatrixBuilder().build(starts + [(o.lat, o.lon) for o in orders], len(starts), travel)
    return orders, starts, coords, mats, known, travel.weekday


def variant(name, orders, rosters, tm, sv, starts, coords, mats):
    brs = []
    for b in rosters[tm]:
        x = copy.copy(b)
        x.skills = list(b.skills) if sv == 'inf' else [0, 1, 2]
        c = coords[b.start_address] if b.start_address else starts[0]
        x.start, (x.lat, x.lon) = starts.index(c), c
        brs.append(x)
    os_ = copy.deepcopy(orders)
    dropped = 0
    for o in os_:
        if o.need and not any(o.skill in b.skills and b.mode in o.need for b in brs):
            o.need, dropped = None, dropped + 1
    return Problem(name, os_, brs, starts, mats), dropped


def stage1_match(set_name, name, P):
    """Заявки совпадают со старой задачей: номер, начало окна, приоритет, навык (без класса транспорта)."""
    path = os.path.join(RES, set_name, f'{name}.txt')
    if not os.path.exists(path):
        return None
    L = open(path).read().split('\n')
    N, V, S = map(int, L[1].split())
    old = [(l.split()[0], int(float(l.split()[2])), int(l.split()[4]), int(l.split()[5]) % 3) for l in L[2:2 + N]]
    new = [(str(o.id), o.earliest, o.priority, o.skill) for o in P.orders]
    return dict(same_orders=sorted(old) == sorted(new), N_old=N, V_old=V, S_old=S)


# ---------------------------------------------------------------- решатели
def run_json(args, timeout=900):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f'{args[0]}: {p.stderr[-500:]}')
    return json.loads(p.stdout.strip().splitlines()[-1])


def evaluate(P, routes):
    routes = (list(routes) + [[]] * len(P.brigades))[:len(P.brigades)]
    c = check_plan(P, routes)
    served = {k for r in routes for k in r}
    un = [k for k in range(len(P.orders)) if k not in served]
    wait = 0.0
    km = 0.0
    lunches = 0
    for v, r in enumerate(routes):
        if not r:
            continue
        s = P.schedule(v, r)
        km += s.km
        lunches += s.lunch is not None
        wait += sum(x.start - P.orders[x.order].earliest for x in s.stops if P.orders[x.order].priority == 1)
    return dict(ok=c.ok, errors=c.errors[:5], used=sum(1 for r in routes if r), km=round(km, 3),
                unserved=len(un), unserved_ids=[P.orders[k].id for k in un],
                pen=sum(PENALTY[P.orders[k].priority] for k in un), wait=round(wait, 1), lunches=lunches,
                obj=round(km + WAIT_KM_PER_MIN * wait, 3), routes=routes)


def forced_unserved(P, ev):
    """Невыполненные заявки, которые не берёт ни одна бригада даже в одиночку — их не выполнит никакой план."""
    return [k for k in range(len(P.orders)) if P.orders[k].id in ev['unserved_ids']
            and not any(P.can(v, k) and P.schedule(v, [k]).feasible for v in range(len(P.brigades)))]


def solve_task(set_name, name, P, meta):
    path = os.path.join(OUT, 'tasks', f'{name}.json')
    if os.path.exists(path):
        return
    wd = os.path.join(OUT, 'work', name)
    os.makedirs(wd, exist_ok=True)
    task = os.path.join(wd, 'task.txt')
    P.write(task)
    opts = SolverRunner.options()
    plans = []
    for seed in SEEDS:
        t0 = time.time()
        ans = run_json([os.path.join(BIN, 'plan'), task, PLAN_SEC, seed] + opts)
        json.dump(ans, open(os.path.join(wd, f'plan_s{seed}.json'), 'w'))
        ev = evaluate(P, ans['routes'])
        ev.update(seed=seed, sec=round(time.time() - t0, 2))
        plans.append(ev)
    best = min(range(len(plans)), key=lambda i: (plans[i]['pen'], plans[i]['used'], plans[i]['obj']))
    answer = os.path.join(wd, f'plan_s{SEEDS[best]}.json')
    t0 = time.time()
    base_ans = run_json([os.path.join(BIN, 'baseline'), task, 1, 1])
    json.dump(base_ans, open(os.path.join(wd, 'baseline.json'), 'w'))
    base = evaluate(P, base_ans['routes'])
    base['sec'] = round(time.time() - t0, 2)
    t0 = time.time()
    bound = run_json([os.path.join(BIN, 'fleet_bound'), task, answer, BOUND_SEC], timeout=BOUND_SEC * 3 + 60)
    bound['sec'] = round(time.time() - t0, 2)
    forced = forced_unserved(P, plans[best])
    work = sum(o.service for o in P.orders)
    norm = sum(SERVICE[o.bk] for o in P.orders)
    doc = dict(set=set_name, name=name, N=len(P.orders), V=len(P.brigades), S=P.n_starts, best=best, plans=plans, base=base,
               bound=bound, forced_unserved=[P.orders[k].id for k in forced], work_min=work, norm_min=norm,
               need_car=sum(1 for o in P.orders if o.need), equip_units=sum(sum(o.demand.values()) for o in P.orders),
               traffic_grid=P.traffic_grid, **meta)
    for d in plans + [base]:
        d.pop('routes')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(doc, open(path, 'w'), ensure_ascii=False, indent=1)
    b = plans[best]
    print(f"{name}: план {b['used']} бр {b['km']:.1f} км, не вып. {b['unserved']}, граница {bound.get('fleet_lower_bound')} "
          f"({bound['sec']:.0f} с); базовый {base['used']} бр, не вып. {base['unserved']}", flush=True)


def run(flt=''):
    houses = HouseIndex()
    for set_name, prefix, f, rosters in groups():
        names = [f'{prefix}_{tm}_{sv}' for tm in TMODES for sv in SKV]
        if not any(flt in n for n in names):
            continue
        if all(os.path.exists(os.path.join(OUT, 'tasks', f'{n}.json')) for n in names if flt in n):
            continue
        t0 = time.time()
        orders, starts, coords, mats, known, weekday = build_group(f, rosters, houses)
        print(f'{prefix}: {len(orders)} заявок, стартов {len(starts)}, дома известны {known}, день недели {weekday}, '
              f'матрицы {time.time() - t0:.0f} с', flush=True)
        for tm in TMODES:
            for sv in SKV:
                name = f'{prefix}_{tm}_{sv}'
                if flt not in name:
                    continue
                P, dropped = variant(name, orders, rosters, tm, sv, starts, coords, mats)
                meta = dict(need_dropped=dropped, houses_known=known, weekday=weekday, date=f.date,
                            stage1=stage1_match(set_name, name, P))
                solve_task(set_name, name, P, meta)


# ---------------------------------------------------------------- отчёт
def old_rows():
    """Старые цифры по задачам: таблица из deck_report.md (deck_numbers.py)."""
    out = {}
    for line in open(os.path.join(RES, 'deck_report.md'), encoding='utf-8'):
        p = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(p) == 9 and re.match(r'(control|day|ctl)_', p[1]):
            bu, bun, bkm = [x.strip() for x in p[8].split('/')]
            out[p[1]] = dict(lb=int(p[4]), seeds=[int(x) for x in p[5].split('/')], km=float(p[6]),
                             lb_km=None if p[7] == '—' else float(p[7]), base_used=int(bu), base_un=int(bun), base_km=float(bkm))
    return out


def task_bounds(d):
    """Доказанные нижние границы задачи, верные для любого плана: (штраф, бригады при этом штрафе или None).
    Штраф — penalty_bound. Бригады — максимум из верных доводов: fleet_bound (лучшего плана 3 с и 30 с), если
    множество выполненных заявок при минимальном штрафе единственно (всё выполнено или не выполнены только те, что
    не берёт никто); парк по оборудованию; перебор выборов клики (alternatives). Пока штраф не доказан, граница
    бригад не выводится."""
    pb = d.get('pen_bound') or {}
    lb_pen = pb.get('lb_pen', 0)
    best = known_best(d)
    if best['pen'] != lb_pen:
        return lb_pen, None
    cands = []
    unique = lb_pen == pb.get('forced_pen', 0)
    if unique:
        b3 = d['plans'][d['best']]
        if b3['pen'] == lb_pen:
            cands.append(d['bound'].get('fleet_lower_bound'))
        lg = d.get('long')
        if lg and lg['runs'][lg['best']]['pen'] == lb_pen:
            cands.append(lg['bound'].get('fleet_lower_bound'))
    cands.append(pb.get('fleet_cap'))
    cands.append((d.get('alt') or {}).get('fleet_lb'))
    cands = [c for c in cands if c is not None]
    return lb_pen, max(cands) if cands else None


def known_best(d):
    """Лучший план из прогонов 3 с и 30 с по (штраф, бригады, км + ожидание)."""
    runs = list(d['plans']) + (d['long']['runs'] if d.get('long') else [])
    return min(runs, key=lambda p: (p['pen'], p['used'], p['obj']))


def run_proven(d, p):
    lb_pen, lb_used = task_bounds(d)
    return p['pen'] == lb_pen and lb_used is not None and p['used'] == lb_used


def proven(d):
    """Минимум (штраф, бригады) доказан для лучшего плана продукта (3 с, лучший из трёх сидов)."""
    return run_proven(d, d['plans'][d['best']])


def why_not(d):
    lb_pen, lb_used = task_bounds(d)
    b = d['plans'][d['best']]
    kb = known_best(d)
    if b['pen'] > lb_pen:
        s = f"штраф {b['pen']} > граница {lb_pen}"
    else:
        s = f"бригад {b['used']} > граница {lb_used}"
    if kb is not b:
        s += f"; за 30 с — {kb['used']} бр, штраф {kb['pen']}"
        if run_proven(d, kb):
            s += ' (доказано)'
    return s


def pct(a, b):
    return f'{100 * a / b:.0f}%' if b else '—'


def summary(rows, title, w):
    n = len(rows)
    runs = [(d, p) for d in rows for p in d['plans']]
    pr = sum(proven(d) for d in rows)
    pr_known = sum(run_proven(d, known_best(d)) for d in rows)
    at_lb = sum(run_proven(d, p) for d, p in runs)
    any_lb = sum(any(run_proven(d, p) for p in d['plans']) for d in rows)
    allpen = sum(1 for d in rows if known_best(d)['pen'] == task_bounds(d)[0])
    gap = [(d['name'], why_not(d)) for d in rows if not proven(d)]
    plan_used = sum(d['plans'][d['best']]['used'] for d in rows)
    plan_used_mean = st.mean(p['used'] for _, p in runs) * n
    base_used = sum(d['base']['used'] for d in rows)
    orders = sum(d['N'] for d in rows)
    plan_un = sum(d['plans'][d['best']]['unserved'] for d in rows)
    base_un = sum(d['base']['unserved'] for d in rows)
    plan_km = sum(d['plans'][d['best']]['km'] for d in rows)
    base_km = sum(d['base']['km'] for d in rows)
    forced = sum(1 for d in rows if task_bounds(d)[0] > 0)
    bad = [(d['name'], p['seed'], p['errors']) for d, p in runs if not p['ok']] + \
          [(d['name'], 'baseline', d['base']['errors']) for d in rows if not d['base']['ok']] + \
          [(d['name'], 'long', p['errors']) for d in rows if d.get('long') for p in d['long']['runs'] if not p['ok']]
    w(f'## {title}: {n} задач, {len(runs)} прогонов плана\n')
    w(f'- План продукта (3 с, лучший из трёх сидов) на доказанном минимуме (штраф, затем бригады): **{pr} из {n}** '
      f'({pct(pr, n)}); с прогонами по 30 с — {pr_known} из {n}.')
    w(f'- Прогонов 3 с на доказанном минимуме: {at_lb} из {len(runs)}; задач, где на нём хотя бы один сид: {any_lb} из {n}.')
    w(f'- Минимальный штраф доказан для лучшего известного плана в {allpen} из {n} задач; задач, где невыполненная заявка '
      f'неизбежна (граница штрафа > 0): {forced}.')
    if gap:
        w(f'- Не доказано ({len(gap)}): ' + '; '.join(f'{nm} — {s}' for nm, s in gap) + '.')
    w(f'- Бригады, сумма: план {plan_used} (лучший сид; среднее по сидам {plan_used_mean:.1f}), базовый вариант '
      f'{base_used} — на {100 * (base_used / plan_used - 1):.0f}% больше.')
    w(f'- Невыполненные заявки из {orders}: план {plan_un}, базовый вариант {base_un} ({100 * base_un / orders:.0f}%).')
    w(f'- Пробег, сумма: план {plan_km:.0f} км, базовый вариант {base_km:.0f} км.')
    if bad:
        w(f'- **Нарушения проверки**: {bad[:5]}')
    else:
        w('- Все планы и базовые варианты прошли независимую проверку (planning/validate.check_plan).')
    w('')


def report():
    tasks = {}
    for fn in os.listdir(os.path.join(OUT, 'tasks')):
        d = json.load(open(os.path.join(OUT, 'tasks', fn), encoding='utf-8'))
        tasks[d['name']] = d
    old = old_rows()
    rows = [tasks[n] for s in SETS for n in sorted(tasks) if tasks[n]['set'] == s]
    lines = []
    w = lines.append
    w('# Цифры для презентации в текущей модели продукта (88 задач)\n')
    w('Пересчёт `research/deck_v2.py` (выходы — `research/runs/results/deck_v2/`). Те же заявки, бригады и варианты '
      'транспорта и навыков, что в рабочем наборе этапа 1 (`deck_report.md`), но задача собрана моделью продукта: обед '
      '45 мин при смене от 6 ч, оборудование (опция клиента) и «унесёт» по транспорту, длительность по коэффициенту '
      'сложности, общ. транспорт по расписаниям, пробки по часам окна и дню недели (сетка HERE), дома всех бригад, '
      f'штраф ожидания аварии {WAIT_KM_PER_MIN:g} км/мин. План — `solver/bin/plan` {PLAN_SEC} с, сиды {SEEDS[0]}–{SEEDS[-1]}, '
      f'лучший по (штраф, бригады, км + штраф ожидания); граница — `solver/bin/fleet_bound` {BOUND_SEC} с на лучшем плане; '
      'базовый вариант — `solver/bin/baseline` (жадный алгоритм ТЗ 2.3).\n')
    same = [d['name'] for d in rows if d.get('stage1') and d['stage1']['same_orders'] and d['stage1']['N_old'] == d['N']
            and d['stage1']['V_old'] == d['V']]
    w(f'Совпадение с этапом 1: в {len(same)} из {len(rows)} задач те же заявки (номер, начало окна, приоритет, навык) '
      'и то же число бригад.\n')
    summary(rows, 'Все наборы', w)
    for s, title in SETS.items():
        summary([d for d in rows if d['set'] == s], title.capitalize(), w)

    w('## Было → стало\n')
    o = [old[d['name']] for d in rows if d['name'] in old]
    if len(o) == len(rows):
        ou = sum(min(x['seeds']) for x in o)
        olb = sum(x['lb'] for x in o)
        nu = sum(d['plans'][d['best']]['used'] for d in rows)
        nlb = sum(task_bounds(d)[1] or 0 for d in rows)
        nlb_n = sum(1 for d in rows if task_bounds(d)[1] is not None)
        ob, oun = sum(x['base_used'] for x in o), sum(x['base_un'] for x in o)
        nb, nun = sum(d['base']['used'] for d in rows), sum(d['base']['unserved'] for d in rows)
        okm, nkm = sum(x['km'] for x in o), sum(d['plans'][d['best']]['km'] for d in rows)
        orders = sum(d['N'] for d in rows)
        w('| | Этап 1 (deck_report.md) | Модель продукта сейчас |')
        w('|---|---|---|')
        w(f'| Бригад в плане, сумма по 88 задачам | {ou} (лучший сид) | {nu} |')
        w(f'| Нижняя граница бригад, сумма | {olb} (все 88) | {nlb} (по {nlb_n} задачам, где штраф доказан) |')
        w(f'| Км плана, сумма | {okm:.0f} (медиана сидов) | {nkm:.0f} (лучший сид) |')
        w(f'| Базовый вариант: бригад | {ob} (+{100 * (ob / ou - 1):.0f}%) | {nb} (+{100 * (nb / nu - 1):.0f}%) |')
        w(f'| Базовый вариант: не выполнено | {oun} ({100 * oun / orders:.0f}%) | {nun} ({100 * nun / orders:.0f}%) |')
        w(f'| Средняя бригад на задачу, план | {ou / len(o):.2f} | {nu / len(rows):.2f} |')
        w('')
        R = [d for d in rows if '_pt_' not in d['name']]
        ru = sum(d['plans'][d['best']]['used'] for d in R)
        rb, run_ = sum(d['base']['used'] for d in R), sum(d['base']['unserved'] for d in R)
        rou, rob = sum(min(old[d['name']]['seeds']) for d in R), sum(old[d['name']]['base_used'] for d in R)
        rN = sum(d['N'] for d in R)
        w(f'Без варианта «все на общ. транспорте» ({len(R)} задач): базовый вариант использует на '
          f'{100 * (rb / ru - 1):.0f}% больше бригад (этап 1: {100 * (rob / rou - 1):.0f}%) и не выполняет '
          f'{100 * run_ / rN:.0f}% заявок; план — {sum(d["plans"][d["best"]]["unserved"] for d in R)} невыполненных, '
          f'в среднем {ru / len(R):.2f} бригады на задачу (этап 1: {rou / len(R):.2f}). В варианте «все на общ. транспорте» '
          'бригада уносит за день 2 единицы оборудования, единиц больше, чем унесут все бригады, поэтому там выходят все '
          'бригады и у плана, и у базового варианта, а часть заявок с оборудованием не выполнить никак.\n')
        diff = [(d['name'], old[d['name']]['lb'], d['bound'].get('fleet_lower_bound'), min(old[d['name']]['seeds']),
                 d['plans'][d['best']]['used']) for d in rows if min(old[d['name']]['seeds']) != d['plans'][d['best']]['used']]
        w(f'Задач, где изменилось число бригад плана: {len(diff)} из {len(rows)}.\n')

    w('## Контрольный день: участок × транспорт (навыки как в реальный день, `inf`)\n')
    w('| Участок, транспорт | Наш план: бригад, км | Граница бригад | Доказан минимум | Базовый: бригад / не выполнено | '
      'Этап 1: план, км / базовый |')
    w('|---|---|---|---|---|---|')
    names = {'vostok': 'Восток', 'yugocentr': 'Югоцентр', 'yugovostok': 'Юго-восток'}
    tnames = {'pt': 'общ. транспорт', 'car': 'машина', 'mix1': 'смешанный 1', 'mix2': 'смешанный 2'}
    for tr, rn in names.items():
        for tm in ('pt', 'car', 'mix2', 'mix1'):
            d = tasks.get(f'control_{tr}_{tm}_inf')
            if not d:
                continue
            b, x = d['plans'][d['best']], old.get(d['name'])
            un = f", не вып. {b['unserved']}" if b['unserved'] else ''
            ostr = f"{min(x['seeds'])} бр, {x['km']:.0f} км / {x['base_used']} бр, {x['base_un']} не вып." if x else ''
            w(f"| {rn}, {tnames[tm]} | {b['used']} бр, {b['km']:.0f} км{un} | {task_bounds(d)[1] or '—'} | "
              f"{'да' if proven(d) else 'нет'} | {d['base']['used']} / {d['base']['unserved']} | {ostr} |")
    w('\n«Смешанный 2» (`mix2`) — вариант со слайда «Все варианты реального дня».\n')

    w('## Как доказывается минимум\n')
    w('Цель лексикографическая: штраф за невыполненные заявки, затем бригады. «Доказан» — штраф и число бригад плана '
      'равны нижним границам, верным для любого плана в модели продукта:')
    w('- **Штраф** (`penalty_bound`): (1) заявку не берёт ни одна бригада даже одна (окно, смена, обед, навык, транспорт, '
      'оборудование); (2) оборудование: бригада без машины уносит за день не больше «унесёт» (ОТ и пешком 2 ед., велосипед 3), '
      'поэтому, если машин нет и единиц больше, чем унесут все, лишнее — невыполненные заявки, минимум штрафа — рюкзак; '
      '(3) клика: заявки, которые одна бригада не сделает вдвоём ни в каком порядке, а взять может только множество U, '
      '— выполнено не больше |U| (например, три вечерние аварии «только машина» на две подходящие машины).')
    w('- **Бригады**: `fleet_bound` (генерация столбцов с обедом и оборудованием) — для выполненных заявок плана; '
      'это граница для всех планов, только если множество выполненных заявок при минимальном штрафе единственно. '
      'Иначе — парк по оборудованию (сколько самых вместительных бригад унесут обязательные единицы) или перебор '
      'выборов клики (`alternatives`: задача без выкинутых заявок, plan + fleet_bound).')
    w('- **Км не доказываются.** Точные методы этапа 1 (`cg_master`, `cgc_master`, `cgm_bp`, `cgx_bp`, `cg*_price.cpp`) '
      'читают старый формат (6 полей заявки, 3 поля бригады, день 720 мин) и не знают обеда, оборудования, конца смены, '
      'а продукт к тому же минимизирует не км, а км + 0,1 км × минуты ожидания аварий. Без расширения разметки (обед — '
      'вторая метка, оборудование — ресурс) и смены цели оптимальность по км в новой модели не доказать; сравнение с '
      'нижней границей км здесь не приводится.')
    w('- **Сравнение с OR-Tools / PyVRP / VROOM не пересчитано**: скрипты `ortools_ref.py`, `pyvrp_ref.py`, '
      '`vroom_ref.py` читают старый формат, а гибкий обед «в ожидании или перед выездом» и оборудование нужно '
      'моделировать заново (в PyVRP перерывов нет вовсе). Цифры 8,78 / 8,64 / 8,83 против 8,17 относятся к модели этапа 1.\n')
    w('## По задачам\n')
    w('| Задача | Заявок | Бригад | Граница штрафа / бригад | fleet_bound | План 3 с: бригады по сидам | Штраф | Км (лучший) | '
      'Ожидание аварий, мин | 30 с: бригады / штраф | Базовый: бригады / не вып. / км | Этап 1: граница / план / км | Граница, с |')
    w('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for d in rows:
        b, x = d['plans'][d['best']], old.get(d['name'])
        ostr = f"{x['lb']} / {min(x['seeds'])} / {x['km']:.1f}" if x else '—'
        lp, lu = task_bounds(d)
        lg = d.get('long')
        lstr = f"{lg['runs'][lg['best']]['used']} / {lg['runs'][lg['best']]['pen']}" if lg else ''
        mark = ' ✓' if proven(d) else ''
        w(f"| {d['name']} | {d['N']} | {d['V']} | {lp} / {lu if lu is not None else '—'} | {d['bound'].get('fleet_lower_bound')} | "
          f"{' / '.join(str(p['used']) for p in d['plans'])}{mark} | {b['pen']} | {b['km']:.1f} | {b['wait']:.0f} | {lstr} | "
          f"{d['base']['used']} / {d['base']['unserved']} / {d['base']['km']:.1f} | {ostr} | {d['bound']['sec']:.0f} |")
    w('')
    bt = [d['bound']['sec'] for d in rows]
    w('## Время\n')
    w(f"- fleet_bound: медиана {st.median(bt):.1f} с, максимум {max(bt):.0f} с (лимит {BOUND_SEC} с; останавливается, "
      f"как только граница сравнялась с планом); не сошёлся (converged=false): "
      f"{sum(1 for d in rows if not d['bound'].get('converged'))} задач.")
    w(f"- Работа на месте по коэффициенту сложности: {sum(d['work_min'] for d in rows) / sum(d['norm_min'] for d in rows) * 100 - 100:+.1f}% "
      f"к нормативу в сумме; данные о доме есть у {sum(d['houses_known'] for d in rows[::8])} из {sum(d['N'] for d in rows[::8])} "
      'заявок (по одной задаче на группу).')
    w(f"- Задачи с сеткой пробок HERE: {sum(1 for d in rows if d['traffic_grid'])} из {len(rows)}.")
    open(os.path.join(RES, 'deck_report_v2.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


# ---------------------------------------------------------------- доказательства штрафа и парка, долгие прогоны
def problems(flt=''):
    """(набор, имя, Problem) заново из кэша — те же задачи, что в run."""
    houses = HouseIndex()
    for set_name, prefix, f, rosters in groups():
        names = [f'{prefix}_{tm}_{sv}' for tm in TMODES for sv in SKV if flt in f'{prefix}_{tm}_{sv}']
        if not names:
            continue
        orders, starts, coords, mats, _, _ = build_group(f, rosters, houses)
        for tm in TMODES:
            for sv in SKV:
                name = f'{prefix}_{tm}_{sv}'
                if name in names:
                    yield set_name, name, variant(name, orders, rosters, tm, sv, starts, coords, mats)[0]


def min_cover(items, need):
    """min Σштраф по подмножеству items [(штраф, единиц)] с Σединиц ≥ need (рюкзак)."""
    if need <= 0:
        return 0
    INF = float('inf')
    dp = [0] + [INF] * need
    for p, u in items:
        if u <= 0:
            continue
        for x in range(need, 0, -1):
            dp[x] = min(dp[x], dp[max(0, x - u)] + p)
    return dp[need]


def max_units(items, pen):
    """max Σединиц по подмножеству items с Σштраф ровно pen (верхняя оценка — не важно, выполнимо ли)."""
    best = {0: 0}
    for p, u in items:
        for q, x in list(best.items()):
            if q + p <= pen and best.get(q + p, -1) < x + u:
                best[q + p] = x + u
    return best.get(pen, 0)


def maximal_cliques(adj):
    out = []

    def bk(R, Pset, X):
        if not Pset and not X:
            out.append(R)
            return
        pivot = max(Pset | X, key=lambda u: len(adj[u] & Pset))
        for v in list(Pset - adj[pivot]):
            bk(R + [v], Pset & adj[v], X & adj[v])
            Pset = Pset - {v}
            X = X | {v}
    bk([], set(adj), set())
    return out


def penalty_bound(P):
    """Нижняя граница штрафа за невыполненные заявки и что из неё следует для парка. Три довода, все — ослабления:
    (1) заявку не берёт ни одна бригада даже в одиночку (окно, смена, обед, навык, транспорт, оборудование);
    (2) оборудование: за день бригады унесут не больше Σ«унесёт» (без машин); лишние единицы — невыполненные заявки,
        дешевле всего — минимальное покрытие по штрафу (рюкзак);
    (3) клика: заявки, которые попарно не может сделать одна бригада ни в каком порядке (раннее начало одной плюс её
        длительность позже последнего допустимого начала другой — от пути и матриц не зависит), а взять может только
        множество бригад U, — выполнено не больше |U| из них.
    Граница = штраф (1) + max((2), (3)). Парк по оборудованию: при штрафе, равном границе, выполнено не меньше
    Σединиц − max единиц невыполненных при таком штрафе, а их надо унести — нужно столько бригад, сколько самых
    вместительных в сумме это унесут."""
    n, V = len(P.orders), len(P.brigades)
    elig = {k: [v for v in range(V) if P.can(v, k) and P.schedule(v, [k]).on_time and not P.shortage(v, [k])] for k in range(n)}
    forced = [k for k in range(n) if not elig[k]]
    pen = {k: PENALTY[P.orders[k].priority] for k in range(n)}
    units = {k: sum(P.orders[k].demand.values()) for k in range(n)}
    p1 = sum(pen[k] for k in forced)
    rest = [k for k in range(n) if elig[k]]
    carries = [b.carry for b in P.brigades]
    cap = None if any(c is None for c in carries) else sum(carries)
    total = sum(units.values())
    excess = 0 if cap is None else total - sum(units[k] for k in forced) - cap
    p2 = min_cover([(pen[k], units[k]) for k in rest], excess) if excess > 0 else 0
    # клики среди заявок с малым множеством бригад
    p3, clique = 0, []
    small = [k for k in rest if len(elig[k]) <= 3]
    pair = {}

    def conflict(a, b):
        key = (min(a, b), max(a, b))
        if key not in pair:
            # не зависит от пути: вторая работа начинается не раньше конца первой, а та — не раньше начала своего окна
            common = set(elig[a]) & set(elig[b])
            pair[key] = all(P.orders[x].earliest + P.service(v, x) > P.latest(y, v) + 1e-6
                            for v in common for x, y in ((a, b), (b, a)))
        return pair[key]
    for U in {tuple(elig[k]) for k in small}:
        cand = [k for k in small if set(elig[k]) <= set(U)]
        if len(cand) <= len(U):
            continue
        adj = {a: {b for b in cand if b != a and conflict(a, b)} for a in cand}
        for C in maximal_cliques(adj):          # Брон — Кербош: все максимальные клики группы
            if len(C) > len(U):
                lb = sum(sorted(pen[k] for k in C)[:len(C) - len(U)])
                if lb > p3:
                    p3, clique = lb, [P.orders[k].id for k in C]
    lb_pen = p1 + max(p2, p3)
    fleet_cap = None
    if cap is not None:
        extra = lb_pen - p1
        dropped_max = sum(units[k] for k in forced) + max_units([(pen[k], units[k]) for k in rest], extra)
        served = total - dropped_max
        fleet_cap, s = 0, 0
        for c in sorted(carries, reverse=True):
            if s >= served:
                break
            s, fleet_cap = s + c, fleet_cap + 1
    return dict(lb_pen=lb_pen, forced=[P.orders[k].id for k in forced], forced_pen=p1, equip_units=total, carry_cap=cap,
                equip_pen=p2, clique=clique, clique_pen=p3, fleet_cap=fleet_cap)


def proofs(flt=''):
    for set_name, name, P in problems(flt):
        path = os.path.join(OUT, 'tasks', f'{name}.json')
        d = json.load(open(path, encoding='utf-8'))
        d['pen_bound'] = penalty_bound(P)
        json.dump(d, open(path, 'w'), ensure_ascii=False, indent=1)
        b = d['plans'][d['best']]
        print(f"{name}: штраф плана {b['pen']}, граница штрафа {d['pen_bound']['lb_pen']}, парк по оборудованию "
              f"{d['pen_bound']['fleet_cap']}, план {b['used']}, fleet_bound {d['bound'].get('fleet_lower_bound')}", flush=True)


def reduced(P, drop, name):
    """Та же задача без заявок drop (индексы): матрицы — подматрицы, расписания и пробки те же."""
    from dispatch.geo.matrices import TravelMatrices
    keep = [k for k in range(len(P.orders)) if k not in set(drop)]
    idx = list(range(P.n_starts)) + [P.node(k) for k in keep]
    M = P.matrices
    sub = lambda A: [[A[i][j] for j in idx] for i in idx]
    mats = TravelMatrices({m: sub(T) for m, T in M.time.items()}, {m: sub(T) for m, T in M.dist.items()}, hourly=M.hourly,
                          pt_hours=sub(M.pt_hours) if M.pt_hours else None, day=M.day, overhead=M.overhead)
    return Problem(name, [P.orders[k] for k in keep], P.brigades, P.starts, mats)


def alternatives(flt=''):
    """Штраф доказан кликой (довод 3): любой план с таким штрафом выкидывает ровно len(C) − |U| заявок клики и больше
    ничего. Для каждого такого выбора — задача без них: plan 3 с и fleet_bound 60 с. Минимум по выборам — граница парка
    для всех планов с минимальным штрафом."""
    import itertools
    for set_name, name, P in problems(flt):
        path = os.path.join(OUT, 'tasks', f'{name}.json')
        d = json.load(open(path, encoding='utf-8'))
        pb, b = d.get('pen_bound') or {}, d['plans'][d['best']]
        if not pb.get('clique') or b['pen'] != pb['lb_pen'] or pb['forced'] or pb['clique_pen'] <= pb['equip_pen']:
            continue
        ids = {str(o.id): k for k, o in enumerate(P.orders)}
        C = [ids[str(i)] for i in pb['clique']]
        pens = {PENALTY[P.orders[k].priority] for k in C}
        need = pb['lb_pen'] // max(pens)
        if len(pens) != 1 or need * max(pens) != pb['lb_pen']:
            continue                      # разные штрафы в клике — выборов больше, не разбираем
        res = []
        for drop in itertools.combinations(C, need):
            Q = reduced(P, drop, f'{name}_alt')
            wd = os.path.join(OUT, 'work', name, 'alt_' + '_'.join(str(P.orders[k].id) for k in drop))
            os.makedirs(wd, exist_ok=True)
            task = os.path.join(wd, 'task.txt')
            Q.write(task)
            ans = run_json([os.path.join(BIN, 'plan'), task, PLAN_SEC, 1] + SolverRunner.options())
            json.dump(ans, open(os.path.join(wd, 'plan.json'), 'w'))
            ev = evaluate(Q, ans['routes'])
            bound = run_json([os.path.join(BIN, 'fleet_bound'), task, os.path.join(wd, 'plan.json'), BOUND_SEC],
                             timeout=BOUND_SEC * 3 + 60)
            res.append(dict(drop=[P.orders[k].id for k in drop], used=ev['used'], unserved=ev['unserved'],
                            bound=bound.get('fleet_lower_bound')))
        valid = [r['bound'] if r['unserved'] == 0 else None for r in res]
        d['alt'] = dict(runs=res, fleet_lb=None if None in valid else min(valid))
        json.dump(d, open(path, 'w'), ensure_ascii=False, indent=1)
        print(f"{name}: план {b['used']}, по выборам {[(r['drop'], r['used'], r['bound']) for r in res]} → граница {d['alt']['fleet_lb']}",
              flush=True)


LONG_SEC, LONG_SEEDS = 30, (1, 2)


def long_runs(flt=''):
    """Задачи, где план (3 с) выше границы fleet_bound: plan 30 с, сиды 1–2, и fleet_bound 60 с на лучшем из них,
    чтобы понять, чья слабость — решателя или границы."""
    for set_name, name, P in problems(flt):
        path = os.path.join(OUT, 'tasks', f'{name}.json')
        d = json.load(open(path, encoding='utf-8'))
        b = d['plans'][d['best']]
        if 'long' in d or (d['bound'].get('fleet_lower_bound') == b['used'] and not b['unserved']):
            continue
        if b['used'] == len(P.brigades) and b['unserved'] and d.get('pen_bound', {}).get('lb_pen') == b['pen']:
            continue                       # все бригады, штраф доказан — дольше искать нечего
        wd = os.path.join(OUT, 'work', name)
        task = os.path.join(wd, 'task.txt')
        P.write(task)
        runs = []
        for seed in LONG_SEEDS:
            ans = run_json([os.path.join(BIN, 'plan'), task, LONG_SEC, seed] + SolverRunner.options(), timeout=LONG_SEC * 4 + 60)
            json.dump(ans, open(os.path.join(wd, f'long_s{seed}.json'), 'w'))
            ev = evaluate(P, ans['routes'])
            ev.pop('routes')
            ev['seed'] = seed
            runs.append(ev)
        i = min(range(len(runs)), key=lambda i: (runs[i]['pen'], runs[i]['used'], runs[i]['obj']))
        bound = run_json([os.path.join(BIN, 'fleet_bound'), task, os.path.join(wd, f'long_s{LONG_SEEDS[i]}.json'), BOUND_SEC],
                         timeout=BOUND_SEC * 3 + 60)
        d['long'] = dict(sec=LONG_SEC, runs=runs, best=i, bound=bound)
        json.dump(d, open(path, 'w'), ensure_ascii=False, indent=1)
        r = runs[i]
        print(f"{name}: 3 с — {b['used']} бр / штраф {b['pen']}; {LONG_SEC} с — {r['used']} бр / штраф {r['pen']} {r['km']:.1f} км, "
              f"граница {bound.get('fleet_lower_bound')}", flush=True)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'run'
    flt = sys.argv[2] if len(sys.argv) > 2 else ''
    {'run': run, 'proofs': proofs, 'long': long_runs, 'alt': alternatives, 'report': lambda _: report()}[cmd](flt)
