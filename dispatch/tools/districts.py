"""Новые синтетические участки Москвы по образцу данных организаторов.

Адреса — настоящие жилые дома из OpenStreetMap (osrm/msk.osm.pbf → data/osm/, см. prepare_osm), районы — их
границы в OSM, поэтому координаты точные и геокодинг не нужен (они сразу пишутся в кэш). Распределения взяты из
трёх участков организаторов (Восток, Юго-восток, Югоцентр):
- заявок 60–75 (у организаторов 56–83), окна по 2 часа с 10:00 до 22:00 с теми же долями, у аварии с вероятностью
  0,3 окно на весь день (0:01–23:59), как на Юго-востоке;
- типы BK и подтипы HD — общие частоты трёх участков (подключение 46%, ремонт 38%, авария 10%, дозаказ 6%);
- заявки по районам — неравномерно, как у организаторов (от 1–2 до 12 на район, самый крупный — не больше 20%);
- бригад ≈ заявки / 5,6, но 11–12, как у организаторов, наборы навыков — частоты составов контрольного дня, транспорт — mix2.
Результат: data/generated/<Участок> Синтетические данные.csv (CP1251, как у организаторов) и data/demo/<Участок>
бригады.xlsx, заявки.xlsx (с колонкой «Транспорт» по правилу tools.demo_data).
"""
import json
import os
import random
import re
import subprocess

from ..config import DATA_DIR, ROOT
from ..geo.geocoder import Geocoder
from .demo_data import DEMO_DIR, mode_of, write_brigades, write_orders

OSM = os.path.join(DATA_DIR, 'osm')
DISTRICTS = {
    'Север': ['район Аэропорт', 'район Беговой', 'район Сокол', 'Хорошёвский район', 'Войковский район', 'район Коптево',
              'Головинский район', 'Савёловский район', 'Тимирязевский район', 'район Левобережный'],
    'Запад': ['район Дорогомилово', 'район Фили-Давыдково', 'район Раменки', 'Можайский район', 'район Очаково-Матвеевское',
              'район Филёвский Парк', 'район Крылатское', 'район Проспект Вернадского', 'Тропарёво-Никулино'],
    'Северо-восток': ['Алексеевский район', 'Останкинский район', 'район Марьина Роща', 'Бутырский район', 'район Отрадное',
                      'район Бибирево', 'район Свиблово', 'Бабушкинский район', 'Лосиноостровский район', 'Ярославский район',
                      'район Ростокино'],
}
SLOTS = [((0, 120), 42), ((120, 240), 43), ((240, 360), 28), ((360, 480), 28), ((480, 600), 31), ((600, 720), 22)]
BK = {'Подключение': 95, 'Локальная заявка': 77, 'Глобальная проблема': 21, 'Дозаказ': 12}
HD = {
    'Подключение': {'Конвергенция абонента': 75, 'Заказ подключения/Дозаказ оборудования': 11, 'Заявка на подключение': 9},
    'Локальная заявка': {'Нет линка': 31, 'IP-адрес 169...': 6, 'Работа с кабелем': 7, 'Разрывы': 5, 'Переключение на Гбит/с': 7,
                         'Низкая скорость': 4, 'Рост ошибок на порту': 5, 'Роутер. Замена техническим специалистом': 4,
                         'TVE/ENT. Замена приставки техником': 3, 'TVE/ENT. Другие ошибки': 2, 'ТВ. Замена приставки техником': 2,
                         'Мониторинг': 1},
    'Глобальная проблема': {'Авария': 15, 'Информация': 6},
    'Дозаказ': {'Дозаказ оборудования': 7, 'Заказ подключения/Дозаказ оборудования': 3, 'Конвергенция абонента': 2},
}
ROSTER = {(0, 1): 21, (0, 1, 2): 7, (1,): 4, (1, 2): 1, (0, 2): 1, (0,): 1}   # наборы навыков контрольного дня
KIND = {'apartments': 3, 'residential': 2, 'yes': 1, 'house': 0.3, 'dormitory': 1}
ABBR = [('улица', 'ул.'), ('проспект', 'пр-кт.'), ('бульвар', 'б-р.'), ('проезд', 'проезд.'), ('шоссе', 'ш.'),
        ('переулок', 'пер.'), ('набережная', 'наб.'), ('площадь', 'пл.'), ('тупик', 'туп.'), ('аллея', 'аллея.')]


