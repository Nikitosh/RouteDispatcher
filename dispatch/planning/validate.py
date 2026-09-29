"""Независимая проверка плана: сама пересчитывает расписание по матрицам задачи (не через Problem.schedule)."""
from dataclasses import dataclass, field

from ..config import SHIFT


@dataclass
class Check:
    ok: bool
    errors: list = field(default_factory=list)


def check_plan(problem, routes, release=None, frozen=None, removed=(), keep=None):
    """Навык и транспорт, начало внутри окна, конец до 22:00, каждая заявка не больше одного раза.
    Для перепланирования: frozen[v] — неизменная выполненная часть маршрута, removed — бригады, которые не могут
    получать новые заявки; release — момент события: с позиции keep[v] (первое отличие от прежнего маршрута) бригада
    выезжает не раньше него."""
    errors, seen = [], {}
    P = problem
    for v, route in enumerate(routes):
        if not route:
            continue
        b = P.brigades[v]
        T = P.time[b.mode]
        head = frozen[v] if frozen else []
        if route[:len(head)] != head:
            errors.append(f'{b.name}: изменена выполненная часть маршрута')
        if v in removed and len(route) > len(head):
            errors.append(f'{b.name} недоступна, но получила заявки')
        t, prev = 0.0, b.start
        for i, k in enumerate(route):
            o = P.orders[k]
            seen[k] = seen.get(k, 0) + 1
            if o.skill not in b.skills:
                errors.append(f'{b.name}: нет навыка для заявки {o.id}')
            if o.need and b.mode not in o.need:
                errors.append(f'{b.name}: транспорт не подходит для заявки {o.id}')
            first_new = keep[v] if keep else len(head)
            depart = max(t, release) if release is not None and i >= first_new else t
            start = max(depart + T[prev][P.n_starts + k], max(0, o.window[0]))
            if start > o.window[1] + 1e-6:
                errors.append(f'{o.id}: начало {start:.0f} позже окна')
            t = start + o.service
            if t > SHIFT + 1e-6:
                errors.append(f'{b.name}: работа после 22:00 (заявка {o.id})')
            prev = P.n_starts + k
    errors += [f'{P.orders[k].id}: назначена {c} раз' for k, c in seen.items() if c > 1]
    return Check(not errors, errors)
