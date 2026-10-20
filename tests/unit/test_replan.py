"""Перепланирование на прямой. Б1: A (x=10) → B (x=20) → C (x=30, окно с 200); Б2: D (x=−10); Б3 — резерв.
Время в пути на машине = расстояние, поэтому у Б1: A 10–40, B 50–80, едет к C 80–90, ждёт окно, C 200–230."""
import pytest

from dispatch.files.readers import InputError
from dispatch.geo.geocoder import Geocoder
from dispatch.planning.planner import Planner
from dispatch.planning.replan import Breakdown, Cancel, Finish, Move, Replanner, Reschedule, Unavailable, Urgent
from dispatch.planning.validate import Check
from tests.helpers import LineMatrices, brigade, line_problem, order


@pytest.fixture
def doc():
    orders = [order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (0, 400), x=20),
              order('C', 'Ремонт', (200, 500), x=30), order('D', 'Подключение', (0, 600), x=-10)]
    brigades = [brigade('Б1'), brigade('Б2', skills=(0, 1)), brigade('Б3')]
    p = line_problem(orders, brigades)
    meta = dict(name='тест', office='офис', traffic={}, overhead={})
    planner = Planner(geocoder=object(), matrices=LineMatrices(), solver=object(), geometry=object(), log=lambda *_: None)
    return planner.document(p, meta, [[0, 1, 2], [3], []], [[0, 1, 2], [3], []], 2, Check(True))


class StraightLines:
    """Подмена RouteGeometry: линий по улицам нет, переезд — прямой отрезок."""

    def leg(self, mode, a, b):
        return None


def replan(doc, tmp_path, at, event):
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                  geometry=StraightLines())
    return r.apply(at, event)


def route_ids(res, name):
    B, O = res['brigades'], res['orders']
    return next(([O[s['k']]['id'] for s in r['stops']] for r in res['plan']['routes'] if B[r['v']]['name'] == name), [])


def test_unavailable_moves_unstarted_orders(doc, tmp_path):
    res = replan(doc, tmp_path, 60, Unavailable('Б1'))
    assert res['plan']['check']['ok'], res['plan']['check']['errors']
    assert route_ids(res, 'Б1') == ['A', 'B']                    # выполненное и начатое осталось
    assert route_ids(res, 'Б2') == ['D', 'C']
    assert [(c['k'], c['now']['brigade']) for c in res['event']['changes']] == [(2, 1)]
    assert res['event']['fixed'] == 3


def test_cancel_and_cancel_started(doc, tmp_path):
    res = replan(doc, tmp_path, 20, Cancel('B'))
    assert route_ids(res, 'Б1') == ['A', 'C'] and res['orders'][1]['cancelled']
    assert res['plan']['metrics']['orders'] == 3
    with pytest.raises(InputError, match='уже выполнена или начата'):
        replan(doc, tmp_path, 20, Cancel('A'))


def test_urgent_order_goes_into_gap_without_reordering(doc, tmp_path):
    res = replan(doc, tmp_path, 60, Urgent('25.0, 0.0', 'Ремонт', window='12:00-16:00', id='N'))
    assert route_ids(res, 'Б1') == ['A', 'B', 'N', 'C']
    n = next(o for o in res['orders'] if o['id'] == 'N')
    assert n['plan']['start'] >= 120 and res['plan']['check']['ok']
    assert [c['why'] for c in res['event']['changes']] == ['новая заявка']


