"""Документ плана (plan.json): его пишут plan и replan, читают replan и страница диспетчера.

Схема: meta, office, brigades[] (смена, обед lunch, запас оборудования stock и сколько унесёт carry, уровень level и
темп speed — множитель длительности работ), orders[] (окно, длительность svc по нормативу и сложности — у бригады
она svc × темп, в остановке маршрута это end − beg — и её разбор detail, оборудование equipment, назначение, причина или объяснение),
plan{routes (остановки, обед lunch = [начало, конец] и lunch_after — после скольких остановок), metrics, check},
baseline{routes, metrics, unassigned}, lower_bound; у результата replan ещё event.
"""
import json
import os

from ..config import DEFAULT_LEVEL, LUNCH, PRIORITY, SERVICE, SHIFT, SKILL, SKILL_NAMES, TYPE_NAMES
from ..model.domain import Brigade, Order


def route_dict(problem, v, schedule):
    lunch = schedule.lunch
    return dict(v=v, brigade=problem.brigades[v].name, km=round(schedule.km, 2), geometry=None,
                stops=[dict(k=s.order, arr=round(s.arrive, 1), beg=round(s.start, 1), end=round(s.end, 1), km=round(s.km, 2))
                       for s in schedule.stops],
                lunch=[round(lunch[0], 1), round(lunch[0] + LUNCH[2], 1)] if lunch else None,
                lunch_after=lunch[1] if lunch else None)


def lunches(doc):
    """Обеды плана документа: {бригада: (начало, после скольких остановок)} — для Problem.schedule(lunch=...) и
    check_plan(lunches=...) при перепланировании."""
    return {rt['v']: (rt['lunch'][0], rt['lunch_after']) for rt in doc['plan']['routes'] if rt.get('lunch')}


def order_dict(problem, k):
    o = problem.orders[k]
    return dict(k=k, id=o.id, type=o.type, bk=o.bk, hd=o.hd, district=o.district, addr=o.address, lat=o.lat, lon=o.lon,
                window=list(o.window), a=o.earliest, latest=problem.latest(k), svc=o.service,
                need=sorted(o.need) if o.need else None, apt=o.apt, gigabit=o.gigabit, car_access=o.car_access,
                detail=o.detail, equipment=o.demand, plan=None, reason=None, explanation=None)


def brigade_dict(v, b, stock=None):
    """stock — выданное утром оборудование (Problem.stock_for); по умолчанию — заданное в файле бригад."""
    return dict(v=v, name=b.name, mode=b.mode, mode_name=b.mode_name, skills=b.skill_names, start_addr=b.start_address,
                lat=b.lat, lon=b.lon, schedule=b.schedule, shift=list(b.shift), shift_text=b.shift_text, lunch=b.lunch,
                carry=b.carry, stock=stock if stock is not None else b.stock, level=b.level or DEFAULT_LEVEL, speed=b.speed)


def set_assignment(order, v, seq, stop):
    order['plan'] = dict(brigade=v, seq=seq, arrive=round(stop.arrive, 1), start=round(stop.start, 1), end=round(stop.end, 1))


def orders_from(doc):
    """Заявки документа как объекты Order (для перепланирования)."""
    out = []
    for o in doc['orders']:
        bk = o['bk']
        out.append(Order(id=o['id'], bk=bk, type=TYPE_NAMES[bk], window=tuple(o['window']), service=o.get('svc', SERVICE[bk]),
                         priority=PRIORITY[bk], skill=SKILL[bk], district=o['district'], address=o['addr'], hd=o.get('hd', ''),
                         need=frozenset(o['need']) if o['need'] else None, lat=o['lat'], lon=o['lon'], apt=o.get('apt'),
                         gigabit=o.get('gigabit', False), car_access=o.get('car_access', 0.0), detail=o.get('detail'),
                         equipment=o.get('equipment')))
    return out


def brigades_from(doc):
    """Бригады и стартовые точки документа: ([Brigade], [(lat, lon)]). Только точки, откуда кто-то выезжает: офис —
    первой, если из него выезжает хоть одна бригада (если все стартуют из дома, матрицы от офиса не нужны). Обед,
    запас, «унесёт», уровень и темп — из документа; в документах без них обеда и ограничений оборудования нет, темп 1."""
    office = (doc['office']['lat'], doc['office']['lon'])
    coords = [(b['lat'], b['lon']) for b in doc['brigades']]
    starts = list(dict.fromkeys(([office] if office in coords else []) + coords))
    out = []
    for b, c in zip(doc['brigades'], coords):
        out.append(Brigade(name=b['name'], mode=b['mode'], skills=[SKILL_NAMES.index(s) for s in b['skills']],
                           start_address=b['start_addr'], start=starts.index(c), lat=b['lat'], lon=b['lon'],
                           schedule=b.get('schedule', ''), shift=tuple(b.get('shift') or (0, SHIFT)), lunch=b.get('lunch', False),
                           carry=b.get('carry'), stock=b.get('stock'), level=b.get('level', ''), speed=b.get('speed', 1.0)))
    return out, starts


def fill_geometry(doc, geometry, log=print):
    """Линии маршрутов по улицам; без OSRM и кэша участок остаётся прямым отрезком на странице."""
    B, O = doc['brigades'], doc['orders']
    missing = False
    for rt in doc['plan']['routes']:
        b = B[rt['v']]
        pts = [(b['lat'], b['lon'])] + [(O[s['k']]['lat'], O[s['k']]['lon']) for s in rt['stops']]
        if rt.get('via'):                      # бригада развернулась в пути: линия проходит через точку разворота
            pts.insert(rt['via']['pos'] + 1, (rt['via']['lat'], rt['via']['lon']))
        rt['geometry'] = geometry.route(b['mode'], pts)
        missing |= rt['geometry'] is None
    geometry.save()
    if missing:
        log('Линий маршрутов нет в кэше и OSRM недоступен: часть маршрутов на странице будет прямыми')


def save(doc, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'plan.json')
    json.dump(doc, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    return path


def load(path_or_dir):
    path = os.path.join(path_or_dir, 'plan.json') if os.path.isdir(path_or_dir) else path_or_dir
    return json.load(open(path, encoding='utf-8'))