def prepare_osm():
    """Выгрузки из osrm/msk.osm.pbf: границы районов (admin_level=8) и объекты с номером дома."""
    os.makedirs(OSM, exist_ok=True)
    src = os.path.join(ROOT, 'osrm', 'msk.osm.pbf')

    def osmium(*args):
        subprocess.run(['nice', '-n', '19', 'osmium', *args, '--overwrite'], check=True, capture_output=True)

    if not os.path.exists(os.path.join(OSM, 'admin8.geojson')):
        osmium('tags-filter', src, 'r/admin_level=8', '-o', os.path.join(OSM, 'admin8.osm.pbf'))
        osmium('export', os.path.join(OSM, 'admin8.osm.pbf'), '-o', os.path.join(OSM, 'admin8.geojson'), '-f', 'geojson',
               '--geometry-types=polygon')
    if not os.path.exists(os.path.join(OSM, 'addr.geojsonseq')):
        osmium('tags-filter', src, 'nw/addr:housenumber', '-o', os.path.join(OSM, 'addr.osm.pbf'))
        osmium('export', os.path.join(OSM, 'addr.osm.pbf'), '-o', os.path.join(OSM, 'addr.geojsonseq'), '-f', 'geojsonseq')


def street(name):
    """«Дмитровское шоссе» → «ш.Дмитровское», как в адресах организаторов."""
    low = name.lower()
    for word, short in ABBR:
        if re.search(rf'(^|\s){word}($|\s)', low):
            return short + re.sub(rf'(^|\s){word}($|\s)', ' ', name, flags=re.I).strip()
    return f'ул.{name}'


def house(number):
    """«16к1» → «16 к 1»."""
    number = number.replace(' ', '')
    m = re.fullmatch(r'(\d+[А-Яа-яA-Za-z]?(?:/\d+)?)к(\d+)(.*)', number)
    return f'{m[1]} к {m[2]}{m[3]}' if m else number


def district_label(osm_name):
    return re.sub(r'\s*район\s*', ' ', osm_name).strip().replace('ё', 'е')


def _centroid(geometry):
    kind, coords = geometry['type'], geometry['coordinates']
    ring = coords[0][0] if kind == 'MultiPolygon' else coords[0] if kind == 'Polygon' else [coords] if kind == 'Point' else None
    if not ring:
        return None
    return sum(c[1] for c in ring) / len(ring), sum(c[0] for c in ring) / len(ring)


def buildings(names):
    """{район: [(lat, lon, адрес, вид здания)]} для районов names."""
    from shapely.geometry import Point, shape
    from shapely.prepared import prep
    polygons = {}
    for f in json.load(open(os.path.join(OSM, 'admin8.geojson')))['features']:
        if f['properties'].get('name') in names:
            polygons[f['properties']['name']] = shape(f['geometry'])
    missing = set(names) - set(polygons)
    if missing:
        raise ValueError(f'нет границ районов в OSM: {missing}')
    prepared = {n: prep(p) for n, p in polygons.items()}
    bounds = [p.bounds for p in polygons.values()]
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    out = {n: [] for n in names}
    for line in open(os.path.join(OSM, 'addr.geojsonseq')):
        f = json.loads(line.lstrip('\x1e'))
        tags = f['properties']
        if 'addr:street' not in tags or tags.get('building') not in KIND:
            continue
        c = _centroid(f['geometry'])
        if not c or not (x0 <= c[1] <= x1 and y0 <= c[0] <= y1):
            continue
        point = Point(c[1], c[0])
        name = next((n for n, pp in prepared.items() if pp.contains(point)), None)
        if name:
            address = f"Город Москва, {street(tags['addr:street'])}, д. {house(tags['addr:housenumber'])}"
            out[name].append((round(c[0], 7), round(c[1], 7), address, tags['building']))
    return out


def pick(rnd, weights):
    return rnd.choices(list(weights), list(weights.values()))[0]


