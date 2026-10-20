"""Нижняя граница парка (fleet_bound) с обедом, оборудованием и темпом бригад: она не выше точного минимума, найденного
перебором маршрутов по Problem.schedule, и учитывает обед, «унесёт» и темп там, где без них хватило бы меньше бригад."""
import json
import os
import random
import subprocess

import pytest

from dispatch.config import SOLVER_BIN
from tests.helpers import brigade, line_problem, order

pytestmark = pytest.mark.e2e

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'fleet_bound')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)


def run(name, *args):
    p = subprocess.run([os.path.join(SOLVER_BIN, name), *map(str, args)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout, json.loads(p.stdout)


def bound(p, tmp_path, routes=None, seconds=1, ng=8):
    """fleet_bound для плана решателя (или для заданных маршрутов): (граница, бригад в плане)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / 'task.txt'
    p.write(str(path))
    text, doc = run('plan', path, 0.3, 1)
    if routes is not None:
        text = json.dumps({'routes': routes})
    answer = tmp_path / 'answer.json'
    answer.write_text(text)
    _, b = run('fleet_bound', path, answer, seconds, ng)
    return b['fleet_lower_bound'], b['used'], json.loads(text)['routes']


def min_fleet(p, served):
    """Точный минимум бригад, обслуживающих ровно served, перебором. Префикс, недопустимый по Problem.schedule,
    не продолжается: времена только растут, обед в новом промежутке встал бы не раньше, чем после префикса,
    оборудование только прибавляется — продолжение тоже недопустимо."""
    served = sorted(served)
    bit = {k: 1 << i for i, k in enumerate(served)}
    full = (1 << len(served)) - 1
    feasible = []
    for v in range(len(p.brigades)):
        sets = set()

        def dfs(route, mask):
            for k in served:
                if mask & bit[k]:
                    continue
                r = route + [k]
                if p.schedule(v, r).feasible:
                    sets.add(mask | bit[k])
                    dfs(r, mask | bit[k])
        dfs([], 0)
        feasible.append(sets)
    best = {0: 0}
    for sets in feasible:
        nxt = dict(best)
        for m, c in best.items():
            for s in sets:
                if not s & m and nxt.get(m | s, 99) > c + 1:
                    nxt[m | s] = c + 1
        best = nxt
    return best.get(full)


def random_problem(rng, n=7, speeds=False):
    kinds = ['Ремонт', 'Подключение', 'Дозаказ', 'Авария']
    orders = []
    for i in range(n):
        a = rng.choice([0, 60, 120, 150, 180, 200, 240, 300, 360])
        orders.append(order(i, rng.choice(kinds), (a, a + rng.choice([0, 20, 60, 120])), x=rng.uniform(-30, 30)))
    brigades = []
    for v in range(3):
        b = brigade(f'Б{v}', rng.choice(['car', 'bike', 'foot', 'pt']))
        b.carry = rng.choice([None, 1, 2])
        if rng.random() < 0.3:
            b.stock = {'router': rng.randint(0, 2), 'stb': rng.randint(0, 1)}
        b.lunch = rng.random() < 0.8
        if rng.random() < 0.3:
            b.shift = rng.choice([(0, 540), (180, 720)])
        if speeds:
            b.speed = rng.choice([0.7, 0.85, 1.0, 1.15, 1.3, 1.6])
        brigades.append(b)
    return line_problem(orders, brigades)


@pytest.mark.parametrize('ng', [8, 24])
def test_bound_never_exceeds_brute_force_optimum(tmp_path, ng):
    rng = random.Random(5)
    tight = 0
    for trial in range(25):
        p = random_problem(rng)
        lb, used, routes = bound(p, tmp_path / str(trial), seconds=0.5, ng=ng)
        opt = min_fleet(p, [k for r in routes for k in r])
        assert opt is not None and opt <= used, trial
        assert lb is not None and lb <= opt, (trial, lb, opt)
        tight += lb == opt
    assert tight >= 20


def test_lunch_makes_bound_tighter(tmp_path):
    """Четыре подключения подряд через всё окно обеда: одна бригада успела бы все, но ей негде пообедать."""
    orders = [order(i, 'Подключение', (w, w), x=10) for i, w in enumerate((100, 170, 240, 310))]
    p = line_problem(orders, [brigade('Б1'), brigade('Б2')])
    lb, used, routes = bound(p, tmp_path)
    assert sorted(k for r in routes for k in r) == [0, 1, 2, 3]
    assert lb == used == min_fleet(p, range(4)) == 2


def test_carry_makes_bound_tighter(tmp_path):
    """Два подключения (каждому нужен роутер) далеко друг от друга по времени: одна бригада успела бы оба, но
    унесёт только один роутер."""
    orders = [order(0, 'Подключение', (0, 60), x=5), order(1, 'Подключение', (300, 360), x=5)]
    brigades = [brigade('Б1'), brigade('Б2')]
    for b in brigades:
        b.carry = 1
    p = line_problem(orders, brigades)
    lb, used, _ = bound(p, tmp_path)
    assert lb == used == min_fleet(p, range(2)) == 2
    for b in brigades:
        b.carry = None
    lb, _, _ = bound(p, tmp_path / 'free', routes=[[0], [1]])
    assert lb == 1


def test_bound_with_speeds_never_exceeds_brute_force_optimum(tmp_path):
    """Темп в ключе типа бригады: длительности работ в поиске маршрутов — свои у каждого типа, граница допустима."""
    rng = random.Random(9)
    tight = 0
    for trial in range(25):
        p = random_problem(rng, speeds=True)
        lb, used, routes = bound(p, tmp_path / str(trial), seconds=0.5)
        opt = min_fleet(p, [k for r in routes for k in r])
        assert opt is not None and opt <= used, trial
        assert lb is not None and lb <= opt, (trial, lb, opt)
        tight += lb == opt
    assert tight >= 20


def test_speed_changes_bound(tmp_path):
    """Два подключения в 10:00 и 11:15 у офиса: быстрая бригада (×0,5 — 35 мин) успевает оба, медленная (×1,5 —
    105 мин) — нет. Медленная и быстрая с одинаковыми остальными полями — разные типы."""
    orders = [order(0, 'Подключение', (0, 0)), order(1, 'Подключение', (75, 75))]
    slow, other = brigade('Медленная'), brigade('Вторая')
    slow.speed = other.speed = 1.5
    p = line_problem(orders, [slow, other])
    lb, used, _ = bound(p, tmp_path / 'slow')
    assert lb == used == min_fleet(p, range(2)) == 2
    other.speed = 0.5
    lb, used, _ = bound(p, tmp_path / 'fast')
    assert lb == used == min_fleet(p, range(2)) == 1