def test_accident_starts_soon_and_prefers_working_brigade(doc, tmp_path):
    res = replan(doc, tmp_path, 100, Urgent('28.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    assert res['brigades'][a['brigade']]['name'] == 'Б1'
    assert a['start'] == 102 and a['wait'] == 2                   # ждала окна у C (x=30), до аварии 2 минуты
    assert route_ids(res, 'Б1') == ['A', 'B', 'X', 'C'] and res['plan']['check']['ok']
    x = next(o for o in res['orders'] if o['id'] == 'X')
    assert x['window'] == [100, 220]                              # окно аварии: 2 часа с момента появления
    assert res['event']['turned'] == [dict(brigade=0, kind='waiting', lat=30.0, lon=0.0, was=2, now=4)]


def test_brigade_on_the_way_turns_around(doc, tmp_path):
    """В 85-ю минуту Б1 на полпути от B (x=20) к C (x=30): разворачивается из x=25 к аварии на x=22."""
    res = replan(doc, tmp_path, 85, Urgent('22.0, 0.0', 'Авария', id='X'))
    assert route_ids(res, 'Б1') == ['A', 'B', 'X', 'C'] and res['plan']['check']['ok'], res['plan']['check']
    a = res['event']['accident']
    assert (a['start'], a['wait']) == (88, 3)
    t = res['event']['turned'][0]
    assert (t['kind'], t['lat'], t['was'], t['now']) == ('transit', 25.0, 2, 4)
    r = next(r for r in res['plan']['routes'] if r['v'] == 0)
    assert r['via'] == dict(pos=2, lat=25.0, lon=0.0)
    assert r['km'] == 10 + 10 + 5 + 3 + 8                          # проеденная половина переезда остаётся в пробеге


def test_working_brigade_finishes_first(doc, tmp_path):
    """В 60-ю минуту Б1 работает у B (50–80): к аварии выезжает только в 80, разворота нет."""
    res = replan(doc, tmp_path, 60, Urgent('22.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    assert res['brigades'][a['brigade']]['name'] == 'Б1' and a['start'] == 82
    assert res['event']['turned'] == []


def test_accident_that_cannot_start_in_two_hours(doc, tmp_path):
    res = replan(doc, tmp_path, 400, Urgent('600.0, 0.0', 'Авария', id='Far'))
    far = next(o for o in res['orders'] if o['id'] == 'Far')
    assert far['plan'] is None and far['reason']['code'] == 'busy'
    assert res['event']['accident'] is None and res['plan']['check']['ok']


class AllToFirst:
    """Подмена решателя: все заявки остатка дня — первой бригаде в порядке номеров."""

    def plan_only(self, problem, seconds, seed, workdir):
        self.problem = problem
        return [list(range(len(problem.orders)))] + [[] for _ in problem.brigades[1:]]


def test_optimize_keeps_done_part_and_starts_from_where_brigades_are(doc, tmp_path):
    """В 60-ю минуту Б1 работает у B (50–80), A выполнена; остаток дня: C, D и новая авария X."""
    solver = AllToFirst()
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                  geometry=StraightLines())
    res = r.apply(60, Urgent('22.0, 0.0', 'Авария', id='X'), strategy='optimize', solver=solver, workdir=str(tmp_path))
    assert route_ids(res, 'Б1')[:2] == ['A', 'B']                       # выполненное и начатое не тронуто
    assert solver.problem.time['car'][0][solver.problem.node(0)] >= 80   # Б1 свободна только после работы у B
    assert res['event']['strategy'] == 'optimize' and res['plan']['check']['ok']


def test_keep_leaves_new_order_unassigned(doc, tmp_path):
    res = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                                  geometry=StraightLines()).apply(60, Urgent('22.0, 0.0', 'Авария', id='X'), strategy='keep')
    x = next(o for o in res['orders'] if o['id'] == 'X')
    assert x['plan'] is None and route_ids(res, 'Б1') == ['A', 'B', 'C']


def test_move_options_and_manual_move(doc, tmp_path):
    """В 60-ю минуту передаём C (x=30) от Б1 резервной Б3: +30 км у Б3, −10 км у Б1."""
    make = lambda: Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                             geometry=StraightLines())
    opts = {o['name']: o for o in make().options(60, 'C')}
    assert opts['Б3']['ok'] and opts['Б3']['new_brigade'] and opts['Б3']['km'] == 30 - 10   # +30 у Б3, −10 у Б1
    assert set(opts) == {'Б2', 'Б3'}
    res = make().apply(60, Move('C', 'Б3'))
    assert route_ids(res, 'Б3') == ['C'] and route_ids(res, 'Б1') == ['A', 'B'] and res['plan']['check']['ok']
    c = next(o for o in res['orders'] if o['id'] == 'C')
    assert c['pinned'] == 2 and res['event']['kind'] == 'move'
    with pytest.raises(InputError, match='уже выполнена или начата'):
        make().apply(60, Move('A', 'Б3'))


def test_finish_later_moves_late_orders(doc, tmp_path):
    res = replan(doc, tmp_path, 60, Finish('B', 495))           # B до 18:15 вместо 11:20: к C (окно до 500) не успевает
    assert res['plan']['check']['ok'], res['plan']['check']['errors']
    assert route_ids(res, 'Б1') == ['A', 'B'] and route_ids(res, 'Б2') == ['D', 'C']
    assert res['orders'][1]['plan']['end'] == 495
    assert res['event']['kind'] == 'finish' and res['event']['delta'] == 415
    assert [c['why'] for c in res['event']['changes']] == ['задержка на заявке B']


def test_finish_earlier_shifts_rest_of_route(doc, tmp_path):
    res = replan(doc, tmp_path, 25, Finish('A', 25))
    assert route_ids(res, 'Б1') == ['A', 'B', 'C'] and not res['event']['changes']
    assert res['orders'][1]['plan']['start'] == 35                # B — сразу после фактического конца A
    assert res['orders'][2]['plan']['start'] == 200               # C — по-прежнему по окну
    with pytest.raises(InputError, match='не раньше момента события'):
        replan(doc, tmp_path, 25, Finish('B', 90))


def test_finish_keep_leaves_late_order_for_a_call(doc, tmp_path):
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                  geometry=StraightLines())
    res = r.apply(60, Finish('B', 495), strategy='keep')
    c = next(o for o in res['orders'] if o['id'] == 'C')
    assert c['plan'] is None and c['reason']['text'].startswith('задержка на заявке B')
    assert route_ids(res, 'Б1') == ['A', 'B'] and res['plan']['check']['ok']


