"""Экран инженера в браузере (Playwright), то, что не проверяет test_engineer_page.py: ссылка «← Диспетчер» на ту же
версию плана, переключение версии и участка, адрес страницы, отметки «выполнено» и «в работе» в версии после события,
часы −5/+5 и ручной ввод, «Сбросить», «На карте». Нужны playwright (pip) с Chromium и доступ к CDN Leaflet; иначе
тест пропускается. Решатели не нужны: план собирается из заданных маршрутов, события — правкой плана."""
import copy
import functools
import http.server
import json
import threading
import urllib.parse

import pytest

from dispatch.config import TravelSettings
from dispatch.geo.matrices import MatrixBuilder
from dispatch.model.domain import Brigade
from dispatch.model.problem import Problem
from dispatch.planning.planner import Planner
from dispatch.planning.validate import check_plan
from dispatch.site.builder import build_site
from dispatch.tools import transit
from tests.e2e.test_api import OFFICE, FakeOsrm
from tests.e2e.test_engineer_page import browser, hhmm, set_clock, sync_api  # noqa: F401  (фикстура и проверка окружения)
from tests.helpers import order

pytestmark = pytest.mark.e2e


def event_doc(doc, prev, **event):
    """Версия плана после события без пересчёта: те же маршруты, поля события — как у replan."""
    m = doc['plan']['metrics']
    ev = copy.deepcopy(doc)
    ev['event'] = dict(dict(changes=[], turned=[], fixed=1, accident=None, previous=prev,
                            before=dict(brigades_used=m['brigades_used'], km_total=m['km_total'], unassigned=m['unassigned'])), **event)
    return ev


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    """Участки «Инженер» (план и два события: отмена заявки 5 во время второй заявки Б1, затем Б2 недоступна) и
    «Второй» (тот же план, бригады В1 и В2, без событий)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transit, 'DIR', str(tmp_path_factory.mktemp('нет_сети')))
        mp.setenv('DISPATCH_HERE_GRID', 'off')                    # как в conftest.py: без сетки пробок
        root = tmp_path_factory.mktemp('eng_gaps')
        cache = root / 'cache'
        cache.mkdir()
        orders = [order(i + 1, 'Ремонт', (0, 600)) for i in range(5)]
        for i, o in enumerate(orders):
            o.lat, o.lon, o.address, o.district = OFFICE[0] + 0.01 * (i + 1), OFFICE[1], f'точка {i}', 'Район'
        mk = lambda name, mode: Brigade(name, mode, [0, 1, 2], start_address='офис', lat=OFFICE[0], lon=OFFICE[1])
        mb = MatrixBuilder(client=FakeOsrm(), cache_dir=str(cache))
        p = Problem('Инженер', orders, [mk('Б1', 'car'), mk('Б2', 'pt')], [OFFICE],
                    mb.build([OFFICE] + [(o.lat, o.lon) for o in orders], 1, TravelSettings()))
        routes = [[0, 1, 2], [3, 4]]
        check = check_plan(p, routes)
        assert check.ok, check.errors
        planner = Planner(geocoder=object(), matrices=mb, solver=object(), geometry=object(), log=lambda *_: None)
        doc = planner.document(p, dict(name='Инженер', office='офис', traffic={'car': 1.3}, overhead={}), routes, routes, 2, check)
        doc['office'].update(lat=OFFICE[0], lon=OFFICE[1])
        plan_dir, ev0_dir, ev1_dir, other_dir = (root / x for x in ('plan', 'ev0', 'ev1', 'other'))
        s1 = next(r for r in doc['plan']['routes'] if r['v'] == 0)['stops'][1]
        at0 = round(s1['beg']) + 1                                              # Б1: первая выполнена, вторая в работе
        ev0 = event_doc(doc, str(plan_dir), kind='cancel', order=4, at=at0)
        ev1 = event_doc(doc, str(plan_dir), kind='unavailable', brigade=1, name='Б2', at=at0 + 30)
        ev1['plan']['routes'] = [r for r in ev1['plan']['routes'] if r['v'] != 1]
        for s in route(doc, 1)['stops']:                                      # заявки Б2 — без исполнителя
            ev1['orders'][s['k']].update(plan=None, reason=dict(text='бригада недоступна'))
        other = copy.deepcopy(doc)
        other['meta'] = dict(doc['meta'], name='Второй')
        for b in other['brigades']:
            b['name'] = b['name'].replace('Б', 'В')
        for d, x in ((plan_dir, doc), (ev0_dir, ev0), (ev1_dir, ev1), (other_dir, other)):
            d.mkdir()
            (d / 'plan.json').write_text(json.dumps(x, ensure_ascii=False))
        out = root / 'site'
        build_site([str(plan_dir), str(ev0_dir), str(ev1_dir), str(other_dir)], str(out), log=lambda *_: None)
    man = json.load(open(out / 'manifest.json'))
    assert [d['slug'] for d in man['districts']] == ['vtoroy', 'inzhener']
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/', doc, at0
    server.shutdown()


@pytest.fixture
def pg(site, browser):
    p = browser.new_page(viewport=dict(width=390, height=844))
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    yield p
    p.evaluate('localStorage.clear()')
    p.close()


def open_eng(pg, site, hash):
    pg.goto(site[0] + 'engineer.html#' + hash)
    pg.wait_for_selector('.eday h1')


def h1(pg):
    return pg.locator('.eday h1').inner_text()


def route(doc, v):
    return next(r for r in doc['plan']['routes'] if r['v'] == v)


@pytest.mark.parametrize('v', [0, 1])
def test_to_dispatch_link_opens_same_event(pg, site, v):
    open_eng(pg, site, f'd=inzhener&v={v}&b=' + urllib.parse.quote('Б1'))
    link = pg.locator('#to-dispatch')
    assert link.get_attribute('href') == f'index.html#d=inzhener&v={v}'
    link.click()
    pg.wait_for_selector('.glabel')
    assert pg.input_value('#scn') == f'e-inzhener-{v}'                          # событие вне историй — «Другие события»
    assert pg.locator('.kpi .l').first.inner_text() == 'Событие'
    assert pg.locator('#eng-link').get_attribute('href') == f'engineer.html#d=inzhener&v={v}'
    assert not pg.errors


def test_version_and_district_selects(pg, site):
    url, doc, at0 = site
    open_eng(pg, site, 'd=inzhener&b=' + urllib.parse.quote('Б2'))
    assert not pg.locator('#version').is_visible()                              # бригада в адресе — выбор свёрнут
    assert pg.locator('#pick-toggle').inner_text() == 'Б2 · Инженер'
    pg.click('#pick-toggle')
    assert pg.locator('#version').is_visible()
    assert pg.locator('#version option').all_inner_texts() == ['Утренний план', f'{hhmm(at0)} — отмена заявки 5',
                                                               f'{hhmm(at0 + 30)} — Б2 недоступна']
    assert pg.locator('.enote').count() == 0 and pg.locator('.stop').count() == 2
    pg.select_option('#version', '1')
    pg.wait_for_function("location.hash.includes('v=1')")
    assert 'Изменение плана в ' + hhmm(at0 + 30) in pg.locator('.enote').inner_text()
    assert 'Б2 недоступна' in pg.locator('.enote').inner_text()
    assert 'Бригада снята с маршрута' in pg.locator('.enone').inner_text() and pg.locator('.stop').count() == 0
    assert pg.locator('#to-dispatch').get_attribute('href') == 'index.html#d=inzhener&v=1'
    pg.select_option('#version', '-1')
    pg.wait_for_function("!location.hash.includes('v=')")
    assert pg.locator('.enote').count() == 0 and pg.locator('.stop').count() == 2 and h1(pg) == 'Б2'
    pg.select_option('#district', '0')                                           # «Второй»: свои бригады, событий нет
    pg.wait_for_function("location.hash.startsWith('#d=vtoroy')")
    assert h1(pg) == 'В1' and pg.input_value('#brigade') == '0'
    assert not pg.locator('#version').is_visible()
    assert pg.locator('#to-dispatch').get_attribute('href') == 'index.html#d=vtoroy'
    assert not pg.errors


def test_hashchange(pg, site):
    open_eng(pg, site, 'd=inzhener&b=' + urllib.parse.quote('Б1'))
    pg.evaluate("location.hash = '#d=inzhener&v=0&b=' + encodeURIComponent('Б2')")
    pg.wait_for_function("document.getElementById('version').value === '0' && document.querySelector('.eday h1').textContent === 'Б2'")
    assert pg.locator('.enote').count() == 1
    pg.evaluate("location.hash = '#d=inzhener&v=0&b=' + encodeURIComponent('Б1')")   # та же версия — только бригада
    pg.wait_for_function("document.querySelector('.eday h1').textContent === 'Б1'")
    assert pg.input_value('#brigade') == '0'
    pg.evaluate("location.hash = '#d=vtoroy'")
    pg.wait_for_function("document.querySelector('.eday h1').textContent === 'В1'")
    assert pg.input_value('#district') == '0' and pg.input_value('#version') == '-1'
    assert not pg.errors


def test_done_and_in_work_stamps_on_event_version(pg, site):
    url, doc, at0 = site
    open_eng(pg, site, 'd=inzhener&v=0&b=' + urllib.parse.quote('Б1'))
    assert 'done' in pg.locator('#stop-1').get_attribute('class') and pg.locator('#stop-1 .stamp').inner_text() == 'выполнено'
    assert 'now' in pg.locator('#stop-2').get_attribute('class') and pg.locator('#stop-2 .stamp.now').inner_text() == 'в работе'
    assert pg.locator('#stop-3 .stamp').count() == 0
    assert pg.locator('#emap .m-num.done').all_inner_texts() == ['1']
    assert pg.locator('#stop-1 [data-mark]').count() == 0                         # выполненный адрес — без кнопок
    second = doc['orders'][route(doc, 0)['stops'][1]['k']]
    pg.click('#stop-2 [data-mark="done"]')                                       # на начатом к событию адресе — «Закончил»
    assert pg.locator('#stop-2 .stamp').count() == 0 and 'закончил ' + hhmm(at0) in pg.locator('#stop-2').inner_text()
    assert pg.evaluate("Object.keys(localStorage).filter(k => k.startsWith('pulse.marks|'))") == ['pulse.marks|inzhener|0|Б1']
    marks = pg.evaluate("(m => (delete m._plan, m))(JSON.parse(localStorage.getItem('pulse.marks|inzhener|0|Б1')))")
    assert marks == {second['id']: {'arrived': round(second['plan']['start']), 'done': at0}}   # допущение в хранилище не пишется
    pg.click('#facts-undo')
    assert pg.evaluate("localStorage.getItem('pulse.marks|inzhener|0|Б1')") is None
    assert pg.locator('#stop-2 .stamp.now').count() == 1 and pg.locator('#stop-2 [data-mark="done"]').count() == 1
    assert not pg.errors


def test_clock_buttons_and_manual_entry(pg, site):
    url, doc, _ = site
    open_eng(pg, site, 'd=inzhener&b=' + urllib.parse.quote('Б1'))
    first = doc['orders'][route(doc, 0)['stops'][0]['k']]
    t0 = round(first['plan']['arrive'])
    assert pg.input_value('#clock') == hhmm(t0)
    pg.click('#demo summary')
    pg.click('[data-shift="5"]')
    assert pg.input_value('#clock') == hhmm(t0 + 5)
    pg.click('[data-shift="-5"]')
    pg.click('[data-shift="-5"]')
    assert pg.input_value('#clock') == hhmm(t0 - 5)
    assert pg.evaluate("localStorage.getItem('dispatch.clock|inzhener|-1|Б1')") == str(t0 - 5)
    pg.fill('#clock', '12:34')
    pg.dispatch_event('#clock', 'change')
    assert pg.input_value('#clock') == '12:34'
    assert pg.evaluate("localStorage.getItem('dispatch.clock|inzhener|-1|Б1')") == '154'
    pg.click('#stop-1 [data-mark="arrived"]')
    assert 'Приехал в 12:34' in pg.locator('#stop-1').inner_text()
    assert pg.input_value('#clock') == hhmm(154 + first['svc'])                  # часы — к концу работ
    pg.reload()
    pg.wait_for_selector('.stop')
    assert pg.input_value('#clock') == hhmm(154 + first['svc'])                  # часы живут в localStorage
    assert not pg.errors


def test_reset_marks(pg, site):
    open_eng(pg, site, 'd=inzhener&b=' + urllib.parse.quote('Б1'))
    key = 'pulse.marks|inzhener|-1|Б1'
    assert pg.locator('#facts-reset').count() == 0                                 # без отметок сбрасывать нечего
    pg.click('#stop-1 [data-mark="arrived"]')
    pg.click('#stop-1 [data-mark="done"]')
    marks = pg.evaluate(f'(m => (m && delete m._plan, m))(JSON.parse(localStorage.getItem({key!r})))')
    assert set(marks['1']) == {'arrived', 'done'}
    pg.click('#demo summary')
    pg.once('dialog', lambda d: d.dismiss())
    pg.click('#facts-reset')                                                      # «Отмена» в подтверждении — всё на месте
    assert pg.evaluate(f'(m => (m && delete m._plan, m))(JSON.parse(localStorage.getItem({key!r})))') == marks
    msgs = []
    pg.once('dialog', lambda d: (msgs.append(d.message), d.accept()))
    pg.click('#facts-reset')
    assert msgs == ['Сбросить все отметки этой бригады?']
    assert pg.evaluate(f'localStorage.getItem({key!r})') is None
    assert pg.evaluate("localStorage.getItem('dispatch.clock|inzhener|-1|Б1')") is None
    assert pg.locator('#stop-1 [data-mark="arrived"]').count() == 1
    assert 'ещё нет' in pg.locator('#fact').inner_text() and pg.locator('#facts-reset').count() == 0
    assert not pg.errors


def test_show_on_map_centers_map(pg, site):
    url, doc, _ = site
    open_eng(pg, site, 'd=inzhener&b=' + urllib.parse.quote('Б1'))
    third = doc['orders'][route(doc, 0)['stops'][2]['k']]
    pg.locator('#stop-3 [data-show]').scroll_into_view_if_needed()
    assert pg.evaluate("document.getElementById('mapcard').getBoundingClientRect().bottom") < 0   # карта выше экрана
    pg.click('#stop-3 [data-show]')
    pg.wait_for_function("(() => { const r = document.getElementById('mapcard').getBoundingClientRect(); "
                         "return r.top >= 0 && r.bottom <= innerHeight; })()", timeout=5000)
    c = pg.evaluate('[map.getCenter().lat, map.getCenter().lng, map.getZoom()]')
    assert c[0] == pytest.approx(third['lat'], abs=1e-6) and c[1] == pytest.approx(third['lon'], abs=1e-6) and c[2] == 16
    assert not pg.errors
