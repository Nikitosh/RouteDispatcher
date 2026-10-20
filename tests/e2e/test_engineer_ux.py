"""Экран инженера на телефоне (Playwright, 390×844): версия плана после события (часы на момент события, выполненное к нему —
без кнопок), комплект «на маршрут / взять» одинаково на телефоне и в листе, свёрнутый выбор бригады и липкая панель
«Дальше», кнопка «Позвонить», карточка адреса без кодов и повторов, свёрнутые часы для показа. Решатели не нужны."""
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
from tests.e2e.test_gaps_engineer import event_doc, route
from tests.helpers import order

pytestmark = pytest.mark.e2e
PHONE = '8 (916) 123-45-67'


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    """Участок «Инженер»: Б1 — три адреса, Б2 — два. Оборудование, запас бригады, телефон у второй заявки Б1, адрес и дом
    «как в выгрузке». События: at1 — Б1 на втором адресе (первый выполнен), at2 — второй адрес Б1 уже закончен."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transit, 'DIR', str(tmp_path_factory.mktemp('нет_сети')))
        mp.setenv('DISPATCH_HERE_GRID', 'off')
        root = tmp_path_factory.mktemp('eng_ux')
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
        for o in doc['orders']:
            o['equipment'] = {'router': 1}
        doc['orders'][0]['equipment'] = {'router': 1, 'stb': 1}
        doc['brigades'][0]['stock'] = {'router': 4, 'stb': 2}                  # с запасом сверх заявок
        o2 = doc['orders'][1]
        o2.update(phone=PHONE, addr='Город Москва, ул.Михайлова, д. 14, кв. 13', hd='TVE/ENT. Другие ошибки')
        o2['detail'] = dict(base=30, parts=[], place=dict(apt=13, entrance=2, floor=5, lift=True),
                            house=dict(levels=9, year=1972, material='Иные', series='индивидуальный проект', lifts=1), access=[])
        r0 = route(doc, 0)['stops']
        at1, at2 = round(r0[1]['beg']) + 1, round(r0[1]['end']) + 1
        dirs = {x: root / x for x in ('plan', 'ev1', 'ev2')}
        docs = {'plan': doc, 'ev1': event_doc(doc, str(dirs['plan']), kind='cancel', order=4, at=at1),
                'ev2': event_doc(doc, str(dirs['plan']), kind='cancel', order=3, at=at2)}
        for k, d in dirs.items():
            d.mkdir()
            (d / 'plan.json').write_text(json.dumps(docs[k], ensure_ascii=False))
        out = root / 'site'
        build_site([str(d) for d in dirs.values()], str(out), log=lambda *_: None)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/', doc, at1, at2
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
    pg.wait_for_selector('.stop')


B1 = 'd=inzhener&b=' + urllib.parse.quote('Б1')


def first_order(doc, v=0, i=0):
    return doc['orders'][route(doc, v)['stops'][i]['k']]


def test_event_version_clock_and_done_stops(pg, site):
    """B1: в версии после события «сейчас» — момент события; выполненное к нему — без «Приехал», «Дальше» — первый
    невыполненный адрес. Утренний план — как раньше."""
    url, doc, at1, at2 = site
    open_eng(pg, site, B1.replace('&b=', '&v=0&b='))
    assert pg.input_value('#clock') == hhmm(at1)
    assert pg.locator('#stop-1 .stamp').inner_text() == 'выполнено' and pg.locator('#stop-1 [data-mark]').count() == 0
    assert pg.locator('#stop-2 .stamp.now').count() == 1 and pg.locator('#stop-2 [data-mark]').inner_text() == 'Закончил'
    assert pg.locator('#next-btn').inner_text() == 'Закончил' and pg.get_attribute('#next-btn', 'data-i') == '1'
    assert pg.locator('.nb-l').inner_text().startswith('На адресе')

    open_eng(pg, site, B1.replace('&b=', '&v=1&b='))                               # второй адрес закончен к событию
    assert pg.input_value('#clock') == hhmm(at2)
    for i in (1, 2):
        assert pg.locator(f'#stop-{i} .stamp').inner_text() == 'выполнено'
        assert pg.locator(f'#stop-{i} [data-mark]').count() == 0
    assert pg.locator('#stop-3 [data-mark="arrived"]').count() == 1
    assert pg.locator('#next-btn').inner_text() == 'Приехал' and pg.get_attribute('#next-btn', 'data-i') == '2'
    assert pg.locator('.nb-l').inner_text().startswith('Дальше')
    assert f'К {hhmm(at2)} по плану выполнено 2 из 3' in pg.locator('#fact').inner_text()
    assert pg.locator('#emap .m-num.done').all_inner_texts() == ['1', '2']

    open_eng(pg, site, B1)                                                        # утренний план: ничего не выполнено
    assert pg.input_value('#clock') == hhmm(first_order(doc)['plan']['arrive'])
    assert pg.locator('.stamp').count() == 0 and pg.locator('#stop-1 [data-mark="arrived"]').count() == 1
    assert pg.get_attribute('#next-btn', 'data-i') == '0'
    assert not pg.errors


def test_kit_same_on_phone_and_sheet(pg, site):
    """B8: «на маршрут» — сумма оборудования заявок маршрута, «взять» — выдано утром (stock); на телефоне и в листе одно и то же."""
    url, doc, *_ = site
    open_eng(pg, site, B1)
    need = {}
    for s in route(doc, 0)['stops']:
        for k, n in doc['orders'][s['k']]['equipment'].items():
            need[k] = need.get(k, 0) + n
    want = {k: (str(need[k]), str(doc['brigades'][0]['stock'][k])) for k in need}
    assert want == {'router': ('3', '4'), 'stb': ('1', '2')}
    attrs = "els => els.map(e => [e.dataset.eq, e.dataset.need, e.dataset.take, e.innerText.replace(/\\s+/g, ' ').trim()])"
    phone = pg.locator('#kit .krow').evaluate_all(attrs)
    sheet = pg.locator('#sheet .sh-kr').evaluate_all(attrs)
    assert {k: (n, t) for k, n, t, _ in phone} == want == {k: (n, t) for k, n, t, _ in sheet}
    assert [x[3] for x in phone] == ['Роутер на маршрут 3, взять 4', 'ТВ-приставка на маршрут 1, взять 2']
    assert [x[3] for x in sheet] == ['Роутер: на маршрут 3, взять 4', 'ТВ-приставка: на маршрут 1, взять 2']
    pg.emulate_media(media='print')
    assert 'Роутер: на маршрут 3, взять 4' in pg.locator('#sheet').inner_text()
    assert not pg.errors


def test_first_screen_and_next_bar(pg, site):
    """U09: бригада в адресе — одна строка выбора, печать в конце страницы, липкая панель «Дальше» ведёт по всем адресам."""
    url, doc, *_ = site
    open_eng(pg, site, B1)
    assert pg.locator('#pick-toggle').inner_text() == 'Б1 · Инженер' and not pg.locator('#brigade').is_visible()
    assert pg.get_attribute('#pick-toggle', 'aria-expanded') == 'false'
    pg.click('#pick-toggle')
    assert pg.locator('#brigade').is_visible()
    pg.click('#pick-toggle')
    after = 'document.getElementById("stops").compareDocumentPosition(document.getElementById("print-btn")) & 4'
    assert pg.evaluate(after)                                                     # печать — после адресов
    bar = pg.locator('#nextbar')
    box = bar.bounding_box()
    assert box['y'] + box['height'] == pytest.approx(844, abs=1)                  # прижата к низу экрана
    assert pg.locator('#next-btn').bounding_box()['height'] >= 52
    stops = route(doc, 0)['stops']
    for i, s in enumerate(stops):
        o = doc['orders'][s['k']]
        assert pg.get_attribute('#next-btn', 'data-i') == str(i) and pg.locator('#next-btn').inner_text() == 'Приехал'
        assert pg.locator('.nb-l').inner_text() == f"Дальше · {hhmm(o['plan']['arrive'])}"
        pg.click('#next-btn')
        assert 'Приехал в' in pg.locator(f'#stop-{i + 1} .fline').inner_text()
        assert pg.locator('#next-btn').inner_text() == 'Закончил'
        assert pg.locator('.nb-l').inner_text().startswith('На адресе · до ')
        pg.click('#next-btn')
        assert 'закончил' in pg.locator(f'#stop-{i + 1} .fline').inner_text()
    assert pg.locator('#next-btn').count() == 0 and 'Все адреса закрыты' in bar.inner_text()
    marks = pg.evaluate("(m => (delete m._plan, m))(JSON.parse(localStorage.getItem('pulse.marks|inzhener|-1|Б1')))")
    assert sorted(marks) == sorted(doc['orders'][s['k']]['id'] for s in stops)
    assert all(set(m) == {'arrived', 'done'} for m in marks.values())
    pg.emulate_media(media='print')
    assert not bar.is_visible()
    assert not pg.errors


def test_call_button_only_with_phone(pg, site):
    """U10: иконка телефона — первой в ряду, ссылка tel:; свой номер из данных, иначе демо-номер сборки (+7 000)."""
    open_eng(pg, site, B1)
    first = pg.locator('#stop-2 .acts > *').first
    assert first.get_attribute('class') == 'btn call' and first.get_attribute('href') == 'tel:+79161234567'
    assert first.locator('svg').count() == 1 and first.get_attribute('aria-label').startswith('Позвонить')   # иконка
    assert pg.locator('#stop-2 .acts > *').all_inner_texts() == ['', 'Маршрут', 'На карте']
    demo = pg.locator('#stop-1 .acts .call')
    assert demo.count() == 1 and demo.get_attribute('href').startswith('tel:+7000')
    assert not pg.errors


def test_stop_card_address_first(pg, site):
    """U23: адрес первой строкой (с пробелом после «ул.», без квартиры — она плашкой), затем окно и приезд; работа без
    внутренних кодов; дом — под «Подробнее», без «индивидуальный проект» и «Иные»."""
    url, doc, *_ = site
    open_eng(pg, site, B1)
    card = pg.locator('#stop-2')
    assert card.locator('.stop-h .addr').inner_text() == 'ул. Михайлова, д. 14'
    assert float(card.locator('.stop-h .addr').evaluate('e => parseFloat(getComputedStyle(e).fontSize)')) >= 20
    o = first_order(doc, 0, 1)
    assert card.locator('.when').inner_text().startswith(f"окно {hhmm(o['window'][0])}–{hhmm(o['window'][1])} · приезд {hhmm(o['plan']['arrive'])}")
    assert card.locator('.place span').all_inner_texts()[0] == 'кв. 13'
    assert card.inner_text().count('кв. 13') == 1
    assert card.locator('.work-l').inner_text() == 'Ошибки телевидения'                     # общий словарь названий
    assert 'TVE' not in pg.locator('#sheet').inner_text()
    more = card.locator('details.more')
    assert more.get_attribute('open') is None and not more.locator('div').first.is_visible()
    more.locator('summary').click()
    text = more.inner_text()
    assert 'Дом: построен в 1972 г., 9 этажей, 1 лифт' in text and 'индивид' not in text.lower() and 'Иные' not in text
    assert not pg.errors


def test_demo_clock_collapsed_and_undo_by_mark(pg, site):
    """U24: часы, −5/+5 и «Сбросить» — в свёрнутом блоке «Время для показа» внизу; «Отменить» — у только что сделанной отметки."""
    open_eng(pg, site, B1)
    assert pg.get_attribute('#demo', 'open') is None
    assert not pg.locator('#clock').is_visible() and not pg.locator('[data-shift="5"]').is_visible()
    assert pg.evaluate('document.getElementById("stops").compareDocumentPosition(document.getElementById("demo")) & 4')
    assert pg.locator('#demo summary').inner_text() == 'Время для показа'
    pg.click('#stop-1 [data-mark="arrived"]')
    undo = pg.locator('#stop-1 #facts-undo')
    assert undo.count() == 1 and pg.locator('#facts-undo').count() == 1
    assert pg.get_attribute('#demo', 'open') is None                             # отметка не раскрывает блок
    undo.click()
    assert pg.locator('#facts-undo').count() == 0 and pg.locator('#stop-1 [data-mark="arrived"]').count() == 1
    pg.click('#demo summary')
    assert pg.locator('#clock').is_visible() and pg.locator('[data-shift="-5"]').is_visible()
    set_clock(pg, 200)
    assert pg.get_attribute('#demo', 'open') is not None and pg.input_value('#clock') == hhmm(200)   # блок не сворачивается
    assert not pg.errors


def test_screen_switch_buttons_same_size(pg, site):
    """«Экран инженера» у диспетчера и «Экран диспетчера» у инженера — одного размера (на телефоне — только значок)."""
    sizes = []
    for w in (1440, 390):
        pg.set_viewport_size({'width': w, 'height': 900})
        pg.goto(site[0] + 'index.html')
        pg.wait_for_selector('.glabel')
        a = pg.locator('#eng-link').bounding_box()
        open_eng(pg, site, B1)
        e = pg.locator('#to-dispatch').bounding_box()
        assert (round(a['width']), round(a['height'])) == (round(e['width']), round(e['height'])), w
        sizes.append(round(a['width']))
    assert sizes[0] > sizes[1]
