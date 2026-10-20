from dispatch.config import GATE_CAR_MIN, PASS_MIN, SERVICE
from dispatch.files import document
from dispatch.model import complexity
from dispatch.model.complexity import estimate, place, work_factor
from dispatch.model.domain import apartment, house_address
from tests.helpers import brigade, line_problem, order

HOUSE = dict(levels=9, flats=144, lifts=0, year=1972, n_entrances=4, office=False, gated=False, floors=None, starts=None,
             entrances=[dict(ref='1', lat=55.1, lon=37.1, flats=[[1, 36]]), dict(ref='2', lat=55.2, lon=37.2, flats=[[37, 72]])])


def connection(apt=None, hd='Заявка на подключение', gigabit=False):
    o = order(1, 'Подключение')
    o.hd, o.apt, o.gigabit = hd, apt, gigabit
    return o


def test_apartment_is_cut_from_address():
    a = 'Город Москва, ул.Восточная, д. 2 к 2, кв. 92'
    assert (apartment(a), house_address(a)) == (92, 'Город Москва, ул.Восточная, д. 2 к 2')
    assert apartment('Домодедово, проезд.Советский 1-й, д. 1А') is None


def test_work_factor_takes_longest_prefix():
    assert work_factor('Локальная заявка', 'TVE/ENT. Замена приставки техником') == 0.80
    assert work_factor('Локальная заявка', 'TVE/ENT. Другие ошибки') == 0.90
    assert work_factor('Подключение', 'Заявка на подключение') == 1.0
    assert work_factor('Глобальная проблема', 'Авария') == 1.0


def test_entrance_and_floor_from_osm_flat_ranges():
    p = place(HOUSE, 50)
    assert (p.entrance, p.floor, p.lift) == (2, 4, False)       # 36 квартир на 9 этажей — по 4 на этаже
    assert (p.lat, p.lon) == (55.2, 37.2)
    assert p.src == dict(entrance='OSM', floor='оценка', lift='Реформа ЖКХ')


def test_cadastre_floor_and_entrances_win_over_estimate():
    h = dict(HOUSE, entrances=[], floors={'50': 7}, starts=[1, 40, 80])
    p = place(h, 50)
    assert (p.entrance, p.floor) == (2, 7) and p.src['floor'] == 'кадастр' and p.src['entrance'] == 'кадастр'


def test_lift_guessed_by_levels_when_unknown():
    assert place(dict(HOUSE, lifts=None, levels=12), 5).lift is True
    assert place(dict(HOUSE, lifts=None, levels=5), 5).lift is False


def test_missing_apartment_is_assumed_and_stable():
    a, b = place(HOUSE, None, '305927'), place(HOUSE, None, '305927')
    assert a.apt_assumed and a.apt == b.apt and 1 <= a.apt <= 144


def test_climb_counts_only_without_lift():
    svc, car, d = estimate(connection(apt=50), HOUSE)                # 4 этаж, 3 подъёма по 0,5 мин за этаж
    assert svc == round(SERVICE['Подключение'] + 0.5 * 3 * 3) and car == 0
    svc_lift, _, _ = estimate(connection(apt=50), dict(HOUSE, lifts=2))
    assert svc_lift == SERVICE['Подключение']
    assert d['place']['floor'] == 4 and d['base'] == SERVICE['Подключение']


def test_office_and_gate():
    svc, car, d = estimate(connection(apt=1), dict(HOUSE, office=True, gated=True))
    assert svc == SERVICE['Подключение'] + PASS_MIN and car == GATE_CAR_MIN
    assert d['access'] == ['пропуск', 'шлагбаум']


def test_old_house_subtype_and_gigabit():
    svc, _, d = estimate(connection(apt=1, hd='Конвергенция абонента', gigabit=True), dict(HOUSE, year=1955))
    assert svc == round(70 * 1.10 * 1.15 + 10)
    assert [x['what'] for x in d['parts']][1:] == ['Конвергенция абонента', 'старый дом, 1955 г.', 'гигабитное подключение']


def test_no_house_keeps_norm_times_subtype():
    o = order(1, 'Ремонт')
    o.hd = 'Работа с кабелем'
    assert estimate(o, None)[:2] == (48, 0.0)


def test_limits():
    o = order(1, 'Ремонт')
    o.hd = 'Мониторинг'
    assert estimate(o, None)[0] == round(30 * 0.6)


def test_car_access_only_for_car_trips():
    a, b = order(1, x=10), order(2, x=20)
    b.car_access = 3.0
    p = line_problem([a, b], [brigade('Б1')])
    assert p.time['car'][0][2] == 20 + 3 and p.time['car'][1][2] == 10 + 3
    assert p.time['car'][0][1] == 10 and p.time['foot'][0][2] == 60


def test_document_keeps_duration_for_replan():
    o = connection(apt=50)
    complexity.apply([o], {o.house: HOUSE})
    p = line_problem([o], [brigade('Б1')])
    back = document.orders_from(dict(orders=[document.order_dict(p, 0)]))[0]
    assert (back.service, back.apt, back.detail) == (o.service, 50, o.detail)
