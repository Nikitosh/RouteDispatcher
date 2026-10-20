"""Карта аварийного покрытия и карта дефицита «районы × окна» (plan --coverage, --deficit).

Покрытие: участок покрыт сеткой точек (STEP км, до RADIUS км от точек дня), день — срезами по SLOT минут. В каждый
срез у каждой бригады с навыком аварий есть место и момент, когда она освободится по плану: работает — после работы,
на адресе работы; в пути или ждёт — сразу, с последнего адреса (или из дома); вне смены — недоступна. Ответ в точке —
через сколько минут ближайшая из них начнёт аварию там, если бросит следующие заявки: время до освобождения плюс дорога
её транспортом (OSRM; машина — с пробками этого часа и дня недели) плюс подход к двери. Норматив реакции — 2 часа.

Дефицит: для каждого района участка и двухчасового окна — сколько типовых подключений (длительность — средняя по
подключениям дня, адреса — заявки этого района по очереди) ещё можно вставить в маршруты работающих бригад, не выводя
новых и не сдвигая других клиентов за их окна. Каждая клетка — от одного и того же утреннего плана, независимо.
"""
import copy
import dataclasses

from ..config import PRIORITY, SERVICE, SHIFT, SKILL, TYPE_NAMES, TravelSettings, day_profile, traffic_ratio
from ..geo.matrices import public_transport
from ..model.domain import Order
from ..model.problem import Problem

STEP, RADIUS = 0.6, 1.0          # км: шаг сетки и отступ от точек дня
SLOT = 30                        # мин между срезами
WINDOWS = [(a, a + 120) for a in range(0, SHIFT, 120)]
CAP = 6                          # больше стольких свободных мест в клетке не ищем
ACCIDENT = SKILL['Глобальная проблема']


def _state(problem, v, stops, t):
    """(узел, момент освобождения) бригады v в момент t или None, если она вне смены."""
    b = problem.brigades[v]
    if not b.shift[0] <= t <= b.shift[1]:
        return None
    node = b.start
    for s in stops:
        if s.start <= t < s.end:
            return problem.node(s.order), s.end
        if s.end <= t:
            node = problem.node(s.order)
    return node, t


def coverage(problem, routes, client, settings=None, log=print):
    """{'grid', 'slots', 'minutes', 'brigade'} или None без OSRM: minutes[срез][точка] — через сколько минут начнут
    аварию (None — никто), brigade — какая бригада."""
    from ..geo.osrm import OsrmError, profile_of
    from ..site.bundle import grid
    settings = settings or TravelSettings()
    pts = list(problem.starts) + [(o.lat, o.lon) for o in problem.orders]
    net = grid(pts, step=STEP, radius=RADIUS)
    crew = [v for v, b in enumerate(problem.brigades) if ACCIDENT in b.skills]
    if not net or not crew:
        return None
    modes = {problem.brigades[v].mode for v in crew}
    table = {}
    try:
        for profile in {profile_of(m) for m in modes} | ({'car', 'foot'} if 'pt' in modes else set()):
            rows = []
            for i in range(0, len(pts), 100):
                part = [[] for _ in pts[i:i + 100]]
                for j in range(0, len(net), 100):
                    for r, line in enumerate(client.table(profile, pts[i:i + 100], net[j:j + 100])):
                        part[r] += [cell if cell else (1e6, 1e6) for cell in line]
                rows += part
            table[profile] = rows
    except OsrmError as e:
        log(f'Карта покрытия не построена: {e}')
        return None

    profile = day_profile(getattr(problem.matrices, 'day', None))

    def ride(mode, a, p, t):                    # как в плане: пробки дня недели по часу, плюс подход к двери
        extra = settings.overhead.get(mode, 0.0)
        if mode == 'pt':
            car, foot = table['car'][a][p], table['foot'][a][p]
            return public_transport(car[1], foot[0], foot[1])[0] + extra
        minutes = table[profile_of(mode)][a][p][0]
        if mode == 'car':
            minutes *= settings.traffic.get('car', 1.0) * traffic_ratio(t, t, profile=profile)
        return minutes + extra

    sched = {v: problem.schedule(v, routes[v]).stops if routes[v] else [] for v in crew}
    slots = list(range(0, SHIFT, SLOT))
    minutes, who = [], []
    for t in slots:
        state = {v: _state(problem, v, sched[v], t) for v in crew}
        row_m, row_b = [], []
        for p in range(len(net)):
            best = None
            for v, st in state.items():
                if st is None:
                    continue
                m = st[1] - t + ride(problem.brigades[v].mode, st[0], p, t)
                if m < 1e5 and (best is None or m < best[0]):
                    best = (m, v)
            row_m.append(round(best[0]) if best else None)
            row_b.append(best[1] if best else None)
        minutes.append(row_m)
        who.append(row_b)
    return dict(grid=[[round(a, 5), round(b, 5)] for a, b in net], step=STEP, slots=slots, minutes=minutes, brigade=who)


