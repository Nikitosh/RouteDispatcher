"""Цепочки событий: решения прошлого события сохраняются в следующем (недоступная бригада, отмена, поломка),
затянувшаяся работа и обед, «закончили» не после начатой следующей, вытеснение аварией с учётом конца смены,
закреплённая заявка и недоступная бригада. Плюс крайние случаи: пустой день, нулевой факт, запас новой бригады."""
import pytest

from dispatch.config import CARRY
from dispatch.files.readers import InputError
from dispatch.geo.geocoder import Geocoder
from dispatch.model import learning
from dispatch.planning import best, fleet
from dispatch.planning.planner import Planner
from dispatch.planning.replan import Breakdown, Cancel, Finish, Move, Replanner, Unavailable, Urgent
from dispatch.planning.validate import Check
from tests.helpers import LineMatrices, brigade, line_problem, order


class NoLines:
    def leg(self, *a):
        return None


class Greedy:
    """Решатель для «optimize»: дешёвые вставки по одной заявке."""
    def plan_only(self, p, seconds=0, seed=0, workdir=None, **kw):
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


def make_doc(orders, brigades, routes, lunch=False):
    for b in brigades:
        b.lunch = lunch
    p = line_problem(orders, brigades)
    planner = Planner(geocoder=object(), matrices=LineMatrices(), solver=object(), geometry=object(), log=lambda *_: None)
    return planner.document(p, dict(name='t', office='o', traffic={}, overhead={}), routes, routes, 1, Check(True))


@pytest.fixture
def rp(tmp_path):
    def run(doc, at, ev, strategy='insert'):
        r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                      geometry=NoLines())
        return r.apply(at, ev, strategy=strategy, solver=Greedy(), workdir=str(tmp_path))
    return run


def ids(doc, name):
    B, O = doc['brigades'], doc['orders']
    return next(([O[s['k']]['id'] for s in r['stops']] for r in doc['plan']['routes'] if B[r['v']]['name'] == name), [])


def base():
    orders = [order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (0, 400), x=20),
              order('C', 'Ремонт', (200, 500), x=30), order('D', 'Подключение', (0, 600), x=-10)]
    return make_doc(orders, [brigade('Б1'), brigade('Б2', skills=(0, 1)), brigade('Б3')], [[0, 1, 2], [3], []])


def test_unavailable_brigade_stays_unavailable_in_next_event(rp):
    d1 = rp(base(), 60, Unavailable('Б1'))
    assert d1['brigades'][0]['unavailable_from'] == 60
    d2 = rp(d1, 100, Urgent('22.0, 0.0', 'Ремонт', window='12:00-16:00', id='N'))
    assert 'N' not in ids(d2, 'Б1') and ids(d2, 'Б1') == ['A', 'B']           # только выполненное до 11:00
    assert d2['plan']['check']['ok'] and d2['brigades'][0]['unavailable_from'] == 60
    with pytest.raises(InputError):
        rp(d2, 120, Move('C', 'Б1'))


@pytest.mark.parametrize('strategy', ['insert', 'optimize'])
def test_cancelled_order_is_not_planned_again(rp, strategy):
    d1 = rp(base(), 20, Cancel('B'))
    d2 = rp(d1, 100, Urgent('25.0, 0.0', 'Ремонт', window='12:00-16:00', id='N'), strategy=strategy)
    b = next(o for o in d2['orders'] if o['id'] == 'B')
    assert b['cancelled'] and b['plan'] is None
    assert all('B' not in ids(d2, x['name']) for x in d2['brigades'])


def test_cancel_of_unassigned_order_does_not_place_it(rp):
    d1 = rp(base(), 60, Urgent('25.0, 0.0', 'Ремонт', window='12:00-16:00', id='N'), strategy='keep')
    assert next(o for o in d1['orders'] if o['id'] == 'N')['plan'] is None
    d2 = rp(d1, 70, Cancel('N'))
    n = next(o for o in d2['orders'] if o['id'] == 'N')
    assert n['cancelled'] and n['plan'] is None and ids(d2, 'Б1') == ['A', 'B', 'C']


def test_breakdown_carry_is_kept_for_next_event(rp):
    d1 = rp(base(), 60, Breakdown('Б1', 'foot'))
    assert d1['brigades'][0]['mode'] == 'foot' and d1['brigades'][0]['carry'] == CARRY['foot']
    r = Replanner(d1, matrices=LineMatrices(), geocoder=Geocoder(online=False), geometry=NoLines())
    assert r.brigades[0].carry == CARRY['foot']


