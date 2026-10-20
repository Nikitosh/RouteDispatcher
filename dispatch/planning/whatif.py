"""Два «что если» к плану дня — дополнительные прогоны решателя на изменённой копии задачи.

Ранний старт по согласованию (plan --early). Билайн на сессии вопросов: «прибыв на адрес раньше, старший инженер
выполняет заявку раньше по согласованию с клиентом». В плане такие места видны как ожидание у клиента. Считаем план,
где начать можно до EARLY_MIN минут раньше окна (аварии не трогаем): сколько бригад и км нужно, если клиенты
согласятся, и кому звонить — заявкам, которые в этом плане начинаются раньше своего окна.

Цена точного времени (plan --windows). Клиент выбирает часовое окно вместо двухчасового — платная опция «точное
время». Каждому из выбранных клиентов достаётся одна из половин его окна (по номеру заявки, одинаково при каждом
запуске); считаем бригады и км для разной доли таких клиентов.

Решатель эвристический, поэтому отдельный прогон варианта может оказаться хуже уже известного плана. План с более
узкими окнами годится и для более широких (доли вложены: клиент, получивший часовое окно при 25%, получает то же окно
при 50% и 100%), а основной план годится и для раннего старта, и для 0% — из подходящих берём лучший. Так 0% совпадает
с основным планом, а ранний старт, который ничего не даёт, помечается useful=False.
"""
import dataclasses
import os
import zlib

from ..config import PENALTY
from ..model.problem import Problem

EARLY_MIN = 60
SHARES = (0.0, 0.25, 0.5, 1.0)


def _variant(problem, orders):
    return Problem(problem.name, orders, problem.brigades, problem.starts, problem.matrices)


def _score(problem, routes):
    """(штраф невыполненных, бригады, км) — порядок целей плана; None, если маршруты в этой задаче недопустимы."""
    if not all(problem.schedule(v, r).feasible for v, r in enumerate(routes) if r):
        return None
    served = {k for r in routes for k in r}
    return (sum(PENALTY[o.priority] for k, o in enumerate(problem.orders) if k not in served), sum(1 for r in routes if r),
            round(sum(problem.km(v, r) for v, r in enumerate(routes)), 1))


def _best(problem, *candidates):
    """Лучший из допустимых в problem; если допустимых нет — первый (ответ решателя на эту задачу)."""
    scored = [(sc, r) for r in candidates if r is not None for sc in [_score(problem, r)] if sc is not None]
    return min(scored, key=lambda x: x[0])[1] if scored else candidates[0]


def _summary(problem, routes):
    used = [r for r in routes if r]
    return dict(brigades=len(used), km=round(sum(problem.km(v, r) for v, r in enumerate(routes)), 1),
                served=sum(len(r) for r in routes))


def early_start(solver, problem, seconds, seed, workdir, minutes=EARLY_MIN, current=None):
    """current — маршруты основного плана (в нумерации problem): они годятся и здесь, берётся лучший."""
    orders = [o if o.skill == 2 or o.window[0] <= 0 else dataclasses.replace(o, window=(max(0, o.window[0] - minutes), o.window[1]))
              for o in problem.orders]
    P = _variant(problem, orders)
    routes = _best(P, solver.plan_only(P, seconds, seed, os.path.join(workdir, 'early')), current)
    base = _score(problem, current) if current is not None else None
    useful = base is None or _score(P, routes) < base[:2] + (base[2] - 0.5,)   # меньше штраф, бригад или км
    calls = []
    for v, r in enumerate(routes):
        for st in (P.schedule(v, r).stops if r else ()):
            o = problem.orders[st.order]
            if o.window[0] - st.start >= 0.5:          # меньше полуминуты раньше — это и есть начало окна
                calls.append(dict(k=st.order, id=o.id, brigade=problem.brigades[v].name, window=list(o.window),
                                  start=round(st.start, 1), earlier=round(o.window[0] - st.start)))
    return dict(_summary(P, routes), minutes=minutes, calls=sorted(calls, key=lambda c: c['start']) if useful else [],
                useful=useful)


def narrow(order, share):
    """Часовое окно вместо двухчасового у доли share клиентов (аварии и окна не по 2 часа — без изменений)."""
    a, b = order.window
    h = zlib.crc32(order.id.encode())
    if order.skill == 2 or b - a != 120 or (h % 1000) / 1000 >= share:
        return order
    first = (h >> 10) % 2 == 0
    return dataclasses.replace(order, window=(a, a + 60) if first else (a + 60, b))


def exact_time(solver, problem, seconds, seed, workdir, shares=SHARES, current=None):
    """current — маршруты основного плана: годятся для доли 0."""
    runs = []
    for share in shares:
        orders = [narrow(o, share) for o in problem.orders]
        P = _variant(problem, orders)
        runs.append((share, orders, P, solver.plan_only(P, seconds, seed, os.path.join(workdir, f'windows{int(share * 100)}'))))
    out, better = [], []
    for share, orders, P, routes in sorted(runs, key=lambda x: -x[0]):     # от узких окон к широким
        better.append(routes)
        best = _best(P, *better, current if share == 0 else None)
        better[-1] = best
        out.append(dict(_summary(P, best), share=share, narrowed=sum(1 for a, b in zip(orders, problem.orders) if a is not b),
                        orders=len(orders)))
    return sorted(out, key=lambda x: x['share'])