def _extended(problem, templates, orders_new):
    """Задача с добавленными заявками: строки и столбцы матриц — копии строк заявок-образцов (тот же адрес)."""
    m = problem.matrices
    n0 = problem.n_starts + len(problem.orders)
    src = list(range(n0)) + [problem.node(k) for k in templates]
    grow = lambda M: [[M[i][j] for j in src] for i in src]
    hours = [[m.pt_hours[i][j] for j in src] for i in src] if m.pt_hours else None
    mats = dataclasses.replace(m, time={x: grow(T) for x, T in m.time.items()}, dist={x: grow(D) for x, D in m.dist.items()},
                               pt_hours=hours)
    return Problem(problem.name, problem.orders + orders_new, problem.brigades, problem.starts, mats)


def _insert(p, routes, k, used):
    """Самая дешёвая по км вставка заявки k в маршруты бригад used без нарушений; (v, маршрут) или None."""
    best = None
    for v in used:
        base = p.schedule(v, routes[v]).km
        for pos in range(len(routes[v]) + 1):
            cand = routes[v][:pos] + [k] + routes[v][pos:]
            s = p.schedule(v, cand)
            if s.feasible and (best is None or s.km - base < best[0]):
                best = (s.km - base, v, cand)
    return best and best[1:]


def deficit(problem, routes):
    """{'districts', 'windows', 'demand', 'free', 'free_all'}: demand[район][окно] — заявок в плане, free — сколько ещё
    типовых подключений помещается у работающих бригад (до CAP), free_all — если выйдут и резервные."""
    used = [v for v, r in enumerate(routes) if r]
    everyone = list(range(len(problem.brigades)))
    conn = [o.service for o in problem.orders if o.bk == 'Подключение']
    service = round(sum(conn) / len(conn)) if conn else SERVICE['Подключение']
    districts = sorted({o.district for o in problem.orders if o.district})
    demand, free, free_all = [], [], []
    for d in districts:
        homes = [k for k, o in enumerate(problem.orders) if o.district == d]
        row_d, row_f, row_a = [], [], []
        for a, b in WINDOWS:
            row_d.append(sum(1 for k in homes if a <= problem.orders[k].window[0] < b))
            new = [Order(id=f'+{i}', bk='Подключение', type=TYPE_NAMES['Подключение'], window=(a, b), service=service,
                         priority=PRIORITY['Подключение'], skill=SKILL['Подключение'], district=d,
                         lat=problem.orders[homes[i % len(homes)]].lat, lon=problem.orders[homes[i % len(homes)]].lon,
                         equipment={}) for i in range(CAP)]
            p = _extended(problem, [homes[i % len(homes)] for i in range(CAP)], new)
            row_f.append(_fill(p, routes, len(problem.orders), used))
            row_a.append(_fill(p, routes, len(problem.orders), everyone))
        demand.append(row_d)
        free.append(row_f)
        free_all.append(row_a)
    return dict(districts=districts, windows=[list(w) for w in WINDOWS], demand=demand, free=free, free_all=free_all,
                service=service, cap=CAP)


def _fill(p, routes, first, brigades):
    """Сколько из CAP пробных заявок (номера first…) вставляется подряд в маршруты бригад brigades."""
    cur = copy.deepcopy(routes)
    for i in range(CAP):
        got = _insert(p, cur, first + i, brigades)
        if not got:
            return i
        cur[got[0]] = got[1]
    return CAP
