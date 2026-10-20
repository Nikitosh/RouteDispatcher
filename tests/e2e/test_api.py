"""Сервер страницы (dispatch/api.py) на пакете данных участка (site/bundle.py): без OSRM и без сети транспорта, с
настоящими решателями. Варианты ответа на событие «заявку закончили позже», новая заявка кликом по карте (точка вне
кэша — через соседей сетки), передача заявки, ошибки."""
import gzip
import json
import os

import pytest

from dispatch.config import SOLVER_BIN, TravelSettings
from dispatch.geo.matrices import MatrixBuilder
from dispatch.model.domain import Brigade
from dispatch.planning.planner import Planner
from dispatch.planning.validate import check_plan
from dispatch.site.builder import slug
from dispatch.site.bundle import build_bundle
from dispatch.tools import transit
from tests.helpers import order

pytestmark = pytest.mark.e2e

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'baseline', 'fleet_bound')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)

OFFICE = (55.70, 37.60)
REGION = slug('Тестовый')


class FakeOsrm:
    """Машина 2 мин/км, велосипед 4, пешком 12; км — по прямой; линии — отрезки."""

    def table(self, profile, src, dst):
        from dispatch.geo.nearby import dist_km
        pace = {'car': 2, 'bike': 4, 'foot': 12}[profile]
        return [[(dist_km(a, b) * pace, dist_km(a, b)) for b in dst] for a in src]


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:                           # фикстура модуля создаётся раньше conftest.py
        mp.setattr(transit, 'DIR', str(tmp_path_factory.mktemp('нет_сети')))
        mp.setenv('DISPATCH_HERE_GRID', 'off')                    # как в conftest.py: без сетки пробок
        yield make_site(tmp_path_factory)


def make_site(tmp_path_factory):
    root = tmp_path_factory.mktemp('api')
    cache = root / 'cache'
    cache.mkdir()
    orders = [order(i, 'Ремонт', (0, 600), x=0) for i in range(1, 7)]
    for i, o in enumerate(orders):
        o.lat, o.lon, o.address = OFFICE[0] + 0.01 * (i + 1), OFFICE[1] + 0.005 * (i % 2), f'точка {i}'
    brigades = [Brigade('Б1', 'car', [0, 1, 2], start_address='офис', start=0, lat=OFFICE[0], lon=OFFICE[1]),
                Brigade('Б2', 'car', [0, 1, 2], start_address='офис', start=0, lat=OFFICE[0], lon=OFFICE[1]),
                Brigade('Б3', 'pt', [0, 1, 2], start_address='офис', start=0, lat=OFFICE[0], lon=OFFICE[1])]
    mb = MatrixBuilder(client=FakeOsrm(), cache_dir=str(cache))
    from dispatch.model.problem import Problem
    pts = [OFFICE] + [(o.lat, o.lon) for o in orders]
    p = Problem('Тестовый', orders, brigades, [OFFICE], mb.build(pts, 1, TravelSettings()))
    routes = [[0, 1, 2], [3, 4, 5], []]
    planner = Planner(geocoder=object(), matrices=mb, solver=object(), geometry=object(), log=lambda *_: None)
    doc = planner.document(p, dict(name='Тестовый', office='офис', traffic={'car': 1.3}, overhead={}), routes, routes, 2,
                           check_plan(p, routes))
    doc['office'].update(lat=OFFICE[0], lon=OFFICE[1])
    out = root / 'site'
    build_bundle(doc, [], str(out), client=FakeOsrm(), log=lambda *_: None, cache_dir=str(cache))
    from dispatch.api import create_app
    app = create_app(str(out))
    return app.test_client(), doc, out


def post(client, url, body):
    r = client.post(url, data=json.dumps(body, ensure_ascii=False), content_type='application/json')
    return r.status_code, r.get_json()


def test_bundle_files(site):
    _, doc, out = site
    folder = out / 'api' / REGION
    assert sorted(os.listdir(folder)) == ['geometry.json.gz', 'houses.json.gz', 'matrix_bike.json.gz',
                                          'matrix_car.json.gz', 'matrix_foot.json.gz']   # сети транспорта нет — pt не кладём


def test_variants_for_late_finish(site):
    client, doc, _ = site
    first = doc['plan']['routes'][0]['stops'][0]
    code, body = post(client, '/api/variants', dict(region=REGION, doc=doc, at=first['beg'] + 5,
                                                    event=dict(kind='finish', order=doc['orders'][first['k']]['id'],
                                                               end=first['end'] + 200)))
    assert code == 200, body
    assert [v['strategy'] for v in body['variants']] == ['insert', 'optimize', 'keep']
    for v in body['variants']:
        e = v['doc']['event']
        assert e['kind'] == 'finish' and e['delta'] == 200 and v['summary']['ok'], v['doc']['plan']['check']
        assert v['doc']['orders'][first['k']]['plan']['end'] == pytest.approx(first['end'] + 200)


def test_new_point_off_the_cache_and_move(site):
    client, doc, _ = site
    code, body = post(client, '/api/variants', dict(region=REGION, doc=doc, at=60,
                                                    event=dict(kind='urgent', lat=OFFICE[0] + 0.033, lon=OFFICE[1] + 0.002,
                                                               type='ремонт', window='12:00-20:00', id='N')))
    assert code == 200, body
    insert = body['variants'][0]['doc']
    assert insert['plan']['check']['ok'] and any(o['id'] == 'N' and o['plan'] for o in insert['orders'])
    code, body = post(client, '/api/options', dict(region=REGION, doc=doc, at=60, order=doc['orders'][5]['id']))
    assert code == 200 and {o['name'] for o in body['options']} == {'Б1', 'Б3'}
    code, body = post(client, '/api/move', dict(region=REGION, doc=doc, at=60, order=doc['orders'][5]['id'], brigade='Б3'))
    assert code == 200 and body['doc']['event']['kind'] == 'move' and body['doc']['plan']['check']['ok']


