"""Внутренности перепланирования (planning/replan.py): резервная бригада в «optimize», снятие заявок, к которым
бригада не успевает выехать так рано, как решил решатель, вытеснение аварией, тип заявки, точка на ломаной."""
import pytest

from dispatch.files.readers import InputError
from dispatch.geo.geocoder import Geocoder
from dispatch.planning.planner import Planner
from dispatch.planning.replan import Replanner, Unavailable, Urgent, along, order_type
from dispatch.planning.validate import Check
from tests.helpers import LineMatrices, brigade, line_problem, order


class NoLines:
    def leg(self, *a):
        return None


class Greedy:
    """Дешёвые вставки по одной заявке; запоминает, сколько бригад ему дали."""

    def __init__(self):
        self.sizes = []

    def plan_only(self, p, seconds=0, seed=0, workdir=None, **kw):
        self.sizes.append(len(p.brigades))
        routes = [[] for _ in p.brigades]
        for k in range(len(p.orders)):
            found = None
            for v in range(len(p.brigades)):
                for pos in range(len(routes[v]) + 1):
                    c = routes[v][:pos] + [k] + routes[v][pos:]
                    s = p.schedule(v, c)
                    if s.feasible and (found is None or s.km < found[0]):
                        found = (s.km, v, c)
            if found:
                routes[found[1]] = found[2]
        return routes


class AllToFirst:
    """Решатель, который «думает», что первая бригада успеет всё: её реальное расписание невыполнимо."""

    def plan_only(self, p, seconds=0, seed=0, workdir=None, **kw):
        self.answer = [list(range(len(p.orders)))] + [[] for _ in p.brigades[1:]]
        return self.answer


def make_doc(orders, brigades, routes, lunch=False):
    for b in brigades:
        b.lunch = lunch
    p = line_problem(orders, brigades)
    planner = Planner(geocoder=object(), matrices=LineMatrices(), solver=object(), geometry=object(), log=lambda *_: None)
    return planner.document(p, dict(name='t', office='o', traffic={}, overhead={}), routes, routes, 1, Check(True))


def replanner(doc, tmp_path):
    return Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                     geometry=NoLines())


def ids(doc, name):
    B, O = doc['brigades'], doc['orders']
    return next(([O[s['k']]['id'] for s in r['stops']] for r in doc['plan']['routes'] if B[r['v']]['name'] == name), [])


def test_optimize_opens_reserve_when_active_cannot_serve(tmp_path):
    """Б1 выбыла; Б2 умеет только подключения — ремонты A и B достаются резервной Б3."""
    orders = [order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (0, 400), x=20),
              order('C', 'Подключение', (0, 600), x=-10)]
    doc = make_doc(orders, [brigade('Б1'), brigade('Б2', skills=(1,)), brigade('Б3')], [[0, 1], [2], []])
    solver = Greedy()
    res = replanner(doc, tmp_path).apply(5, Unavailable('Б1'), strategy='optimize', solver=solver, workdir=str(tmp_path))
    assert solver.sizes == [1, 1, 2]                    # сначала только работающая Б2, затем + резерв
    assert ids(res, 'Б3') == ['A', 'B'] and ids(res, 'Б2') == ['C'] and ids(res, 'Б1') == []
    assert res['plan']['check']['ok'] and res['plan']['metrics']['unassigned'] == 0


def test_optimize_pops_orders_brigade_cannot_reach(tmp_path):
    """Решатель отдал всё Б1, но A (x=10) и B (x=−10) с окнами до 10:30 ей не успеть: B снимается и уходит Б2."""
    orders = [order('A', 'Ремонт', (0, 30), x=10), order('B', 'Ремонт', (0, 30), x=-10)]
    doc = make_doc(orders, [brigade('Б1'), brigade('Б2')], [[0], [1]])
    solver = AllToFirst()
    res = replanner(doc, tmp_path).apply(0, Urgent('5.0, 0.0', 'Ремонт', id='N'), strategy='optimize', solver=solver,
                                         workdir=str(tmp_path))
    assert solver.answer[0] == [0, 1, 2]                # ответ решателя: A, B, N — все первой бригаде
    assert ids(res, 'Б1') == ['A', 'N'] and ids(res, 'Б2') == ['B']
    assert res['plan']['check']['ok'] and res['plan']['metrics']['unassigned'] == 0


