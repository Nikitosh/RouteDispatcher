"""Экран инженера в браузере (Playwright): адреса бригады по порядку, ссылки навигации в Яндекс Карты от предыдущей точки,
обзвон накануне, «что взять утром», обед и маршрутный лист для печати. Нужны playwright (pip) с Chromium и доступ к CDN
Leaflet; иначе тест пропускается. Решатели не нужны: план собирается из заданных маршрутов."""
import functools
import http.server
import json
import threading
import urllib.parse
import urllib.request

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
from tests.helpers import order

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip('playwright.sync_api')

try:
    urllib.request.urlopen('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js', timeout=5)
except Exception:
    pytest.skip('нет доступа к CDN Leaflet', allow_module_level=True)


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transit, 'DIR', str(tmp_path_factory.mktemp('нет_сети')))
        mp.setenv('DISPATCH_HERE_GRID', 'off')                    # как в conftest.py: без сетки пробок
        root = tmp_path_factory.mktemp('eng')
        cache = root / 'cache'
        cache.mkdir()
        orders = [order(i + 1, 'Ремонт', (0, 600)) for i in range(5)]
        for i, o in enumerate(orders):
            o.lat, o.lon, o.address, o.district = OFFICE[0] + 0.01 * (i + 1), OFFICE[1], f'точка {i}', 'Район'
        mk = lambda name, mode: Brigade(name, mode, [0, 1, 2], start_address='офис', lat=OFFICE[0], lon=OFFICE[1])
        brigades = [mk('Б1', 'car'), mk('Б2', 'pt')]
        mb = MatrixBuilder(client=FakeOsrm(), cache_dir=str(cache))
        p = Problem('Инженер', orders, brigades, [OFFICE], mb.build([OFFICE] + [(o.lat, o.lon) for o in orders], 1,
                                                                    TravelSettings()))
        routes = [[0, 1, 2], [3, 4]]
        check = check_plan(p, routes)
        assert check.ok, check.errors
        planner = Planner(geocoder=object(), matrices=mb, solver=object(), geometry=object(), log=lambda *_: None)
        doc = planner.document(p, dict(name='Инженер', office='офис', traffic={'car': 1.3}, overhead={}), routes, routes, 2, check)
        doc['office'].update(lat=OFFICE[0], lon=OFFICE[1])
        # поля, которых в плане может не быть: доступ, оборудование, запас бригады, обед
        doc['orders'][1]['detail'] = dict(base=30, parts=[dict(what='норматив: ремонт', value='30 мин')],
                                          place=dict(entrance=2, floor=5, lift=False), house=dict(levels=5, year=1958), access=['пропуск'])
        for o in doc['orders']:
            o['equipment'] = {'router': 1}
        doc['orders'][0]['equipment'] = {'router': 1, 'stb': 1}
        doc['brigades'][0]['stock'], doc['brigades'][0]['carry'] = {'router': 4, 'stb': 1}, None   # выдано утром
        r0 = next(r for r in doc['plan']['routes'] if r['v'] == 0)
        r0['lunch'] = [r0['stops'][0]['end'], r0['stops'][0]['end'] + 30]
        plan_dir = root / 'plan'
        plan_dir.mkdir()
        (plan_dir / 'plan.json').write_text(json.dumps(doc, ensure_ascii=False))
        out = root / 'site'
        build_site([str(plan_dir)], str(out), log=lambda *_: None)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/', doc
    server.shutdown()


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, url, brigade, **viewport):
    pg = browser.new_page(viewport=viewport or dict(width=390, height=844))
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(url + 'engineer.html#d=inzhener&b=' + urllib.parse.quote(brigade))
    pg.wait_for_selector('.stop')
    return pg, errors


def rtext(href):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    pts = [tuple(map(float, p.split(','))) for p in q['rtext'][0].split('~')]
    return pts, q['rtt'][0]


def test_stops_and_navigation(site, browser):
    url, doc = site
    pg, errors = open_page(browser, url, 'Б1')
    route = next(r for r in doc['plan']['routes'] if r['v'] == 0)
    assert pg.locator('.stop').count() == len(route['stops'])
    assert pg.locator('.eday h1').inner_text() == 'Б1'
    links = pg.locator('.acts .nav').evaluate_all('els => els.map(e => e.href)')
    prev = (OFFICE[0], OFFICE[1])                                    # первая — от точки старта бригады
    for st, href in zip(route['stops'], links):
        o = doc['orders'][st['k']]
        pts, mode = rtext(href)
        assert mode == 'auto'
        assert pts[0] == pytest.approx(prev, abs=1e-5) and pts[1] == pytest.approx((o['lat'], o['lon']), abs=1e-5)
        prev = (o['lat'], o['lon'])
    first = doc['orders'][route['stops'][0]['k']]['plan']
    fmt = lambda m: f'{10 + round(m) // 60:02d}:{round(m) % 60:02d}'
    assert pg.locator('#stop-1 .work-t').inner_text() == f"{fmt(first['start'])}–{fmt(first['end'])}"
    assert not errors
    pg.close()


