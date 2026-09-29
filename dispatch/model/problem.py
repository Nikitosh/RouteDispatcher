"""Задача планирования: заявки, бригады, стартовые точки и матрицы, расписание маршрута, запись для решателя.

Требуемый транспорт кодируется в номере навыка, поэтому решатель на C++ про транспорт не знает: навык заявки = s + 3·c,
где s — навык (0 ремонт, 1 подключение, 2 авария), c — класс требования (0 — любой транспорт, далее — встретившиеся
наборы допустимых видов). У бригады в маске стоит бит s + 3·c для каждого её навыка s и каждого класса c, который
допускает её транспорт. Классов не больше 9 — маска 32-битная.
"""
from ..config import MODES, SHIFT
from ..model.domain import Schedule, Stop


class ProblemError(Exception):
    pass


class Problem:
    def __init__(self, name, orders, brigades, starts, matrices):
        self.name = name
        self.orders = orders
        self.brigades = brigades
        self.starts = starts                     # [(lat, lon)], 0 — офис
        self.time, self.dist = matrices.time, matrices.dist
        self._refresh_classes()

    def _refresh_classes(self):
        needs = sorted({o.need for o in self.orders if o.need}, key=sorted)
        self.classes = [None] + needs
        if len(self.classes) > 9:
            raise ProblemError('слишком много разных требований к транспорту (больше 8)')

    @property
    def n_starts(self):
        return len(self.starts)

    def node(self, k):
        """Номер точки заявки k в матрицах."""
        return self.n_starts + k

    # ---- допуск
    def has_skill(self, v, k):
        return self.orders[k].skill in self.brigades[v].skills

    def has_mode(self, v, k):
        need = self.orders[k].need
        return need is None or self.brigades[v].mode in need

    def can(self, v, k):
        return self.has_skill(v, k) and self.has_mode(v, k)

    def latest(self, k):
        """Позже этого начать нельзя: конец окна и конец смены с учётом длительности работы."""
        o = self.orders[k]
        return min(o.window[1], SHIFT - o.service)

    # ---- расписание
    def schedule(self, v, route, release=None, keep=0):
        """Расписание маршрута бригады v. Если задан release, бригада выезжает к заявкам начиная с позиции keep
        не раньше этого момента (перепланирование: она узнаёт о новом маршруте в момент события)."""
        b = self.brigades[v]
        T, D = self.time[b.mode], self.dist[b.mode]
        t, prev, km, stops, ok = 0.0, b.start, 0.0, [], True
        for i, k in enumerate(route):
            o, n = self.orders[k], self.node(k)
            depart = max(t, release) if release is not None and i >= keep else t
            arrive = depart + T[prev][n]
            start = max(arrive, o.earliest)
            if start > self.latest(k) + 1e-6 or not self.can(v, k):
                ok = False
            km += D[prev][n]
            stops.append(Stop(k, arrive, start, start + o.service, D[prev][n]))
            t, prev = start + o.service, n
        return Schedule(ok, km, stops)

    def km(self, v, route):
        return self.schedule(v, route).km if route else 0.0

    # ---- решатель
    def skill_code(self, k):
        return self.orders[k].skill + 3 * self.classes.index(self.orders[k].need)

    def skill_mask(self, v):
        b = self.brigades[v]
        return sum(1 << (s + 3 * c) for s in b.skills for c, need in enumerate(self.classes) if need is None or b.mode in need)

    def write(self, path):
        """Формат задачи решателя: имя; N V S; N строк заявок; V строк бригад; по каждому виду транспорта матрицы
        времени и расстояния (S+N)×(S+N)."""
        with open(path, 'w') as f:
            f.write(f'{self.name}\n{len(self.orders)} {len(self.brigades)} {self.n_starts}\n')
            for k, o in enumerate(self.orders):
                f.write(f'{o.id} {o.service} {o.earliest} {self.latest(k)} {o.priority} {self.skill_code(k)}\n')
            for v, b in enumerate(self.brigades):
                f.write(f'{b.start} {MODES.index(b.mode)} {self.skill_mask(v)}\n')
            for m in MODES:
                for row in self.time[m]:
                    f.write(' '.join(f'{x:.3f}' for x in row) + '\n')
                for row in self.dist[m]:
                    f.write(' '.join(f'{x:.4f}' for x in row) + '\n')
