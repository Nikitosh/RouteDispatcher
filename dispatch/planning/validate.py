"""Независимая проверка плана: сама пересчитывает расписание по матрицам задачи (не через Problem.schedule).
Длительность работы — с темпом бригады: ⌊длительность·темп + 0,5⌋ (domain.work_minutes)."""
from dataclasses import dataclass, field

from ..config import EQUIPMENT, EQUIPMENT_NAMES, LUNCH, LUNCH_MIN_SHIFT, LUNCH_TOL
from ..model.domain import work_minutes


@dataclass
class Check:
    ok: bool
    errors: list = field(default_factory=list)


def _lunch_needed(b):
    return b.lunch and b.shift[1] - b.shift[0] >= LUNCH_MIN_SHIFT and b.shift[0] <= LUNCH[1]


def _equipment_errors(P, v, route):
    b = P.brigades[v]
    need = {i: sum(P.orders[k].demand.get(i, 0) for k in route) for i in EQUIPMENT}
    out = []
    if b.stock is not None:
        out += [f'{b.name}: нет оборудования: {EQUIPMENT_NAMES[i]} — по маршруту {need[i]}, выдано {b.stock.get(i, 0)}'
                for i in EQUIPMENT if need[i] > b.stock.get(i, 0)]
    if b.carry is not None and sum(need.values()) > b.carry:
        out.append(f'{b.name}: нет оборудования: по маршруту {sum(need.values())} ед., унести можно {b.carry}')
    return out


def check_plan(problem, routes, release=None, frozen=None, removed=(), keep=None, origins=None, done=None, lunches=None):
    """Навык и транспорт, начало внутри окна, конец до 22:00, обед, оборудование, каждая заявка не больше одного раза.
    Для перепланирования: frozen[v] — неизменная выполненная часть маршрута, removed — бригады, которые не могут
    получать новые заявки; release — момент события: с позиции keep[v] (первое отличие от прежнего маршрута) бригада
    выезжает не раньше него; origins[v] = (точка, момент, км) — если бригада была в пути, к первой новой заявке она
    едет из точки на дороге, а не от прежнего адреса; done[v] = (n, конец n-й) — первые n остановок неизменны и уже
    проверены (их времена из документа), проверку маршрута начинаем после них; lunches[v] = (начало, после скольких
    остановок) — обед прежнего плана: он уже взят, если приходится на первые n остановок или начался до release; если
    бригада свободна для новой части маршрута только после 15:15, а обеда не было, он не нужен.

    Обед: 45 мин, начало с 13:00 до 15:15, при смене от 6 часов; место — между работами (на месте следующей по
    приезде, иначе перед выездом) или после последней. Для каждого промежутка считается, как сдвинутся следующие
    работы; маршрут допустим, если хоть один промежуток подходит."""
    errors, seen = [], {}
    P = problem
    la, lb, ld = LUNCH
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
        errors += _equipment_errors(P, v, route)
        t, prev, first = float(b.shift[0]), b.start, 0
        if done and v in done:
            first, t = done[v]
            prev = P.n_starts + route[first - 1]
            for k in route[:first]:
                seen[k] = seen.get(k, 0) + 1
        need_lunch, lunch_end = _lunch_needed(b), None
        old = lunches.get(v) if lunches else None
        if need_lunch and old is not None and (old[1] < first or (release is not None and old[0] < release and old[0] >= t - 1e-6)):
            need_lunch = False
            if old[1] == first:
                lunch_end = old[0] + ld
        if need_lunch and max(t, release if release is not None else t) > lb + LUNCH_TOL:
            need_lunch = False                     # всё окно обеда занята или ждала заявок
        first_new = keep[v] if keep else len(head)
        legs, t0 = [], t
        for i, k in enumerate(route):
            if i < first:
                continue
            o = P.orders[k]
            seen[k] = seen.get(k, 0) + 1
            if o.skill not in b.skills:
                errors.append(f'{b.name}: нет навыка для заявки {o.id}')
            if o.need and b.mode not in o.need:
                errors.append(f'{b.name}: транспорт не подходит для заявки {o.id}')
            p = prev
            if origins and v in origins and i == first_new:
                p = origins[v][0]
            legs.append((i, k, p))

            def depart(free, i=i, v=v, first_new=first_new, lunch_end=lunch_end, first=first):
                d = max(free, release) if release is not None and i >= first_new else free
                if origins and v in origins and i == first_new:
                    d = max(origins[v][1], release or 0.0)
                if lunch_end is not None and i == first:
                    d = max(d, lunch_end)
                return d
            legs[-1] += (depart,)
            start = max(depart(t) + T[p][P.n_starts + k], max(0, o.window[0]))
            if start > o.window[1] + 1e-6:
                errors.append(f'{o.id}: начало {start:.0f} позже окна')
            t = start + work_minutes(o.service, b.speed)
            if t > b.shift[1] + 1e-6:
                errors.append(f'{b.name}: работа после конца смены {b.shift_text} (заявка {o.id})')
            prev = P.n_starts + k
        if need_lunch and not _lunch_fits(P, b, legs, t0, t, (la, lb, ld)):
            errors.append(f'{b.name}: нет места для обеда (45 мин с началом между 13:00 и 15:15)')
    errors += [f'{P.orders[k].id}: назначена {c} раз' for k, c in seen.items() if c > 1]
    return Check(not errors, errors)


def _lunch_fits(P, b, legs, t0, end_a, window):
    """Есть ли промежуток для обеда. legs — (позиция, заявка, откуда едет, правило выезда) после выполненной части,
    t0 — когда бригада свободна перед первой из них, end_a — конец последней работы без обеда. Для каждого
    промежутка g: работы до него — без обеда, в нём обед, после — со сдвигом."""
    la, lb, ld = window
    ls = max(end_a, la)
    if ls <= lb + LUNCH_TOL and ls + ld <= b.shift[1] + 1e-6:
        return True                                # после последней работы
    T = P.time[b.mode]
    off = getattr(P, 'offset', None) or []
    for g in range(len(legs)):
        t, ok = t0, True
        for j, (i, k, p, depart) in enumerate(legs):
            o = P.orders[k]
            a = max(0, o.window[0])
            d = depart(t)
            arrive = d + T[p][P.n_starts + k]
            start = max(arrive, a)
            if j == g:
                free = d + (off[p] if p < len(off) else 0.0)
                ls = max(arrive, la)               # на месте, по приезде
                if ls <= lb + LUNCH_TOL:
                    start = max(ls + ld, a)
                elif max(free, la) <= lb + LUNCH_TOL:   # перед выездом
                    start = max(arrive + (max(free, la) + ld - free), a)
                else:
                    ok = False
            svc = work_minutes(o.service, b.speed)
            if not ok or start > o.window[1] + 1e-6 or start + svc > b.shift[1] + 1e-6:
                ok = False
                break
            t = start + svc
        if ok:
            return True
    return False
