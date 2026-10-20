"""Коэффициент сложности: длительность работы на месте по заявке и дому.

длительность = норматив × подтип работы × тип дома + гигабит + подъём по лестнице + пропуск,
в пределах SERVICE_LIMITS от норматива. Шлагбаум или закрытый двор мешает только машине, поэтому идёт не в работу,
а надбавкой к поездке на машине (Order.car_access).

Дом — запись cache/houses.json (tools/houses.py): этажность и лифты (Реформа ЖКХ, OSM), этаж каждой квартиры
(кадастровая оценка), подъезды с координатами и диапазонами квартир (OSM), год, офис, шлагбаум. Чего нет в данных,
оцениваем: подъезды по числу квартир, лифт с 6 этажей. Если в адресе нет квартиры, берём условную — по номеру заявки,
одинаковую при каждом запуске.
"""
import math
import zlib
from dataclasses import dataclass, field
from typing import Optional

from ..config import (CLIMB_MIN, CLIMB_TRIPS, GATE_CAR_MIN, GIGABIT_MIN, NEW_HOUSE, OLD_HOUSE, PASS_MIN, SERVICE,
                      SERVICE_LIMITS, WORK_FACTOR)


@dataclass
class Place:
    apt: Optional[int] = None
    apt_assumed: bool = False        # квартиры в адресе нет — условная
    entrance: Optional[int] = None
    floor: Optional[int] = None
    lift: Optional[bool] = None
    lat: Optional[float] = None      # точка подъезда
    lon: Optional[float] = None
    src: dict = field(default_factory=dict)   # откуда подъезд, этаж и лифт


def _ranges(entrances):
    """[(номер подъезда, первая, последняя квартира, точка)] по диапазонам квартир OSM."""
    out = []
    for i, e in enumerate(entrances):
        for lo, hi in e.get('flats') or ():
            out.append((_ref(e, i), lo, hi, e))
    return out


def _ref(e, i):
    return int(e['ref']) if str(e.get('ref') or '').isdigit() else i + 1


def place(house, apt, order_id='', assume=True):
    """Подъезд, этаж и лифт квартиры apt в доме house (None — дома нет в данных). assume — брать условную квартиру,
    если в адресе её нет (для аварии на узле связи квартира не нужна)."""
    p = Place(apt=apt)
    if not house:
        return p
    flats, levels = house.get('flats'), house.get('levels')
    if p.apt is None and flats and assume:
        p.apt, p.apt_assumed = 1 + zlib.crc32(str(order_id).encode()) % int(flats), True
    lifts = house.get('lifts')
    if lifts is not None:
        p.lift, p.src['lift'] = lifts > 0, 'Реформа ЖКХ'
    elif levels:
        p.lift, p.src['lift'] = levels >= 6, 'оценка по этажности'
    if p.apt is None:
        return p
    ents = house.get('entrances') or []
    hit = next(((ref, lo, hi, e) for ref, lo, hi, e in _ranges(ents) if lo <= p.apt <= hi), None)
    starts = house.get('starts')
    if hit:
        p.entrance, p.src['entrance'] = hit[0], 'OSM'
    elif starts:
        p.entrance, p.src['entrance'] = sum(1 for s in starts if s <= p.apt), 'кадастр'
    elif flats and levels:
        n = len(ents) or house.get('n_entrances') or max(1, round(flats / (levels * 4)))
        p.entrance, p.src['entrance'] = min(n, 1 + (p.apt - 1) // math.ceil(flats / n)), 'оценка'
    floor = (house.get('floors') or {}).get(str(p.apt))
    if floor is not None:
        p.floor, p.src['floor'] = floor, 'кадастр'
    elif levels and p.entrance:
        if hit:
            lo, hi = hit[1], hit[2]
        else:
            n = len(ents) or house.get('n_entrances') or max(1, round((flats or levels * 4) / (levels * 4)))
            per = math.ceil((flats or levels * 4 * n) / n)
            lo, hi = 1 + (p.entrance - 1) * per, p.entrance * per
        per_floor = max(1, round((hi - lo + 1) / levels))
        p.floor, p.src['floor'] = min(levels, 1 + (p.apt - lo) // per_floor), 'оценка'
    point = next((e for i, e in enumerate(ents) if _ref(e, i) == p.entrance), None) if p.entrance else None
    if point:
        p.lat, p.lon = point['lat'], point['lon']
    return p


def work_factor(bk, hd, table=None):
    """Множитель подтипа работы: самое длинное совпавшее начало подтипа HD; ключ (BK, '') — тип целиком."""
    table = WORK_FACTOR if table is None else table
    best = max(((len(pre), f) for (b, pre), f in table.items() if b == bk and (hd or '').startswith(pre)), default=None)
    return best[1] if best else 1.0


def estimate(order, house, factors=None):
    """Длительность заявки (мин), надбавка к поездке на машине (мин) и разбор для карточки. factors — множители,
    уточнённые по фактическим выездам (model/learning.py); по умолчанию — из config."""
    old_f, new_f = (factors['old'], factors['new']) if factors else (OLD_HOUSE[1], NEW_HOUSE[1])
    base = SERVICE[order.bk]
    parts = [dict(what=f'норматив: {order.type.lower()}', value=f'{base} мин')]
    f = work_factor(order.bk, order.hd, factors['work'] if factors else None)
    if f != 1.0:
        parts.append(dict(what=order.hd or order.type, value=f'×{f:.2f}'.replace('.', ',')))
    year = (house or {}).get('year')
    if year and year < OLD_HOUSE[0]:
        f *= old_f
        parts.append(dict(what=f'старый дом, {year} г.', value=f'×{old_f:.2f}'.replace('.', ',')))
    elif year and year >= NEW_HOUSE[0]:
        f *= new_f
        parts.append(dict(what=f'новый дом, {year} г.', value=f'×{new_f:.2f}'.replace('.', ',')))
    minutes = base * f
    if order.gigabit and order.bk == 'Подключение':
        minutes += GIGABIT_MIN
        parts.append(dict(what='гигабитное подключение', value=f'+{GIGABIT_MIN} мин'))
    trips = CLIMB_TRIPS.get(order.bk, 0)
    p = place(house, order.apt, order.id, assume=trips > 0)
    if p.floor and p.floor > 1 and p.lift is False and trips:
        climb = CLIMB_MIN * (p.floor - 1) * trips
        minutes += climb
        parts.append(dict(what=f'{p.floor} этаж без лифта, {trips} подъёма', value=f'+{climb:g} мин'.replace('.', ',')))
    if house and house.get('office'):
        minutes += PASS_MIN
        parts.append(dict(what='офисное здание: пропуск', value=f'+{PASS_MIN} мин'))
    lo, hi = SERVICE_LIMITS
    service = int(round(min(max(minutes, lo * base), hi * base)))
    car = GATE_CAR_MIN if house and house.get('gated') else 0.0
    if car:
        parts.append(dict(what='шлагбаум или закрытый двор', value=f'+{GATE_CAR_MIN} мин, машина'))
    house_info = {k: house.get(k) for k in ('levels', 'lifts', 'flats', 'year', 'material', 'series')} if house else None
    detail = dict(base=base, parts=parts, place=p.__dict__, house=house_info,
                  access=[x for x, on in (('пропуск', house and house.get('office')), ('шлагбаум', house and house.get('gated'))) if on])
    return service, car, detail


def apply(orders, houses, factors=None):
    """Проставляет заявкам длительность, надбавку для машины и разбор; houses — HouseIndex или dict."""
    for o in orders:
        o.service, o.car_access, o.detail = estimate(o, houses.get(o.house), factors)
