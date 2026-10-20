"""Командная строка plan и replan целиком: разбор флагов cmd_plan / cmd_replan и что из них попадает в plan.json.

Работает без сети и без OSRM: маленький участок с адресами «широта, долгота» (геокодинг не нужен), OSRM — подмена
OsrmClient.table / route (время по прямой), кэши — во временной папке (cache/ и data/ проекта не трогаются), решатели —
настоящие из solver/bin с коротким --time."""
import json
import os
import urllib.request

import pytest

from dispatch import cli
from dispatch.config import ROOT, SOLVER_BIN
from dispatch.geo import geocoder as geocoder_mod
from dispatch.geo import geometry as geometry_mod
from dispatch.geo import matrices as matrices_mod
from dispatch.geo.geocoder import Geocoder
from dispatch.geo.houses import HouseIndex
from dispatch.geo.nearby import dist_km
from dispatch.geo.osrm import OsrmClient, OsrmError
from dispatch.model import learning, rating
from dispatch.tools import rasp, transit

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'baseline', 'fleet_bound')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)

T = '0.3'                                          # время решателя: участок маленький
OFFICE = (55.75, 37.60)
PACE = {'car': 2, 'bike': 4, 'foot': 12}           # мин/км по прямой
# (заявка, тип BK, подтип HD, окно, район, сдвиг от офиса, транспорт)
ORDERS = [
    ('101', 'Подключение', 'Конвергенция абонента', '10:00', '12:00', 'Р1', (0.004, 0.003), ''),
    ('102', 'Дозаказ', 'Дозаказ оборудования', '10:00', '12:00', 'Р1', (0.008, -0.004), ''),
    ('103', 'Локальная заявка', 'Нет линка', '12:00', '14:00', 'Р2', (-0.006, 0.006), ''),
    ('104', 'Подключение', 'Заявка на подключение', '14:00', '16:00', 'Р2', (-0.010, -0.002), ''),
    ('105', 'Глобальная проблема', 'Авария', '14:00', '16:00', 'Р1', (0.012, 0.010), 'авто'),
    ('106', 'Локальная заявка', 'Разрывы', '16:00', '18:00', 'Р2', (-0.004, -0.009), ''),
    ('107', 'Подключение', 'Конвергенция абонента', '18:00', '20:00', 'Р1', (0.006, 0.012), ''),
    ('108', 'Локальная заявка', 'Нет линка', '20:00', '22:00', 'Р2', (-0.012, 0.004), ''),
]
BRIGADES = [('Б1', 'авто', 'ремонт, подключение, аварии'), ('Б2', 'общ. транспорт', 'ремонт, подключение'),
            ('Б3', 'велосипед', 'ремонт, подключение, аварии')]
WATCHED = [os.path.join(ROOT, d) for d in ('cache', os.path.join('data', 'demo'), 'out')]


def _snapshot():
    out = {}
    for d in WATCHED:
        for base, _, files in os.walk(d):
            for f in files:
                p = os.path.join(base, f)
                out[p] = os.stat(p).st_mtime_ns
    return out


@pytest.fixture(scope='module', autouse=True)
def project_untouched():
    """Ни один тест модуля не пишет в cache/, data/demo/ и out/ проекта."""
    before = _snapshot()
    yield
    assert _snapshot() == before


def fake_table(self, profile, src, dst):
    return [[(dist_km(a, b) * PACE[profile], dist_km(a, b)) for b in dst] for a in src]


def fake_route(self, profile, a, b):
    return [[a[0], a[1]], [b[0], b[1]]]


def offline(mp, root):
    """Кэши — в root/cache, OSRM — подмена, сеть закрыта."""
    cache = root / 'cache'
    cache.mkdir(exist_ok=True)
    for mod in (geocoder_mod, geometry_mod, matrices_mod):
        mp.setattr(mod, 'CACHE_DIR', str(cache))
    mp.setattr(HouseIndex.__init__, '__defaults__', (str(cache / 'houses.json'),))
    mp.setattr(OsrmClient, 'table', fake_table)
    mp.setattr(OsrmClient, 'route', fake_route)

    def no_network(*a, **k):
        raise AssertionError('тест обратился к сети')
    mp.setattr(urllib.request, 'urlopen', no_network)
    mp.setattr(Geocoder, '_nominatim', staticmethod(no_network))
    mp.setattr(transit, 'DIR', str(root / 'transit'))
    mp.setattr(rasp, 'DIR', str(root / 'transit' / 'yandex'))
    mp.setenv('DISPATCH_HERE_GRID', 'off')
    return cache


