"""Задача планирования: заявки, бригады, стартовые точки и матрицы, расписание маршрута, запись для решателя.

Требуемый транспорт кодируется в номере навыка, поэтому решатель на C++ про транспорт не знает: навык заявки = s + 3·c,
где s — навык (0 ремонт, 1 подключение, 2 авария), c — класс требования (0 — любой транспорт, далее — встретившиеся
наборы допустимых видов). У бригады в маске стоит бит s + 3·c для каждого её навыка s и каждого класса c, который
допускает её транспорт. Классов не больше 9 — маска 32-битная.

Обед (config.LUNCH): 45 минут, начать с 13:00 до 15:15, если смена не короче 6 часов. Берётся между работами: в
промежутке перед очередной работой — на её месте по приезде (в том числе пока бригада ждёт начала окна), а если там
начать до 15:15 уже нельзя — перед выездом; или после последней работы. Начало — как можно раньше, но не раньше
13:00. Из всех мест выбирается то, при котором все работы начинаются как можно раньше, при равенстве — более раннее:
обед в ожидании, который никого не сдвигает, раньше обеда после последней работы, а тот — раньше обеда, который
сдвигает работы. Маршрут, который кончается до 15:15, обед не задерживает. Маршрут без места для обеда недопустим. То
же правило — в решателе (solver/src/core/problem.hpp) и в проверке (planning/validate.py).

Оборудование: у заявки — Order.demand, у бригады — выданный запас (Brigade.stock, по видам) и сколько она унесёт
(Brigade.carry, всего). Сумма по маршруту целиком не больше того и другого.

Темп бригады (Brigade.speed): заявку k бригада v делает за service(v, k) = ⌊Order.service·темп + 0,5⌋ минут — в
расписании, в проверке и в решателе (последнее поле строки бригады в задаче).
"""
import copy

from ..config import (EQUIPMENT, LUNCH, LUNCH_MIN_SHIFT, LUNCH_TOL, MODES, SHIFT, day_profile, traffic_ratio, window_hours,
                      zone_of)
from ..model.domain import Schedule, Stop, work_minutes


def lunch_gap(free, arrive, a, window):
    """Обед в промежутке перед работой: бригада свободна с free, без обеда приехала бы в arrive, окно работы с a.
    (начало обеда, приезд, начало работы) или None, если обед в этот промежуток не помещается."""
    la, lb, ld = window
    if la <= free <= lb + LUNCH_TOL:               # свободна уже в окне обеда: обед перед выездом, а не у двери
        return free, arrive + ld, max(arrive + ld, a)   # клиента — начало работы то же, что «на месте»
    ls = max(arrive, la)
    if ls <= lb + LUNCH_TOL:                       # на месте, по приезде
        return ls, arrive, max(ls + ld, a)
    ls = max(free, la)
    if ls <= lb + LUNCH_TOL:                       # перед выездом
        arr = arrive + (ls + ld - free)
        return ls, arr, max(arr, a)
    return None


class ProblemError(Exception):
    pass


