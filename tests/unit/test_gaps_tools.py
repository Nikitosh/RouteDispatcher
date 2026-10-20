"""Инструменты синтетики и калибровки (dispatch/tools): calibrate, reviews, homes, districts, demo_data, facts.

Без сети и без настоящих выгрузок OSM: districts и homes читают крошечные admin8.geojson / addr.geojsonseq из временной
папки (десять квадратных районов «Севера» и по двадцать домов в каждом), все пути записи (data/demo, data/generated,
cache/) подменены временными. tools/houses.py не проверяем: ему нужна выгрузка OSM (osmium по osrm/cfd.osm.pbf)."""
import csv
import json
import os
import shutil
import urllib.request

import openpyxl
import pytest

from dispatch import cli
from dispatch.config import CACHE_DIR, ROOT
from dispatch.files.readers import read_brigades, read_orders
from dispatch.files.tables import read_table
from dispatch.geo import geocoder as geocoder_mod
from dispatch.geo import geometry as geometry_mod
from dispatch.geo import matrices as matrices_mod
from dispatch.geo.geocoder import Geocoder
from dispatch.geo.houses import HouseIndex
from dispatch.geo.nearby import dist_km
from dispatch.geo.osrm import OsrmClient
from dispatch.model import learning, rating
from dispatch.planning.whatif import early_start
from dispatch.tools import calibrate, demo_data, districts, facts, homes, reviews
from tests.helpers import brigade, line_problem, order

WATCHED = [os.path.join(ROOT, d) for d in ('cache', os.path.join('data', 'demo'), os.path.join('data', 'generated'), 'out')]
REGION = 'Север'
LAT0, LON0, DLAT, DLON = 55.80, 37.50, 0.01, 0.02          # районы — клетки 2 × 5


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
    """Ни один тест модуля не пишет в cache/, data/demo/, data/generated/ и out/ проекта."""
    before = _snapshot()
    yield
    assert _snapshot() == before