def test_calls_kit_lunch_and_print(site, browser):
    url, doc = site
    pg, errors = open_page(browser, url, 'Б1')
    calls = pg.locator('#calls').inner_text()
    assert 'Обзвон накануне' in calls and 'пропуск' in calls
    assert 'Накануне: попросить пропуск в офисное здание' in pg.locator('#stop-2').inner_text()
    assert '5 этаж без лифта' in pg.locator('#stop-2').inner_text()
    kit = [' '.join(r.split()) for r in pg.locator('#kit .krow').all_inner_texts()]   # 3 роутера на маршрут, выдано 4
    assert 'Роутер на маршрут 3, взять 4' in kit, kit
    assert 'ТВ-приставка на маршрут 1, взять 1' in kit, kit
    assert pg.locator('.lunch').first.inner_text().startswith('Обед')
    pg.emulate_media(media='print')
    assert not pg.locator('#emap').is_visible() and not pg.locator('#brigade').is_visible()
    heads = pg.locator('.sh-t th').all_inner_texts()
    assert heads[0] == '№' and heads[-1] == 'Подпись клиента'
    assert pg.locator('.sh-t tbody tr:not(.lr)').count() == 3
    assert not errors
    pg.close()


def test_public_transport_mode_and_switch(site, browser):
    url, doc = site
    pg, errors = open_page(browser, url, 'Б2', width=1400, height=900)
    _, mode = rtext(pg.locator('.acts .nav').first.get_attribute('href'))
    assert mode == 'mt'
    assert pg.locator('#kit').inner_text() and 'Обед' not in pg.locator('#stops').inner_text()
    pg.select_option('#brigade', '0')
    pg.wait_for_function("document.querySelector('.eday h1').textContent === 'Б1'")
    assert 'b=%D0%911' in pg.url or 'b=Б1' in urllib.parse.unquote(pg.url)
    assert pg.locator('#to-dispatch').get_attribute('href').startswith('index.html#d=inzhener')
    assert not errors
    pg.close()


def hhmm(m):
    m = round(m) + 600
    return f'{m // 60:02d}:{m % 60:02d}'


def set_clock(pg, minutes):
    pg.evaluate("document.getElementById('demo').open = true")    # часы — в свёрнутом блоке «Время для показа»
    pg.fill('#clock', hhmm(minutes))
    pg.dispatch_event('#clock', 'change')


def test_status_marks_delay_and_facts_csv(site, browser, tmp_path):
    from dispatch.model import learning
    url, doc = site
    pg, errors = open_page(browser, url, 'Б1')
    route = next(r for r in doc['plan']['routes'] if r['v'] == 0)
    first, second = (doc['orders'][s['k']] for s in route['stops'][:2])
    key = 'pulse.marks|inzhener|-1|Б1'
    assert pg.input_value('#clock') == hhmm(first['plan']['arrive'])        # часы для показа: по умолчанию первый приезд
    marks = pg.locator('[data-mark]')
    assert marks.count() == 1 and marks.inner_text() == 'Приехал'          # кнопка только у текущего адреса
    assert pg.locator('#stop-1 [data-mark="arrived"]').count() == 1
    arrived = round(first['plan']['start']) + 12
    set_clock(pg, arrived)
    pg.click('#stop-1 [data-mark="arrived"]')
    assert f'Приехал в {hhmm(arrived)}' in pg.locator('#stop-1').inner_text()
    assert 'позже плана' in pg.locator('#stop-1').inner_text()
    assert pg.evaluate(f'(m => (m && delete m._plan, m))(JSON.parse(localStorage.getItem({key!r})))') == {first['id']: {'arrived': arrived}}
    assert pg.locator('#stop-1 [data-mark]').inner_text() == 'Закончил'
    assert 'ожидается' in pg.locator('#stop-2').inner_text()               # опоздание сдвигает следующие адреса
    done = arrived + first['svc'] + 10
    set_clock(pg, done)
    pg.click('#stop-1 [data-mark="done"]')
    assert pg.evaluate(f'(m => (m && delete m._plan, m))(JSON.parse(localStorage.getItem({key!r})))') == {first['id']: {'arrived': arrived, 'done': done}}
    assert pg.locator('[data-mark]').count() == 1 and pg.locator('#stop-2 [data-mark="arrived"]').count() == 1
    assert 'Закончил' not in pg.locator('#stop-1 [data-mark]').all_inner_texts()
    late = done - round(first['plan']['end'])
    assert f'опоздание {late} мин' in pg.locator('#fact').inner_text()
    assert f"ожидается {hhmm(second['plan']['start'] + late)}" in pg.locator('#stop-2').inner_text()
    assert pg.locator('#facts-csv').count() == 0                            # выгрузка фактов — у диспетчера, не у инженера
    pg.goto(url + 'index.html#d=inzhener')                                  # тот же браузер — те же отметки
    pg.wait_for_selector('#facts-csv')
    with pg.expect_download() as dl:
        pg.click('#facts-csv')
    path = tmp_path / 'facts.csv'
    dl.value.save_as(path)
    text = path.read_text(encoding='utf-8-sig').splitlines()
    assert text[0] == 'Дата;Заявка;Тип заявки BK;Тип заявки HD;Адрес;Гигабит;План мин;Факт мин;Бригада'
    row = text[1].split(';')
    assert row[1] == first['id'] and row[5] in ('Да', 'Нет') and row[6] == str(first['svc'])
    assert row[7] == str(done - arrived) and row[8] == 'Б1' and row[0] == (doc['meta'].get('date') or '')
    facts, _ = learning.read(str(path))                                    # годится для `python -m dispatch plan --facts`
    assert len(facts) == 1 and facts[0].actual == done - arrived
    pg.goto(url + 'engineer.html#d=inzhener&b=' + urllib.parse.quote('Б1'))
    pg.wait_for_selector('.stop')
    pg.click('#facts-undo')                                                 # отмена последней отметки
    assert pg.evaluate(f'(m => (m && delete m._plan, m))(JSON.parse(localStorage.getItem({key!r})))') == {first['id']: {'arrived': arrived}}
    assert not errors
    pg.close()


