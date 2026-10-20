"""Перестройка плана после события в течение дня (ответы жюри, assumptions.md 2.7).

- Выполненное и начатое к моменту события не меняется; начатую работу не прерывают.
- Бригада продолжает свой прежний маршрут, пока он совпадает с новым; с первой изменённой заявки она выезжает не
  раньше момента события. Если в этот момент она едет к заявке, которую у неё забирают или перед которой ставят
  другую, она разворачивается из точки, где находится (точка на линии переезда по улицам, пропорционально
  прошедшему времени); если ждёт начала окна у клиента — едет от клиента. Работающая на адресе сначала заканчивает.
- Новая обычная заявка встаёт в свободный промежуток: порядок остальных не меняется, они только сдвигаются во времени
  внутри окон. Нет места — резервная бригада, иначе заявка остаётся неназначенной с причиной.
- Авария в течение дня может перестроить остаток дня. Окно аварии — от момента появления до +2 часов. Перебираем
  бригады и первые места их неначатой части; заявки, которые после вставки перестали успевать, переносим к другим
  бригадам. Цена: минута ожидания аварии, перенос заявки, вывод резервной бригады и километры (ReplanSettings).
  Штраф за ожидание — только для таких аварий: аварии из утреннего файла планируются по своему окну.
- Недоступный инженер: его неначатые заявки переходят к другим бригадам, затем к резерву.
- Отмена: заявка снимается, освободившееся время отдаётся неназначенным заявкам.
- Заявка выполнена быстрее или медленнее плана («закончит в 15:10»): дальше бригада едет от фактического конца. Если
  теперь она не успевает к каким-то клиентам, эти заявки снимаются с неё по одной (первая опаздывающая) и
  пристраиваются как новые; если освободилось время — его получают неназначенные заявки.
- Клиент перенёс окно: если заявка успевает в новое окно на своём месте, меняется только время; иначе она снимается
  с бригады и пристраивается как новая.
- Сломалась машина: с момента события бригада продолжает день на другом транспорте (по умолчанию общественном) оттуда,
  где она сейчас; заявки, которым нужна машина или к которым она теперь не успевает, пристраиваются как новые.
"""
import copy
import dataclasses
import math
import os
from dataclasses import asdict, dataclass
from typing import Optional

from ..config import CARRY, LUNCH, MODE_NAMES, MODES, PENALTY, PRIORITY, SERVICE, SHIFT, SKILL, TYPE_NAMES, ReplanSettings, TravelSettings
from ..files import document as docs
from ..files.readers import InputError, parse_minutes, parse_modes
from ..formatting import hhmm
from ..geo.geocoder import Geocoder
from ..geo.geometry import RouteGeometry
from ..geo.houses import HouseIndex
from ..geo.matrices import MatrixBuilder
from ..geo.osrm import OsrmError
from ..model import complexity
from ..model.domain import Order, Stop, apartment, house_address
from ..model.problem import Problem
from .metrics import plan_metrics
from .validate import check_plan

BK_BY_TYPE = {v: k for k, v in TYPE_NAMES.items()}   # «Авария» → «Глобальная проблема»
ACCIDENT_SLACK = 20                                   # мин: «лучший план» начинает аварию не позже самого раннего прибытия + это


@dataclass
class Unavailable:
    brigade: str                      # имя или номер бригады


@dataclass
class Cancel:
    order: str                        # номер заявки


@dataclass
class Urgent:
    address: str
    type: str = 'Авария'
    window: Optional[str] = None      # «15:00-17:00»; по умолчанию с момента события до конца смены
    need: Optional[str] = None
    id: Optional[str] = None
    district: str = ''


@dataclass
class Finish:
    order: str                        # номер заявки
    end: object                       # когда работа закончилась или закончится: «15:10» или минуты от 10:00


@dataclass
class Reschedule:
    order: str                        # номер заявки
    window: object                    # новое окно: «15:00-17:00» или (начало, конец) в минутах от 10:00


@dataclass
class Breakdown:
    brigade: str                      # имя или номер бригады
    mode: str = 'pt'                  # на чём бригада продолжает день: pt, foot, bike


@dataclass
class Move:
    order: str                        # номер заявки
    brigade: str                      # кому передать (имя или номер)


def along(line, share):
    """Точка на ломаной [[lat, lon], ...] на доле share её длины."""
    if len(line) < 2:
        return tuple(line[0])
    seg = [math.hypot(b[0] - a[0], (b[1] - a[1]) * math.cos(math.radians(a[0]))) for a, b in zip(line, line[1:])]
    goal = max(0.0, min(1.0, share)) * sum(seg)
    for (a, b), d in zip(zip(line, line[1:]), seg):
        if goal <= d and d > 0:
            x = goal / d
            return a[0] + (b[0] - a[0]) * x, a[1] + (b[1] - a[1]) * x
        goal -= d
    return tuple(line[-1])


def order_type(text):
    t = text.strip().lower()
    if not t:
        raise InputError('не указан тип заявки (авария, подключение, ремонт, дозаказ)')
    for name in BK_BY_TYPE:
        if name.lower().startswith(t[:4]):
            return name
    raise InputError(f'неизвестный тип заявки «{text}» (авария, подключение, ремонт, дозаказ)')


