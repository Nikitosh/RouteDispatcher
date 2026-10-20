"""Запуск решателей из solver/bin (собираются `make -C solver`).
plan — наш план (кооперативный поиск; роли потоков выбирает сам по числу ядер), baseline — базовый вариант из
раздела 2.3 ТЗ, fleet_bound — нижняя граница числа бригад (генерация столбцов). Вход — файл задачи, выход — одна
строка JSON в stdout; схема — docs/SOLVER_IO.md.

Параметры plan для аналитики: maxv — не больше стольких бригад, wveh — цена бригады в км (по умолчанию 1e4: сначала
меньше бригад), wwait — цена минуты ожидания аварии в км (по умолчанию config.WAIT_KM_PER_MIN). С малым wveh и maxv=K
решатель ищет минимум км не больше чем на K бригадах — кривая «бригады против км»."""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from ..config import SOLVER_BIN, WAIT_KM_PER_MIN

BOUND_SECONDS = 30     # нижней границе; она останавливается раньше, как только сравнялась с планом
BOUND_NG = 24          # память ng-маршрутов в точном поиске границы: с 8 на Юго-востоке ЛП 10,1, с 24 — 11,03 (минимум 12)


class SolverError(Exception):
    pass


@dataclass
class SolverResult:
    routes: list                 # routes[v] — индексы заявок по порядку
    lower_bound: int = None      # доказанный минимум бригад, если посчитан


def parse_answer(name, text):
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise SolverError(f'{name}: ответ не JSON ({e}): {text[:300]!r}')
    if not isinstance(doc, dict):
        raise SolverError(f'{name}: ответ не объект JSON: {text[:300]!r}')
    return doc


def answer_routes(name, doc, n_brigades):
    routes = doc.get('routes')
    if not isinstance(routes, list) or len(routes) != n_brigades:
        raise SolverError(f'{name}: в ответе должно быть {n_brigades} маршрутов')
    return routes


class SolverRunner:
    def __init__(self, bin_dir=None):
        self.bin_dir = bin_dir or SOLVER_BIN

    def _run(self, name, args, timeout=600):
        exe = os.path.join(self.bin_dir, name)
        if not os.path.exists(exe):
            raise SolverError(f'Нет {exe}. Соберите решатели: make -C solver')
        p = subprocess.run([exe] + [str(a) for a in args], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise SolverError(f'{name}: код {p.returncode}\n{p.stderr[-2000:]}')
        return p.stdout

    MAX_ORDERS, MAX_BRIGADES = 152, 32         # предел решателя plan (solver/src/search/fast_routes.hpp)

    @classmethod
    def check_size(cls, problem):
        n, v = len(problem.orders), len(problem.brigades)
        if n > cls.MAX_ORDERS or v > cls.MAX_BRIGADES:
            raise SolverError(f'в задаче {n} заявок и {v} бригад, решатель рассчитан на участок: до {cls.MAX_ORDERS} '
                              f'заявок и {cls.MAX_BRIGADES} бригад — разделите на участки')

    @staticmethod
    def options(maxv=None, wveh=None, wwait=None):
        """Параметры plan: ['maxv=K', 'wveh=X', 'wwait=Y'] для заданных; wwait по умолчанию — из config."""
        wwait = WAIT_KM_PER_MIN if wwait is None else wwait
        return [f'{k}={x:g}' for k, x in (('maxv', maxv), ('wveh', wveh), ('wwait', wwait)) if x is not None]

    def plan_only(self, problem, seconds=3.0, seed=1, workdir=None, maxv=None, wveh=None, wwait=None):
        self.check_size(problem)
        workdir = workdir or tempfile.mkdtemp(prefix='dispatch_')
        os.makedirs(workdir, exist_ok=True)
        task = os.path.join(workdir, 'task.txt')
        problem.write(task)
        args = [task, seconds, seed] + self.options(maxv, wveh, wwait)
        return answer_routes('plan', parse_answer('plan', self._run('plan', args)), len(problem.brigades))

    @staticmethod
    def score(problem, doc, wwait=None):
        """Цель плана из ответа решателя для сравнения запусков: штраф невыполненных, бригады, км с ценой ожидания аварий."""
        from ..config import PENALTY
        served = {k for r in doc['routes'] for k in r}
        lost = sum(PENALTY[o.priority] for k, o in enumerate(problem.orders) if k not in served)
        w = WAIT_KM_PER_MIN if wwait is None else wwait
        return lost, sum(1 for r in doc['routes'] if r), doc.get('km', 0.0) + w * doc.get('wait', 0.0)

    def solve(self, problem, seconds=3.0, seed=1, workdir=None, bound=True, maxv=None, wveh=None, wwait=None, tries=1,
              answer=None):
        """Наш план, базовый вариант и (по желанию) нижняя граница числа бригад. Файлы задачи и ответа остаются в
        workdir — их можно проверить независимо. tries — сколько запусков с сидами seed, seed+1, …: решатель
        многопоточный и от запуска к запуску даёт немного разные планы, берём лучший. answer — текст ответа прошлого
        прогона на ту же задачу: он соревнуется с новыми запусками (tries=0 — только он)."""
        self.check_size(problem)
        workdir = workdir or tempfile.mkdtemp(prefix='dispatch_')
        os.makedirs(workdir, exist_ok=True)
        task = os.path.join(workdir, 'task.txt')
        problem.write(task)
        ours_text = ours = None
        texts = [answer] if answer is not None else []
        runs = range(seed, seed + (tries if answer is not None else max(1, tries)))
        for sd in [None] * len(texts) + list(runs):
            text = texts.pop(0) if sd is None else self._run('plan', [task, seconds, sd] + self.options(maxv, wveh, wwait))
            doc = parse_answer('plan', text)
            answer_routes('plan', doc, len(problem.brigades))
            if sd is None and not all(problem.schedule(v, r).feasible for v, r in enumerate(doc['routes']) if r):
                if not runs:                       # входные файлы или матрицы поменялись — старый ответ не подходит
                    raise SolverError('ответ прошлого прогона (--answer) недопустим для этой задачи')
                continue
            if ours is None or self.score(problem, doc, wwait) < self.score(problem, ours, wwait):
                ours_text, ours = text, doc
        answer = os.path.join(workdir, 'answer.json')
        open(answer, 'w', encoding='utf-8').write(ours_text)
        base = parse_answer('baseline', self._run('baseline', [task, 1, seed]))
        lb = None
        if bound:
            try:
                lb = parse_answer('fleet_bound', self._run('fleet_bound', [task, answer, BOUND_SECONDS, BOUND_NG])).get('fleet_lower_bound')
            except (SolverError, subprocess.TimeoutExpired):
                lb = None
        n = len(problem.brigades)
        return (SolverResult(answer_routes('plan', ours, n), lb), SolverResult(answer_routes('baseline', base, n)), task)