def write_inputs(folder):
    """Тест заявки.csv (участок «Тест» — как в истории выездов) и бригады.csv."""
    orders = folder / 'Тест заявки.csv'
    lines = ['Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Транспорт']
    for oid, bk, hd, a, b, district, (dy, dx), need in ORDERS:
        lines.append(f'{oid};{bk};{hd};17.08.2026 {a};17.08.2026 {b};{district};'
                     f'{OFFICE[0] + dy:.6f}, {OFFICE[1] + dx:.6f};{need}')
    lines.append(f'Адрес офиса;{OFFICE[0]:.6f}, {OFFICE[1]:.6f}')
    orders.write_text('\n'.join(lines) + '\n', encoding='cp1251')
    brigades = folder / 'бригады.csv'
    brigades.write_text('Бригада;Транспорт;Навыки\n' + ''.join(f'{n};{m};{s}\n' for n, m, s in BRIGADES), encoding='cp1251')
    return str(orders), str(brigades)


def run_plan(capsys, inputs, out, *flags):
    orders, brigades = inputs
    code = cli.main(['plan', orders, '-b', brigades, '-o', str(out), '--time', T, '--no-geometry', *flags])
    text = capsys.readouterr()
    return code, text.out + text.err, (json.load(open(out / 'plan.json', encoding='utf-8')) if (out / 'plan.json').exists() else None)


@pytest.fixture(scope='module')
def base(tmp_path_factory):
    """План по умолчанию (с линиями по улицам от подмены OSRM) — для сравнения и для replan."""
    root = tmp_path_factory.mktemp('base')
    with pytest.MonkeyPatch.context() as mp:
        offline(mp, root)
        inputs = write_inputs(root)
        out = root / 'out'
        code = cli.main(['plan', *inputs[:1], '-b', inputs[1], '-o', str(out), '--time', T])
        assert code == 0
        yield root, inputs, out, json.load(open(out / 'plan.json', encoding='utf-8'))


@pytest.fixture
def env(tmp_path, monkeypatch, base):
    offline(monkeypatch, tmp_path)
    return base[1]


def test_default_plan(base):
    _, _, out, doc = base
    assert doc['plan']['check']['ok'] and doc['plan']['metrics']['served'] == len(ORDERS)
    m = doc['meta']
    assert m['name'] == 'Тест' and m['seed'] == 1 and m['lunch'] is True and m['equipment'] is True
    assert m['traffic'] == {'car': 1.3} and m['overhead'] == {'car': 5.0, 'pt': 3.0, 'bike': 3.0, 'foot': 3.0}
    for key in ('coverage', 'deficit', 'learned', 'early', 'exact_time', 'quality', 'ratings', 'curve', 'price'):
        assert key not in doc                                          # без флагов — ничего лишнего
    assert any(r['lunch'] for r in doc['plan']['routes'])              # обед по умолчанию есть
    assert any(o['equipment'] for o in doc['orders'])                  # дозаказу нужен роутер
    assert all(r['geometry'] for r in doc['plan']['routes'])           # линии от подмены OSRM
    assert (out / 'plan.xlsx').exists()


def test_seed_traffic_overhead_merge_with_defaults(env, tmp_path, capsys):
    """--traffic bike=1.1 дополняет пробки по умолчанию: машина остаётся 1,3; --overhead — так же."""
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--seed', '7', '--traffic', 'bike=1.1', '--overhead', 'car=2',
                               '--flat-traffic')
    assert code == 0, text
    m = doc['meta']
    assert m['seed'] == 7
    assert m['traffic'] == {'car': 1.3, 'bike': 1.1}
    assert m['overhead'] == {'car': 2.0, 'pt': 3.0, 'bike': 3.0, 'foot': 3.0}
    assert m['traffic_hours'] is False


