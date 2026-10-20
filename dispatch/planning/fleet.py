"""Сколько и каких бригад не хватает, чтобы закрыть невлезшие заявки.

Перебираем «кандидата в бригаду»: транспорт × стартовая точка (офис или дом одной из бригад), со всеми навыками и
сменой 10:00–22:00, и жадно набираем ему маршрут из невлезших заявок — самая дешёвая по км выполнимая вставка, пока
что-то вставляется. Берём кандидата, который закрывает больше всего (аварии важнее подключений, подключения — ремонтов,
при равенстве — меньше км), и повторяем с оставшимися заявками. У ответа навыки — только те, что нужны его заявкам,
а смена — 5/2 (10:00–19:00 или 13:00–22:00), если её хватает.
Это оценка сверху: один план с новыми бригадами решатель мог бы составить и плотнее.
"""
from ..config import CARRY, MODE_NAMES, MODES, PENALTY, SHIFT, SKILL_NAMES
from ..files.readers import SHIFTS_5_2
from ..formatting import hhmm
from ..model.domain import Brigade
from ..model.problem import Problem


SHORT_SHIFTS = SHIFTS_5_2                       # 10:00–19:00 и 13:00–22:00


def _route_for(problem, v, todo):
    """Жадный маршрут бригады v из заявок todo: (маршрут, км)."""
    route, km = [], 0.0
    left = [k for k in todo if problem.can(v, k)]
    while left:
        best = None
        for k in left:
            for pos in range(len(route) + 1):
                cand = route[:pos] + [k] + route[pos:]
                s = problem.schedule(v, cand)
                if s.feasible and (best is None or s.km < best[0]):
                    best = (s.km, cand, k)
        if best is None:
            break
        km, route = best[0], best[1]
        left.remove(best[2])
    return route, km


def _start_name(problem, s, office):
    if s == 0:
        return office
    b = next((b for b in problem.brigades if b.start == s), None)
    if not b:
        return f'точка {s}'
    return 'от дома ' + (b.name.replace('Бригада ', 'Бригады ', 1) if b.name.startswith('Бригада ') else f'«{b.name}»')


def shortage(problem, unassigned, office='офис', max_new=5):
    """[{mode, mode_name, skills, start, shift, orders, km}] — по бригаде на каждую добавку, пока невлезшие заявки
    закрываются; в конце, если что-то не закрыть никакой бригадой, — {orders, mode: None}."""
    todo, out = list(unassigned), []
    while todo and len(out) < max_new:
        best = None
        for mode in MODES:
            for s in range(problem.n_starts):
                extra = Brigade(f'Новая {len(out) + 1}', mode, list(range(len(SKILL_NAMES))), start=s, shift=(0, SHIFT), carry=CARRY[mode])
                p = Problem(problem.name, problem.orders, problem.brigades + [extra], problem.starts, problem.matrices)
                route, km = _route_for(p, len(problem.brigades), todo)
                if not route:
                    continue
                score = (sum(PENALTY[problem.orders[k].priority] for k in route), -km)
                if best is None or score > best[0]:
                    best = (score, mode, s, route, km)
        if best is None:
            break
        _, mode, s, route, km = best
        skills = sorted({problem.orders[k].skill for k in route})
        shift = (0, SHIFT)
        for short in SHORT_SHIFTS:                     # хватит ли смены 5/2 — такую бригаду найти проще
            extra = Brigade('Новая', mode, skills, start=s, shift=short, carry=CARRY[mode])
            p = Problem(problem.name, problem.orders, problem.brigades + [extra], problem.starts, problem.matrices)
            if p.schedule(len(problem.brigades), route).feasible:
                shift = short
                break
        out.append(dict(mode=mode, mode_name=MODE_NAMES[mode], skills=[SKILL_NAMES[x] for x in skills],
                        start=_start_name(problem, s, office), shift=f'{hhmm(shift[0])}–{hhmm(shift[1])}',
                        orders=[problem.orders[k].id for k in route], km=round(km, 1)))
        todo = [k for k in todo if k not in route]
    if todo:
        out.append(dict(mode=None, orders=[problem.orders[k].id for k in todo]))
    return out