def test_finish_optimize_starts_brigade_after_real_end(doc, tmp_path):
    solver = AllToFirst()
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                  geometry=StraightLines())
    res = r.apply(60, Finish('B', 120), strategy='optimize', solver=solver, workdir=str(tmp_path))
    assert solver.problem.time['car'][0][solver.problem.node(0)] >= 120     # Б1 свободна с фактического конца B
    assert route_ids(res, 'Б1')[:2] == ['A', 'B'] and res['plan']['check']['ok']
    assert res['orders'][1]['plan']['end'] == 120


@pytest.mark.parametrize('order_id, end, message', [
    ('Z', 100, 'нет заявки Z'),
    ('B', 40, 'раньше начала работы'),
    ('B', 800, 'после 22:00'),
    ('C', 300, 'не раньше момента события'),
])
def test_finish_errors(doc, tmp_path, order_id, end, message):
    with pytest.raises(InputError, match=message):
        replan(doc, tmp_path, 60, Finish(order_id, end))


def test_finish_accepts_clock_time(doc, tmp_path):
    res = replan(doc, tmp_path, 60, Finish('B', '11:30'))
    assert res['orders'][1]['plan']['end'] == 90 and res['event']['end'] == 90 and res['event']['delta'] == 10


def test_chained_events_keep_actual_finish(doc, tmp_path):
    """После «B закончили в 18:15» следующее событие (отмена C) не возвращает B к плановому концу."""
    first = replan(doc, tmp_path, 60, Finish('B', 495))
    second = replan(first, tmp_path, 100, Cancel('C'))
    assert second['orders'][1]['plan']['end'] == 495 and second['plan']['check']['ok']
    assert second['orders'][2]['cancelled']


def test_finish_texts_in_report_and_site(doc, tmp_path):
    from dispatch.files.report import replan_summary
    from dispatch.site.builder import event_label
    res = replan(doc, tmp_path, 60, Finish('B', 495))
    assert replan_summary(res).splitlines()[0] == 'Событие в 11:00: Б1 закончит заявку B в 18:15 — на 415 мин позже плана'
    assert event_label(res) == '11:00 — заявка B закончится в 18:15 (+415 мин)'
    early = replan(doc, tmp_path, 25, Finish('A', 25))
    assert 'на 15 мин раньше плана' in replan_summary(early)
    moved = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                      geometry=StraightLines()).apply(60, Move('C', 'Б3'))
    assert replan_summary(moved).splitlines()[0] == 'Событие в 11:00: заявка C передана: Б3'


def test_finish_of_unassigned_order(doc, tmp_path):
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                  geometry=StraightLines())
    left = r.apply(60, Finish('B', 495), strategy='keep')               # C осталась без исполнителя
    with pytest.raises(InputError, match='заявка C не назначена'):
        replan(left, tmp_path, 100, Finish('C', 300))


