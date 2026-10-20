"""Режим «лучшие монтажники» и цена качества.

Жюри: распределение с приоритетом монтажников с наиболее высоким рейтингом, число монтажников условно неограниченно.
Монтажники парка упорядочены по рейтингу (по видам работ дня с весами их долей). Ищем наименьшее K, при котором
лучшие K монтажников выполняют все заявки дня: решатель на подзадаче из первых K, двоичный поиск по K. Если нижняя
граница числа бригад для найденного плана равна K, то лучших K − 1 не хватило бы — это доказано.

Цена качества — та же задача для K от найденного до всего парка: чем шире выбор, тем меньше бригад и км, но ниже
средний рейтинг исполнителя на заявке. Последняя точка (весь парк) — режим «экономия».
"""
import os

from ..config import SKILL as SKILL_OF
from ..model.problem import Problem


def mix(problem):
    n = len(problem.orders)
    return {s: sum(1 for o in problem.orders if o.skill == s) / n if n else 1 / 3 for s in range(3)}


def ranked(problem, ratings):
    """Номера бригад парка по убыванию рейтинга."""
    m = mix(problem)
    return sorted(range(len(problem.brigades)), key=lambda v: -ratings[problem.brigades[v].name].overall(m))


def subproblem(problem, idx):
    return Problem(problem.name, problem.orders, [problem.brigades[v] for v in idx], problem.starts, problem.matrices)


def order_rating(problem, routes, ratings):
    """Средний рейтинг исполнителя по виду работ заявки, по выполненным заявкам."""
    xs = [ratings[problem.brigades[v].name].of(problem.orders[k].skill).value for v, r in enumerate(routes) for k in r]
    return round(sum(xs) / len(xs), 3) if xs else None


def point(problem, routes, ratings, k):
    used = [r for r in routes if r]
    return dict(k=k, brigades=len(used), km=round(sum(problem.km(v, r) for v, r in enumerate(routes)), 1),
                served=sum(len(r) for r in routes), rating=order_rating(problem, routes, ratings))


class BestMode:
    def __init__(self, planner, ratings, log=print):
        self.planner, self.ratings, self.log = planner, ratings, log

    def _solve(self, problem, order, k, seconds, seed, workdir):
        sub = subproblem(problem, order[:k])
        return sub, self.planner.solver.plan_only(sub, seconds, seed, os.path.join(workdir, f'k{k}'))

    def run(self, problem, meta, out_dir, seconds=3.0, seed=1, quick=1.0):
        order = ranked(problem, self.ratings)
        n, total = len(order), len(problem.orders)
        full = lambda routes: sum(len(r) for r in routes) == total
        lo, hi = 1, n                                   # лучших hi хватает, lo − 1 — нет
        if not full(self._solve(problem, order, n, quick, seed, out_dir)[1]):
            raise ValueError('весь парк не выполняет все заявки дня — режим «лучшие» не определён')
        while lo < hi:
            mid = (lo + hi) // 2
            if full(self._solve(problem, order, mid, quick, seed, out_dir)[1]):
                hi = mid
            else:
                lo = mid + 1
        k = hi
        doc, sub, routes = self._final(problem, meta, order, k, out_dir, seconds, seed)
        if not full(routes):                            # длинный прогон не закрыл день — берём K + 1
            self.log(f'Лучших {k} не хватило при полном прогоне, беру {k + 1}')
            k += 1
            doc, sub, routes = self._final(problem, meta, order, k, out_dir, seconds, seed)
        curve = [point(sub, routes, self.ratings, k)]
        curve += [point(*self._solve(problem, order, kk, seconds, seed, out_dir), self.ratings, kk)
                  for kk in sorted({*range(k + 3, n, 3), n} - {k})]
        proven = doc['lower_bound'] == k
        self.log(f'Лучшие {k} из {n} монтажников закрывают день' + (' (меньше нельзя — доказано)' if proven else ''))
        doc['quality'] = dict(k=k, pool=n, proven=proven, curve=curve)
        return doc

    def _final(self, problem, meta, order, k, out_dir, seconds, seed):
        sub = subproblem(problem, order[:k])
        doc = self.planner.run(sub, meta, out_dir, seconds, seed)
        routes = [[] for _ in sub.brigades]
        for rt in doc['plan']['routes']:
            routes[rt['v']] = [st['k'] for st in rt['stops']]
        return doc, sub, routes


def attach(doc, ratings):
    """Рейтинги бригад плана и средний рейтинг исполнителя по заявкам — в документ (для страницы и отчёта)."""
    from ..model.rating import KINDS
    doc['ratings'] = {}
    for b in doc['brigades']:
        e = ratings.get(b['name'])
        if e:
            doc['ratings'][b['name']] = dict(on_time=e.on_time, on_time_reviews=e.on_time_reviews,
                                             by_kind={KINDS[s]: dict(value=r.value, reviews=r.reviews, few=r.few)
                                                      for s, r in e.by_skill.items()})
    xs = []
    for o in doc['orders']:
        if o['plan']:
            e = ratings.get(doc['brigades'][o['plan']['brigade']]['name'])
            r = e and e.of(SKILL_OF[o['bk']])
            if r:
                o['plan']['rating'] = r.value
                xs.append(r.value)
    doc['plan']['metrics']['rating'] = round(sum(xs) / len(xs), 3) if xs else None
    return doc
