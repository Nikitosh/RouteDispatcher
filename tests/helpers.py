"""Маленькие задачи «на прямой» для модульных тестов: точка — число x, время в пути = |Δx| × темп вида транспорта,
расстояние = |Δx| км. Так расписание легко посчитать в уме."""
from dispatch.config import MODES, PRIORITY, SERVICE, SKILL, TYPE_NAMES
from dispatch.geo.matrices import TravelMatrices
from dispatch.model.domain import Brigade, Order
from dispatch.model.problem import Problem

PACE = {'car': 1.0, 'pt': 1.5, 'bike': 2.0, 'foot': 3.0}    # минут на единицу расстояния
BK = {v: k for k, v in TYPE_NAMES.items()}


def order(oid, kind='Ремонт', window=(0, 120), x=0.0, need=None):
    bk = BK[kind]
    return Order(id=str(oid), bk=bk, type=kind, window=window, service=SERVICE[bk], priority=PRIORITY[bk], skill=SKILL[bk],
                 district='Тест', address=f'точка {x}', need=frozenset(need) if need else None, lat=float(x), lon=0.0)


def brigade(name, mode='car', skills=(0, 1, 2), start=0, x=0.0):
    return Brigade(name=name, mode=mode, skills=list(skills), start=start, lat=float(x), lon=0.0, start_address='офис')


class LineMatrices:
    """Подмена MatrixBuilder: матрицы по координате lat точки."""

    def __init__(self):
        self.calls = 0

    def build(self, points, n_starts, settings=None):
        self.calls += 1
        xs = [p[0] for p in points]
        T = {m: [[abs(a - b) * PACE[m] for b in xs] for a in xs] for m in MODES}
        D = {m: [[abs(a - b) for b in xs] for a in xs] for m in MODES}
        return TravelMatrices(T, D)


def line_problem(orders, brigades, starts=((0.0, 0.0),)):
    starts = list(starts)
    points = starts + [(o.lat, o.lon) for o in orders]
    return Problem('тест', orders, brigades, starts, LineMatrices().build(points, len(starts)))

