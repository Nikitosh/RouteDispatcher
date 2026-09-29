"""Объяснения для диспетчера.

Причина неназначения проверяется по шагам ТЗ: навык → транспорт → смена → успевает ли бригада из стартовой точки →
заняты ли подходящие бригады. «Почему эта бригада»: для каждой другой бригады заявка пробуется на лучшее место её
маршрута.
"""
from dataclasses import dataclass, field

from ..config import SKILL_NAMES
from ..formatting import hhmm, km_text


@dataclass
class Reason:
    code: str        # skill, transport, shift, window, free, busy
    text: str


@dataclass
class Explanation:
    brigade: int
    added_km: float
    summary: str
    others: list = field(default_factory=list)    # [{code, brigades, names, text}]


def _plural(items, one, many):
    return one if len(items) == 1 else many


class Explainer:
    def __init__(self, problem):
        self.p = problem

    def names(self, vs):
        return ', '.join(self.p.brigades[v].name for v in vs)

    def who(self, vs):
        return self.names(vs) if len(vs) <= 4 else f'все {len(vs)}'

    def window(self, k):
        w = self.p.orders[k].window
        return f'{hhmm(w[0])}–{hhmm(w[1])}'

    def best_insertion(self, v, route, k):
        """Минимальный прирост км при вставке k в маршрут v или None, если ни одно место не подходит."""
        base = self.p.km(v, route)
        best = None
        for pos in range(len(route) + 1):
            s = self.p.schedule(v, route[:pos] + [k] + route[pos:])
            if s.feasible and (best is None or s.km - base < best):
                best = s.km - base
        return best

    # ---- неназначенные
    def static_reason(self, k):
        """Причины, не зависящие от плана; None — заявку в принципе может взять хотя бы одна бригада."""
        p, o = self.p, self.p.orders[k]
        V = range(len(p.brigades))
        with_skill = [v for v in V if p.has_skill(v, k)]
        skill = SKILL_NAMES[o.skill]
        if not with_skill:
            return Reason('skill', f'Нет исполнителя с навыком «{skill}»')
        suitable = [v for v in with_skill if p.has_mode(v, k)]
        if not suitable:
            return Reason('transport', f'Нет исполнителя с навыком «{skill}» и транспортом «{o.need_text()}»')
        if p.latest(k) < o.earliest:
            return Reason('shift', f'Работа ({o.service} мин) не помещается в смену до 22:00 при окне {self.window(k)}')
        if not any(p.schedule(v, [k]).feasible for v in suitable):
            first = min(max(p.schedule(v, [k]).stops[0].arrive, o.earliest) for v in suitable)
            return Reason('window', f'Не успеть к окну {self.window(k)}: даже из стартовой точки раньше всех начать можно в {hhmm(first)}')
        return None

    def unassigned(self, routes, k):
        """Причина, по которой заявка k не попала в план routes."""
        r = self.static_reason(k)
        if r:
            return r
        p = self.p
        suitable = [v for v in range(len(p.brigades)) if p.can(v, k)]
        for v in suitable:
            if not p.schedule(v, [k]).feasible:
                continue
            if not routes[v]:
                return Reason('free', f'Могла бы взять свободная бригада {p.brigades[v].name} (план можно улучшить)')
            if self.best_insertion(v, routes[v], k) is not None:
                return Reason('free', f'Помещается в маршрут бригады {p.brigades[v].name} (план можно улучшить)')
        return Reason('busy', f'Все подходящие исполнители ({self.who(suitable)}) заняты в окно {self.window(k)}')

    def greedy_unassigned(self, routes, k):
        """Причина для базового варианта ТЗ: заявки по порядку, каждая в конец маршрута первой подходящей бригады.
        routes — маршруты к очереди заявки k (только заявки с меньшими номерами)."""
        r = self.static_reason(k)
        if r:
            return r
        suitable = [v for v in range(len(self.p.brigades)) if self.p.can(v, k)]
        return Reason('busy', f'К её очереди у всех подходящих исполнителей ({self.who(suitable)}) маршрут уже заполнен: '
                              f'дописать в конец и начать в окне {self.window(k)} не успевает никто')

    # ---- назначенные
    def assigned(self, routes, k):
        p = self.p
        v0 = next(v for v, r in enumerate(routes) if k in r)
        route = routes[v0]
        i = route.index(k)
        stop = p.schedule(v0, route).stops[i]
        o = p.orders[k]
        added = p.km(v0, route) - p.km(v0, route[:i] + route[i + 1:])
        groups = {'ok': [], 'time': [], 'idle': [], 'skill': [], 'transport': []}
        for v in range(len(p.brigades)):
            if v == v0:
                continue
            if not p.has_skill(v, k):
                groups['skill'].append(v)
            elif not p.has_mode(v, k):
                groups['transport'].append(v)
            elif not routes[v]:
                (groups['idle'] if p.schedule(v, [k]).feasible else groups['time']).append(v)
            else:
                d = self.best_insertion(v, routes[v], k)
                if d is None:
                    groups['time'].append(v)
                else:
                    groups['ok'].append((v, d))
        if not groups['ok'] and not groups['idle']:
            why = 'единственная бригада, которая успевает начать в окне'
        elif not groups['ok']:
            why = 'остальные подходящие бригады свободны, но их вывод — ещё одна бригада в работе'
        elif added < 0.05:
            why = 'адрес по пути, пробег почти не растёт'
        elif any(d < added - 0.05 for _, d in groups['ok']):
            why = f'добавляет {km_text(added)} км; так общий план выходит короче'
        else:
            why = f'добавляет к маршруту {km_text(added)} км — меньше, чем другие бригады, которые успевают'
        when = (f'приезд {hhmm(stop.arrive)}, ждёт начала окна, начало {hhmm(stop.start)}' if stop.start > stop.arrive + 1
                else f'начало в {hhmm(stop.start)}')
        others = []
        if groups['ok']:
            ok = sorted(groups['ok'], key=lambda x: x[1])
            detail = f'+{km_text(ok[0][1])} км' if len(ok) == 1 else ', '.join(f'{p.brigades[v].name} +{km_text(d)} км' for v, d in ok)
            others.append(('ok', [v for v, _ in ok], f"{_plural(ok, 'успевает', 'успевают')}, но {detail}"))
        if groups['time']:
            g = groups['time']
            others.append(('time', g, f"навык есть, но при своём маршруте {_plural(g, 'не успевает', 'не успевают')} начать в окне {self.window(k)}"))
        if groups['idle']:
            g = groups['idle']
            others.append(('idle', g, f"{_plural(g, 'свободна', 'свободны')}, но это ещё одна бригада в работе"))
        if groups['transport']:
            others.append(('transport', groups['transport'], f'нужен транспорт «{o.need_text()}»'))
        if groups['skill']:
            others.append(('skill', groups['skill'], f'нет навыка «{SKILL_NAMES[o.skill]}»'))
        return Explanation(v0, added, f'{p.brigades[v0].name}: {why}; {when}.',
                           [dict(code=c, brigades=g, names=self.names(g), text=t) for c, g, t in others])
