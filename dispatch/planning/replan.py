"""Перестройка плана после события в течение дня (ответы жюри, assumptions.md 2.7).

- Выполненное и начатое к моменту события не меняется; начатую работу не прерывают.
- Бригада продолжает свой прежний маршрут, пока он совпадает с новым; с первой изменённой заявки она выезжает не
  раньше момента события.
- Новая обычная заявка встаёт в свободный промежуток: порядок остальных не меняется, они только сдвигаются во времени
  внутри окон. Нет места — резервная бригада, иначе заявка остаётся неназначенной с причиной.
- Авария в течение дня может перестроить остаток дня. Окно аварии — от момента появления до +2 часов. Перебираем
  бригады и первые места их неначатой части; заявки, которые после вставки перестали успевать, переносим к другим
  бригадам. Цена: минута ожидания аварии, перенос заявки, вывод резервной бригады и километры (ReplanSettings).
  Штраф за ожидание — только для таких аварий: аварии из утреннего файла планируются по своему окну.
- Недоступный инженер: его неначатые заявки переходят к другим бригадам, затем к резерву.
- Отмена: заявка снимается, освободившееся время отдаётся неназначенным заявкам.
"""
import copy
from dataclasses import asdict, dataclass
from typing import Optional

from ..config import PENALTY, PRIORITY, SERVICE, SHIFT, SKILL, TYPE_NAMES, ReplanSettings, TravelSettings
from ..files import document as docs
from ..files.readers import InputError, parse_minutes, parse_modes
from ..formatting import hhmm
from ..geo.geocoder import Geocoder
from ..geo.matrices import MatrixBuilder
from ..model.domain import Order
from ..model.problem import Problem
from .metrics import plan_metrics
from .validate import check_plan

BK_BY_TYPE = {v: k for k, v in TYPE_NAMES.items()}   # «Авария» → «Глобальная проблема»


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


def order_type(text):
    t = text.strip().lower()
    for name in BK_BY_TYPE:
        if name.lower().startswith(t[:4]):
            return name
    raise InputError(f'неизвестный тип заявки «{text}» (авария, подключение, ремонт, дозаказ)')


