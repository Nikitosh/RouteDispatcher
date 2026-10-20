"""Страница диспетчера в браузере (Playwright), то, что не проверяют test_page.py: блоки правой панели из полей плана
(свободные окна, цена правил, цена качества, точное время, ранний старт, уточнение по историческим данным, норматив, нехватка
бригад, покрытие аварий), формы событий через сервер пересчёта, «Показать» и «закрыть», передача заявки кнопкой,
разворот бригады при проигрывании, свои версии по участкам, ссылки на экран инженера, адрес #sc= и клавиатура.
Нужны playwright (pip) с Chromium и доступ к CDN Leaflet; иначе тест пропускается."""
import copy
import json
import math
import threading
import urllib.error
import urllib.request

import pytest

from dispatch.site.builder import build_site
from dispatch.site.bundle import build_bundle
from dispatch.tools import transit
from tests.e2e.test_api import FakeOsrm
from tests.e2e.test_page import browser, hm, sync_api, url  # noqa: F401  (фикстуры модуля и проверка окружения)

pytestmark = pytest.mark.e2e


def post(base, path, body):
    req = urllib.request.Request(base + 'api/' + path, method='POST', headers={'Content-Type': 'application/json'},
                                 data=json.dumps(body).encode())
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        raise AssertionError(e.read().decode()) from e


# поля плана, которые считают отдельные режимы (lct plan --price, --quality, --exact, --early, --facts, --norms, покрытие)
DEFICIT = dict(service=60, windows=[[0, 120], [120, 240]], districts=['Район', 'Центр'], free=[[0, 2], [5, 1]],
               demand=[[3, 1], [0, 2]], free_all=[[0, 4], [5, 1]], cap=4)
PRICE = [dict(what='Без обеда', brigades=2, bound=2, km=14.2, served=6), dict(what='Без навыков', brigades=3, bound=2, km=12.0, served=5)]
QUALITY = dict(pool=5, k=3, proven=True, curve=[dict(k=3, brigades=3, km=18.5, rating=4.6), dict(k=4, brigades=3, km=16.0, rating=4.4),
                                                dict(k=5, brigades=3, km=15.6, rating=4.2)])
EXACT = [dict(share=0, brigades=3, km=15.6, served=6, orders=6), dict(share=0.5, brigades=3, km=17.1, served=6, orders=6),
         dict(share=1, brigades=4, km=20.0, served=5, orders=6)]
EARLY = dict(calls=[dict(k=3, id='4', window=[240, 600], start=200, earlier=40)], brigades=2, km=13.0, minutes=60)
LEARNED = dict(facts=12, rows=[dict(what='Ремонт', was=1.0, now=1.2, n=7)])
SHORTAGE = [dict(mode='car', mode_name='Авто', skills=['Ремонт'], shift='10:00–22:00', start='офис', orders=['3']), dict(orders=['7'])]
GRID = 6                                                   # клеток покрытия; срезы — по часу с 10:00


