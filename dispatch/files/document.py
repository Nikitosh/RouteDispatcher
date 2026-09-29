"""Документ плана (plan.json): его пишут plan и replan, читают replan и страница диспетчера.

Схема: meta, office, brigades[], orders[] (окно, назначение, причина или объяснение), plan{routes, metrics, check},
baseline{routes, metrics, unassigned}, lower_bound; у результата replan ещё event.
"""
import json
import os

from ..config import PRIORITY, SERVICE, SKILL, SKILL_NAMES, TYPE_NAMES
from ..model.domain import Brigade, Order


def route_dict(problem, v, schedule):
    return dict(v=v, brigade=problem.brigades[v].name, km=round(schedule.km, 2), geometry=None,
                stops=[dict(k=s.order, arr=round(s.arrive, 1), beg=round(s.start, 1), end=round(s.end, 1), km=round(s.km, 2))
                       for s in schedule.stops])


def order_dict(problem, k):
    o = problem.orders[k]
    return dict(k=k, id=o.id, type=o.type, bk=o.bk, hd=o.hd, district=o.district, addr=o.address, lat=o.lat, lon=o.lon,
                window=list(o.window), a=o.earliest, latest=problem.latest(k), svc=o.service,
                need=sorted(o.need) if o.need else None, plan=None, reason=None, explanation=None)


def brigade_dict(v, b):
    return dict(v=v, name=b.name, mode=b.mode, mode_name=b.mode_name, skills=b.skill_names, start_addr=b.start_address,
                lat=b.lat, lon=b.lon)


def set_assignment(order, v, seq, stop):
    order['plan'] = dict(brigade=v, seq=seq, arrive=round(stop.arrive, 1), start=round(stop.start, 1), end=round(stop.end, 1))


def orders_from(doc):
    """Заявки документа как объекты Order (для перепланирования)."""
    out = []
    for o in doc['orders']:
        bk = o['bk']
        out.append(Order(id=o['id'], bk=bk, type=TYPE_NAMES[bk], window=tuple(o['window']), service=SERVICE[bk], priority=PRIORITY[bk],
                         skill=SKILL[bk], district=o['district'], address=o['addr'], hd=o.get('hd', ''),
                         need=frozenset(o['need']) if o['need'] else None, lat=o['lat'], lon=o['lon']))
    return out


def brigades_from(doc):
    """Бригады и стартовые точки документа: ([Brigade], [(lat, lon)]), точка 0 — офис."""
    starts = [(doc['office']['lat'], doc['office']['lon'])]
    out = []
    for b in doc['brigades']:
        c = (b['lat'], b['lon'])
        if c not in starts:
            starts.append(c)
        out.append(Brigade(name=b['name'], mode=b['mode'], skills=[SKILL_NAMES.index(s) for s in b['skills']],
                           start_address=b['start_addr'], start=starts.index(c), lat=b['lat'], lon=b['lon']))
    return out, starts


def fill_geometry(doc, geometry, log=print):
    """Линии маршрутов по улицам; без OSRM и кэша участок остаётся прямым отрезком на странице."""
    B, O = doc['brigades'], doc['orders']
    missing = False
    for rt in doc['plan']['routes']:
        b = B[rt['v']]
        pts = [(b['lat'], b['lon'])] + [(O[s['k']]['lat'], O[s['k']]['lon']) for s in rt['stops']]
        rt['geometry'] = geometry.route(b['mode'], pts)
        missing |= rt['geometry'] is None
    geometry.save()
    if missing:
        log('Линий маршрутов нет в кэше и OSRM недоступен: часть маршрутов на странице будет прямыми')


def save(doc, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'plan.json')
    json.dump(doc, open(path, 'w'), ensure_ascii=False)
    return path


def load(path_or_dir):
    path = os.path.join(path_or_dir, 'plan.json') if os.path.isdir(path_or_dir) else path_or_dir
    return json.load(open(path))