class Replanner:
    def __init__(self, doc, matrices=None, geocoder=None, travel=None, settings=None):
        self.doc = doc
        self.orders = docs.orders_from(doc)
        self.brigades, self.starts = docs.brigades_from(doc)
        self.routes = [[] for _ in self.brigades]
        for rt in doc['plan']['routes']:
            self.routes[rt['v']] = [s['k'] for s in rt['stops']]
        self.original = copy.deepcopy(self.routes)
        self.matrices = matrices or MatrixBuilder()
        self.geocoder = geocoder or Geocoder()
        m = doc['meta']
        self.travel = travel or TravelSettings(m.get('traffic') or {}, m.get('overhead') or {})
        self.settings = settings or ReplanSettings()
        self.removed = set()
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
        return self.problem.schedule(v, route, release=self.at, keep=self.keep(v, route))

    def frozen(self, v):
        """Сколько первых заявок бригады выполнено или начато к моменту события."""
        if not self.original[v]:
            return 0
        return sum(1 for s in self.problem.schedule(v, self.original[v]).stops if s.start < self.at)

    def active(self):
        return [v for v in range(len(self.brigades)) if v not in self.removed]

    def best_insert(self, k):
        """Самая дешёвая по км вставка без изменения порядка остальных заявок; резерв — в последнюю очередь.
        (цена, бригада, маршрут) или None."""
        best = None
        for v in self.active():
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
        lat, lon = self.geocoder.locate([ev.address], log=lambda *_: None)[ev.address]
        if ev.window:
            a, b = (parse_minutes(x, '--window') for x in ev.window.split('-'))
        else:
            a, b = self.at, SHIFT
        if bk == 'Глобальная проблема':
            a, b = max(a, self.at), min(b, self.at + self.settings.react, SHIFT)
        o = Order(id=ev.id or f'N{len(self.orders) + 1}', bk=bk, type=TYPE_NAMES[bk], window=(max(a, self.at), b),
                  service=SERVICE[bk], priority=PRIORITY[bk], skill=SKILL[bk], district=ev.district, address=ev.address,
                  hd='срочная', need=parse_modes(ev.need, '--need') if ev.need else None, lat=lat, lon=lon)
        self.orders.append(o)
        return len(self.orders) - 1

    def _build(self):
        points = self.starts + [(o.lat, o.lon) for o in self.orders]
        self.problem = Problem(self.doc['meta']['name'], self.orders, self.brigades, self.starts,
                               self.matrices.build(points, len(self.starts), self.travel))

    def apply(self, at, event):
        self.at = parse_minutes(at, '--at') if isinstance(at, str) else at
        if isinstance(event, Urgent):
            self.new_order = self._add_order(event)
        self._build()
        before = self._positions(self.original, lambda v, r: self.problem.schedule(v, r))
        self.frozen_len = {v: self.frozen(v) for v in range(len(self.brigades))}
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
            v = next((i for i, r in enumerate(self.routes) if k in r), None)
            if v is not None:
                if self.routes[v].index(k) < self.frozen_len[v]:
                    raise InputError(f'заявка {event.order} уже выполнена или начата к {hhmm(self.at)}')
                self.routes[v].remove(k)
            self.cancelled = k
        elif isinstance(event, Urgent):
            k = self.new_order
            if self.orders[k].skill == 2:
                pending += self._place_accident(k)
            pending.append((k, 'новая авария' if self.orders[k].skill == 2 else 'новая заявка'))
        self.unplaced = []
        for k, why in sorted(pending, key=lambda x: (self.orders[x[0]].priority, self.problem.latest(x[0]))):
            if any(k in r for r in self.routes):
                continue
            b = self.best_insert(k)
            if b:
                self.routes[b[1]] = b[2]
            else:
                self.unplaced.append((k, why))
        was_unassigned = [o['k'] for o in self.doc['orders'] if o['plan'] is None and not o.get('cancelled')]
        for k in was_unassigned:
            b = self.best_insert(k)
            if b:
                self.routes[b[1]] = b[2]
        after = self._positions(self.routes, self.schedule)
        self.changes = self._changes(before, after, pending, was_unassigned)
        self.event = self._event_info(event)
        return self.result()

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
                    late = next((i for i, st in enumerate(s.stops) if i > pos and st.start > self.problem.latest(st.order) + 1e-6), None)
                    if late is None:
                        cand = None                        # сама авария не успевает в своё окно
                        break
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
        self.accident = dict(brigade=v, start=start, wait=start - self.at, displaced=len(out))
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
            if b and a and b[0] == a[0]:
                continue                              # та же бригада: время могло сдвинуться, это не изменение
            reason = why.get(k, '')
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
        unplaced = dict(self.unplaced)
        for k, o in enumerate(orders):
            if k == self.cancelled:
                o.update(cancelled=True, reason=dict(code='cancelled', text=f'Отменена в {hhmm(self.at)}'))
            elif o['plan'] is None and (k in unplaced or o.get('new')):
                w = P.orders[k].window
                o['reason'] = dict(code='busy', text=f"{unplaced.get(k, 'новая заявка')}: ни одна подходящая бригада не успевает "
                                                     f"в окно {hhmm(w[0])}–{hhmm(w[1])}")
            elif o['plan'] is None and o['reason'] is None:
                o['reason'] = next((x['reason'] for x in self.doc['orders'] if x['k'] == k), None)
        unassigned = [k for k, o in enumerate(orders) if o['plan'] is None and not o.get('cancelled')]
        frozen = [r[:self.frozen_len[v]] for v, r in enumerate(self.original)]
        check = check_plan(P, self.routes, release=self.at, frozen=frozen, removed=self.removed,
                           keep={v: self.keep(v, r) for v, r in enumerate(self.routes)})
        metrics = plan_metrics(P, self.routes, unassigned, sched)
        metrics['orders'] -= self.cancelled is not None
        metrics['served'] = sum(len(r) for r in self.routes)
        pm = self.doc['plan']['metrics']
        doc['plan'] = dict(routes=[docs.route_dict(P, v, s) for v, s in sched.items()], check=asdict(check), metrics=metrics)
        doc['event'] = dict(self.event, at=self.at, changes=self.changes, accident=self.accident,
                            before=dict(brigades_used=pm['brigades_used'], km_total=pm['km_total'], unassigned=pm['unassigned']),
                            fixed=sum(self.frozen_len.values()))
        doc['meta'] = dict(doc['meta'], replanned_at=self.at)
        return doc