def test_traffic_flag_rejects_unknown_mode(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(['plan', 'a.csv', '-b', 'b.csv', '--traffic', 'boat=2'])
    assert 'car=1.5' in capsys.readouterr().err


def test_coverage_and_deficit(env, tmp_path, capsys):
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--coverage', '--deficit')
    assert code == 0, text
    c = doc['coverage']
    assert c and len(c['grid']) > 0 and c['slots'][0] == 0 and len(c['minutes']) == len(c['slots']) == 24
    assert all(len(row) == len(c['grid']) for row in c['minutes'])
    assert {v for row in c['brigade'] for v in row if v is not None} <= {0, 2}   # аварии умеют только Б1 и Б3
    d = doc['deficit']
    assert sorted(d['districts']) == ['Р1', 'Р2'] and len(d['windows']) == 6


def test_coverage_without_osrm_logs_and_keeps_plan(env, tmp_path, capsys, monkeypatch):
    """OSRM отвечает только по точкам дня (их хватает для матриц), сетку покрытия посчитать нечем: план есть,
    coverage = None и понятная строка в выводе."""
    known = set()

    def table(self, profile, src, dst):
        if not known:
            known.update(src)
        if not set(dst) <= known:
            raise OsrmError('OSRM недоступен (тест)')
        return fake_table(self, profile, src, dst)
    monkeypatch.setattr(OsrmClient, 'table', table)
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--coverage')
    assert code == 0, text
    assert doc['coverage'] is None and 'Карта покрытия не построена' in text
    assert doc['plan']['metrics']['served'] == len(ORDERS)


def test_facts_learned(env, tmp_path, capsys):
    """История выездов «Тест»: «Нет линка» вдвое дольше прогноза, Б1 — ещё медленнее; plan --facts кладёт поправки в learned."""
    facts = tmp_path / 'выезды.csv'
    rows = [{'Дата': '01.09.2026', 'Участок': 'Тест', 'Бригада': 'Б1', 'Заявка': f'F{i}', 'Тип заявки BK': 'Локальная заявка',
             'Тип заявки HD': 'Нет линка', 'Адрес': f'{OFFICE[0]:.6f}, {OFFICE[1]:.6f}', 'Гигабит': 'Нет', 'План мин': 39,
             'Факт мин': 90} for i in range(30)]
    learning.write(str(facts), rows)
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--facts', str(facts))
    assert code == 0, text
    learned = doc['learned']
    assert learned['facts'] == 30 and learned['rows']
    assert all(abs(r['now'] - r['was']) >= 0.03 for r in learned['rows'])
    assert any(r['now'] > r['was'] for r in learned['rows'])
    assert 'Длительности уточнены по 30 фактическим выездам' in text


def write_reviews(path, best='Б3'):
    """Отзывы: у best пятёрки, у остальных — тройки; по всем видам работ."""
    rows = []
    n = 0
    for name, _, _ in BRIGADES:
        for skill in range(3):
            for age in range(20):
                n += 1
                rows.append((rating.Review(age=age, brigade=name, skill=skill, work=5 if name == best else 3, on_time=5), f'R{n}'))
    rating.write(str(path), rows)


def test_reviews_attach_ratings(env, tmp_path, capsys):
    reviews = tmp_path / 'отзывы.csv'
    write_reviews(reviews)
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--reviews', str(reviews))
    assert code == 0, text
    assert set(doc['ratings']) == {'Б1', 'Б2', 'Б3'}
    assert doc['ratings']['Б3']['by_kind']['Ремонт']['value'] > doc['ratings']['Б1']['by_kind']['Ремонт']['value']
    assert 'Авария' not in doc['ratings']['Б2']['by_kind']                 # рейтинг только по навыкам бригады
    assert doc['plan']['metrics']['rating'] is not None
    assert all(o['plan']['rating'] for o in doc['orders'] if o['plan'])
    assert 'quality' not in doc


def test_best_needs_reviews(env, tmp_path, capsys):
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--best')
    assert code == 1 and 'для --best нужен --reviews' in text and doc is None


def test_best_mode_quality(env, tmp_path, capsys):
    reviews = tmp_path / 'отзывы.csv'
    write_reviews(reviews)
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--reviews', str(reviews), '--best')
    assert code == 0, text
    q = doc['quality']
    assert q['pool'] == 3 and 1 <= q['k'] <= 3 and q['curve'][0]['k'] == q['k']
    assert q['curve'][-1]['k'] == 3 or q['k'] == 3                       # последняя точка — весь парк
    assert len(doc['brigades']) == q['k'] and doc['brigades'][0]['name'] == 'Б3'   # лучший по рейтингу — первым
    assert doc['ratings'] and doc['plan']['metrics']['rating'] is not None
    assert 'shortage' not in doc


def test_early_and_windows(env, tmp_path, capsys):
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--early', '--windows')
    assert code == 0, text
    e = doc['early']
    assert e['minutes'] == 60 and e['served'] == len(ORDERS) and e['brigades'] >= 1 and isinstance(e['useful'], bool)
    for c in e['calls']:                                               # звонить тем, кто начнёт раньше окна
        assert c['start'] < c['window'][0] and 1 <= c['earlier'] <= 60
    x = doc['exact_time']
    assert [p['share'] for p in x] == [0.0, 0.25, 0.5, 1.0]
    assert x[0]['narrowed'] == 0 and x[-1]['narrowed'] == len(ORDERS) - 1    # все, кроме аварии
    assert all(p['orders'] == len(ORDERS) for p in x)


def test_no_lunch(env, tmp_path, capsys):
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--no-lunch')
    assert code == 0, text
    assert doc['meta']['lunch'] is False
    assert all(r['lunch'] is None for r in doc['plan']['routes'])
    assert all(b['lunch'] is False for b in doc['brigades'])


def test_no_equipment(env, tmp_path, capsys):
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--no-equipment')
    assert code == 0, text
    assert doc['meta']['equipment'] is False
    assert all(o['equipment'] == {} for o in doc['orders'])


def test_curve_and_price(env, tmp_path, capsys):
    code, text, doc = run_plan(capsys, env, tmp_path / 'o', '--curve', '--price')
    assert code == 0, text
    used = doc['plan']['metrics']['brigades_used']
    limits = [p['limit'] for p in doc['curve']]
    assert limits == list(range(max(1, used - 3), min(3, used + 3) + 1))
    assert all(p['brigades'] <= p['limit'] for p in doc['curve'])
    assert doc['curve'][-1]['served'] == len(ORDERS)
    what = [p['what'] for p in doc['price']]
    assert what == ['все умеют всё', 'все на машинах', 'без требований к транспорту', 'без обеда', 'без учёта оборудования']
    assert all({'brigades', 'served', 'km', 'bound'} <= set(p) for p in doc['price'])


# ---- replan: разбор флагов события
def replan(capsys, base, out, *flags):
    code = cli.main(['replan', str(base[2]), '--at', '13:00', '-o', str(out), '--no-geometry', *flags])
    text = capsys.readouterr()
    return code, text.out + text.err, json.load(open(out / 'plan.json', encoding='utf-8'))


def test_replan_reschedule(env, base, tmp_path, capsys):
    code, text, doc = replan(capsys, base, tmp_path / 'r', '--reschedule', '106', '18:00-20:00')
    assert code == 0, text
    e = doc['event']
    k = next(o['k'] for o in doc['orders'] if o['id'] == '106')
    assert e['kind'] == 'window' and e['order'] == k and e['strategy'] == 'insert'
    assert e['was'] == [360, 480] and e['now'] == [480, 600]
    assert os.path.abspath(e['previous']) == os.path.abspath(base[2]) and not os.path.isabs(e['previous'])   # без личных путей
    assert doc['plan']['check']['ok']


@pytest.mark.parametrize('to', ['foot', 'pt'])
def test_replan_breakdown_to(env, base, tmp_path, capsys, to):
    code, text, doc = replan(capsys, base, tmp_path / 'r', '--breakdown', 'Б1', '--to', to)
    assert code == 0, text
    e = doc['event']
    assert e['kind'] == 'breakdown' and e['name'] == 'Б1' and e['brigade'] == 0
    assert e['was'] == 'car' and e['now'] == to


def test_replan_urgent_need_id_district(env, base, tmp_path, capsys):
    code, text, doc = replan(capsys, base, tmp_path / 'r', '--urgent', f'{OFFICE[0] + 0.003:.6f}, {OFFICE[1]:.6f}',
                             '--type', 'ремонт', '--window', '15:00-17:00', '--need', 'авто', '--id', 'U1',
                             '--district', 'Центр')
    assert code == 0, text
    e = doc['event']
    new = doc['orders'][-1]
    assert e['kind'] == 'urgent' and e['order'] == new['k'] == len(ORDERS) and e['type'] == 'Ремонт'
    assert new['id'] == 'U1' and new['district'] == 'Центр' and new['need'] == ['car'] and new['new'] is True
    assert new['window'] == [300, 420]
    if new['plan']:                                                    # «только авто» — в машину
        assert doc['brigades'][new['plan']['brigade']]['mode'] == 'car'


def test_tries_and_previous_answer(env, base, tmp_path, capsys):
    """--tries 2 пишет meta.tries; --answer с --tries 0 воспроизводит план прошлого прогона без запуска решателя."""
    _, _, out0, doc0 = base
    code, _, doc = run_plan(capsys, env, tmp_path / 'a', '--tries', '2')
    assert code == 0 and doc['meta']['tries'] == 2 and doc['plan']['check']['ok']
    code, _, doc = run_plan(capsys, env, tmp_path / 'b', '--tries', '0', '--answer', str(out0 / 'answer.json'))
    assert code == 0 and doc['plan']['check']['ok']
    route_ids = lambda d: [[d['orders'][s['k']]['id'] for s in r['stops']] for r in d['plan']['routes']]
    assert route_ids(doc) == route_ids(doc0)