def test_overrun_into_planned_lunch_moves_lunch(rp):
    """Работа затянулась на время обеда: обеда в 13:00 не было, он ставится заново после работы."""
    orders = [order('A', 'Ремонт', (130, 300), x=10), order('B', 'Ремонт', (240, 400), x=20)]
    d = make_doc(orders, [brigade('Б1')], [[0, 1]], lunch=True)
    assert d['plan']['routes'][0]['lunch'][0] == 180
    d2 = rp(d, 190, Finish('A', 200))
    r = d2['plan']['routes'][0]
    assert r['stops'][0]['end'] == 200 and r['lunch'][0] >= 200 and d2['plan']['check']['ok']


def test_finish_before_started_next_stop_is_rejected(rp):
    with pytest.raises(InputError, match='уже начала заявку B'):
        rp(base(), 60, Finish('A', 45))                 # к 11:00 бригада уже на B (начало 10:50)


def test_accident_displaces_order_past_shift_end():
    """Смена до 19:00: заявка C, которая с аварией выходит за конец смены, уходит другой бригаде."""
    orders = [order('C', 'Ремонт', (500, 700), x=10), order('D', 'Ремонт', (0, 700), x=12)]
    bs = [brigade('Б1'), brigade('Б2', skills=(0, 1))]
    bs[0].shift = (0, 540)
    d = make_doc(orders, bs, [[0], [1]])
    r = Replanner(d, matrices=LineMatrices(), geocoder=Geocoder(online=False), geometry=NoLines())
    d2 = r.apply(440, Urgent('10.0, 0.0', 'Авария', id='X'))
    x = next(o for o in d2['orders'] if o['id'] == 'X')
    assert x['plan'] and x['plan']['brigade'] == 0 and ids(d2, 'Б2') == ['D', 'C'] and d2['plan']['check']['ok']


def test_pinned_order_not_returned_to_unavailable_brigade(rp):
    d1 = rp(base(), 20, Move('C', 'Б2'))
    assert ids(d1, 'Б2') == ['D', 'C']
    d2 = rp(d1, 60, Unavailable('Б2'), strategy='optimize')
    assert 'C' not in ids(d2, 'Б2') and d2['plan']['check']['ok']


def test_shortage_brigade_respects_carry():
    orders = [order(i, 'Подключение', (0, 700), x=i, need=('foot',)) for i in range(1, 5)]
    p = line_problem(orders, [brigade('Б1', mode='car')])
    for o in p.orders:
        o.equipment = {'router': 1}
    out = fleet.shortage(p, list(range(4)))
    assert out and all(x['mode'] is None or len(x['orders']) <= CARRY['foot'] for x in out)


def test_empty_day_and_bad_facts(tmp_path):
    assert best.mix(line_problem([], [brigade('Б1')])) == {0: 1 / 3, 1: 1 / 3, 2: 1 / 3}
    path = tmp_path / 'f.csv'
    path.write_text('Заявка;Тип заявки BK;Тип заявки HD;Адрес;Гигабит;План мин;Факт мин\n'
                    '1;Подключение;Интернет;г. Москва, ул. А, д. 1;Нет;60;0\n'
                    '2;Подключение;Интернет;г. Москва, ул. А, д. 1;Нет;60;\n'
                    '3;Подключение;Интернет;г. Москва, ул. А, д. 1;Нет;60;72,5\n', encoding='utf-8')
    facts, rows = learning.read(str(path))
    assert [f.actual for f in facts] == [72.5] and len(rows) == 1


def test_runner_refuses_oversize_task_clearly():
    from dispatch.solve.runner import SolverError, SolverRunner
    p = line_problem([order(i, x=i) for i in range(153)], [brigade('Б1')])
    with pytest.raises(SolverError, match='до 152 заявок'):
        SolverRunner(bin_dir='/нет').plan_only(p)


def test_task_file_ids_are_one_word(tmp_path):
    o = order('A 1', x=1)
    o.service = 33.6
    p = line_problem([o], [brigade('Б1')])
    p.write(str(tmp_path / 't.txt'))
    assert (tmp_path / 't.txt').read_text().splitlines()[2].split()[:2] == ['A_1', '34']