def test_accident_bumps_later_order_to_other_brigade(tmp_path):
    """Авария у x=15 сразу: Б1 едет к ней первой, B (окно до 11:00) уже не успевает и уходит к Б2."""
    orders = [order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (0, 60), x=20),
              order('D', 'Ремонт', (100, 300), x=25)]
    doc = make_doc(orders, [brigade('Б1'), brigade('Б2', skills=(0, 1))], [[0, 1], [2]])
    res = replanner(doc, tmp_path).apply(0, Urgent('15.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    assert (a['brigade'], a['start'], a['displaced']) == (0, 15, 1)
    assert ids(res, 'Б1') == ['X', 'A'] and ids(res, 'Б2') == ['B', 'D']
    why = {res['orders'][c['k']]['id']: c['why'] for c in res['event']['changes']}
    assert why == {'X': 'новая авария', 'B': 'вытеснена аварией X'}
    assert res['plan']['check']['ok']


def test_order_type():
    assert [order_type(x) for x in ('авария', ' Ремонт ', 'подкл', 'Дозаказ оборудования')] == \
        ['Авария', 'Ремонт', 'Подключение', 'Дозаказ']
    with pytest.raises(InputError, match='неизвестный тип заявки «уборка»'):
        order_type('уборка')


def test_order_type_rejects_empty():
    with pytest.raises(InputError):
        order_type('  ')


def test_along_multi_segment_line():
    line = [[0.0, 0.0], [0.0, 3.0], [4.0, 3.0]]          # на экваторе: отрезки 3 и 4
    assert along(line, 0) == (0.0, 0.0)
    assert along(line, 0.5) == (0.5, 3.0)               # 3,5 из 7 — на втором отрезке
    assert along(line, 3 / 7) == pytest.approx((0.0, 3.0))
    assert along(line, 1) == (4.0, 3.0) and along(line, 2) == (4.0, 3.0) and along(line, -1) == (0.0, 0.0)
    assert along([[1.0, 2.0]], 0.7) == (1.0, 2.0)
    assert along([[0.0, 0.0], [0.0, 0.0], [2.0, 0.0]], 0.5) == (1.0, 0.0)   # нулевой отрезок пропускается


def test_accident_info_follows_final_plan(tmp_path):
    """B с окном до 10:40 вытеснена аварией и вставлена обратно той же Б1 перед ней (Б2 не умеет ремонт): в событии
    авария начинается после B, вытесненных нет."""
    orders = [order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (0, 40), x=20)]
    doc = make_doc(orders, [brigade('Б1'), brigade('Б2', skills=(1,))], [[0, 1], []])
    res = replanner(doc, tmp_path).apply(0, Urgent('15.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    stops = {res['orders'][st['k']]['id']: st for rt in res['plan']['routes'] for st in rt['stops']}
    assert ids(res, 'Б1') == ['B', 'X', 'A']
    assert a['brigade'] == 0 and a['start'] == round(stops['X']['beg'], 1) > 15 and a['wait'] == a['start']
    assert a['displaced'] == 0


def test_reserve_goes_to_accident_without_lunch(tmp_path):
    """Авария в 14:00, свободна только резервная Б2: едет сразу (обед перед выездом отложил бы начало на 45 мин).
    Авария занимает конец окна обеда (до 15:15) — обеда в этот день нет."""
    orders = [order('A', 'Ремонт', (0, 600), x=5)]
    doc = make_doc(orders, [brigade('Б1', skills=(0, 1)), brigade('Б2')], [[0], []], lunch=True)
    res = replanner(doc, tmp_path).apply(240, Urgent('20.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    b2 = next(rt for rt in res['plan']['routes'] if rt['brigade'] == 'Б2')
    assert a['brigade'] == 1 and a['start'] == 260 and a['wait'] == 20
    assert not b2['lunch'] and res['plan']['check']['ok']


def test_accident_lunch_after_when_it_fits(tmp_path):
    """Авария в 13:00 рядом: Б2 едет без обеда и обедает после аварии (с 14:30 — до 15:15 успевает)."""
    orders = [order('A', 'Ремонт', (0, 600), x=5)]
    doc = make_doc(orders, [brigade('Б1', skills=(0, 1)), brigade('Б2')], [[0], []], lunch=True)
    res = replanner(doc, tmp_path).apply(180, Urgent('10.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    b2 = next(rt for rt in res['plan']['routes'] if rt['brigade'] == 'Б2')
    assert a['brigade'] == 1 and a['start'] == 190
    assert b2['lunch'] and b2['lunch'][0] >= a['start'] + 80 - 1e-6 and res['plan']['check']['ok']
