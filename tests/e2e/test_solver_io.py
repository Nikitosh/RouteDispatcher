"""Контракт с решателями (docs/SOLVER_IO.md): настоящие бинарники на маленькой задаче «на прямой»."""
import json
import os
import subprocess

import pytest

from dispatch.config import SOLVER_BIN
from dispatch.planning.validate import check_plan
from dispatch.solve.runner import SolverRunner
from tests.helpers import brigade, line_problem, order

pytestmark = pytest.mark.e2e

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'baseline', 'fleet_bound')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)

PLAN_KEYS = {'solver', 'routes', 'feasible', 'unserved', 'used', 'km', 'ms'}


@pytest.fixture(scope='module')
def task(tmp_path_factory):
    orders = [order(i, 'Ремонт', (0, 400), x=i) for i in range(1, 7)] + [order(7, 'Авария', (0, 100), x=50)]
    brigades = [brigade('Б1'), brigade('Б2', 'foot', (0,)), brigade('Б3', 'car', (2,), start=1, x=40)]
    problem = line_problem(orders, brigades, starts=((0.0, 0.0), (40.0, 0.0)))
    path = tmp_path_factory.mktemp('io') / 'task.txt'
    problem.write(str(path))
    return problem, str(path)


def run(name, *args):
    p = subprocess.run([os.path.join(SOLVER_BIN, name), *map(str, args)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert len(lines) == 1, p.stdout
    return p.stdout, json.loads(lines[0])


@pytest.mark.parametrize('name', ['plan', 'baseline'])
def test_answer_schema(task, name):
    problem, path = task
    _, doc = run(name, path, 0.3, 1)
    assert set(doc) == PLAN_KEYS and doc['solver'] == name
    assert len(doc['routes']) == len(problem.brigades)
    assert doc['feasible'] is True and doc['unserved'] == 0
    assert doc['used'] == sum(1 for r in doc['routes'] if r)
    assert check_plan(problem, doc['routes']).ok


def test_fleet_bound_schema(task, tmp_path):
    problem, path = task
    text, plan = run('plan', path, 0.3, 1)
    answer = tmp_path / 'answer.json'
    answer.write_text(text)
    _, doc = run('fleet_bound', path, answer, 1)
    assert set(doc) == {'fleet_lower_bound', 'lagrangian', 'converged', 'used', 'ms'}
    assert doc['used'] == plan['used']
    assert 1 <= doc['fleet_lower_bound'] <= plan['used']


def test_runner_reads_answers(task, tmp_path):
    problem, _ = task
    ours, base, _ = SolverRunner().solve(problem, seconds=0.3, workdir=str(tmp_path))
    assert check_plan(problem, ours.routes).ok and check_plan(problem, base.routes).ok
    assert ours.lower_bound is not None and ours.lower_bound <= sum(1 for r in ours.routes if r)
