"""Доказательство минимума бригад (solver/bin/fleet_proof): дерево составов, оценка ЛП в узлах и перебор маршрутов в
листах. На маленьких задачах точный минимум известен перебором (test_bound_lunch.min_fleet): при K = минимум − 1
инструмент обязан закрыть все ветки (ЛП или отсутствием точного покрытия среди перебранных маршрутов), при K = минимум —
оставить лист, где покрытие есть. Каждый перебранный маршрут допустим по Problem.schedule."""
import json
import os
import random
import subprocess

import pytest

from dispatch.config import SOLVER_BIN
from tests.e2e.test_bound_lunch import min_fleet, random_problem, run

pytestmark = pytest.mark.e2e

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'fleet_proof')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)


def prove(p, tmp_path, K, ng=8, routes=None):
    """Дерево и листы fleet_proof для плана решателя: (заголовок, узлы, листы с маршрутами)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    task = tmp_path / 'task.txt'
    p.write(str(task))
    text, _ = run('plan', task, 0.3, 1)
    if routes is not None:
        text = json.dumps({'routes': routes})
    answer = tmp_path / 'answer.json'
    answer.write_text(text)
    prefix = tmp_path / f'K{K}'
    r = subprocess.run([os.path.join(SOLVER_BIN, 'fleet_proof'), task, answer, str(K), '2', prefix, f'--ng={ng}'],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    lines = [json.loads(x) for x in open(f'{prefix}_tree.jsonl', encoding='utf-8')]
    leaves = [json.load(open(n['file'])) for n in lines[1:] if not n['closed']]
    return lines[0], lines[1:], leaves, json.loads(text)['routes']


def exact_cover(served, cols, cap):
    """Есть ли выбор маршрутов: каждая заявка ровно в одном, у типа t ровно cap[t] маршрутов."""
    served = set(served)
    by_first = {}
    for t, r in cols:
        by_first.setdefault(min(r), []).append((t, frozenset(r)))
    need = list(cap)

    def go(left):
        if not left:
            return not any(need)
        k = min(left)
        for t, s in by_first.get(k, []):
            if need[t] and s <= left:
                need[t] -= 1
                if go(left - s):
                    return True
                need[t] += 1
        return False
    return go(frozenset(served))


def check_routes(p, head, leaves):
    for leaf in leaves:
        assert leaf['complete']
        for t, r in leaf['cols']:
            assert any(p.schedule(v, r).feasible for v in head['types'][t]), (t, r)


def test_proof_matches_brute_force(tmp_path):
    rng = random.Random(11)
    proven = covered = 0
    for trial in range(20):
        p = random_problem(rng)
        head, nodes, leaves, routes = prove(p, tmp_path / f'{trial}a', 0)
        served = head['served']
        opt = min_fleet(p, served)
        assert opt is not None
        if opt >= 1:   # K = opt − 1: доказать
            head, nodes, leaves, _ = prove(p, tmp_path / f'{trial}b', opt - 1, routes=routes)
            check_routes(p, head, leaves)
            assert not any(exact_cover(served, lf['cols'], lf['cap']) for lf in leaves), trial
            proven += 1
        # K = opt: план на opt бригад есть, значит, есть лист с покрытием
        head, nodes, leaves, _ = prove(p, tmp_path / f'{trial}c', opt, routes=routes)
        check_routes(p, head, leaves)
        assert any(exact_cover(served, lf['cols'], lf['cap']) for lf in leaves), trial
        covered += 1
    assert proven >= 15 and covered == 20


def test_larger_ng_is_still_a_lower_bound(tmp_path):
    rng = random.Random(3)
    for trial in range(10):
        p = random_problem(rng, n=8)
        head, nodes, leaves, routes = prove(p, tmp_path / f'{trial}a', 0)
        opt = min_fleet(p, head['served'])
        head, nodes, leaves, _ = prove(p, tmp_path / f'{trial}c', opt, ng=24, routes=routes)
        assert any(exact_cover(head['served'], lf['cols'], lf['cap']) for lf in leaves), trial
