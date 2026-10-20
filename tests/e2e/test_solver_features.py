"""Решатели на C++ и модель на Python согласны: обед, оборудование, штраф ожидания аварий, параметры plan, старый
формат задачи (docs/SOLVER_IO.md)."""
import json
import os
import random
import subprocess

import pytest

from dispatch.config import SOLVER_BIN
from dispatch.planning.validate import check_plan
from dispatch.solve.runner import SolverRunner
from tests.helpers import brigade, line_problem, order

pytestmark = pytest.mark.e2e

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'baseline', 'fleet_bound')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)


def run(name, *args):
    p = subprocess.run([os.path.join(SOLVER_BIN, name), *map(str, args)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def random_problem(rng, n=12, speeds=False):
    kinds = ['Ремонт', 'Подключение', 'Дозаказ', 'Авария']
    orders = []
    for i in range(n):
        a = rng.choice([0, 60, 120, 150, 180, 200, 240, 300, 360, 420])
        o = order(i, rng.choice(kinds), (a, a + rng.choice([0, 20, 60, 120, 240])), x=rng.uniform(-30, 30))
        if rng.random() < 0.2:
            o.hd = 'ТВ. Замена приставки техником' if o.bk == 'Локальная заявка' else 'Интернет + ТВ'
        orders.append(o)
    brigades = []
    for v in range(3):
        b = brigade(f'Б{v}', rng.choice(['car', 'bike', 'foot', 'pt']))
        b.carry = rng.choice([None, 1, 2, 3])
        if rng.random() < 0.4:
            b.stock = {'router': rng.randint(0, 3), 'stb': rng.randint(0, 1)}
        b.lunch = rng.random() < 0.8
        if rng.random() < 0.3:
            b.shift = rng.choice([(0, 540), (180, 720)])
        if speeds:
            b.speed = rng.choice([0.7, 0.85, 0.9, 1.0, 1.15, 1.25, 1.3, 4 / 3])
        brigades.append(b)
    return line_problem(orders, brigades)


def python_baseline(p):
    """Базовый вариант ТЗ по Problem.schedule: заявка — в конец маршрута первой бригады, которой это допустимо."""
    routes = [[] for _ in p.brigades]
    for k in range(len(p.orders)):
        for v in range(len(p.brigades)):
            if p.can(v, k) and p.schedule(v, routes[v] + [k]).feasible:
                routes[v].append(k)
                break
    return routes


def accident_wait(p, routes):
    return sum(st.start - p.orders[st.order].earliest for v, r in enumerate(routes) if r
               for st in p.schedule(v, r).stops if p.orders[st.order].priority == 1)


def test_feasibility_agrees_with_python(tmp_path):
    """Базовый вариант решателя — это проверка допустимости маршрута на C++ после каждой вставки: он должен
    совпасть с тем же жадным алгоритмом на Problem.schedule."""
    rng = random.Random(3)
    for trial in range(40):
        p = random_problem(rng)
        path = tmp_path / f't{trial}.txt'
        p.write(str(path))
        doc = run('baseline', path)
        assert doc['routes'] == python_baseline(p), trial
        assert check_plan(p, doc['routes']).ok


def test_feasibility_with_speeds_agrees_with_python(tmp_path):
    """То же с темпом бригад: длительность ⌊норматив·темп + 0,5⌋ в решателе и в Problem.schedule совпадает, окна,
    смена и обед — с длительностью своей бригады."""
    rng = random.Random(4)
    for trial in range(60):
        p = random_problem(rng, speeds=True)
        path = tmp_path / f't{trial}.txt'
        p.write(str(path))
        doc = run('baseline', path)
        assert doc['routes'] == python_baseline(p), trial
        assert check_plan(p, doc['routes']).ok


def test_plan_with_speeds_is_valid(tmp_path):
    rng = random.Random(12)
    for trial in range(6):
        p = random_problem(rng, 14, speeds=True)
        path = tmp_path / f't{trial}.txt'
        p.write(str(path))
        doc = run('plan', path, 0.3, 1)
        assert doc['feasible'] and check_plan(p, doc['routes']).ok, trial
        km = sum(p.km(v, r) for v, r in enumerate(doc['routes']))
        assert abs(doc['km'] - km) < 1e-2 and abs(doc['wait'] - accident_wait(p, doc['routes'])) < 0.1


def test_speed_one_written_or_omitted_is_the_same(tmp_path):
    """Строка бригады без поля темпа читается с темпом 1 — как раньше; продукт пишет темп, только если он у кого-то
    не 1."""
    rng = random.Random(7)
    p = random_problem(rng)
    path = tmp_path / 'old.txt'
    p.write(str(path))
    n = len(p.orders)
    lines = path.read_text().splitlines()
    assert all(len(lines[2 + n + v].split()) == 11 for v in range(len(p.brigades)))
    p.brigades[0].speed = 0.5
    p.write(str(tmp_path / 'x.txt'))
    p.brigades[0].speed = 1.0
    lines = (tmp_path / 'x.txt').read_text().splitlines()
    assert lines[2 + n].split()[-1] == '0.5'
    for v in range(1, len(p.brigades)):
        lines[2 + n + v] += ' 1.0'
    lines[2 + n] = ' '.join(lines[2 + n].split()[:-1] + ['1.0'])
    new = tmp_path / 'new.txt'
    new.write_text('\n'.join(lines) + '\n')
    assert run('baseline', new)['routes'] == run('baseline', path)['routes'] == python_baseline(p)


def test_plan_is_valid_and_wait_matches(tmp_path):
    rng = random.Random(11)
    for trial in range(6):
        p = random_problem(rng, 14)
        path = tmp_path / f't{trial}.txt'
        p.write(str(path))
        doc = run('plan', path, 0.3, 1)
        assert doc['feasible'] and check_plan(p, doc['routes']).ok, trial
        km = sum(p.km(v, r) for v, r in enumerate(doc['routes']))
        assert abs(doc['km'] - km) < 1e-2 and abs(doc['wait'] - accident_wait(p, doc['routes'])) < 0.1


def test_route_without_lunch_place_only_valid_in_old_format(tmp_path):
    """Четыре подключения подряд через всё окно обеда: в новом формате маршрут недопустим, в старом (без полей обеда
    и оборудования) — допустим, как раньше."""
    orders = [order(i, 'Подключение', (w, w), x=10) for i, w in enumerate((100, 170, 240, 310))]
    p = line_problem(orders, [brigade('Б1')])
    path = tmp_path / 'new.txt'
    p.write(str(path))
    assert run('baseline', path)['routes'] == [[0, 1, 2]]          # четвёртая не помещается: негде пообедать
    lines = path.read_text().splitlines()
    for i in range(2, 6):
        lines[i] = ' '.join(lines[i].split()[:6])
    lines[6] = ' '.join(lines[6].split()[:4])
    old = tmp_path / 'old.txt'
    old.write_text('\n'.join(lines) + '\n')
    assert run('baseline', old)['routes'] == [[0, 1, 2, 3]]
    doc = run('plan', old, 0.2, 1)
    assert doc['feasible'] and doc['unserved'] == 0 and set(doc) >= {'wait', 'km'}
    answer = tmp_path / 'answer.json'
    answer.write_text(json.dumps(doc))
    assert run('fleet_bound', old, answer, 0.5)['fleet_lower_bound'] == 1


def test_wait_penalty_starts_accident_earlier(tmp_path):
    """Три ремонта у офиса и авария в 10 км с окном на весь день: по км выгодно ехать к аварии последней, со штрафом
    ожидания — первой."""
    orders = [order(f'Р{i}', 'Ремонт', (0, 600), x=i) for i in (1, 2, 3)] + [order('А', 'Авария', (0, 600), x=10)]
    p = line_problem(orders, [brigade('Б1')])
    lazy = SolverRunner().plan_only(p, 0.3, workdir=str(tmp_path / 'a'), wwait=0)
    eager = SolverRunner().plan_only(p, 0.3, workdir=str(tmp_path / 'b'), wwait=0.5)   # по умолчанию 0,1: здесь ничья
    assert lazy[0][-1] == 3 and eager[0][0] == 3
    assert accident_wait(p, eager) < accident_wait(p, lazy) and p.km(0, eager[0]) > p.km(0, lazy[0])


def test_maxv_and_vehicle_cost(tmp_path):
    """Две группы заявок по разные стороны офиса: одна бригада успевает всё, но две проезжают меньше."""
    orders = [order(f'L{i}', 'Ремонт', (0, 600), x=-40 - i) for i in range(3)] + \
             [order(f'R{i}', 'Ремонт', (0, 600), x=40 + i) for i in range(3)]
    p = line_problem(orders, [brigade('Б1'), brigade('Б2')])
    used = lambda routes: sum(1 for r in routes if r)
    km = lambda routes: sum(p.km(v, r) for v, r in enumerate(routes))
    r = SolverRunner()
    one = r.plan_only(p, 0.3, workdir=str(tmp_path / 'a'))
    two = r.plan_only(p, 0.3, workdir=str(tmp_path / 'b'), wveh=0)
    capped = r.plan_only(p, 0.3, workdir=str(tmp_path / 'c'), wveh=0, maxv=1)
    assert used(one) == 1 and used(two) == 2 and used(capped) == 1
    assert km(two) < km(one) and abs(km(capped) - km(one)) < 1e-6
    assert all(check_plan(p, x).ok for x in (one, two, capped))
    none = r.plan_only(p, 0.3, workdir=str(tmp_path / 'd'), maxv=0)
    assert used(none) == 0


def test_unknown_parameter_is_an_error(tmp_path):
    p = line_problem([order(1)], [brigade('Б1')])
    path = tmp_path / 't.txt'
    p.write(str(path))
    proc = subprocess.run([os.path.join(SOLVER_BIN, 'plan'), str(path), '0.1', '1', 'speed=3'], capture_output=True, text=True)
    assert proc.returncode != 0 and 'speed=3' in proc.stderr