def orders_per_district(rnd, names, n):
    """Неравномерно, но самый крупный район — не больше 20% заявок (как у организаторов)."""
    share = sorted((rnd.gammavariate(1.6, 1) for _ in names), reverse=True)
    for _ in range(20):
        total = sum(share)
        share = [min(x, 0.2 * total) for x in share]
    return {d: max(1, round(n * x / sum(share))) for d, x in zip(names, share)}


def generate(region, seed):
    rnd = random.Random(f'{region}|{seed}')
    names = DISTRICTS[region]
    homes = buildings(names)
    per = orders_per_district(rnd, names, rnd.randint(60, 75))
    taken, ids, rows = set(), set(), []
    for d in names:
        candidates = sorted(homes[d], key=lambda b: rnd.random() / KIND[b[3]])
        for b in candidates[:per[d]]:
            bk = pick(rnd, BK)
            hd = pick(rnd, HD[bk])
            if bk == 'Глобальная проблема' and rnd.random() < 0.3:
                start, end = '0:01', '23:59'
            else:
                slot = pick(rnd, dict(SLOTS))
                start, end = f'{10 + slot[0] // 60}:00', f'{10 + slot[1] // 60}:00'
            oid = rnd.randint(10000, 99999)
            while oid in ids:
                oid = rnd.randint(10000, 99999)
            ids.add(oid)
            taken.add(b)
            rows.append([str(oid), bk, hd, f'17.08.2026 {start}', f'17.08.2026 {end}', district_label(d), b[2],
                         'Да' if rnd.random() < 0.03 else 'Нет', b])
    rnd.shuffle(rows)
    order = ['Подключение', 'Дозаказ', 'Локальная заявка', 'Глобальная проблема']   # как у организаторов: группами по типу
    rows.sort(key=lambda r: order.index(r[1]))
    central = max(names, key=lambda d: per[d])
    office = next((b for b in homes[central] if b[3] == 'yes' and b not in taken), homes[central][0])
    return rows, office


def write(region, seed=1):
    prepare_osm()
    rows, office = generate(region, seed)
    gen_dir = os.path.join(DATA_DIR, 'generated')
    os.makedirs(gen_dir, exist_ok=True)
    synth = os.path.join(gen_dir, f'{region} Синтетические данные.csv')
    head = ['Заявка', 'Тип заявки BK', 'Тип заявки HD', 'Начало', 'Окончание', 'Район', 'Адрес', 'Гигабитное подключение']
    office_addr = office[2].replace('Город Москва, ', 'г. Москва, ')
    with open(synth, 'w', encoding='cp1251', newline='') as f:
        f.write('\r\n'.join(';'.join(r) for r in [head] + [r[:8] for r in rows] + [['Адрес офиса', office_addr]]) + '\r\n')
    geocoder = Geocoder()                              # координаты из OSM сразу в кэш геокодинга
    for r in rows:
        geocoder.remember(r[6], r[8][0], r[8][1], 'osm')
    geocoder.remember(office_addr, office[0], office[1], 'osm')
    geocoder.save()

    rnd = random.Random(f'{region}|{seed}|brigades')
    nb = min(12, max(11, round(len(rows) / 5.6)))
    brigades = [[f'Бригада {i}', mode_of(f'{region} Бригада {i}', False, 'mix2'), list(pick(rnd, ROSTER)), '']
                for i in range(1, nb + 1)]
    if sum(2 in b[2] for b in brigades) < 2:
        brigades[0][2], brigades[1][2] = [0, 1, 2], [1, 2]
    if not any(2 in b[2] and b[1] == 'car' for b in brigades):   # для аварий «только машина» нужна машина с навыком
        next(b for b in brigades if 2 in b[2])[1] = 'car'
    os.makedirs(DEMO_DIR, exist_ok=True)
    pb = os.path.join(DEMO_DIR, f'{region} бригады.xlsx')
    write_brigades(pb, [tuple(b) for b in brigades])
    po = os.path.join(DEMO_DIR, f'{region} заявки.xlsx')
    total, need = write_orders(synth, po)
    return dict(synth=synth, brigades=pb, orders=po, n=total, need=need, nb=nb, office=office_addr,
                per_district={district_label(d): sum(1 for r in rows if r[5] == district_label(d)) for d in DISTRICTS[region]})