def test_cancelled_order_cannot_come_back(rp):
    from dispatch.planning.replan import Reschedule
    d1 = rp(base(), 20, Cancel('B'))
    for ev in (Reschedule('B', '15:00-17:00'), Move('B', 'Б2'), Cancel('B')):
        with pytest.raises(InputError, match='отменена'):
            rp(d1, 60, ev)
    d2 = rp(d1, 30, Cancel('C'))
    m = d2['plan']['metrics']
    assert m['orders'] == 2 and m['served'] == 2 and m['unassigned'] == 0       # отмены прошлых событий тоже не в счёт


def test_cancelled_new_order_keeps_cancel_reason(rp):
    d1 = rp(base(), 60, Urgent('25.0, 0.0', 'Ремонт', window='12:00-16:00', id='N'), strategy='keep')
    d2 = rp(d1, 70, Cancel('N'))
    d3 = rp(d2, 80, Urgent('26.0, 0.0', 'Ремонт', window='12:00-16:00', id='M'))
    n = next(o for o in d3['orders'] if o['id'] == 'N')
    assert n['cancelled'] and n['reason']['code'] == 'cancelled'


@pytest.mark.parametrize('window, ok', [('14:00–16:00', True), ('16:00-14:00', False), ('9:00-9:30', False), ('14:00', False)])
def test_urgent_window_parsing(rp, window, ok):
    ev = Urgent('25.0, 0.0', 'Ремонт', window=window, id='N')
    if ok:
        n = next(o for o in rp(base(), 60, ev)['orders'] if o['id'] == 'N')
        assert n['window'] == [240, 360]
    else:
        with pytest.raises(InputError):
            rp(base(), 60, ev)


def test_replan_refuses_without_traffic_grid_of_the_plan(rp):
    d = base()
    d['meta']['traffic_grid'] = True                   # план считали с сеткой HERE, а здесь её нет (тесты — без неё)
    d['meta']['traffic_hours'] = True
    with pytest.raises(InputError, match='сетк'):
        rp(d, 60, Cancel('C'))


def test_reschedule_is_listed_as_change_even_for_same_brigade(rp):
    from dispatch.planning.replan import Reschedule
    d = rp(base(), 20, Reschedule('C', '13:00-14:00'))
    ch = [c for c in d['event']['changes'] if d['orders'][c['k']]['id'] == 'C']
    assert ch and ch[0]['why'] == 'клиент перенёс окно' and ch[0]['was']['brigade'] == ch[0]['now']['brigade']


def test_new_order_without_district_takes_nearest(rp):
    d = rp(base(), 60, Urgent('20.5, 0.0', 'Ремонт', window='12:00-16:00', id='N'))
    assert next(o for o in d['orders'] if o['id'] == 'N')['district'] == 'Тест'


def test_accident_start_rounded_like_plan(rp):
    d = rp(base(), 60, Urgent('10.3, 0.0', 'Авария', id='X'))
    a, x = d['event']['accident'], next(o for o in d['orders'] if o['id'] == 'X')
    assert a['start'] == round(a['start'], 1) and abs(a['start'] - x['plan']['start']) < 0.051


def test_drop_infeasible_removes_only_the_blocking_order(monkeypatch):
    """Маршрут не выполним не из-за опоздания (обед, оборудование): снимаем одну заявку, без которой он выполним
    (из таких — с наименьшим приоритетом, затем самую позднюю), а не весь хвост с конца."""
    import dataclasses
    orders = [order('A', 'Ремонт', (0, 600), x=10), order('B', 'Подключение', (0, 600), x=11),
              order('C', 'Ремонт', (0, 600), x=12), order('D', 'Подключение', (0, 600), x=13)]
    d = make_doc(orders, [brigade('Б1')], [[0, 1, 2, 3]])
    r = Replanner(d, matrices=LineMatrices(), geocoder=Geocoder(online=False), geometry=NoLines())
    r._prepare(0, Cancel('A'))
    real = Replanner.schedule
    monkeypatch.setattr(Replanner, 'schedule', lambda self, v, route: dataclasses.replace(real(self, v, route),
                        feasible=not ({1, 2} <= set(route))))      # мешают вместе B и C — хватит снять одну
    out = r._drop_infeasible(0, 'обед')
    assert [k for k, _ in out] == [2] and r.routes[0] == [1, 3]   # C: ремонт (приоритет ниже), D осталась


def test_lunch_before_departure_when_already_free():
    from dispatch.model.problem import lunch_gap
    assert lunch_gap(200, 230, 240, (180, 315, 45)) == (200, 275, 275)       # свободна в 13:20: обед дома, приезд позже
    assert lunch_gap(100, 190, 190, (180, 315, 45)) == (190, 190, 235)       # до 13:00 занята: обед на месте