def text(items):
    """Строки для отчёта: «+1 бригада: Авто; Аварии; старт — офис → закроет 74198, 57299 (12,4 км)»."""
    lines = []
    for i, x in enumerate(items, 1):
        if x['mode'] is None:
            lines.append(f"не закрыть добавкой бригад: {', '.join(x['orders'])}")
        else:
            lines.append(f"+{i} бригада: {x['mode_name']}; {', '.join(x['skills'])}; старт — {x['start']}; {x['shift']} → "
                         f"закроет {', '.join(x['orders'])} ({str(x['km']).replace('.', ',')} км)")
    return lines


# ---- кривая «бригады против км» и цена ограничений: прогоны решателя на изменённой задаче
def curve(solver, problem, used, seconds=3.0, seed=1, workdir=None, span=3):
    """[{brigades: K, served, km}] для K от used − span до used + span: решатель с пределом K бригад и нулевой ценой
    бригады — сначала все заявки, сколько влезет, потом минимум км. Ниже минимума видно, сколько заявок не закрыть,
    выше — насколько короче пробег, если вывести больше людей."""
    import os
    out = []
    for k in range(max(1, used - span), min(len(problem.brigades), used + span) + 1):
        routes = solver.plan_only(problem, seconds, seed, os.path.join(workdir, f'curve_{k}') if workdir else None,
                                  maxv=k, wveh=0)
        out.append(dict(brigades=sum(1 for r in routes if r), limit=k, served=sum(len(r) for r in routes),
                        km=round(sum(problem.km(v, r) for v, r in enumerate(routes)), 1)))
    return out


def _variants(problem):
    """(название, бригады, заявки) — задача без одного из правил; только правила, которые в задаче что-то значат."""
    import copy
    import dataclasses
    B, O = problem.brigades, problem.orders
    def brigades(**kw):
        out = []
        for b in B:
            b = copy.copy(b)
            for k, v in kw.items():
                setattr(b, k, v(b) if callable(v) else v)
            out.append(b)
        return out
    out = []
    if any(len(b.skills) < len(SKILL_NAMES) for b in B):
        out.append(('все умеют всё', brigades(skills=list(range(len(SKILL_NAMES)))), O))
    if any(b.mode != 'car' for b in B):
        out.append(('все на машинах', brigades(mode='car', carry=CARRY['car']), O))
    if any(o.need for o in O):
        out.append(('без требований к транспорту', B, [dataclasses.replace(o, need=None) for o in O]))
    if any(b.shift != (0, SHIFT) for b in B):
        out.append(('все смены 10:00–22:00', brigades(shift=(0, SHIFT)), O))
    if any(getattr(b, 'lunch', False) for b in B):
        out.append(('без обеда', brigades(lunch=False), O))
    if any(o.demand for o in O):
        out.append(('без учёта оборудования', B, [dataclasses.replace(o, equipment={}) for o in O]))
    return out


def price(solver, problem, seconds=3.0, seed=1, workdir=None):
    """Цена ограничений: [{what, brigades, km, served, bound}] — план без одного правила за раз. bound — доказанный
    минимум бригад для такой задачи (нижняя граница), если сошлась."""
    import os
    out = []
    for i, (what, brigades, orders) in enumerate(_variants(problem)):
        p = Problem(problem.name, orders, brigades, problem.starts, problem.matrices)
        ours, _, _ = solver.solve(p, seconds, seed, os.path.join(workdir, f'price_{i}') if workdir else None)
        out.append(dict(what=what, brigades=sum(1 for r in ours.routes if r), served=sum(len(r) for r in ours.routes),
                        km=round(sum(p.km(v, r) for v, r in enumerate(ours.routes)), 1), bound=ours.lower_bound))
    return out