def fake_table(self, profile, src, dst):
    pace = {'car': 2, 'bike': 4, 'foot': 12}[profile]
    return [[(dist_km(a, b) * pace, dist_km(a, b)) for b in dst] for a in src]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Кэши, data/demo, data/generated и data/osm — во временной папке; OSRM — подмена; сеть закрыта."""
    cache, demo, osm = tmp_path / 'cache', tmp_path / 'demo', tmp_path / 'osm'
    for d in (cache, demo, osm):
        d.mkdir()
    for mod in (geocoder_mod, geometry_mod, matrices_mod):
        monkeypatch.setattr(mod, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(HouseIndex.__init__, '__defaults__', (str(cache / 'houses.json'),))
    for mod in (demo_data, districts, homes, reviews, facts):
        monkeypatch.setattr(mod, 'DEMO_DIR', str(demo))
    monkeypatch.setattr(districts, 'DATA_DIR', str(tmp_path))         # data/generated → tmp/generated
    monkeypatch.setattr(districts, 'OSM', str(osm))
    monkeypatch.setattr(homes, 'OSM', str(osm))
    monkeypatch.setattr(OsrmClient, 'table', fake_table)

    def no_network(*a, **k):
        raise AssertionError('тест обратился к сети')
    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    monkeypatch.setattr(Geocoder, '_nominatim', staticmethod(no_network))
    write_osm(osm)
    return tmp_path


def cell(i):
    """Клетка района i: (lat0, lon0, lat1, lon1)."""
    r, c = divmod(i, 5)
    return LAT0 + r * DLAT, LON0 + c * DLON, LAT0 + (r + 1) * DLAT, LON0 + (c + 1) * DLON


def write_osm(osm):
    """Границы районов «Севера» и по 20 жилых домов (и одно офисное здание) в каждом."""
    features = []
    with open(osm / 'addr.geojsonseq', 'w', encoding='utf-8') as f:
        for i, name in enumerate(districts.DISTRICTS[REGION]):
            a, b, c, d = cell(i)
            features.append(dict(type='Feature', properties=dict(name=name, admin_level='8'),
                                 geometry=dict(type='Polygon', coordinates=[[[b, a], [d, a], [d, c], [b, c], [b, a]]])))
            for j in range(21):
                lat, lon = a + DLAT * (0.1 + 0.8 * (j % 5) / 4), b + DLON * (0.1 + 0.8 * (j // 5) / 4)
                tags = {'building': 'yes' if j == 20 else 'apartments', 'addr:street': f'Тестовая {i} улица',
                        'addr:housenumber': f'{j + 1}к1'}
                f.write('\x1e' + json.dumps(dict(type='Feature', properties=tags,
                                                 geometry=dict(type='Point', coordinates=[lon, lat])), ensure_ascii=False) + '\n')
        # дом вне районов и нежилой дом — не берутся
        f.write(json.dumps(dict(type='Feature', properties={'building': 'apartments', 'addr:street': 'Дальняя улица',
                                                            'addr:housenumber': '1'},
                                geometry=dict(type='Point', coordinates=[30.0, 59.9])), ensure_ascii=False) + '\n')
        f.write(json.dumps(dict(type='Feature', properties={'building': 'garage', 'addr:street': 'Тестовая 0 улица',
                                                            'addr:housenumber': '99'},
                                geometry=dict(type='Point', coordinates=[LON0 + 0.001, LAT0 + 0.001])), ensure_ascii=False) + '\n')
    json.dump(dict(type='FeatureCollection', features=features), open(osm / 'admin8.geojson', 'w', encoding='utf-8'),
              ensure_ascii=False)


def inside_region(lat, lon):
    return LAT0 <= lat <= LAT0 + 2 * DLAT and LON0 <= lon <= LON0 + 5 * DLON


# ---- calibrate
def write_pairs(path, pairs):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['Откуда', 'Куда', 'Км', 'OSRM, мин', 'Яндекс, мин', 'Ссылка'])
        for x, y in pairs:
            w.writerow(['55.7,37.6', '55.71,37.61', '3.00', str(x).replace('.', ','), y, ''])


def test_calibrate_fit_line_and_ratio(tmp_path):
    """Яндекс = 1,5 · OSRM + 2: прямая восстанавливается точно, коэффициент — медиана отношений; пустые строки не в счёт."""
    xs = [4.0, 6.0, 10.0, 12.5, 20.0, 30.0]
    path = tmp_path / 'pairs.csv'
    write_pairs(path, [(x, f'{1.5 * x + 2:.2f}'.replace('.', ',')) for x in xs] + [(8.0, ''), (9.0, '  ')])
    r = calibrate.fit(str(path))
    assert r['n'] == 6
    assert r['slope'] == pytest.approx(1.5) and r['intercept'] == pytest.approx(2.0)
    ratios = sorted((1.5 * x + 2) / x for x in xs)
    assert r['ratio'] == pytest.approx((ratios[2] + ratios[3]) / 2)


def test_calibrate_fit_needs_five_rows(tmp_path):
    path = tmp_path / 'pairs.csv'
    write_pairs(path, [(x, str(x * 1.3)) for x in (5, 6, 7, 8)] + [(9, '')])
    with pytest.raises(ValueError, match='меньше 5'):
        calibrate.fit(str(path))


def test_calibrate_fit_cli(tmp_path, capsys):
    path = tmp_path / 'pairs.csv'
    write_pairs(path, [(x, str(2 * x)) for x in (5, 6, 7, 8, 9)])
    assert cli.main(['calibrate', 'fit', str(path)]) == 0
    out = capsys.readouterr().out
    assert 'Пар: 5' in out and 'Коэффициент (медиана Яндекс/OSRM): 2.00' in out and '--traffic car=2.00' in out


def test_calibrate_sample_offline(sandbox):
    """sample: пары точек 1–15 км, время OSRM (подмена, без пробок и подхода), колонка «Яндекс, мин» пустая."""
    orders = sandbox / 'Тест заявки.csv'
    lines = ['Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес']
    for i in range(6):
        lines.append(f'{i};Локальная заявка;Нет линка;17.08.2026 10:00;17.08.2026 12:00;Р;{55.70 + 0.02 * i:.4f}, 37.6000')
    lines.append('Адрес офиса;55.7000, 37.6000')
    orders.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    path, n = calibrate.sample(str(orders), str(sandbox / 'pairs.csv'), n=10, log=lambda *_: None)
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig'), delimiter=';'))
    assert n == len(rows) == 10
    for r in rows:
        km, minutes = float(r['Км']), float(r['OSRM, мин'])
        assert 1.0 <= km <= 15 and minutes == pytest.approx(2 * km, abs=0.1) and r['Яндекс, мин'] == ''
        assert r['Ссылка'].startswith('https://yandex.ru/maps/?rtext=')


# ---- reviews: вспомогательные функции
def test_expected_rating_is_monotone_and_bounded():
    xs = [reviews.expected(q) for q in (1.0, 2.5, 3.5, 4.45, 5.0)]
    assert xs == sorted(xs) and all(1 <= x <= 5 for x in xs)
    assert reviews.expected(3.0) == pytest.approx(3.0, abs=1e-6)       # симметрично вокруг середины шкалы
    assert reviews.expected(5.0) < 5 and reviews.expected(1.0) > 1      # обрезка шкалы тянет к середине
    assert reviews.expected(4.0, sd=0.01) == pytest.approx(4.0)


def test_spearman():
    a = dict(x=1, y=2, z=3, w=4)
    assert reviews.spearman(a, dict(x=10, y=20, z=30, w=40)) == 1
    assert reviews.spearman(a, dict(x=4, y=3, z=2, w=1)) == -1
    assert reviews.spearman(a, dict(x=1, y=3, z=2, w=4)) == pytest.approx(0.8)


# ---- districts → homes → reviews → facts: цепочка синтетики на крошечном OSM
def test_gen_district_deterministic(sandbox, tmp_path_factory, monkeypatch):
    x = districts.write(REGION, seed=3)
    names = [districts.district_label(d) for d in districts.DISTRICTS[REGION]]
    assert 60 <= x['n'] <= 75 and x['n'] == sum(x['per_district'].values())
    assert list(x['per_district']) == names and all(c >= 1 for c in x['per_district'].values())
    assert max(x['per_district'].values()) <= 0.2 * x['n'] + 1                # самый крупный район — не больше 20%
    assert x['synth'] == str(sandbox / 'generated' / f'{REGION} Синтетические данные.csv')
    f = read_orders(x['orders'])
    assert len(f.orders) == x['n'] and f.office == x['office']
    assert sum(1 for o in f.orders if o.need) == x['need'] > 0                  # «только машина» по правилу demo_data
    assert all(o.address.startswith('Город Москва, ул.Тестовая') and o.address.split(', д. ')[1].endswith('к 1')
               for o in f.orders)
    coords = Geocoder(online=False).locate([o.address for o in f.orders] + [f.office], log=lambda *_: None)
    assert all(inside_region(*c) for c in coords.values())                   # координаты OSM — сразу в кэше геокодинга
    b = read_brigades(x['brigades'])
    assert len(b) == x['nb'] and 11 <= x['nb'] <= 12
    assert sum(2 in br.skills for br in b) >= 2 and any(2 in br.skills and br.mode == 'car' for br in b)
    first = open(x['synth'], encoding='cp1251').read()
    # тот же seed в другой папке — тот же участок; другой seed — другой
    other = tmp_path_factory.mktemp('again')
    monkeypatch.setattr(districts, 'DATA_DIR', str(other))
    assert districts.write(REGION, seed=3)['per_district'] == x['per_district']
    assert open(other / 'generated' / f'{REGION} Синтетические данные.csv', encoding='cp1251').read() == first
    assert open(districts.write(REGION, seed=4)['synth'], encoding='cp1251').read() != first


def test_gen_district_cli(sandbox, capsys):
    assert cli.main(['gen-district', REGION, '--seed', '2']) == 0
    out = capsys.readouterr().out
    assert out.startswith(f'{REGION}: ') and 'бригад' in out and str(sandbox / 'demo') in out


def test_residential_filters_kind_and_radius(sandbox):
    a, b, _, _ = cell(0)
    got = homes.residential([(a + DLAT / 2, b + DLON / 2)])
    assert got and all(homes._dist(h, (a + DLAT / 2, b + DLON / 2)) < homes.RADIUS for h in got)
    assert not any(h[2].endswith('д. 99') for h in got)                       # гараж — не жилой
    assert all(h[2].startswith('ул.Тестовая') for h in got)
    assert not any(h[2].endswith('д. 21 к 1') for h in got)                   # «building=yes» — не дом для homes


def test_homes_reviews_facts_chain(sandbox):
    districts.write(REGION, seed=1)
    # дома бригад: в колонке «Старт», внутри участка, рядом с заявкой, повторный запуск — те же
    path, rows = homes.assign(REGION, log=lambda *_: None)
    assert path == str(sandbox / 'demo' / f'{REGION} бригады.xlsx')
    starts = [r[3] for r in rows]
    assert all(s.startswith('Город Москва, ул.Тестовая') for s in starts)
    b = read_brigades(path)
    assert [x.start_address for x in b] == starts
    coords = Geocoder(online=False).locate(starts, log=lambda *_: None)
    assert all(inside_region(*c) for c in coords.values())
    assert [r[3] for r in homes.assign(REGION, log=lambda *_: None)[1]] == starts

    # отзывы: парк до 30, байесовский рейтинг ближе к истине, чем простое среднее
    r = reviews.write(REGION)
    assert r['n'] == reviews.POOL and os.path.exists(r['pool']) and os.path.exists(r['reviews'])
    assert r['pool'].startswith(str(sandbox)) and r['reviews'].startswith(str(sandbox))
    assert r['mae_bayes'] < r['mae_naive'] and r['rank_bayes'] > r['rank_naive'] - 0.05
    pool = read_brigades(r['pool'])
    assert len(pool) == reviews.POOL and [p.name for p in pool[:len(b)]] == [x.name for x in b]
    assert all(p.start_address for p in pool)
    back = rating.read(r['reviews'])
    assert len(back) == r['visits'] and sum(1 for x in back if x.work is not None) == r['rated']
    assert reviews.write(REGION) == r                                           # детерминированно

    # история выездов: обучение находит «истинные» множители, ошибка на отложенных днях падает
    logs = []
    f = facts.build(log=logs.append)
    assert f['path'] == str(sandbox / 'demo' / 'выезды.csv')
    got, raw = learning.read(f['path'])
    assert len(got) == facts.DAYS * facts.PER_DAY and {x.region for x in got} == {REGION}
    assert f['after'] < f['before']
    conv = next(x for x in f['report'] if x.get('what', '').startswith('Подключение') and 'Конверген' in x['what'])
    assert conv['now'] > conv['was']                                          # истина 1,25 против наших 1,10
    slow = next(x for x in f['report'] if x.get('brigade') == 'Бригада 5' and x.get('region') == REGION)
    assert slow['now'] > 1.05                                                   # истинный темп ×1,2
    assert any('Ошибка прогноза на последних' in line for line in logs)


# ---- demo_data: по файлам организаторов
@pytest.fixture
def geocode_copy(sandbox):
    """Координаты адресов организаторов — из копии кэша проекта (новых адресов нет, сеть закрыта)."""
    src = os.path.join(CACHE_DIR, 'geocode.json')
    if not os.path.exists(src):
        pytest.skip('нет cache/geocode.json')
    shutil.copy(src, sandbox / 'cache' / 'geocode.json')
    return sandbox


@pytest.mark.parametrize('variant', ['car', 'mix2'])
def test_demo_data_write_all(geocode_copy, variant):
    out = geocode_copy / 'demo_out'
    done = demo_data.write_all(variant, out_dir=str(out))
    assert [d[0] for d in done] == list(demo_data.REGIONS)
    for region, b, o, nb, total, need in done:
        assert b == str(out / f'{region} бригады.xlsx') and o == str(out / f'{region} заявки.xlsx')
        brigades = read_brigades(b)
        assert len(brigades) == nb > 0
        if variant == 'car':
            assert all(x.mode == 'car' for x in brigades)
        f = read_orders(o)
        assert len(f.orders) == total and sum(1 for x in f.orders if x.need) == need > 0
        assert all((x.need == frozenset({'car'})) == demo_data.needs_car(x.bk, x.hd) for x in f.orders)
        assert openpyxl.load_workbook(o).active[1][-1].value == 'Транспорт'
        synth = read_table(demo_data.organizers_file(region, 'Синтетические'))
        assert total == sum(1 for r in synth[1:] if not r[0].lower().startswith('адрес'))
        assert sum(1 for x in f.orders if x.apt) > total // 2                   # квартиры — из контрольного дня
    again = demo_data.write_all(variant, out_dir=str(geocode_copy / 'again'))
    assert [d[3:] for d in again] == [d[3:] for d in done]
    assert [read_table(d[1]) for d in again] == [read_table(d[1]) for d in done]


# ---- найдено тестами
def test_early_call_less_than_minute():
    p = line_problem([order('B', 'Ремонт', (120, 300), x=119.6)], [brigade('Б1')])

    class Fixed:
        def plan_only(self, problem, seconds, seed, workdir):
            return [[0]]
    res = early_start(Fixed(), p, 1, 1, '/tmp')
    assert all(c['earlier'] >= 1 for c in res['calls']), res['calls']
