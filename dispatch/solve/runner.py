"""Запуск решателей из solver/bin (собираются `make -C solver`).
plan — наш план (кооперативный поиск; роли потоков выбирает сам по числу ядер), baseline — базовый вариант из
раздела 2.3 ТЗ, fleet_bound — нижняя граница числа бригад (генерация столбцов). Вход — файл задачи, выход — одна
строка JSON в stdout; схема — docs/SOLVER_IO.md."""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from ..config import SOLVER_BIN


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

    def solve(self, problem, seconds=3.0, seed=1, workdir=None, bound=True):
        """Наш план, базовый вариант и (по желанию) нижняя граница числа бригад. Файлы задачи и ответа остаются в
        workdir — их можно проверить независимо."""
        workdir = workdir or tempfile.mkdtemp(prefix='dispatch_')
        os.makedirs(workdir, exist_ok=True)
        task = os.path.join(workdir, 'task.txt')
        problem.write(task)
        ours_text = self._run('plan', [task, seconds, seed])
        ours = parse_answer('plan', ours_text)
        answer = os.path.join(workdir, 'answer.json')
        open(answer, 'w').write(ours_text)
        base = parse_answer('baseline', self._run('baseline', [task, 1, seed]))
        lb = None
        if bound:
            try:
                lb = parse_answer('fleet_bound', self._run('fleet_bound', [task, answer, 3])).get('fleet_lower_bound')
            except (SolverError, subprocess.TimeoutExpired):
                lb = None
        n = len(problem.brigades)
        return (SolverResult(answer_routes('plan', ours, n), lb), SolverResult(answer_routes('baseline', base, n)), task)
