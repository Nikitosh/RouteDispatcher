"""Метрики плана: две обязательные по ТЗ (исполнители, пробег по каждому и всего) и вспомогательные."""


def plan_metrics(problem, routes, unassigned, schedules=None):
    used = [v for v in range(len(problem.brigades)) if routes[v]]
    sched = schedules or {v: problem.schedule(v, routes[v]) for v in used}
    by = {problem.brigades[v].name: round(sched[v].km, 2) for v in used}
    delays = [s.start - problem.orders[s.order].earliest for v in used for s in sched[v].stops if problem.orders[s.order].skill == 2]
    return dict(brigades_used=len(used), brigades_total=len(problem.brigades), km_total=round(sum(by.values()), 2),
                km_by_brigade=by, served=len(problem.orders) - len(unassigned), orders=len(problem.orders),
                unassigned=len(unassigned),
                accident_delay_avg=round(sum(delays) / len(delays), 1) if delays else None,
                accident_delay_max=round(max(delays), 1) if delays else None)