def test_dispatcher_sees_engineer_marks(site, browser):
    url, doc = site
    ctx = browser.new_context(viewport=dict(width=1400, height=900))
    disp, eng = ctx.new_page(), ctx.new_page()
    errors = []
    for p in (disp, eng):
        p.on('pageerror', lambda e: errors.append(str(e)))
    disp.goto(url + 'index.html#d=inzhener')
    disp.wait_for_selector('.grow2[data-v]')
    assert disp.locator('.fs').count() == 0
    eng.goto(url + 'engineer.html#d=inzhener&b=' + urllib.parse.quote('Б1'))
    eng.wait_for_selector('.stop')
    route = next(r for r in doc['plan']['routes'] if r['v'] == 0)
    first = doc['orders'][route['stops'][0]['k']]
    arrived = round(first['plan']['start']) + 20
    set_clock(eng, arrived)
    eng.click('#stop-1 [data-mark="arrived"]')
    disp.wait_for_selector('.grow2[data-v] .fs')                               # обновилось по событию storage
    assert 'опаздывает на' in disp.locator('.grow2[data-v="0"] .fs').inner_text()
    done = arrived + first['svc']
    set_clock(eng, done)
    eng.click('#stop-1 [data-mark="done"]')
    disp.wait_for_function(f"document.querySelector('.grow2[data-v=\"0\"] .fs').textContent.includes('{done - round(first['plan']['end'])}')")
    disp.locator('.work[data-k="%d"]' % first['k']).click()
    assert f'приехал {hhmm(arrived)} / закончил {hhmm(done)}' in disp.locator('.field').inner_text()
    assert disp.locator('#field-finish').count() == 0                        # без сервера пересчёта кнопки нет
    assert not errors
    ctx.close()


@pytest.mark.parametrize('shift, text', [(1, 'по плану'), (-3, 'по плану'), (6, 'на 6 мин позже плана')])
def test_arrival_within_minutes_is_on_plan(site, browser, shift, text):
    """План считает доли минуты, отметка — целые минуты: расхождение до 5 мин от начала работ — «по плану»."""
    url, doc = site
    pg, errors = open_page(browser, url, 'Б1')
    first = doc['orders'][next(r for r in doc['plan']['routes'] if r['v'] == 0)['stops'][0]['k']]
    pg.evaluate('localStorage.clear()')
    pg.reload()
    pg.wait_for_selector('.stop')
    set_clock(pg, round(first['plan']['start']) + shift)
    pg.click('#stop-1 [data-mark="arrived"]')
    assert text in pg.locator('#stop-1 .fline').inner_text()
    assert pg.locator('#stop-1').evaluate('e => getComputedStyle(e).opacity') == '1'
    pg.evaluate('localStorage.clear()')
    assert not errors
    pg.close()
