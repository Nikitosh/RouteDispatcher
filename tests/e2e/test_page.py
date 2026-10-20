"""Страница диспетчера в браузере (Playwright): смены бригад в списке и на диаграмме, событие «Закончили не по плану»
от формы до применённой версии. Нужны playwright (pip) с Chromium и доступ к CDN Leaflet; иначе тест пропускается."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from dispatch.config import SOLVER_BIN, TravelSettings
from dispatch.geo.matrices import MatrixBuilder
from dispatch.model.domain import Brigade
from dispatch.model.problem import Problem
from dispatch.planning.planner import Planner
from dispatch.planning.validate import check_plan
from dispatch.site.builder import build_site
from dispatch.site.bundle import build_bundle
from dispatch.tools import transit
from tests.e2e.test_api import OFFICE, FakeOsrm
from tests.helpers import order

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip('playwright.sync_api')

try:
    urllib.request.urlopen('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js', timeout=5)
except Exception:
    pytest.skip('нет доступа к CDN Leaflet', allow_module_level=True)
if not os.path.exists(os.path.join(SOLVER_BIN, 'plan')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)


@pytest.fixture(scope='module')
def url(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transit, 'DIR', str(tmp_path_factory.mktemp('нет_сети')))
        mp.setenv('DISPATCH_HERE_GRID', 'off')                    # как в conftest.py: без сетки пробок
        root = tmp_path_factory.mktemp('page')
        cache = root / 'cache'
        cache.mkdir()
        windows = [(0, 600)] * 3 + [(240, 600)] * 2 + [(0, 300)]
        orders = [order(i + 1, 'Ремонт', w) for i, w in enumerate(windows)]
        for i, o in enumerate(orders):
            o.lat, o.lon, o.address, o.district = OFFICE[0] + 0.01 * (i + 1), OFFICE[1], f'точка {i}', 'Район'
        mk = lambda name, shift: Brigade(name, 'car', [0, 1, 2], start_address='офис', lat=OFFICE[0], lon=OFFICE[1],
                                         schedule='5/2' if shift != (0, 720) else '', shift=shift)
        brigades = [mk('Б1', (0, 720)), mk('Б2', (180, 720)), mk('Б3', (0, 540))]
        mb = MatrixBuilder(client=FakeOsrm(), cache_dir=str(cache))
        p = Problem('Смены', orders, brigades, [OFFICE], mb.build([OFFICE] + [(o.lat, o.lon) for o in orders], 1,
                                                                  TravelSettings()))
        routes = [[0, 1, 2], [3, 4], [5]]
        check = check_plan(p, routes)
        assert check.ok, check.errors
        planner = Planner(geocoder=object(), matrices=mb, solver=object(), geometry=object(), log=lambda *_: None)
        doc = planner.document(p, dict(name='Смены', office='офис', traffic={'car': 1.3}, overhead={}), routes, routes, 3, check)
        doc['office'].update(lat=OFFICE[0], lon=OFFICE[1])
        plan_dir = root / 'plan'
        plan_dir.mkdir()
        (plan_dir / 'plan.json').write_text(json.dumps(doc, ensure_ascii=False))
        out = root / 'site'
        build_site([str(plan_dir)], str(out), log=lambda *_: None)
        build_bundle(doc, [], str(out), client=FakeOsrm(), log=lambda *_: None, cache_dir=str(cache))
        man = json.load(open(out / 'manifest.json'))
        json.dump(dict(man, api=True), open(out / 'manifest.json', 'w'), ensure_ascii=False)
        from werkzeug.serving import make_server

        from dispatch.api import create_app
        server = make_server('127.0.0.1', 0, create_app(str(out)), threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield f'http://127.0.0.1:{server.server_port}/', doc
        server.shutdown()


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope='module')
def page(url, browser):
    pg = browser.new_page(viewport=dict(width=1400, height=1000))
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(url[0])
    pg.wait_for_selector('.glabel')
    yield pg, errors
    pg.close()


def test_shifts_in_list_and_gantt(page):
    pg, errors = page
    labels = pg.locator('.glabel small').all_inner_texts()
    assert labels[0].endswith('км') and labels[1].endswith('13–22') and labels[2].endswith('10–19')
    assert pg.locator('.track .off').count() == 2                      # до 13:00 у Б2 и после 19:00 у Б3
    assert 'смена 13:00–22:00' in pg.locator('#brigades .bt').text_content()      # транспорт и смены — в свёрнутой таблице
    assert not errors


def test_finish_event_form_to_applied_version(page, url):
    pg, errors = page
    doc = url[1]
    first = next(s for r in doc['plan']['routes'] if r['v'] == 0 for s in r['stops'])
    pg.get_by_role('button', name='Событие').first.click()
    pg.click('.seg button[data-k="finish"]')
    at = pg.locator('#f-at')
    at.fill('10:30')
    at.dispatch_event('change')
    options = pg.locator('#f-order option').all_inner_texts()
    assert any(o.startswith(doc['orders'][first['k']]['id'] + ' — Б1') for o in options)
    pg.select_option('#f-order', doc['orders'][first['k']]['id'])
    end = pg.locator('#f-end')
    planned = round(first['end']) + 30
    assert end.input_value() == f'{10 + planned // 60}:{planned % 60:02d}'           # по умолчанию — план + 30 мин
    end.fill('13:00')
    end.dispatch_event('change')
    pg.click('#f-go')
    pg.wait_for_selector('.variant', timeout=60000)
    assert pg.locator('.variant').count() == 3
    pg.click('[data-apply="0"]')                                       # «Применить» без «Показать»
    pg.wait_for_function("document.body.innerText.includes('Моё:')")
    text = pg.locator('body').inner_text()
    assert f"заявка {doc['orders'][first['k']]['id']} закончится в 13:00" in text
    assert not errors


@pytest.mark.parametrize('kind, fill, title', [
    ('window', {'#f-nwin': '17:00-19:00'}, 'клиент перенёс окно заявки'),
    ('breakdown', {}, 'Б1: сломалась машина'),
])
def test_reschedule_and_breakdown_forms(page, kind, fill, title):
    pg, errors = page
    pg.reload()
    pg.wait_for_selector('.glabel')
    pg.get_by_role('button', name='Событие').first.click()
    pg.click(f'.seg button[data-k="{kind}"]')
    at = pg.locator('#f-at')
    at.fill('10:30')
    at.dispatch_event('change')
    for sel, value in fill.items():
        pg.locator(sel).fill(value)
        pg.locator(sel).dispatch_event('change')
    pg.click('#f-go')
    pg.wait_for_selector('.variant', timeout=60000)
    pg.click('[data-apply="0"]')
    pg.wait_for_function(f"document.body.innerText.includes({title!r})")
    assert not errors


def test_engineer_link_from_brigade_list(page, url):
    pg, _ = page
    pg.goto(url[0])
    pg.wait_for_selector('.glabel')
    href = pg.locator('.glabel a[title="Экран инженера"]').first.get_attribute('href')      # кнопка — в строке диаграммы
    assert href.startswith('engineer.html#d=') and '&b=' in href


def test_engineer_marks_prefill_finish_form(page, url):
    pg, errors = page
    doc = url[1]
    first = next(s for r in doc['plan']['routes'] if r['v'] == 0 for s in r['stops'])
    o = doc['orders'][first['k']]
    arrived, done = round(o['plan']['start']), round(o['plan']['end']) + 25
    pg.goto(url[0])
    pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))",
                ['pulse.marks|smeny|-1|Б1', {o['id']: {'arrived': arrived, 'done': done}}])
    pg.reload()
    pg.wait_for_selector('.glabel')
    assert 'опаздывает на 25 мин' in pg.locator('.grow2[data-v="0"] .fs').inner_text()
    pg.locator(f'.work[data-k="{first["k"]}"]').click()
    hm = lambda m: f'{10 + (m + 0) // 60}:{m % 60:02d}'
    pg.click('#field-finish')
    assert pg.locator('.seg button.on').get_attribute('data-k') == 'finish'
    assert pg.input_value('#f-order') == o['id'] and pg.input_value('#f-end') == hm(done)
    pg.evaluate('localStorage.clear()')
    assert not errors


def test_drag_work_to_another_brigade(page, url):
    """Перетаскивание работы на строку другой бригады: цена при наведении, по отпусканию — новая версия «Моё»."""
    pg, errors = page
    pg.goto(url[0])
    pg.wait_for_selector('.glabel')
    doc = url[1]
    k = next(s['k'] for r in doc['plan']['routes'] if r['v'] == 0 for s in r['stops'])
    target = next(doc['brigades'][r['v']]['name'] for r in doc['plan']['routes'] if r['v'] != 0)
    work = pg.locator(f'.work[data-k="{k}"]')
    assert work.get_attribute('draggable') == 'true'
    work.drag_to(pg.locator(f'.grow2[data-b="{target}"] .track'))     # цены придут и после отпускания
    pg.wait_for_function("document.body.innerText.includes('Моё:')", timeout=30000)
    assert f"→ {target}" in pg.locator('body').inner_text() and not errors


# ---------- сценарии: один список (истории показа + планы участков), шкала дня с отметкой события


@pytest.fixture(scope='module')
def story(url, tmp_path_factory):
    """Сайт со сценариями: план (с кривой «бригады и пробег») и событие «отмена» в 11:00, посчитанное сервером."""
    base, doc = url
    late = max(doc['orders'], key=lambda o: o['plan']['start'])               # ещё не начата к 11:00
    req = urllib.request.Request(base + 'api/variants', method='POST', headers={'Content-Type': 'application/json'},
                                 data=json.dumps(dict(region='smeny', doc=doc, at=60,
                                                      event=dict(kind='cancel', order=late['id']))).encode())
    try:
        ev = json.load(urllib.request.urlopen(req, timeout=60))['variants'][0]['doc']
    except urllib.error.HTTPError as e:
        raise AssertionError(e.read().decode()) from e
    root = tmp_path_factory.mktemp('story')
    plan_dir, ev_dir = root / 'plan', root / 'ev'
    plan = dict(doc, curve=[dict(limit=2, served=5, km=9.0), dict(limit=3, served=6, km=8.0)])
    ev['event']['previous'] = str(plan_dir)
    for d, x in ((plan_dir, plan), (ev_dir, ev)):
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(x, ensure_ascii=False))
    journeys = [dict(slug='day', title='День', steps=[
        dict(slug='plan', title='План дня', text='Утренний план.', dir=str(plan_dir)),
        dict(slug='cancel', title='Отмена', text='Клиент отменил заявку.', dir=str(ev_dir)),
        dict(slug='curve', title='Бригады и пробег', text='Кривая.', dir=str(plan_dir), show='curve')])]
    out = root / 'site'
    build_site([str(plan_dir), str(ev_dir)], str(out), log=lambda *_: None, scenarios=journeys)
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/', ev['event']['at']
    server.shutdown()


@pytest.fixture
def spage(story, browser):
    pg = browser.new_page(viewport=dict(width=1400, height=1000))
    pg.errors = []
    pg.on('pageerror', lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def hm(m):
    return f'{10 + m // 60:02d}:{m % 60:02d}'


def test_scenarios_in_one_list(spage, story):
    pg = spage
    pg.goto(story[0])
    pg.wait_for_selector('.glabel')
    groups = pg.locator('#scn optgroup').evaluate_all('gs => gs.map(g => [g.label, [...g.children].map(o => o.textContent)])')
    assert groups == [['Планы участков', ['Смены']], ['День', ['План дня', 'Отмена', 'Бригады и пробег']]]
    assert pg.input_value('#scn') == 'd-smeny'                                # без адреса — план первого участка
    assert pg.locator('#scn-n').inner_text() == '1 из 4'
    assert not pg.locator('#scn-prev').is_enabled() and pg.locator('#scn-next').is_enabled()   # первый в списке
    assert 'План на день: 3 бригады, 6 заявок.' in pg.locator('#ctx').inner_text()   # без текста сценария — что на экране
    pg.select_option('#scn', 'day-plan')
    pg.wait_for_function("location.hash === '#sc=day-plan'")
    assert 'Утренний план.' in pg.locator('#ctx').inner_text() and 'Смены' in pg.locator('#ctx').inner_text()
    assert pg.locator('#scn-prev').is_enabled()
    pg.click('#scn-next')
    pg.wait_for_function("location.hash === '#sc=day-cancel'")
    assert 'Клиент отменил заявку.' in pg.locator('#ctx').inner_text()
    assert pg.locator('.kpi .l').first.inner_text() == 'Событие'
    assert not pg.errors


def test_panel_puts_scenario_block_first(spage, story):
    pg = spage
    pg.goto(story[0] + '#sc=day-curve')
    pg.wait_for_selector('.glabel')
    assert pg.locator('#panel .ptitle').first.inner_text().startswith('Бригады и пробег')
    assert pg.locator('#panel details.more').count() == 0
    pg.select_option('#scn', 'day-plan')
    pg.wait_for_function("location.hash === '#sc=day-plan'")
    titles = pg.locator('#panel > .ptitle').all_inner_texts()               # без show — сводка участка и всё раскрыто
    assert titles[0].startswith('Итог по участку') and any(t.startswith('Бригады и пробег') for t in pg.locator('#panel .ptitle').all_inner_texts())
    assert pg.locator('#panel details.more').count() == 0 and pg.locator('#brigades tr[data-v]').count() >= 1
    assert not pg.errors


def test_timeline_switches_plan_at_event(spage, story):
    pg = spage
    url, at = story
    pg.goto(url + '#sc=day-cancel')
    pg.wait_for_selector('.glabel')
    assert pg.locator('#tl-ev').inner_text() == hm(at) and pg.locator('#play-time').inner_text() == hm(at)   # открылось на событии
    assert pg.locator('#gantt .nowline').count() == 1 and pg.locator('#map .evm').count() == 1
    pg.locator('#play-range').fill(str(at - 30))                               # до события — утренний план
    assert pg.locator('#play-time').inner_text() == hm(at - 30)
    assert pg.locator('.kpi .l').first.inner_text() == 'Заявки'
    assert pg.locator('#panel .ptitle').first.inner_text() == f'Бригады в {hm(at - 30)}'
    assert pg.locator('#gantt .tline').is_visible() and pg.locator('#play-all').is_visible()
    pg.click('#tl-ev')                                                         # отметка события — пересчитанный план
    assert pg.locator('.kpi .l').first.inner_text() == 'Событие'
    assert pg.locator('#panel .ptitle').first.inner_text().startswith(f'Что изменилось в {hm(at)}')
    pg.click('#play-all')
    assert pg.locator('#play-time').inner_text() == 'весь день' and not pg.locator('#gantt .tline').is_visible()
    assert not pg.locator('#play-panel').is_visible()
    assert not pg.errors


def test_play_stops_at_event(spage, story):
    pg = spage
    url, at = story
    pg.goto(url + '#sc=day-cancel')
    pg.wait_for_selector('.glabel')
    pg.locator('#play-range').fill(str(at - 6))
    pg.click('#play-btn')
    pg.wait_for_function("document.getElementById('play-btn').textContent === 'Пуск'", timeout=5000)
    assert pg.locator('#play-time').inner_text() == hm(at)
    assert pg.locator('#panel .ptitle').first.inner_text().startswith('Что изменилось')
    pg.click('#play-btn')                                                      # дальше — по пересчитанному плану
    pg.wait_for_function(f"document.getElementById('play-time').textContent > '{hm(at)}'", timeout=5000)
    pg.click('#play-btn')
    assert pg.locator('.kpi .l').first.inner_text() == 'Событие'
    assert not pg.errors


@pytest.mark.parametrize('hash, want', [('#j=day&s=1', 'day-cancel'), ('#s=curve', 'day-curve'), ('#d=smeny', 'd-smeny'),
                                        ('#sc=нет', 'd-smeny')])
def test_old_links_open_scenarios(spage, story, hash, want):
    pg = spage
    pg.goto(story[0] + hash)
    pg.wait_for_selector('.glabel')
    assert pg.input_value('#scn') == want
    assert not pg.errors


def test_scenario_menu_mouse_and_keyboard(spage, story):
    pg = spage
    pg.goto(story[0] + '#sc=day-plan')
    pg.wait_for_selector('.glabel')
    assert pg.locator('#scn-t').inner_text() == 'План дня' and pg.locator('#scn-n').inner_text() == '2 из 4'
    assert not pg.locator('#scn-menu').is_visible()
    pg.click('#scn-face')
    assert pg.locator('#scn-menu').is_visible() and pg.locator('#scn-face').get_attribute('aria-expanded') == 'true'
    assert [t.lower() for t in pg.locator('#scn-menu h4').all_inner_texts()] == ['планы участков', 'день']
    assert pg.locator('#scn-menu .scn-it.on').inner_text() == 'План дня'
    pg.click('#scn-menu .scn-it[data-key="day-curve"]')                      # мышью
    pg.wait_for_function("location.hash === '#sc=day-curve'")
    assert not pg.locator('#scn-menu').is_visible() and pg.locator('#scn-t').inner_text() == 'Бригады и пробег'
    pg.focus('#scn-face')                                                     # клавиатурой: вверх — предыдущий
    pg.keyboard.press('Enter')
    pg.keyboard.press('ArrowUp')
    pg.keyboard.press('Enter')
    pg.wait_for_function("location.hash === '#sc=day-cancel'")
    pg.click('#scn-face')
    pg.keyboard.press('Escape')
    assert not pg.locator('#scn-menu').is_visible()
    pg.click('#scn-face')
    pg.mouse.click(5, 700)                                                    # клик мимо закрывает
    assert not pg.locator('#scn-menu').is_visible() and not pg.errors


def test_tour_highlights_elements(spage, story):
    pg = spage
    pg.goto(story[0] + '#sc=day-cancel')
    pg.wait_for_selector('.glabel')
    assert pg.locator('#tour').count() == 0                                   # в тестах (webdriver) сам не открывается
    pg.click('#tour-btn')
    assert pg.locator('.tour-card b').inner_text() == 'Сценарий' and pg.locator('.tour-n').inner_text().startswith('1 из')
    box = pg.locator('.tour-hole').bounding_box()
    scn = pg.locator('.scn').bounding_box()
    assert abs(box['x'] - scn['x'] + 6) < 2 and abs(box['width'] - scn['width'] - 12) < 2   # подсветка — вокруг элемента
    pg.click('#tour-next')
    assert pg.locator('.tour-card b').inner_text() == 'Где вы сейчас'
    pg.keyboard.press('ArrowRight')
    pg.keyboard.press('ArrowLeft')
    assert pg.locator('.tour-card b').inner_text() == 'Где вы сейчас'
    pg.keyboard.press('Escape')
    assert pg.locator('#tour').count() == 0 and pg.evaluate("localStorage.getItem('pulse.tour')") == '1'
    pg.click('#tour-btn')
    n = int(pg.locator('.tour-n').inner_text().split(' из ')[1])
    for _ in range(n):
        pg.click('#tour-next')
    assert pg.locator('#tour').count() == 0 and not pg.errors


def test_marks_of_previous_plan_are_ignored(page, url):
    """После пересчёта плана отметки к прежнему маршруту бригады не читаются (подпись маршрута _plan)."""
    pg, errors = page
    doc = url[1]
    first = next(s for r in doc['plan']['routes'] if r['v'] == 0 for s in r['stops'])
    o = doc['orders'][first['k']]
    pg.goto(url[0])
    for sig, late in (('старый', False), (None, True)):
        marks = {o['id']: {'arrived': round(o['plan']['start']) + 30}}
        if sig:
            marks['_plan'] = sig
        pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", ['pulse.marks|smeny|-1|Б1', marks])
        pg.reload()
        pg.wait_for_selector('.glabel')
        assert (pg.locator('.grow2[data-v="0"] .fs').count() == 1) == late
    pg.evaluate('localStorage.clear()')
    assert not errors