class Replanner:
    def __init__(self, doc, matrices=None, geocoder=None, travel=None, settings=None, houses=None, geometry=None):
        self.doc = doc
        self.orders = docs.orders_from(doc)
        self.brigades, self.starts = docs.brigades_from(doc)
        self.routes = [[] for _ in self.brigades]
        for rt in doc['plan']['routes']:
            self.routes[rt['v']] = [s['k'] for s in rt['stops']]
        self.original = copy.deepcopy(self.routes)
        self.lunch0 = docs.lunches(doc)                   # обеды плана документа: {бригада: (начало, после скольких остановок)}
        self.doc_stops = [[] for _ in self.brigades]     # остановки документа с их временами (в т.ч. после прошлых событий)
        for rt in doc['plan']['routes']:
            self.doc_stops[rt['v']] = [Stop(s['k'], s['arr'], s['beg'], s['end'], s['km']) for s in rt['stops']]
        self.matrices = matrices or MatrixBuilder()
        self.geocoder = geocoder or Geocoder()
        m = doc['meta']
        self.travel = travel or TravelSettings(m.get('traffic') or {}, m.get('overhead') or {}, hourly=m.get('traffic_hours', False),
                                               weekday=m.get('weekday'))
        self.settings = settings or ReplanSettings()
        self.houses = houses if houses is not None else HouseIndex()
        self.geometry = geometry or RouteGeometry()
        self.where = {}                     # бригады, которые в момент события в пути или ждут клиента
        self.moved = None                   # ручная передача: (заявка, бригада)
        self.pinned = {o['k']: o['pinned'] for o in doc['orders'] if o.get('pinned') is not None}   # заявка → бригада
        # недоступные бригады и отменённые заявки прошлых событий остаются такими и в следующих
        self.removed = {v for v, b in enumerate(doc['brigades']) if b.get('unavailable_from') is not None}
        self.cancelled_before = {o['k'] for o in doc['orders'] if o.get('cancelled')}
        self.cancelled = None
        self.new_order = None
        self.accident = None

    # ---- расписание с учётом события
    def keep(self, v, route):
        """Сколько первых заявок маршрута совпадает с прежним: до них бригада едет по старому плану."""
        orig, p = self.original[v], 0
        while p < min(len(route), len(orig)) and route[p] == orig[p]:
            p += 1
        return p

    def schedule(self, v, route):
        """Расписание после события: неизменное начало маршрута — как в документе (с фактическими временами, в т.ч.
        после прошлых событий), дальше — пересчёт с момента события."""
        keep = self.keep(v, route)
        k = self.new_order                             # к аварии события едут без обеда, если он помещается позже
        urgent = k if k is not None and self.orders[k].skill == 2 else None
        return self.problem.schedule(v, route, release=self.at, keep=keep, origin=self.origin(v, keep),
                                     done=self.doc_stops[v][:keep], lunch=self.lunch0.get(v), urgent=urgent)

    def origin(self, v, keep):
        """Откуда бригада едет к первой изменённой заявке, если менять пришлось её текущий переезд."""
        w = self.where.get(v)
        if not w or keep != self.frozen_len[v]:
            return None
        point = w['point'] if w['kind'] == 'transit' else self.problem.node(w['toward'])
        return point, self.at, w['km']

    def _xy(self, node):
        P = self.problem
        if node < P.n_starts:
            return P.starts[node]
        o = P.orders[node - P.n_starts]
        return o.lat, o.lon

    def _locate(self):
        """Где бригады в момент события: {v: в пути (точка на линии переезда) или ждёт клиента}."""
        P, found = self.problem, {}
        for v, route in enumerate(self.original):
            f = self.frozen_len[v]
            if f >= len(route) or f >= len(self.doc_stops[v]):
                continue                                   # всё сделала или ещё на заявке с новым концом (Finish)
            b = P.brigades[v]
            prev, nxt = (b.start if f == 0 else P.node(route[f - 1])), P.node(route[f])
            ride = P.time[b.mode][prev][nxt]
            arrive = self.doc_stops[v][f].arrive
            if self.at <= arrive - ride + 1e-6:
                continue                                   # ещё на прежнем адресе (работает) или не выехала
            km = P.dist[b.mode][prev][nxt]
            if self.at < arrive - 1e-6 and ride > 0:
                share = (self.at - (arrive - ride)) / ride
                a, c = self._xy(prev), self._xy(nxt)
                lat, lon = along(self.geometry.leg(b.mode, a, c) or [list(a), list(c)], share)
                found[v] = dict(kind='transit', lat=round(lat, 6), lon=round(lon, 6), km=km * share, toward=route[f], pos=f)
            else:
                found[v] = dict(kind='waiting', km=km, toward=route[f], pos=f)
        return found

    def frozen(self, v):
        """Сколько первых заявок бригады выполнено или начато к моменту события."""
        return sum(1 for s in self.doc_stops[v] if s.start < self.at)

    def active(self):
        return [v for v in range(len(self.brigades)) if v not in self.removed]

    def best_insert(self, k, only=None):
        """Самая дешёвая по км вставка без изменения порядка остальных заявок; резерв — в последнюю очередь.
        only — только в эту бригаду. (цена, бригада, маршрут) или None."""
        best = None
        if only is not None and only in self.removed:
            return None
        for v in ([only] if only is not None else self.active()):
            if not self.problem.can(v, k):
                continue
            route = self.routes[v]
            base = self.schedule(v, route).km if route else 0.0
            for pos in range(self.frozen(v), len(route) + 1):
                cand = route[:pos] + [k] + route[pos:]
                s = self.schedule(v, cand)
                cost = s.km - base + (1e3 if not route else 0.0)
                if s.feasible and (best is None or cost < best[0]):
                    best = (cost, v, cand)
        return best

    # ---- событие
    def _add_order(self, ev):
        bk = BK_BY_TYPE[order_type(ev.type)]
        house = house_address(ev.address)
        lat, lon = self.geocoder.locate([house], log=lambda *_: None)[house]
        if ev.window:
            parts = ev.window.replace('–', '-').split('-')
            if len(parts) != 2:
                raise InputError(f'окно «{ev.window}»: нужно ЧЧ:ММ-ЧЧ:ММ')
            a, b = (parse_minutes(x, '--window') for x in parts)
            if not 0 <= a < b <= SHIFT:
                raise InputError(f'окно {hhmm(a)}–{hhmm(b)} должно лежать внутри 10:00–22:00')
        else:
            a, b = self.at, SHIFT
        if bk == 'Глобальная проблема':
            a, b = max(a, self.at), min(b, self.at + self.settings.react, SHIFT)
        district = ev.district or min(self.orders, key=lambda o: (o.lat - lat) ** 2 + ((o.lon - lon) * 0.56) ** 2).district \
            if self.orders else ''                   # район не задан — как у ближайшей заявки
        o = Order(id=ev.id or f'N{len(self.orders) + 1}', bk=bk, type=TYPE_NAMES[bk], window=(max(a, self.at), b),
                  service=SERVICE[bk], priority=PRIORITY[bk], skill=SKILL[bk], district=district, address=ev.address,
                  hd='срочная', need=parse_modes(ev.need, '--need') if ev.need else None, lat=lat, lon=lon,
                  apt=apartment(ev.address))
        o.service, o.car_access, o.detail = complexity.estimate(o, self.houses.get(house))
        self.orders.append(o)
        return len(self.orders) - 1

    def _build(self):
        points = self.starts + [(o.lat, o.lon) for o in self.orders]
        self.problem = Problem(self.doc['meta']['name'], self.orders, self.brigades, self.starts,
                               self.matrices.build(points, len(self.starts), self.travel))
        if self.doc['meta'].get('traffic_grid') and self.travel.hourly and not self.problem.traffic_grid:
            raise InputError('план посчитан с сеткой пробок HERE (data/traffic/here_grid.npz), а здесь её нет или нет '
                             'numpy: время в пути разошлось бы с планом')

    STRATEGIES = ('insert', 'optimize', 'keep')

    def apply(self, at, event, strategy='insert', solver=None, seconds=3.0, seed=1, workdir=None):
        """Событие и пересчёт остатка дня. strategy: insert — минимум перестановок (вставки, авария может вытеснить
        заявки); optimize — лучший план на остаток дня решателем (solver — SolverRunner); keep — ничего не менять:
        новые и снятые заявки остаются без исполнителя."""
        pending, before = self._prepare(at, event)
        self.unplaced = []
        was_unassigned = [o['k'] for o in self.doc['orders'] if o['plan'] is None and not o.get('cancelled') and o['k'] != self.cancelled]
        if self.moved:
            k, v = self.moved
            b = self.best_insert(k, only=v)
            if not b:
                raise InputError(f'{self.brigades[v].name} не может взять заявку {self.orders[k].id}: '
                                 + self.move_options(k)[v]['why'])
            self.routes[v] = b[2]
        elif strategy == 'keep':
            self.unplaced = [(k, why) for k, why in pending]
        elif strategy == 'optimize':
            self._optimize(pending, solver, seconds, seed, workdir)
        else:
            self._insert(pending, was_unassigned)
        after = self._positions(self.routes, self.schedule)
        self.changes = self._changes(before, after, pending, was_unassigned)
        self.event = dict(self._event_info(event), strategy=strategy)
        return self.result()

    def _prepare(self, at, event):
        """Момент события, задача с точками разворота, само событие. (заявки к пристройке, прежние позиции)."""
        self.at = parse_minutes(at, '--at') if isinstance(at, str) else at
        if isinstance(event, Urgent):
            self.new_order = self._add_order(event)
        self.rescheduled = self.broken = None
        if isinstance(event, Reschedule):
            self._reschedule(event)
        self._build()
        before = {s.order: (v, s.start) for v, stops in enumerate(self.doc_stops) for s in stops}
        self.finished = None
        if isinstance(event, Finish):
            self._finish(event)
        self.frozen_len = {v: self.frozen(v) for v in range(len(self.brigades))}
        self.where = self._locate()
        moving = [v for v, w in self.where.items() if w['kind'] == 'transit']
        if moving:
            n = len(self.starts)
            for v in moving:
                self.starts.append((self.where[v]['lat'], self.where[v]['lon']))
                self.where[v]['point'] = len(self.starts) - 1
            try:
                self._build()                              # матрицы от точек на дороге — нужен OSRM
            except OsrmError:
                del self.starts[n:]
                self.where = {v: w for v, w in self.where.items() if w['kind'] != 'transit'}
                self._build()
        pending = []                                   # (заявка, почему её переносим)
        if isinstance(event, Unavailable):
            v = self._brigade(event.brigade)
            f = self.frozen_len[v]
            pending += [(k, f'{self.brigades[v].name} недоступна с {hhmm(self.at)}') for k in self.routes[v][f:]]
            self.routes[v] = self.routes[v][:f]
            self.removed.add(v)
        elif isinstance(event, Cancel):
            k = next((i for i, o in enumerate(self.orders) if o.id == str(event.order)), None)
            if k is None:
                raise InputError(f'нет заявки {event.order}')
            if k in self.cancelled_before:
                raise InputError(f'заявка {event.order} уже отменена')
            v = next((i for i, r in enumerate(self.routes) if k in r), None)
            if v is not None:
                if self.routes[v].index(k) < self.frozen_len[v]:
                    raise InputError(f'заявка {event.order} уже выполнена или начата к {hhmm(self.at)}')
                self.routes[v].remove(k)
            self.cancelled = k
        elif isinstance(event, Urgent):
            k = self.new_order
            pending.append((k, 'новая авария' if self.orders[k].skill == 2 else 'новая заявка'))
        elif isinstance(event, Finish):
            k = self.finished['order']
            pending += self._drop_infeasible(self.finished['brigade'], f'задержка на заявке {self.orders[k].id}')
        elif isinstance(event, Reschedule):
            k = self.rescheduled['order']
            v = next((i for i, r in enumerate(self.routes) if k in r), None)
            why = f'клиент перенёс окно на {hhmm(self.orders[k].window[0])}–{hhmm(self.orders[k].window[1])}'
            if v is not None:                          # время этой бригады — заново, начиная с заявки
                self.original[v] = self.original[v][:self.routes[v].index(k)]
            if v is None:
                pending.append((k, why))
            elif not self.schedule(v, self.routes[v]).feasible:
                self.routes[v] = [x for x in self.routes[v] if x != k]
                pending.append((k, why))
                pending += self._drop_infeasible(v, why)
        elif isinstance(event, Breakdown):
            v = self._brigade(event.brigade)
            if event.mode not in MODES or event.mode == 'car':
                raise InputError(f'после поломки машины бригада едет на общественном транспорте, велосипеде или пешком, а не «{event.mode}»')
            self.broken = dict(brigade=v, was=self.brigades[v].mode, now=event.mode)
            self.brigades[v].mode = event.mode
            if CARRY[event.mode] is not None:          # без машины унесёт меньше; уже выполненное в счёт не идёт
                done = self.routes[v][:self.frozen_len[v]]
                self.brigades[v].carry = CARRY[event.mode] + sum(self.problem.demand(done).values())
            self.original[v] = self.original[v][:self.frozen_len[v]]   # дальше — заново с места поломки
            pending += self._drop_infeasible(v, f'{self.brigades[v].name}: сломалась машина, дальше — '
                                                f'{MODE_NAMES[event.mode].lower()}')
        elif isinstance(event, Move):
            k = self._order(event.order)
            if k in self.cancelled_before:
                raise InputError(f'заявка {event.order} отменена')
            v = next((i for i, r in enumerate(self.routes) if k in r), None)
            if v is not None:
                if self.routes[v].index(k) < self.frozen_len[v]:
                    raise InputError(f'заявка {event.order} уже выполнена или начата к {hhmm(self.at)}')
                self.routes[v].remove(k)
            self.moved = (k, self._brigade(event.brigade))
            self.pinned[k] = self.moved[1]
            pending.append((k, 'передана вручную'))
        return pending, before

    def _finish(self, ev):
        """Фактический конец работы: остановка получает новый конец, следующие остановки бригады больше не берутся
        из документа — расписание дальше считается от этого конца."""
        k = self._order(ev.order)
        end = parse_minutes(ev.end, 'время окончания') if isinstance(ev.end, str) else float(ev.end)
        v = next((v for v, stops in enumerate(self.doc_stops) for s in stops if s.order == k), None)
        if v is None:
            raise InputError(f'заявка {ev.order} не назначена')
        i = next(i for i, s in enumerate(self.doc_stops[v]) if s.order == k)
        st = self.doc_stops[v][i]
        if st.start >= self.at - 1e-6:            # как frozen(): начатая — та, что началась раньше момента события
            raise InputError(f'заявка {ev.order} по плану начинается в {hhmm(st.start)}, не раньше момента события {hhmm(self.at)}')
        later = next((s for s in self.doc_stops[v][i + 1:] if s.start < self.at - 1e-6), None)
        if later is not None:
            raise InputError(f'после заявки {ev.order} бригада уже начала заявку {self.orders[later.order].id} '
                             f'в {hhmm(later.start)}: конец {ev.order} — не позже этого')
        if end <= st.start:
            raise InputError(f'конец {hhmm(end)} раньше начала работы {hhmm(st.start)}')
        if end > SHIFT:
            raise InputError('конец работы после 22:00')
        self.finished = dict(order=k, brigade=v, end=end, planned=st.end, delta=round(end - st.end))
        self.doc_stops[v] = self.doc_stops[v][:i] + [dataclasses.replace(st, end=end)]

    def _drop_infeasible(self, v, why):
        """Неначатые заявки бригады v, которые она теперь не может выполнить (не успевает, нет навыка или нужного
        транспорта): снимаем по одной — первую такую, — пока маршрут не станет выполнимым. Если причину по остановкам
        не видно (обед, оборудование), снимаем ту одну заявку, без которой маршрут выполним (из таких — с наименьшим
        приоритетом, затем самую позднюю), и только если такой нет — последнюю."""
        out, route, f = [], self.routes[v], self.frozen_len[v]
        while len(route) > f and not self.schedule(v, route).feasible:
            s = self.schedule(v, route)
            bad = next((st.order for i, st in enumerate(s.stops) if i >= f
                        and (not self.problem.can(v, st.order) or st.start > self.problem.latest(st.order, v) + 1e-6
                             or st.start + self.problem.service(v, st.order) > self.brigades[v].shift[1] + 1e-6)), None)
            if bad is None:
                fits = [k for k in route[f:] if self.schedule(v, [x for x in route if x != k]).feasible]
                bad = max(fits, key=lambda k: (self.orders[k].priority, route.index(k))) if fits else route[-1]
            route = [x for x in route if x != bad]
            out.append((bad, why))
        self.routes[v] = route
        return out

    def _reschedule(self, ev):
        """Новое окно заявки — до построения задачи, чтобы пробки по часам считались уже по нему."""
        k = self._order(ev.order)
        if k in self.cancelled_before:
            raise InputError(f'заявка {ev.order} отменена')
        if isinstance(ev.window, str):
            a, b = (parse_minutes(x, 'окно') for x in ev.window.replace('–', '-').split('-'))
        else:
            a, b = (float(x) for x in ev.window)
        if not 0 <= a < b <= SHIFT:
            raise InputError(f'окно {hhmm(a)}–{hhmm(b)} должно лежать внутри 10:00–22:00')
        if a < self.at:
            raise InputError(f'новое окно начинается раньше момента события {hhmm(self.at)}')
        if any(s.order == k and s.start < self.at for stops in self.doc_stops for s in stops):
            raise InputError(f'заявка {ev.order} уже выполнена или начата к {hhmm(self.at)}')
        o = self.orders[k]
        self.rescheduled = dict(order=k, was=list(o.window), now=[a, b])
        self.orders[k] = dataclasses.replace(o, window=(a, b))

    def _insert(self, pending, was_unassigned):
        k = self.new_order
        if k is not None and self.orders[k].skill == 2:
            pending[:0] = self._place_accident(k)
        for k, why in sorted(pending, key=lambda x: (self.orders[x[0]].priority, self.problem.latest(x[0]))):
            if any(k in r for r in self.routes):
                continue
            b = self.best_insert(k)
            if b:
                self.routes[b[1]] = b[2]
            else:
                self.unplaced.append((k, why))
        for k in was_unassigned:
            b = self.best_insert(k)
            if b:
                self.routes[b[1]] = b[2]

    def _optimize(self, pending, solver, seconds, seed, workdir):
        """Лучший план на остаток дня: выполненное и начатое фиксируем, остальные заявки (и новые) решатель
        распределяет заново. Каждая бригада стартует оттуда и тогда, где и когда освободится: из точки разворота,
        с адреса текущей работы, от клиента, у которого ждёт окна, или из дома; момент освобождения прибавляется ко
        всем выездам из её стартовой точки — решатель про перепланирование не знает."""
        import tempfile
        fixed = {v: self.routes[v][:self.frozen_len[v]] for v in range(len(self.brigades))}
        busy = {k for r in fixed.values() for k in r}
        rest = [k for k in range(len(self.orders)) if k not in busy and k != self.cancelled and k not in self.cancelled_before]
        workdir = workdir or tempfile.mkdtemp(prefix='dispatch_rest_')
        # решатель считает бригады только в остатке дня и не знает, что работавшие утром уже задействованы: сначала
        # даём ему бригады утреннего плана, резерв — только если без него заявки не влезают
        # порядок целей: все заявки → меньше бригад → авария раньше → км. Сначала работающие бригады (с узким, потом с
        # полным окном аварии), затем по одной резервной — ближайшей к аварии или к заявке, которая не влезла
        active = [v for v in self.active() if self.original[v]]
        reserve = [v for v in self.active() if not self.original[v]]
        while True:
            routes = None
            for tight in (True, False):
                routes = self._solve_rest(active, rest, solver, seconds, seed, workdir, tight)
                if sum(len(r) for r in routes) == len(rest):
                    break
            if sum(len(r) for r in routes) == len(rest) or not reserve:
                break
            served = {rest[j] for r in routes for j in r}
            target = self.new_order if self.new_order in rest and self.new_order not in served else \
                next(k for k in rest if k not in served)
            o = self.orders[target]
            reserve.sort(key=lambda v: (o.lat - self.brigades[v].lat) ** 2 + (o.lon - self.brigades[v].lon) ** 2)
            active = active + [reserve.pop(0)]
        placed = set()
        for i, v in enumerate(active):
            self.routes[v] = fixed[v] + [rest[j] for j in routes[i]]
            placed.update(rest[j] for j in routes[i])
        for v in active:                                # бригада не успела выехать так рано, как решил решатель
            while not self.schedule(v, self.routes[v]).feasible and len(self.routes[v]) > self.frozen_len[v]:
                k = self.routes[v].pop()
                placed.discard(k)
        for k, pv in self.pinned.items():               # закреплённые вручную — обратно своей бригаде
            if k in placed and k not in self.routes[pv] and pv not in self.removed:
                v = next(v for v, r in enumerate(self.routes) if k in r)
                b = self.best_insert(k, only=pv)
                if b:
                    self.routes[v].remove(k)
                    self.routes[pv] = self.best_insert(k, only=pv)[2]
        why = dict(pending)
        for k in rest:
            if k not in placed:
                b = self.best_insert(k)
                if b:
                    self.routes[b[1]] = b[2]
                else:
                    self.unplaced.append((k, why.get(k, 'не поместилась при пересчёте дня')))
        k = self.new_order
        if k is not None and self.orders[k].skill == 2:
            v = next((v for v, r in enumerate(self.routes) if k in r), None)
            if v is not None:
                st = next(s for s in self.schedule(v, self.routes[v]).stops if s.order == k)
                self.accident = dict(brigade=v, start=round(st.start, 1), wait=round(st.start - self.at, 1), displaced=0)

    def _solve_rest(self, active, rest, solver, seconds, seed, workdir, tight=True):
        """Маршруты остатка дня для бригад active по заявкам rest (индексы в rest)."""
        at = self.at
        points, ready = [], []
        for v in active:
            w, f = self.where.get(v), self.frozen_len[v]
            if w and w['kind'] == 'transit':
                points.append((w['lat'], w['lon']))
                ready.append(at)
            elif w:
                o = self.orders[w['toward']]
                points.append((o.lat, o.lon))
                ready.append(at)
            elif f:
                last = self.doc_stops[v][f - 1]
                o = self.orders[last.order]
                points.append((o.lat, o.lon))
                ready.append(max(at, last.end))
            else:
                points.append(self.starts[self.brigades[v].start])
                ready.append(at)
        brigades = []
        for i, v in enumerate(active):                  # запас за вычетом выполненного, обед — если ещё не было
            f, lunch = self.frozen_len[v], self.lunch0.get(v)
            taken = lunch is not None and (lunch[1] < f or lunch[0] < at)
            brigades.append(self.problem.rest_brigade(v, self.routes[v][:f], lunch_taken=taken))
            if taken and lunch[0] <= at < lunch[0] + LUNCH[2]:
                ready[i] = max(ready[i], lunch[0] + LUNCH[2])       # обедает в момент события — выедет после
        for i, b in enumerate(brigades):
            ready[i] = max(ready[i], b.shift[0])        # раньше начала смены не выезжает
            b.start, b.shift = i, (0, b.shift[1])       # начало смены — в матрице (ready), а не в бригаде
        m = self.matrices.build(points + [(self.orders[k].lat, self.orders[k].lon) for k in rest], len(points), self.travel)
        mats = dataclasses.replace(m, offset=ready)
        orders = [self.orders[k] for k in rest]
        sub = Problem(self.doc['meta']['name'], orders, brigades, points, mats)
        k = self.new_order
        if tight and k is not None and self.orders[k].skill == 2 and k in rest:
            # аварию — как можно раньше: решателю хватает начать в окне, поэтому сужаем окно до самого раннего
            # прибытия подходящей бригады плюс ACCIDENT_SLACK; не получилось — пробуем с полным окном
            i = rest.index(k)
            first = min((sub.time[b.mode][b.start][sub.node(i)] for v, b in enumerate(brigades) if sub.can(v, i)), default=None)
            if first is not None:
                o = orders[i]
                orders[i] = dataclasses.replace(o, window=(o.window[0], min(o.window[1], max(o.window[0], first) + ACCIDENT_SLACK)))
                narrow = Problem(sub.name, orders, brigades, points, mats)
                return solver.plan_only(narrow, seconds, seed, os.path.join(workdir, f'n{len(active)}a'))
        return solver.plan_only(sub, seconds, seed, os.path.join(workdir, f'n{len(active)}'))

    def _order(self, ref):
        k = next((i for i, o in enumerate(self.orders) if o.id == str(ref)), None)
        if k is None:
            raise InputError(f'нет заявки {ref}')
        return k

    def options(self, at, order):
        """Цены передачи заявки order всем бригадам в момент at: [{brigade, name, ok, km, shifted, start, why}]."""
        self._prepare(at, None)
        k = self._order(order)
        if k in self.cancelled_before:
            raise InputError(f'заявка {order} отменена')
        return [dict(o, brigade=v, name=self.brigades[v].name) for v, o in sorted(
            self.move_options(k).items(), key=lambda x: (not x[1]['ok'], x[1].get('new_brigade', False), x[1].get('km', 0)))]

    def move_options(self, k):
        """Цена передачи заявки k каждой бригаде: {бригада: {ok, km, shifted, start, why}}. km — прирост пробега плана
        (с учётом того, что прежняя бригада заявку отдаёт), shifted — сколько визитов бригады сдвинется больше чем на 5 мин."""
        out = {}
        owner = next((v for v, r in enumerate(self.routes) if k in r), None)
        saved = 0.0
        if owner is not None:
            r = self.routes[owner]
            saved = self.schedule(owner, r).km - self.schedule(owner, [x for x in r if x != k]).km
        for v in range(len(self.brigades)):
            if v == owner:
                continue
            o = self.orders[k]
            if v in self.removed:
                out[v] = dict(ok=False, why='бригада недоступна')
            elif not self.problem.has_skill(v, k):
                out[v] = dict(ok=False, why=f'нет навыка «{o.type}»')
            elif not self.problem.has_mode(v, k):
                out[v] = dict(ok=False, why=f'нужен другой транспорт: {o.need_text()}')
            else:
                b = self.best_insert(k, only=v)
                if not b:
                    out[v] = dict(ok=False, why='не успевает начать в окне при своём маршруте')
                    continue
                before = {st.order: st.start for st in self.schedule(v, self.routes[v]).stops} if self.routes[v] else {}
                after = self.schedule(v, b[2])
                shifted = sum(1 for st in after.stops if st.order in before and abs(st.start - before[st.order]) > 5)
                start = next(st.start for st in after.stops if st.order == k)
                added = after.km - (self.schedule(v, self.routes[v]).km if self.routes[v] else 0.0)
                out[v] = dict(ok=True, km=round(added - saved, 1), shifted=shifted, start=round(start, 1),
                              new_brigade=not self.routes[v])
        return out

    def _brigade(self, ref):
        v = next((i for i, b in enumerate(self.brigades) if b.name == ref or str(i + 1) == str(ref)), None)
        if v is None:
            raise InputError(f'нет бригады «{ref}»')
        return v

    def _place_accident(self, k):
        """Лучшая бригада и место для аварии; возвращает вытесненные заявки (их пристроят к другим бригадам)."""
        S = self.settings
        best = None
        for v in self.active():
            if not self.problem.can(v, k):
                continue
            route, f = self.routes[v], self.frozen_len[v]
            base = self.schedule(v, route).km if route else 0.0
            for pos in range(f, min(len(route), f + 3) + 1):
                cand, out = route[:pos] + [k] + route[pos:], []
                while True:
                    s = self.schedule(v, cand)
                    if s.feasible:
                        break
                    late = next((i for i, st in enumerate(s.stops) if i > pos and st.order not in self.pinned
                                 and (st.start > self.problem.latest(st.order, v) + 1e-6
                                      or st.start + self.problem.service(v, st.order) > self.brigades[v].shift[1] + 1e-6)), None)
                    if late is None:                       # опозданий нет — не хватает места обеду или запаса
                        tail = [i for i in range(len(cand) - 1, pos, -1) if cand[i] not in self.pinned]
                        if not tail or not self.schedule(v, cand[:pos + 1]).feasible:
                            cand = None                    # сама авария не успевает в своё окно
                            break
                        late = tail[0]                     # снимаем последнюю после аварии
                    out.append(cand.pop(late))
                if cand is None:
                    continue
                start = self.schedule(v, cand).stops[pos].start
                trial, self.routes[v] = copy.deepcopy(self.routes), cand
                lost = moved_km = 0.0
                for kk in out:
                    b = self.best_insert(kk)
                    if b:
                        self.routes[b[1]] = b[2]
                        moved_km += b[0]
                    else:
                        lost += PENALTY[self.orders[kk].priority]
                cost = (1e4 * lost + S.per_minute * (start - self.at) + S.per_change * len(out)
                        + (S.open_reserve if not route else 0.0) + self.schedule(v, cand).km - base + moved_km)
                self.routes = trial
                if best is None or cost < best[0]:
                    best = (cost, v, cand, out, start)
        if best is None:
            return []
        _, v, cand, out, start = best
        self.routes[v] = cand
        self.accident = dict(brigade=v, start=round(start, 1), wait=round(start - self.at, 1), displaced=len(out))
        return [(kk, f'вытеснена аварией {self.orders[k].id}') for kk in out]

    # ---- результат
    def _positions(self, routes, sched):
        return {s.order: (v, s.start) for v, r in enumerate(routes) if r for s in sched(v, r).stops}

    def _changes(self, before, after, pending, was_unassigned):
        why = dict(pending)
        out = []
        for k in sorted(set(before) | set(after) | set(why)):
            if k == self.cancelled:
                continue
            b, a = before.get(k), after.get(k)
            moved = self.rescheduled and k == self.rescheduled['order']
            if b and a and b[0] == a[0] and not (moved and abs(b[1] - a[1]) > 0.5):
                continue                              # та же бригада: время могло сдвинуться, это не изменение
            reason = why.get(k, '') or ('клиент перенёс окно' if moved else '')
            if b is None and a is not None and k in was_unassigned:
                reason = 'освободилось время'
            out.append(dict(k=k, was=None if b is None else dict(brigade=b[0], start=round(b[1], 1)),
                            now=None if a is None else dict(brigade=a[0], start=round(a[1], 1)), why=reason))
        return out

    def _event_info(self, ev):
        if isinstance(ev, Unavailable):
            v = self._brigade(ev.brigade)
            return dict(kind='unavailable', brigade=v, name=self.brigades[v].name)
        if isinstance(ev, Cancel):
            return dict(kind='cancel', order=self.cancelled)
        if isinstance(ev, Move):
            k, v = self.moved
            return dict(kind='move', order=k, brigade=v, name=self.brigades[v].name)
        if isinstance(ev, Reschedule):
            r = self.rescheduled
            return dict(kind='window', order=r['order'], was=r['was'], now=r['now'])
        if isinstance(ev, Breakdown):
            b = self.broken
            return dict(kind='breakdown', brigade=b['brigade'], name=self.brigades[b['brigade']].name, was=b['was'],
                        now=b['now'], now_name=MODE_NAMES[b['now']])
        if isinstance(ev, Finish):
            f = self.finished
            return dict(kind='finish', order=f['order'], brigade=f['brigade'], name=self.brigades[f['brigade']].name,
                        end=f['end'], planned=f['planned'], delta=f['delta'])
        o = self.orders[self.new_order]
        return dict(kind='urgent', order=self.new_order, type=o.type, addr=o.address)

    def result(self):
        P = self.problem
        doc = copy.deepcopy(self.doc)
        orders = doc['orders']
        for k in range(len(orders), len(self.orders)):
            orders.append(dict(docs.order_dict(P, k), new=True))
        sched = {v: self.schedule(v, r) for v, r in enumerate(self.routes) if r}
        for o in orders:
            o.update(plan=None, reason=None)
        if self.rescheduled:                               # новое окно — в документ, следующие события берут его
            k = self.rescheduled['order']
            orders[k].update(window=list(self.rescheduled['now']), a=P.orders[k].earliest, latest=P.latest(k))
        if self.broken:
            v = self.broken['brigade']
            doc['brigades'][v].update(mode=self.broken['now'], mode_name=MODE_NAMES[self.broken['now']],
                                      mode_was=self.broken['was'], carry=self.brigades[v].carry)
        for v in self.removed:
            doc['brigades'][v].setdefault('unavailable_from', self.at)
        for v, s in sched.items():
            for i, stop in enumerate(s.stops):
                docs.set_assignment(orders[stop.order], v, i + 1, stop)
        changed = {c['k']: c for c in self.changes}
        for k, c in changed.items():
            if c['now']:
                o, name = orders[k], self.brigades[c['now']['brigade']].name
                text = f"{name}: {c['why'] or 'перенесена'}; начало в {hhmm(c['now']['start'])}"
                if P.orders[k].skill == 2 and o.get('new'):
                    text += f", через {round(c['now']['start'] - self.at)} мин после появления"
                o['explanation'] = dict(summary=text, others=[], changed=True)
        for k, v in self.pinned.items():
            orders[k]['pinned'] = v
        unplaced = dict(self.unplaced)
        for k, o in enumerate(orders):
            if k == self.cancelled:
                o.update(cancelled=True, reason=dict(code='cancelled', text=f'Отменена в {hhmm(self.at)}'))
            elif o['plan'] is None and not o.get('cancelled') and (k in unplaced or o.get('new')):
                w = P.orders[k].window
                o['reason'] = dict(code='busy', text=f"{unplaced.get(k, 'новая заявка')}: ни одна подходящая бригада не успевает "
                                                     f"в окно {hhmm(w[0])}–{hhmm(w[1])}")
            elif o['plan'] is None and o['reason'] is None:
                o['reason'] = next((x['reason'] for x in self.doc['orders'] if x['k'] == k), None)
        unassigned = [k for k, o in enumerate(orders) if o['plan'] is None and not o.get('cancelled')]
        frozen = [r[:self.frozen_len[v]] for v, r in enumerate(self.original)]
        keep = {v: self.keep(v, r) for v, r in enumerate(self.routes)}
        origins = {v: o for v in self.where if (o := self.origin(v, keep[v]))}
        done = {v: min(keep[v], len(self.doc_stops[v])) for v in keep}      # остановки с временами из документа
        check = check_plan(P, self.routes, release=self.at, frozen=frozen, removed=self.removed, keep=keep, origins=origins,
                           done={v: (n, self.doc_stops[v][n - 1].end) for v, n in done.items() if n}, lunches=self.lunch0)
        for v, s in sched.items():                     # выполненное и начатое — с прежними временами
            for st, was in zip(s.stops[:self.frozen_len[v]], self.doc_stops[v]):
                if st.order != was.order or abs(st.start - was.start) > 1e-6:
                    check.errors.append(f'{self.brigades[v].name}: начатая заявка {self.orders[was.order].id} сдвинулась')
                    check.ok = False
        metrics = plan_metrics(P, self.routes, unassigned, sched)
        metrics['orders'] -= sum(1 for o in orders if o.get('cancelled'))
        metrics['served'] = sum(len(r) for r in self.routes)
        pm = self.doc['plan']['metrics']
        doc['plan'] = dict(routes=[docs.route_dict(P, v, s) for v, s in sched.items()], check=asdict(check), metrics=metrics)
        turned = []
        for rt in doc['plan']['routes']:
            v, w = rt['v'], self.where.get(rt['v'])
            if v not in origins:
                continue
            lat, lon = (w['lat'], w['lon']) if w['kind'] == 'transit' else (P.orders[w['toward']].lat, P.orders[w['toward']].lon)
            rt['via'] = dict(pos=w['pos'], lat=lat, lon=lon)          # отсюда бригада поехала по новому маршруту
            new = self.routes[v][w['pos']] if len(self.routes[v]) > w['pos'] else None
            turned.append(dict(brigade=v, kind=w['kind'], lat=lat, lon=lon, was=w['toward'], now=new))
        if self.accident:                              # по итоговому плану: вытесненная заявка могла встать перед
            k = self.new_order                         # аварией, а вернувшиеся к той же бригаде — не вытеснены
            st = next((st for v, s in sched.items() for st in s.stops if st.order == k), None)
            if st is not None:
                v = next(v for v, s in sched.items() if any(x.order == k for x in s.stops))
                self.accident.update(brigade=v, start=round(st.start, 1), wait=round(st.start - self.at, 1))
            self.accident['displaced'] = sum(1 for c in self.changes if c['why'].startswith('вытеснена аварией'))
        doc['event'] = dict(self.event, at=self.at, changes=self.changes, accident=self.accident, turned=turned,
                            before=dict(brigades_used=pm['brigades_used'], km_total=pm['km_total'], unassigned=pm['unassigned']),
                            fixed=sum(self.frozen_len.values()))
        doc['meta'] = dict(doc['meta'], replanned_at=self.at)
        for key in ('early', 'exact_time', 'quality', 'norms'):   # «что если» утреннего плана к новой версии не относятся
            doc.pop(key, None)
        return doc
