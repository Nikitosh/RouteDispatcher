"""Перепланирование на прямой. Б1: A (x=10) → B (x=20) → C (x=30, окно с 200); Б2: D (x=−10); Б3 — резерв.
Время в пути на машине = расстояние, поэтому у Б1: A 10–40, B 50–80, C ждёт окно, 200–230."""
import pytest

from dispatch.files.readers import InputError
from dispatch.geo.geocoder import Geocoder
from dispatch.planning.planner import Planner
from dispatch.planning.replan import Cancel, Replanner, Unavailable, Urgent
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


def replan(doc, tmp_path, at, event):
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False))
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
    res = replan(doc, tmp_path, 100, Urgent('25.0, 0.0', 'Ремонт', window='12:00-16:00', id='N'))
    assert route_ids(res, 'Б1') == ['A', 'B', 'N', 'C']
    n = next(o for o in res['orders'] if o['id'] == 'N')
    assert n['plan']['start'] >= 120 and res['plan']['check']['ok']
    assert [c['why'] for c in res['event']['changes']] == ['новая заявка']


def test_accident_starts_soon_and_prefers_working_brigade(doc, tmp_path):
    res = replan(doc, tmp_path, 100, Urgent('28.0, 0.0', 'Авария', id='X'))
    a = res['event']['accident']
    assert res['brigades'][a['brigade']]['name'] == 'Б1'
    assert a['start'] == 108 and a['wait'] == 8                   # выехала в момент события, 8 минут пути
    assert route_ids(res, 'Б1') == ['A', 'B', 'X', 'C'] and res['plan']['check']['ok']
    x = next(o for o in res['orders'] if o['id'] == 'X')
    assert x['window'] == [100, 220]                              # окно аварии: 2 часа с момента появления


def test_accident_that_cannot_start_in_two_hours(doc, tmp_path):
    res = replan(doc, tmp_path, 400, Urgent('600.0, 0.0', 'Авария', id='Far'))
    far = next(o for o in res['orders'] if o['id'] == 'Far')
    assert far['plan'] is None and far['reason']['code'] == 'busy'
    assert res['event']['accident'] is None and res['plan']['check']['ok']