def coverage(office):
    grid = [[office[0] + 0.01 * (i // 3), office[1] + 0.01 * (i % 3)] for i in range(GRID)]
    minutes = [[10, 30, 70, 130, None, 50] if s < 4 else [10, 20, 30, 40, 50, None] for s in range(12)]
    return dict(step=1.0, grid=grid, slots=[60 * s for s in range(12)], minutes=minutes, brigade=[[0, 0, 1, 1, 0, 2]] * 12)


@pytest.fixture(scope='module')
def site(url, tmp_path_factory):
    """Сайт с сервером пересчёта: участок «Смены» (план, отмена в 11:00 из сервера, событие с разворотом Б1 — вне
    историй) и участок «Блоки» — копия плана с полями всех блоков панели; история «День» со всеми видами шагов."""
    base, doc = url
    late = max(doc['orders'], key=lambda o: o['plan']['start'])
    ev = post(base, 'variants', dict(region='smeny', doc=doc, at=60, event=dict(kind='cancel', order=late['id'])))['variants'][0]['doc']
    root = tmp_path_factory.mktemp('gaps')
    plan_dir, ev_dir, turn_dir, blocks_dir = (root / x for x in ('plan', 'ev', 'turn', 'blocks'))
    ev['event']['previous'] = str(plan_dir)
    # разворот: Б1 после первой заявки едет ко второй через точку разворота; линия маршрута по ногам, нога 1 разрезана
    turned = copy.deepcopy(ev)
    r0 = next(r for r in turned['plan']['routes'] if r['v'] == 0)
    assert [s['k'] for s in r0['stops']] == [0, 1, 2]
    O = turned['orders']
    start = [doc['brigades'][0]['lat'], doc['brigades'][0]['lon']]
    p0, p1, p2 = ([O[s['k']]['lat'], O[s['k']]['lon']] for s in r0['stops'])
    via = [(p0[0] + p1[0]) / 2, p0[1] + 0.02]
    r0['geometry'] = [[start, p0], [p0, via], [via, p1], [p1, p2]]
    r0['via'] = dict(pos=1, lat=via[0], lon=via[1])
    turned['event'].update(at=40, turned=[dict(brigade=0, kind='transit', was=2, now=1, lat=via[0], lon=via[1])])
    # «Блоки»: заявка 3 без исполнителя, поля всех блоков
    blocks = copy.deepcopy(doc)
    blocks['meta'] = dict(doc['meta'], name='Блоки')
    b0 = next(r for r in blocks['plan']['routes'] if r['v'] == 0)
    b0['stops'] = [s for s in b0['stops'] if s['k'] != 2]
    blocks['orders'][2].update(plan=None, reason=dict(text='никто не успевает'))
    blocks['plan']['metrics']['rating'] = 4.6
    blocks.update(deficit=DEFICIT, price=PRICE, quality=QUALITY, exact_time=EXACT, early=EARLY, learned=LEARNED,
                  norms=dict(late=2), shortage=SHORTAGE, coverage=coverage((doc['office']['lat'], doc['office']['lon'])))
    for d, x in ((plan_dir, doc), (ev_dir, ev), (turn_dir, turned), (blocks_dir, blocks)):
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(x, ensure_ascii=False))
    journeys = [dict(slug='day', title='День', steps=[
        dict(slug='plan', title='План дня', text='Утренний план.', dir=str(plan_dir)),
        dict(slug='cancel', title='Отмена', text='Клиент отменил заявку.', dir=str(ev_dir)),
        dict(slug='order', title='Заявка 2', text='Карточка заявки.', dir=str(plan_dir), order='2'),
        dict(slug='rules', title='Правила', text='Цена правил.', dir=str(blocks_dir), show='learned,price'),
        dict(slug='cover', title='Покрытие', text='Аварии.', dir=str(blocks_dir), show='coverage')])]
    out = root / 'site'
    cache = root / 'cache'
    cache.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transit, 'DIR', str(tmp_path_factory.mktemp('нет_сети')))
        mp.setenv('DISPATCH_HERE_GRID', 'off')                    # как в conftest.py: без сетки пробок
        mp.setattr('dispatch.api.SECONDS', 1.0)                  # решатель на сервере — секунда на вариант
        build_site([str(plan_dir), str(ev_dir), str(turn_dir), str(blocks_dir)], str(out), log=lambda *_: None, scenarios=journeys)
        build_bundle(doc, [], str(out), client=FakeOsrm(), log=lambda *_: None, cache_dir=str(cache))
        man = json.load(open(out / 'manifest.json'))
        json.dump(dict(man, api=True, events=True), open(out / 'manifest.json', 'w'), ensure_ascii=False)
        from werkzeug.serving import make_server

        from dispatch.api import create_app
        server = make_server('127.0.0.1', 0, create_app(str(out)), threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield dict(url=f'http://127.0.0.1:{server.server_port}/', doc=doc, ev=ev, turned=turned, via=via)
        server.shutdown()


@pytest.fixture
def pg(site, browser):
    p = browser.new_page(viewport=dict(width=1400, height=1000))
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    yield p
    p.close()


def open_page(pg, site, hash=''):
    pg.goto(site['url'] + hash)
    pg.wait_for_selector('.glabel')


def kpi0(pg):
    return pg.locator('.kpi .l').first.inner_text()


def paths(pg):
    return pg.locator('#map .leaflet-overlay-pane path').count()


# ---------- блоки панели из полей плана


def test_blocks_rendered_from_doc_keys(pg, site):
    open_page(pg, site, '#sc=d-bloki')
    kpis = pg.locator('.kpiwrap').inner_text()
    assert [t for t in pg.locator('.kpi .l').all_inner_texts()] == ['Заявки', 'Бригад в работе', 'Пробег', 'Минимум бригад']
    chips = pg.locator('#chips .chip').all_inner_texts()                          # прочее — мелкими значками под плитками
    assert 'Без учёта сложности работ: 2 опоздания' in chips
    assert 'Ранний старт: 2 бригады, 13,0 км' in chips and '★ 4,60 — рейтинг на заявке; лучшие 3 из 5' in chips
    assert '…' not in kpis
    titles = [t.split('\n')[0] for t in pg.locator('#panel > .ptitle').all_inner_texts()]   # без show — сводка и всё раскрыто
    assert titles[0] == 'Итог по участку' and pg.locator('#panel details.more').count() == 0
    assert titles.index('Где ещё можно предложить подключение') < titles.index('Во что обходится каждое правило')
    assert 'В резерве' in pg.locator('#brigades .bline').inner_text()                # бригады — строкой под диаграммой
    more = pg.locator('#panel')
    text = more.inner_text()
    assert 'подключений по 60 мин' in text
    cells = pg.locator('.dft td[title]')
    assert cells.all_inner_texts() == ['—', '2', '4+', '1']                        # 0 — прочерк, 5 при пределе 4 — «4+»
    assert cells.nth(1).get_attribute('title') == 'в плане 1; свободно 2, с резервом 4'
    assert [cells.nth(i).get_attribute('class') for i in range(4)] == ['df0', 'df1', 'df2', 'df1']
    rules = [' '.join(r.split()) for r in pg.locator('.rule').all_inner_texts()]        # разница с планом 3 бр., 15,6 км
    assert rules[0].startswith('Без обеда −1,4 км') and '−1 бригада' in rules[0]
    assert rules[1].startswith('Без навыков −3,6 км') and '−1 заявка' in rules[1] and 'бригад' not in rules[1]
    q = pg.locator('.qt tr.on td').first.text_content()                           # таблица — свёрнута под графиком
    assert pg.locator('.quality circle.qp.sel').count() == 1 and pg.locator('.quality polyline.ql').count() == 1
    assert 'Цена качества' in text and q == '3' and 'Выбрано 3: меньше лучших не закрывают день (доказано).' in text
    row = pg.locator('.lt tr').nth(1).inner_text().split('\t')                    # по историческим данным — таблицей
    assert 'По историческим данным' in text and '12 выездов' in text and row[:3] == ['Ремонт', '×1,00', '×1,20 +20%'] and row[3] == '7'
    assert 'Цена точного времени' in text
    assert 'Часовое окно для 50% клиентов: +1,5 км при том же парке. Для всех: 4 бригады, без исполнителя 1.' in text
    early = more.locator('details.early')
    assert 'Ранний старт: обзвон клиентов' in early.locator('summary').inner_text()
    assert '1 заявка → 2 бригады, 13,0 км' in early.locator('summary').inner_text()
    bottom = pg.locator('#unassigned').inner_text()
    assert 'Без исполнителя\n1' in bottom and 'никто не успевает' in bottom
    assert '+1 бригада: Авто; Ремонт; смена 10:00–22:00; старт: офис закроет 3' in bottom
    assert 'Не закрываются дополнительными бригадами: 7' in bottom
    early.locator('summary').click()                                              # строка обзвона — карточка заявки
    early.locator('tr.click').click()
    card = pg.locator('#panel .ocard')
    assert 'заявка 4' in card.inner_text() and 'Можно предложить начало в 13:20, на 40 мин раньше окна' in card.inner_text()
    assert not pg.errors


def test_early_start_without_gain_and_whole_fleet(pg, site):
    """Ранний старт без выигрыша — не совет, а приглушённая строка; «меньше нельзя» при нехватке — «весь парк»."""
    open_page(pg, site, '#sc=d-bloki')
    pg.evaluate("D.early = Object.assign({}, D.early, {useful: false}); D.lower_bound = D.plan.metrics.brigades_used;"
                "D.plan.metrics.unassigned = 1; render()")                         # в копии плана метрики не пересчитаны
    chips = pg.locator('#chips .chip').all_inner_texts()
    assert 'Ранний старт не даёт выигрыша' in chips and not any(c.startswith('Ранний старт:') for c in chips)
    assert pg.locator('#panel details.early').count() == 0 and 'Ранний старт не даёт выигрыша' in pg.locator('#panel').inner_text()
    used, fleet = pg.evaluate("[D.plan.metrics.brigades_used, D.brigades.length]")
    want = 'весь парк, его не хватает на 1 заявку' if used == fleet else 'нельзя — доказано для'
    tile = 'весь парк, не хватает на 1 заявку' if used == fleet else 'меньше нельзя — доказано'
    assert tile in pg.locator('#kpis').inner_text() and want in pg.locator('#panel .sum-n').inner_text()
    assert not any('нельзя' in c for c in chips)                                  # граница — плиткой, не значком
    assert not pg.errors


def test_scenario_show_puts_blocks_first(pg, site):
    open_page(pg, site, '#sc=day-rules')
    titles = pg.locator('#panel > .ptitle').all_inner_texts()
    assert len(titles) == 2 and titles[0].startswith('По историческим данным') and titles[1].startswith('Во что обходится каждое правило')
    assert pg.locator('#panel details.more > summary').inner_text() == \
        'Ещё по участку: где предложить подключение, цена качества, цена точного времени, ранний старт'
    assert not pg.errors


def test_coverage_checkbox_and_slider(pg, site):
    open_page(pg, site, '#sc=d-bloki')
    assert not pg.locator('#cov-box').is_visible()                               # рамка — только в своём сценарии
    open_page(pg, site, '#sc=day-cover')
    assert pg.locator('#cov-box').is_visible() and pg.locator('#cov-on').is_checked()   # сценарий show=coverage включает слой
    pg.uncheck('#cov-on')
    assert not pg.locator('#cov-ctl').is_visible()
    base = paths(pg)
    red = '#map .leaflet-overlay-pane path[fill="#D92D20"]'
    pg.check('#cov-on')
    assert paths(pg) == base + GRID and pg.locator('#cov-ctl').is_visible()
    assert pg.locator('#cov-time').inner_text() == '18:00'                       # срез по умолчанию — 8-й
    assert pg.locator(red).count() == 1
    pg.locator('#cov-range').fill('2')
    assert pg.locator('#cov-time').inner_text() == '12:00'
    assert paths(pg) == base + GRID and pg.locator(red).count() == 2             # в 12:00 две клетки дольше двух часов
    pg.locator('#play-range').fill('100')                                        # при проигрывании покрытия нет
    assert not pg.locator('#cov-box').is_visible()
    pg.click('#play-all')
    assert pg.locator('#cov-box').is_visible() and paths(pg) == base + GRID
    pg.uncheck('#cov-on')
    assert paths(pg) == base
    assert pg.locator('.covleg').inner_text().split() == ['≤1', 'ч', '1–2', 'ч', '>2', 'ч']
    assert not pg.errors


# ---------- события через сервер пересчёта


def map_click(pg):
    box = pg.locator('#map').bounding_box()
    pg.locator('#map').click(position=dict(x=box['width'] * 0.8, y=box['height'] * 0.4))


def calc(pg):
    pg.click('#f-go')
    pg.wait_for_selector('.variant', timeout=60000)


@pytest.mark.parametrize('kind', ['urgent', 'order', 'cancel', 'unavailable'])
def test_event_forms(pg, site, kind):
    open_page(pg, site, '#sc=d-smeny')
    pg.click('#ev-btn')
    pg.click(f'.seg button[data-k="{kind}"]')
    assert pg.locator('.seg button.on').get_attribute('data-k') == kind
    if kind in ('urgent', 'order'):
        assert 'Укажите адрес кликом по карте' in pg.locator('#panel').inner_text()
        if kind == 'order':
            pg.select_option('#f-type', 'ремонт')
            pg.fill('#f-win', '16:00-18:00')
            pg.dispatch_event('#f-win', 'change')
        map_click(pg)
        assert 'Точка: 55.' in pg.locator('#panel').inner_text() and pg.locator('#map .pick').count() == 1
        title = 'новая заявка: авария' if kind == 'urgent' else 'новая заявка: ремонт'
    elif kind == 'cancel':
        pg.select_option('#f-order', '5')
        title = 'отменена заявка 5'
    else:
        pg.select_option('#f-brig', 'Б3')
        title = 'Б3 недоступна'
    calc(pg)
    pg.click('[data-apply="0"]')
    pg.wait_for_function("document.body.innerText.includes('Моё:')")
    assert f'Моё: 12:00 — {title}' in pg.locator('#ctx').inner_text()
    assert kpi0(pg) == 'Событие' and title in pg.locator('.kpi .d').first.inner_text()
    if kind == 'order':
        assert pg.locator('#map .pick').count() == 0                             # точка формы ушла вместе с формой
    assert not pg.errors


@pytest.mark.parametrize('hash, kpi, was', [('#sc=d-smeny', 'Заявки', None), ('#sc=day-cancel', 'Событие', 'отменена заявка 5')])
def test_show_variant_then_close_restores_view(pg, site, hash, kpi, was):
    open_page(pg, site, hash)
    assert kpi0(pg) == kpi
    pg.click('#ev-btn')
    pg.click('.seg button[data-k="unavailable"]')
    pg.select_option('#f-brig', 'Б3')
    calc(pg)
    pg.click('[data-show="0"]')                                                  # «Показать» — вариант на карте и в KPI
    assert kpi0(pg) == 'Событие' and 'Б3 недоступна' in pg.locator('.kpi .d').first.inner_text()
    pg.click('#f-close')                                                         # «закрыть» — как было до формы
    assert kpi0(pg) == kpi
    if was:
        assert was in pg.locator('.kpi .d').first.inner_text()
        assert pg.locator('#panel .ptitle').first.inner_text().startswith('Что изменилось в 11:00')
    else:
        assert pg.locator('#panel .ptitle').first.inner_text().startswith('Итог по участку')
    assert 'Моё:' not in pg.locator('#ctx').inner_text()
    assert not pg.errors


def test_move_button_and_options(pg, site):
    """Передача заявки кнопкой в карточке: время — в окне prompt, варианты бригад, по клику — новая версия «Моё»."""
    open_page(pg, site, '#sc=d-smeny')
    k = next(k for k, o in enumerate(site['doc']['orders']) if o['id'] == '5')
    pg.locator(f'.work[data-k="{k}"]').click()
    pg.once('dialog', lambda d: d.accept('11:00'))
    pg.click('#move-btn')
    pg.wait_for_selector('.mopt')
    assert 'Передача в 11:00' in pg.locator('#panel').inner_text()
    names = pg.locator('.mopt b').all_inner_texts()
    assert 'Б2' not in names and 'Б1' in names                                   # своя бригада в списке не нужна
    target = pg.locator('.mopt[data-b]').first.get_attribute('data-b')
    pg.click(f'.mopt[data-b="{target}"]')
    pg.wait_for_function("document.body.innerText.includes('Моё:')", timeout=30000)
    assert f'Моё: 11:00 — заявка 5 → {target}' in pg.locator('#ctx').inner_text()
    assert pg.locator('#scn optgroup[label="Мои события"] option').all_inner_texts() == [f'11:00 — заявка 5 → {target}']
    assert not pg.errors


def test_user_versions_per_district_and_base_before_event(pg, site):
    """Своя версия от события «отмена»: до момента своей версии шкала показывает её основу (отмену, 5 заявок),
    а не утренний план (6); версии хранятся по участкам и возвращаются вместе с участком."""
    open_page(pg, site, '#sc=day-cancel')
    assert pg.locator('#eng-link').get_attribute('href') == 'engineer.html#d=smeny&v=1'
    phone = pg.locator('.glabel a[title="Экран инженера"]').first.get_attribute('href')
    assert phone.startswith('engineer.html#d=smeny&v=1&b=')
    pg.click('#ev-btn')
    assert pg.input_value('#f-at') == '11:30'                                    # событие сценария + 30 мин
    pg.click('.seg button[data-k="unavailable"]')
    pg.select_option('#f-brig', 'Б3')
    calc(pg)
    pg.click('[data-apply="0"]')
    pg.wait_for_function("document.body.innerText.includes('Моё:')")
    assert pg.input_value('#scn') == 'u0'
    assert pg.locator('#eng-link').get_attribute('href') == 'engineer.html#d=smeny'   # своей версии нет у инженера
    assert pg.locator('#tl-ev').inner_text() == '11:30'
    pg.locator('#play-range').fill('70')                                         # 11:10 — до своей версии
    assert pg.locator('#play-time').inner_text() == '11:10'
    assert 'из 5' in pg.locator('#play-panel').inner_text()                       # основа — план после отмены
    assert kpi0(pg) == 'Заявки'
    pg.select_option('#scn', 'd-bloki')
    pg.wait_for_function("location.hash === '#sc=d-bloki'")
    assert pg.locator('#scn optgroup[label="Мои события"]').count() == 0          # у другого участка своих версий нет
    pg.select_option('#scn', 'd-smeny')
    pg.wait_for_function("location.hash === '#sc=d-smeny'")
    assert pg.locator('#scn optgroup[label="Мои события"] option').count() == 1
    pg.select_option('#scn', 'u0')
    assert 'Моё: 11:30 — Б3 недоступна' in pg.locator('#ctx').inner_text()
    assert kpi0(pg) == 'Событие'
    assert not pg.errors


# ---------- сценарии, разворот, проигрывание


def test_scenario_with_order_opens_card(pg, site):
    open_page(pg, site, '#sc=day-order')
    card = pg.locator('#panel .ocard')
    assert card.is_visible() and 'заявка 2' in card.inner_text()
    assert pg.locator('.work.sel').get_attribute('data-k') == '1'
    pg.click('#back')
    assert pg.locator('#panel .ocard').count() == 0 and pg.locator('.work.sel').count() == 0
    assert not pg.errors


def test_other_events_group(pg, site):
    open_page(pg, site)
    groups = pg.locator('#scn optgroup').evaluate_all('gs => gs.map(g => [g.label, [...g.children].map(o => [o.value, o.textContent])])')
    assert [g[0] for g in groups] == ['Планы участков', 'День', 'Другие события']   # планы участков — первыми
    assert groups[2][1] == [['e-smeny-0', 'Смены: 10:40 — отмена заявки 5']]     # событие вне историй
    assert groups[0][1] == [['d-bloki', 'Блоки'], ['d-smeny', 'Смены']]
    assert pg.input_value('#scn') == 'd-bloki' and pg.locator('#scn-n').inner_text().startswith('1 из')
    assert not pg.errors


def pin(pg, name):
    got = pg.evaluate("""() => layer.getLayers().filter(l => l.options.icon && /class="pin"/.test(l.options.icon.options.html || ''))
        .map(l => [l.options.icon.options.html.replace(/<[^>]+>/g, ''), l.getLatLng().lat, l.getLatLng().lng])""")
    return next((lat, lon) for n, lat, lon in got if n == name)


def test_turned_route_marker_panel_and_play(pg, site):
    open_page(pg, site, '#sc=e-smeny-0')
    assert pg.locator('#map .reroute.big').count() == 1                          # открылось на моменте события — подсветка
    assert pg.locator('#panel .turned').inner_text().replace('\n', ' ').strip() == \
        'Б1: в пути к заявке 3. С текущей точки — сразу к заявке 2 (ремонт)'
    assert pg.locator('#eng-link').get_attribute('href') == 'engineer.html#d=smeny&v=0'
    assert pg.locator('.glabel a[title="Экран инженера"]').first.get_attribute('href').startswith('engineer.html#d=smeny&v=0&b=')
    pg.click('#play-all')                                                         # весь день — значок без подсветки
    assert pg.locator('#map .reroute').count() == 1 and pg.locator('#map .reroute.big').count() == 0
    st = next(r for r in site['turned']['plan']['routes'] if r['v'] == 0)['stops']
    O, via = site['turned']['orders'], site['via']
    # перед приездом ко второй заявке Б1 уже за точкой разворота: идёт по второй половине склеенной ноги
    t1 = 2 * math.floor((st[1]['arr'] - 0.5) / 2)
    assert t1 >= 40 and (t1 - st[0]['end']) / (st[1]['arr'] - st[0]['end']) > 0.5
    pg.locator('#play-range').fill(str(t1))
    lat, lon = pin(pg, 'Б1')
    assert via[0] < lat < O[1]['lat'] and O[1]['lon'] < lon < via[1]
    # следующая нога — от второй заявки к третьей по прямой, а не обратно от точки разворота
    t2 = 2 * round((st[1]['end'] + st[2]['arr']) / 4)
    assert st[1]['end'] < t2 < st[2]['arr']
    pg.locator('#play-range').fill(str(t2))
    lat, lon = pin(pg, 'Б1')
    assert lon == pytest.approx(O[1]['lon'], abs=1e-9) and O[1]['lat'] < lat < O[2]['lat']
    assert 'в пути' in pg.locator('#panel').inner_text()
    assert not pg.errors


def test_play_to_end_and_restart(pg, site):
    open_page(pg, site, '#sc=d-smeny')
    pg.locator('#play-range').fill('700')
    pg.click('#play-btn')
    assert pg.locator('#play-btn').inner_text() == 'Пауза'
    pg.wait_for_function("document.getElementById('play-btn').textContent === 'Пуск'", timeout=5000)
    assert pg.locator('#play-time').inner_text() == '22:00' and pg.input_value('#play-range') == '720'
    assert 'Выполнено 6 из 6' in pg.locator('#play-panel').inner_text()
    pg.click('#play-btn')                                                         # с конца дня — снова с 10:00
    assert pg.locator('#play-time').inner_text() < '10:30'
    pg.click('#play-btn')
    assert pg.locator('#play-btn').inner_text() == 'Пуск'
    assert not pg.errors


def test_hashchange_switches_scenario(pg, site):
    open_page(pg, site, '#sc=d-smeny')
    pg.evaluate("location.hash = '#sc=day-plan'")
    pg.wait_for_function("document.getElementById('scn').value === 'day-plan'")
    assert 'Утренний план.' in pg.locator('#ctx').inner_text()
    pg.evaluate("location.hash = '#sc=day-cancel'")
    pg.wait_for_function("document.getElementById('scn').value === 'day-cancel'")
    assert kpi0(pg) == 'Событие'
    assert not pg.errors


def test_enter_on_work_opens_card(pg, site):
    open_page(pg, site, '#sc=d-smeny')
    pg.locator('.work[data-k="0"]').focus()
    pg.keyboard.press('Enter')
    card = pg.locator('#panel .ocard')
    assert card.is_visible() and 'заявка 1' in card.inner_text()
    pg.locator('#back').focus()
    pg.keyboard.press('Enter')                                                    # «← Список» — обычная кнопка
    assert pg.locator('#panel .ocard').count() == 0
    assert not pg.errors


def test_scenario_only_blocks(pg, site):
    """Блоки своего сценария: рейтинг монтажников, обед и оборудование, старт из дома, смены."""
    open_page(pg, site, '#sc=d-bloki')
    html = pg.evaluate("""(() => { const R = Object.assign({}, cur(), {ratings: {'Б1': {by_kind: {'Ремонт': {value: 4.5, reviews: 10}}}}});
      return Object.fromEntries(Object.entries(ONLY).map(([k, f]) => [k, f(R) || ''])); })()""")
    assert 'Рейтинг монтажников' in html['ratings'] and '★' in html['ratings']
    assert 'Обед и оборудование' in html['lunch'] and 'Старт из дома' in html['homes'] and 'Смены бригад' in html['shifts']
    assert 'Ещё по участку' not in html['ratings'] and not pg.errors


def test_plan_routes_dotted_and_turn_highlighted(pg, site):
    open_page(pg, site, '#sc=d-smeny')
    dashes = pg.evaluate("[...document.querySelectorAll('#map path')].map(p => p.getAttribute('stroke-dasharray')).filter(Boolean)")
    assert '6 7' in dashes                                                    # план — пунктиром, как в начале дня
    assert pg.locator('#map .pin').count() >= 1                                # и бригады у точек старта
    open_page(pg, site, '#sc=e-smeny-0')
    pg.click('#tl-ev')                                                        # остановились на событии — подсветка
    assert 'новый маршрут' in pg.locator('#map .reroute.big span').inner_text()
    assert pg.evaluate("[...document.querySelectorAll('#map path')].some(p => p.getAttribute('stroke') === '#D92D20')")
    assert not pg.errors


# ---------- правки интерфейса: событие в фокусе, «до и после», резерв, легенда, клавиатура


def test_event_focus_panel_and_compare(pg, site):
    """Событие открывается на своём моменте: заявка события — крупным маркером, справа — что изменилось словами из
    данных и отменённая заявка в списке, внизу — сравнение «до события → после», а не с базовым вариантом."""
    open_page(pg, site, '#sc=day-cancel')
    at = site['ev']['event']['at']
    assert pg.locator('#play-time').inner_text() == hm(at) and pg.locator('#map .evm').count() == 1
    assert pg.locator('#map .evm').inner_text() == 'отменена · 5'
    panel = pg.locator('#panel')
    assert 'Клиент отменил заявку 5' in panel.locator('.evtext').inner_text()
    row = panel.locator('.chg-r').first
    assert '5' in row.locator('.chg-h').inner_text() and 'отменена' in row.locator('.chg-m').inner_text()
    assert panel.locator('.sum.ba').inner_text().count('→') == 3                  # бригады, км, без исполнителя: до → после
    before = paths(pg)
    row.hover()                                                                   # наведение на строку — заявка на карте
    assert paths(pg) == before + 2
    pg.mouse.move(5, 5)
    assert paths(pg) == before
    cmp = pg.locator('#compare')
    assert cmp.locator('.ptitle').inner_text().startswith('До события и после') and 'базов' not in cmp.inner_text()
    assert pg.locator('#map-leg').inner_text().split('\n')[0].startswith('пройдено')
    pg.click('#play-all')
    assert 'заявка' in pg.locator('#map-leg').inner_text() and 'пройдено' not in pg.locator('#map-leg').inner_text()
    assert not pg.errors


def test_reserve_brigade_starts_at_event(pg, site):
    """Бригада, у которой до события маршрута не было, на диаграмме работает с момента события, не с 10:00."""
    open_page(pg, site, '#sc=day-cancel')
    at = site['ev']['event']['at']
    v = pg.evaluate("(() => { const r = E.plan.routes.find(r => r.stops.length); D.plan.routes = D.plan.routes.filter(x => x.v !== r.v); render(); return r.v; })()")
    row = pg.locator(f'.grow2[data-v="{v}"]')
    assert row.locator('.off.rsv').get_attribute('title') == f'в резерве до {hm(at)}'
    assert 'из резерва' in row.locator('.glabel small').inner_text()
    assert not pg.errors


def test_gantt_click_scrolls_to_card_and_event_mark_keeps_scroll(pg, site):
    open_page(pg, site, '#sc=day-cancel')
    pg.locator('#gantt-card').scroll_into_view_if_needed()
    pg.locator('.work').first.click()
    pg.wait_for_function("document.getElementById('panel').getBoundingClientRect().top < innerHeight / 2", timeout=3000)
    assert pg.locator('#panel .ocard').is_visible()
    pg.click('#back')
    pg.evaluate("window.scrollTo(0, 200)")
    y = pg.evaluate("scrollY")
    pg.click('#tl-ev')
    pg.wait_for_timeout(300)
    assert pg.evaluate("scrollY") == y                                           # метка события страницу не прокручивает
    assert not pg.errors


def test_keyboard_skips_map_markers(pg, site):
    open_page(pg, site, '#sc=d-smeny')
    assert pg.locator('#map .leaflet-marker-icon').count() > 0
    assert pg.locator('#map .leaflet-marker-icon:not([tabindex="-1"])').count() == 0    # маркеры кликаются, но не в порядке Tab
    for n in range(1, 30):                                                        # до «Пуск» — десяток нажатий, не 87
        pg.keyboard.press('Tab')
        if pg.evaluate("document.activeElement.id") == 'play-btn':
            break
    assert n <= 15
    pg.focus('#skip-tl')
    pg.keyboard.press('Enter')
    assert pg.evaluate("document.activeElement.id") == 'play-btn'
    ticks = pg.locator('#tl-ticks span').all_inner_texts()
    assert [t for t in ticks if t] == ['10:00', '12:00', '14:00', '16:00', '18:00', '20:00', '22:00']
    assert not pg.errors


def test_district_summary_and_tidy_texts(pg, site):
    open_page(pg, site, '#sc=d-smeny')
    s = pg.locator('#panel').inner_text()
    assert s.startswith('Итог по участку') and 'Заявки\n6 из 6' in s
    js = pg.evaluate("""[tidy('Все подходящие (Бригада 2, Бригада 3, Бригада 11) заняты'), addrText('Город Москва, ул.Михайлова, д. 21'),
        addrText('г. Москва, пр-кт.Рязанский, д. 2'), addrText('аллея.Жемчуговой, д. 1'), tidy('АВАРИЯ на линии'), work('TVE/ENT. Замена приставки техником')]""")
    assert js[0] == 'Все подходящие (Бригады 2, 3, 11) заняты'
    assert js[1:4] == ['ул. Михайлова, д. 21', 'пр-кт Рязанский, д. 2', 'аллея Жемчуговой, д. 1']
    assert js[4] == 'Авария на линии' and js[5] == 'замена ТВ-приставки'
    assert not pg.errors


def test_reserve_brigade_labelled_and_event_moment_keeps_map(pg, site):
    """Бригада, у которой до события не было маршрута, — «из резерва» у значка; в момент события значки бригад
    не гаснут (карта подсвечивает затронутое, а не переключается)."""
    open_page(pg, site)
    evs = [k for k in pg.evaluate("[...document.querySelectorAll('#scn option')].map(o => o.value)") if k]
    for key in evs:
        open_page(pg, site, '#sc=' + key)
        if not pg.evaluate("!!(E && E.event)"):
            continue
        pg.evaluate("""() => { const v = cur().plan.routes.find(rt => rt.stops.length).v;
          const P = parent(); P.plan.routes = P.plan.routes.map(rt => rt.v === v ? Object.assign({}, rt, {stops: []}) : rt);
          T = null; render(); }""")
        assert pg.locator('#map .rsv-tag').count() >= 1
        pg.evaluate("setT(E.event.at)")
        pg.wait_for_timeout(300)
        assert pg.locator('#map .rsv-tag').count() >= 1
        assert all(float(o or 1) == 1 for o in pg.evaluate(
            "[...document.querySelectorAll('#map .leaflet-marker-icon')].map(m => m.style.opacity)"))
        assert not pg.errors
        return
    raise AssertionError('в тестовом сайте нет событий')