def test_cancel_unavailable_and_static_files(site):
    client, doc, out = site
    for event in (dict(kind='cancel', order=doc['orders'][5]['id']), dict(kind='unavailable', brigade='Б2')):
        code, body = post(client, '/api/variants', dict(region=REGION, doc=doc, at=60, event=event))
        assert code == 200 and all(v['summary']['ok'] for v in body['variants']), body
        assert body['variants'][0]['doc']['event']['kind'] == event['kind']
    (out / 'index.html').write_text('<html>страница</html>')
    (out / 'app.js').write_text('// скрипт')
    assert 'страница' in client.get('/').get_data(as_text=True)
    assert client.get('/app.js').get_data(as_text=True) == '// скрипт'


def test_reschedule_and_breakdown(site):
    client, doc, _ = site
    k = doc['plan']['routes'][1]['stops'][-1]['k']
    for event in (dict(kind='window', order=doc['orders'][k]['id'], window='18:00-20:00'),
                  dict(kind='breakdown', brigade='Б1', mode='pt')):
        code, body = post(client, '/api/variants', dict(region=REGION, doc=doc, at=60, event=event))
        assert code == 200, body
        assert all(v['summary']['ok'] for v in body['variants']) and body['variants'][0]['doc']['event']['kind'] == event['kind']
    assert body['variants'][0]['doc']['brigades'][0]['mode'] == 'pt'


def test_errors(site):
    client, doc, _ = site
    code, body = post(client, '/api/variants', dict(region='net-takogo', doc=doc, at=60, event=dict(kind='cancel', order='1')))
    assert code == 400 and 'нет данных участка' in body['error']
    for region in ('..', '', 'Нет такого', 5, '../api'):                        # не имя папки участка
        code, body = post(client, '/api/variants', dict(region=region, doc=doc, at=60, event=dict(kind='cancel', order='1')))
        assert code == 400 and 'участок' in body['error'], region
    bad = [({}, 'нет полей'), (dict(region=REGION, doc={}, at=60, event={}), 'не документ плана'),
           (dict(region=REGION, doc=doc, at=900, event={}), 'at —'), (dict(region=REGION, doc=doc, at=60, event='отмена'), 'event —'),
           (dict(region=REGION, doc=doc, at=60, event=dict(kind='urgent', type='авария')), 'нужна точка')]
    for b, text in bad:
        code, body = post(client, '/api/variants', b)
        assert code == 400 and text in body['error'], b
    r = client.post('/api/options', data='не json', content_type='application/json')
    assert r.status_code == 400 and 'JSON' in r.get_json()['error']
    code, body = post(client, '/api/variants', dict(region=REGION, doc=doc, at=60, event=dict(kind='чудо')))
    assert code == 400 and 'неизвестное событие' in body['error']
    code, body = post(client, '/api/variants', dict(region=REGION, doc=doc, at=60,
                                                    event=dict(kind='finish', order='6', end=300)))
    assert code == 400 and 'не раньше момента события' in body['error']


def test_bundle_with_timetable_has_pt_and_hours(tmp_path, monkeypatch):
    """Есть сеть транспорта: в пакете — пары точек дня из кэша плана и пары с сеткой, с часами."""
    monkeypatch.setattr(transit, 'available', lambda folder=None: True)
    monkeypatch.setattr(transit, 'times', lambda pts, walk=None, hourly=False, **k:
                        {(i, j): (12.0, [11.0, 13.0]) for i in range(len(pts)) for j in range(len(pts)) if i != j})
    cache = tmp_path / 'cache'
    cache.mkdir()
    doc = dict(meta=dict(name='Пт'), office=dict(lat=OFFICE[0], lon=OFFICE[1]), orders=[dict(lat=55.71, lon=37.60, addr='а')],
               brigades=[dict(lat=OFFICE[0], lon=OFFICE[1], mode='pt')])
    event = dict(doc, event=dict(turned=[dict(lat=55.705, lon=37.60)]))         # точка разворота — тоже точка дня
    from dispatch.geo.geometry import RouteGeometry
    from dispatch.geo.store import JsonStore
    lines = JsonStore(str(cache / 'geometry.json'))
    lines.put(RouteGeometry._key('car', (55.7, 37.6), (55.71, 37.6)), [[55.7, 37.6], [55.71, 37.6]])   # pt — по машинным дорогам
    lines.save()
    build_bundle(doc, [event], str(tmp_path / 'site'), client=FakeOsrm(), log=lambda *_: None, cache_dir=str(cache))
    geo = json.load(gzip.open(tmp_path / 'site' / 'api' / slug('Пт') / 'geometry.json.gz', 'rt'))
    assert list(geo.values()) == [[[55.7, 37.6], [55.71, 37.6]]]
    pt = json.load(gzip.open(tmp_path / 'site' / 'api' / slug('Пт') / 'matrix_pt.json.gz', 'rt'))
    assert '55.70500,37.60000|55.71000,37.60000' in pt
    day = pt['55.70000,37.60000|55.71000,37.60000']
    assert day[0] == 12.0 and day[2] == [11.0, 13.0]
    day_pts = ('55.70000,37.60000', '55.71000,37.60000', '55.70500,37.60000')
    assert all(any(p in k for p in day_pts) for k in pt)               # между точками сетки пар нет
    assert all(len(v) == 3 for v in pt.values())