def test_reschedule_fits_in_place_or_moves(doc, tmp_path):
    """C (x=30, окно с 200) у Б1 после B (до 80). Окно 14:00–16:00 — остаётся у Б1, просто позже; окно 11:05–11:25 не
    успевает никто (Б1 и резерв приедут в 11:30) — C без исполнителя с причиной."""
    res = replan(doc, tmp_path, 60, Reschedule('C', '14:00-16:00'))
    assert route_ids(res, 'Б1') == ['A', 'B', 'C'] and res['orders'][2]['plan']['start'] == 240
    assert res['orders'][2]['window'] == [240, 360] and res['event']['kind'] == 'window'
    assert res['event']['was'] == [200, 500] and res['plan']['check']['ok']
    moved = replan(doc, tmp_path, 60, Reschedule('C', (65, 85)))
    c = moved['orders'][2]
    assert 'C' not in route_ids(moved, 'Б1') and moved['plan']['check']['ok'] and c['plan'] is None
    assert c['reason']['text'].startswith('клиент перенёс окно на 11:05–11:25') and c['latest'] == 85
    later = replan(doc, tmp_path, 60, Reschedule('C', (65, 120)))
    assert later['orders'][2]['plan']['start'] == 90                    # теперь можно раньше: сразу после B


@pytest.mark.parametrize('order_id, window, message', [
    ('A', '14:00-16:00', 'уже выполнена или начата'),
    ('C', '09:00-12:00', 'внутри 10:00–22:00'),
    ('C', '10:10-12:00', 'раньше момента события'),
])
def test_reschedule_errors(doc, tmp_path, order_id, window, message):
    with pytest.raises(InputError, match=message):
        replan(doc, tmp_path, 60, Reschedule(order_id, window))


def test_breakdown_continues_on_public_transport(doc, tmp_path):
    """В 60-ю минуту у Б1 (работает у B до 80) ломается машина: C (x=30) она доезжает на общ. транспорте (темп 1,5)."""
    res = replan(doc, tmp_path, 60, Breakdown('Б1'))
    assert res['plan']['check']['ok'], res['plan']['check']['errors']
    assert route_ids(res, 'Б1') == ['A', 'B', 'C']
    assert res['orders'][2]['plan']['arrive'] == 80 + 10 * 1.5
    b1 = res['brigades'][0]
    assert (b1['mode'], b1['mode_was'], b1['mode_name']) == ('pt', 'car', 'Общ. транспорт')
    assert res['event']['kind'] == 'breakdown' and res['event']['now_name'] == 'Общ. транспорт'


def test_breakdown_hands_car_only_orders_to_others(doc, tmp_path):
    doc['orders'][2]['need'] = ['car']                                   # C — только на машине
    res = replan(doc, tmp_path, 60, Breakdown('Б1', 'foot'))
    assert 'C' not in route_ids(res, 'Б1') and res['plan']['check']['ok']
    assert [x['why'] for x in res['event']['changes'] if x['k'] == 2] == ['Б1: сломалась машина, дальше — пешком']
    with pytest.raises(InputError, match='а не «car»'):
        replan(doc, tmp_path, 60, Breakdown('Б1', 'car'))


def test_breakdown_while_driving_starts_from_the_road(doc, tmp_path):
    """В 45-ю минуту Б1 едет от A (x=10, до 40) к B (x=20): машина ломается на x≈15, дальше — общ. транспорт."""
    res = replan(doc, tmp_path, 45, Breakdown('Б1'))
    b = res['orders'][1]['plan']
    assert b['brigade'] == 0 and b['arrive'] == pytest.approx(45 + 5 * 1.5)
    assert res['event']['turned'] and res['plan']['check']['ok']


def test_new_event_texts(doc, tmp_path):
    from dispatch.files.report import replan_summary
    from dispatch.site.builder import event_label
    res = replan(doc, tmp_path, 60, Reschedule('C', '14:00-16:00'))
    assert replan_summary(res).splitlines()[0] == 'Событие в 11:00: клиент перенёс окно заявки C: 13:20–18:20 → 14:00–16:00'
    assert event_label(res) == '11:00 — клиент перенёс окно заявки C на 14:00–16:00'
    res = replan(doc, tmp_path, 60, Breakdown('Б1', 'bike'))
    assert replan_summary(res).splitlines()[0] == 'Событие в 11:00: Б1: сломалась машина, дальше — велосипед'
    assert event_label(res) == '11:00 — Б1: сломалась машина'