class Problem:
    def __init__(self, name, orders, brigades, starts, matrices):
        self.name = name
        self.orders = orders
        self.brigades = brigades
        self.starts = starts                     # [(lat, lon)], 0 — офис
        self.matrices = matrices                 # без надбавок заявок — для задачи с другими длительностями
        self.time, self.dist = matrices.time, matrices.dist
        self.offset = getattr(matrices, 'offset', None) or []
        self.traffic_grid = False                    # пробки по сетке HERE (иначе районы)
        if getattr(matrices, 'hourly', False):
            self._traffic_hours(getattr(matrices, 'pt_hours', None))
        self._add_car_access()
        if getattr(matrices, 'offset', None):
            self._add_offset(matrices.offset)
        self._refresh_classes()

    def _traffic_hours(self, pt_hours=None):
        """Поездка к заявке — в часы её окна (от начала до последнего допустимого старта): машина — с пробками этих
        часов, общественный транспорт — по расписанию этих часов (pt_hours), если оно есть. Пробки множат только
        дорогу: подход от парковки к двери (overhead матриц) от часа не зависит."""
        span = {self.node(k): (o.earliest, max(o.earliest, self.latest(k))) for k, o in enumerate(self.orders)}
        profile = day_profile(getattr(self.matrices, 'day', None))
        pts = list(self.starts) + [(o.lat, o.lon) for o in self.orders]
        nodes = sorted(span)
        oh = (getattr(self.matrices, 'overhead', None) or {}).get('car', 0.0)   # к заявкам (все узлы span) — с подходом
        from ..geo import here_grid                     # сетка пробок HERE ~300 м по линии поездки, если есть
        R = here_grid.traffic_ratios(pts, [pts[j] for j in nodes], [span[j] for j in nodes], profile=profile) if nodes else None
        self.traffic_grid = R is not None
        if R is not None:
            col = {j: c for c, j in enumerate(nodes)}
            car = [[oh + (x - oh) * float(R[i, col[j]]) if i != j and j in col else x for j, x in enumerate(row)]
                   for i, row in enumerate(self.time['car'])]
        else:                                           # районы: поездка — в среднем по её концам
            zone = [zone_of(*p) for p in pts]
            ratio = {}
            for (za, sa) in set(zone):
                for j, s in span.items():
                    ratio[za, j] = traffic_ratio(*s, share=[(x + y) / 2 for x, y in zip(sa, zone[j][1])], profile=profile)
            car = [[oh + (x - oh) * ratio[zone[i][0], j] if i != j and j in span else x for j, x in enumerate(row)]
                   for i, row in enumerate(self.time['car'])]
        self.time = dict(self.time, car=car)
        if pt_hours:
            hours = {j: window_hours(*s) for j, s in span.items()}
            pt = [row[:] for row in self.time['pt']]
            for i, row in enumerate(pt):
                for j in hours:
                    cell = pt_hours[i][j]
                    if cell and i != j:
                        row[j] = sum(cell[min(h, len(cell) - 1)] for h in hours[j]) / len(hours[j])
            self.time = dict(self.time, pt=pt)

    def _add_offset(self, offset):
        """Перепланирование: бригада из стартовой точки i свободна с момента offset[i] — он в матрице, решатель про
        перепланирование не знает."""
        self.time = {m: [[x + (offset[i] if i < len(offset) and j != i else 0.0) for j, x in enumerate(row)]
                         for i, row in enumerate(T)] for m, T in self.time.items()}

    def _add_car_access(self):
        """Шлагбаум или закрытый двор (Order.car_access): лишние минуты в поездке на машине к заявке."""
        extra = {self.node(k): o.car_access for k, o in enumerate(self.orders) if o.car_access}
        if not extra:
            return
        car = [row[:] for row in self.time['car']]
        for i, row in enumerate(car):
            for j, add in extra.items():
                if i != j:
                    row[j] += add
        self.time = dict(self.time, car=car)

    def _refresh_classes(self):
        needs = sorted({o.need for o in self.orders if o.need}, key=sorted)
        self.classes = [None] + needs
        if len(self.classes) > 9:
            raise ProblemError('слишком много разных требований к транспорту (больше 8)')

    @property
    def n_starts(self):
        return len(self.starts)

    def node(self, k):
        """Номер точки заявки k в матрицах."""
        return self.n_starts + k

    # ---- допуск
    def has_skill(self, v, k):
        return self.orders[k].skill in self.brigades[v].skills

    def has_mode(self, v, k):
        need = self.orders[k].need
        return need is None or self.brigades[v].mode in need

    def can(self, v, k):
        return self.has_skill(v, k) and self.has_mode(v, k)

    def service(self, v, k):
        """Длительность заявки k у бригады v: с её темпом (domain.work_minutes)."""
        return work_minutes(self.orders[k].service, self.brigades[v].speed)

    def latest(self, k, v=None):
        """Позже этого начать нельзя: конец окна и конец дня (22:00) с учётом длительности работы — у бригады v или,
        без неё, у самой быстрой (так конец окна пишется в задачу решателя; конец смены бригады проверяется отдельно)."""
        o = self.orders[k]
        if v is not None:
            svc = self.service(v, k)
        else:
            svc = min((self.service(u, k) for u in range(len(self.brigades))), default=o.service)
        return min(o.window[1], SHIFT - svc)

    def in_time(self, v, k, start):
        return start <= self.latest(k, v) + 1e-6 and start + self.service(v, k) <= self.brigades[v].shift[1] + 1e-6

    # ---- обед
    def lunch_window(self, v):
        """(начать не раньше, начать не позже, длительность) обеда бригады v или None: обед выключен, смена короче
        6 часов или бригада свободна только после окна обеда (смена начинается позже; при перепланировании — была
        занята или ждала заявок всё окно)."""
        b = self.brigades[v]
        if not b.lunch or b.shift[1] - b.shift[0] < LUNCH_MIN_SHIFT or self.ready(v) > LUNCH[1] + LUNCH_TOL:
            return None
        return LUNCH

    def _off(self, node):
        return self.offset[node] if node < len(self.offset) else 0.0

    def ready(self, v):
        """С какого момента бригада v свободна в стартовой точке: начало смены (при перепланировании — момент, когда
        освободится; он в матрице времени)."""
        b = self.brigades[v]
        return b.shift[0] + self._off(b.start)

    # ---- оборудование
    def demand(self, route):
        return {i: sum(self.orders[k].demand.get(i, 0) for k in route) for i in EQUIPMENT}

    def shortage(self, v, route):
        """Чего не хватает маршруту: {вид: штук} сверх выданного запаса и 'carry': штук сверх того, что бригада унесёт."""
        b, need = self.brigades[v], self.demand(route)
        short = {i: need[i] - b.stock.get(i, 0) for i in EQUIPMENT if b.stock is not None and need[i] > b.stock.get(i, 0)}
        if b.carry is not None and sum(need.values()) > b.carry:
            short['carry'] = sum(need.values()) - b.carry
        return short

    def stock_for(self, v, route):
        """Что выдать бригаде утром под маршрут: заданный в файле бригад запас, иначе нужное маршруту плюс по одной
        запасной единице каждого вида, пока бригада может унести."""
        b = self.brigades[v]
        if b.stock is not None:
            return {i: b.stock.get(i, 0) for i in EQUIPMENT}
        out = self.demand(route)
        for i in EQUIPMENT:
            if b.carry is None or sum(out.values()) < b.carry:
                out[i] += 1
        return out

    def rest_brigade(self, v, done, lunch_taken=False):
        """Бригада v для задачи на остаток дня (перепланирование): запас и «унесёт» уменьшены на оборудование
        выполненных заявок done, обед — только если он ей нужен и ещё не был."""
        b, used = copy.copy(self.brigades[v]), self.demand(done)
        if b.stock is not None:
            b.stock = {i: max(0, b.stock.get(i, 0) - used[i]) for i in EQUIPMENT}
        if b.carry is not None:
            b.carry = max(0, b.carry - sum(used.values()))
        b.lunch = self.lunch_window(v) is not None and not lunch_taken
        return b

    # ---- расписание
    def schedule(self, v, route, release=None, keep=0, origin=None, done=None, lunch=None, urgent=None, _skip=None):
        """Расписание маршрута бригады v. Если задан release, бригада выезжает к заявкам начиная с позиции keep
        не раньше этого момента (перепланирование: она узнаёт о новом маршруте в момент события).
        origin = (точка, момент, км) — откуда бригада едет к заявке keep: она была в пути или ждала клиента, когда
        маршрут изменился; км — уже проеденная часть прежнего переезда.
        done — уже выполненные первые остановки с их фактическими временами (Stop).
        lunch — обед прежнего плана (начало, после скольких остановок): он остаётся, если приходится на done или уже
        начался к release, а бригада к его началу уже закончила работы done (тогда к следующей заявке она выезжает не
        раньше его конца; если работа затянулась на время обеда, его не было); иначе обед ставится заново
        по правилу (docstring модуля). Если бригада освобождается (конец done, release) только после 15:15, а обеда
        не было, обед не нужен: окно обеда она провела на работе или в ожидании заявок.
        urgent — заявка (авария события), к которой бригада едет без обеда: промежуток перед ней для обеда не берётся.
        Обед — позже, а если авария занимает конец окна обеда, обеда в этот день нет (окно обеда бригада провела на
        работе). Если так план не складывается — как обычно."""
        if urgent is not None and urgent in route:
            s = self.schedule(v, route, release, keep, origin, done, lunch, _skip=route.index(urgent))
            if s.feasible:
                return s
        b = self.brigades[v]
        T, D = self.time[b.mode], self.dist[b.mode]
        win = self.lunch_window(v) if route else None
        t, prev, km, stops = float(b.shift[0]), b.start, 0.0, []
        if done:
            stops = list(done)
            t, prev, km = done[-1].end, self.node(done[-1].order), sum(s.km for s in done)
        first, ok = len(stops), True
        kept = None
        if win and lunch is not None and (lunch[1] < first or (release is not None and lunch[0] < release and lunch[0] >= t - 1e-6)):
            kept, win = (lunch[0], lunch[1]), None
        if win and max(t, release if release is not None else t) > win[1] + LUNCH_TOL:
            win = None                         # окно обеда бригада провела на работе или в ожидании заявок
        tA, path_a = t, []                     # путь A: обед впереди
        tB, path_b, gap, ls_b = None, [], None, None   # путь B: обед был — в промежутке перед остановкой gap
        same = False                           # обед на пути B никого не сдвинул: времена те же, что на A
        for i in range(first, len(route)):
            k = route[i]
            o, n = self.orders[k], self.node(k)
            svc = self.service(v, k)
            p, extra = prev, 0.0
            if origin is not None and i == keep:
                p, extra = origin[0], origin[2]

            def depart(t0):
                d = max(t0, release) if release is not None and i >= keep else t0
                if origin is not None and i == keep:
                    d = max(origin[1], release or 0.0)
                if kept and kept[1] == first and i == first:
                    d = max(d, kept[0] + LUNCH[2])
                return d
            leg = extra + D[p][n]
            d_a = depart(tA)
            arr_a = d_a + T[p][n]
            s_a = max(arr_a, o.earliest)
            if win:
                s_b = arr_b = None
                if tB is not None:
                    arr_b = depart(tB) + T[p][n]
                    s_b = max(arr_b, o.earliest)
                cand = lunch_gap(d_a + self._off(p), arr_a, o.earliest, win) if i != _skip else None
                # при равенстве — обед здесь: работы между промежутками начинаются раньше; если прежний обед никого
                # не сдвинул, он остаётся — времена те же, обед раньше
                if cand and (s_b is None or cand[2] < s_b or (cand[2] == s_b and not same)):
                    path_b, gap, (ls_b, arr_b, s_b) = [], i, cand
                    same = s_b == s_a
                if s_b is not None and self.in_time(v, k, s_b):
                    path_b.append(Stop(k, arr_b, s_b, s_b + svc, leg))
                    tB = s_b + svc
                else:
                    tB = None
            if not self.in_time(v, k, s_a) or not self.can(v, k):
                ok = False
            path_a.append(Stop(k, arr_a, s_a, s_a + svc, leg))
            km += leg
            tA, prev = s_a + svc, n
        taken = kept
        if win:
            ls = max(tA, win[0])
            if tB is not None and same:        # обед в промежутке никого не сдвинул: начала те же, что без него, но
                path_a, taken = path_a[:gap - first] + path_b, (ls_b, gap)   # приезд — после обеда перед выездом
            elif ls <= win[1] + LUNCH_TOL and ls + win[2] <= b.shift[1] + 1e-6:
                taken = (ls, len(route)) if tA > win[0] else None   # после последней работы; кончила до 13:00 — не нужен
            elif tB is not None:
                path_a, taken = path_a[:gap - first] + path_b, (ls_b, gap)
            elif _skip is not None and _skip >= first and path_a[_skip - first].end > win[1] + LUNCH_TOL:
                taken = None                   # к аварии без обеда, а после неё окно обеда уже кончилось — обеда нет,
                                               # как у бригады, которая провела окно обеда на работе
            else:
                ok = False                     # обеду нет места
        short = self.shortage(v, route)
        return Schedule(ok and not short, km, stops + path_a, taken, short, ok)

    def km(self, v, route):
        return self.schedule(v, route).km if route else 0.0

    # ---- решатель
    def skill_code(self, k):
        return self.orders[k].skill + 3 * self.classes.index(self.orders[k].need)

    def skill_mask(self, v):
        b = self.brigades[v]
        return sum(1 << (s + 3 * c) for s in b.skills for c, need in enumerate(self.classes) if need is None or b.mode in need)

    def write(self, path):
        """Формат задачи решателя (docs/SOLVER_IO.md): имя; N V S; N строк заявок (… нужно роутеров, приставок);
        V строк бригад (старт, транспорт, маска, конец смены, когда свободна в стартовой точке, обед: начать с, до,
        минут — 0, если не нужен; запас роутеров, приставок, унесёт всего, −1 — без предела; темп — если у кого-то он
        не 1, иначе поле опускается и файл тот же, что раньше); по каждому виду
        транспорта матрицы времени и расстояния (S+N)×(S+N). Если смена у кого-то начинается позже 10:00, у каждой
        бригады своя стартовая точка, а начало смены прибавлено ко всем выездам из неё."""
        own = any(b.shift[0] > 0 for b in self.brigades)
        nodes = ([b.start for b in self.brigades] if own else list(range(self.n_starts))) + \
            [self.node(k) for k in range(len(self.orders))]
        S = len(self.brigades) if own else self.n_starts
        late = [b.shift[0] for b in self.brigades] if own else [0] * S
        paced = any(b.speed != 1 for b in self.brigades)    # темп пишется, только если он у кого-то не 1
        with open(path, 'w') as f:
            f.write(f'{self.name}\n{len(self.orders)} {len(self.brigades)} {S}\n')
            for k, o in enumerate(self.orders):
                need = ' '.join(str(o.demand.get(i, 0)) for i in EQUIPMENT)
                oid = '_'.join(str(o.id).split()) or '-'    # номер — одним словом: решатель читает строку по пробелам
                f.write(f'{oid} {int(round(o.service))} {o.earliest} {self.latest(k)} {o.priority} {self.skill_code(k)} {need}\n')
            for v, b in enumerate(self.brigades):
                lunch = ' '.join(f'{x:g}' for x in (self.lunch_window(v) or (0, 0, 0)))
                cap = [b.stock.get(i, 0) if b.stock is not None else -1 for i in EQUIPMENT] + [-1 if b.carry is None else b.carry]
                f.write(f'{v if own else b.start} {MODES.index(b.mode)} {self.skill_mask(v)} {b.shift[1]} {float(self.ready(v))!r} {lunch} '
                        + ' '.join(map(str, cap)) + (f' {float(b.speed)!r}' if paced else '') + '\n')
            for m in MODES:
                T, D = self.time[m], self.dist[m]
                for i, a in enumerate(nodes):
                    add = late[i] if i < S else 0
                    f.write(' '.join(f'{T[a][c] + (add if j >= S else 0):.3f}' for j, c in enumerate(nodes)) + '\n')
                for a in nodes:
                    f.write(' '.join(f'{D[a][c]:.4f}' for c in nodes) + '\n')
