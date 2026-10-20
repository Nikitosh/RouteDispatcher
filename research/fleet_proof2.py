"""Доказательство «на K бригадах нельзя» в модели второго этапа: solver/bin/fleet_proof перебирает все маршруты с
приведённой стоимостью не выше K − L (окна, смены, обед, оборудование, дома бригад), здесь — точное покрытие CP-SAT:
каждая обслуживаемая заявка ровно в одном маршруте, маршрутов не больше K, бригад каждого типа не больше их числа.
INFEASIBLE при полном переборе — K бригад мало, значит, план на K + 1 оптимален по парку.

Дерево составов (сколько бригад каждого типа выходит) считает fleet_proof: узел закрывает ЛП, листы с точным
составом, где ЛП не закрыла, — перебор маршрутов и здесь CP-SAT.

Запуск из корня: research/.venv/bin/python research/fleet_proof2.py out/Юго-восток [--K 11] [--seconds 60] [--skip-tree]
"""
import argparse
import json
import os
import subprocess
import sys
import time

from ortools.sat.python import cp_model

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir', help='папка плана: task.txt и answer.json')
    ap.add_argument('--K', type=int, help='сколько бригад проверяем (по умолчанию на одну меньше плана)')
    ap.add_argument('--seconds', type=float, default=60, help='время ЛП в узле')
    ap.add_argument('--out', help='куда писать дерево и листы (по умолчанию research/runs/proof_<папка>)')
    ap.add_argument('--common', action='store_true', help='ослабленная задача с общим стартом')
    ap.add_argument('--ng', type=int, default=8, help='память ng-маршрутов в оценке')
    ap.add_argument('--cpsat-tl', type=float, default=3600)
    ap.add_argument('--skip-tree', action='store_true', help='дерево уже посчитано — только CP-SAT по листам')
    a = ap.parse_args()
    t0 = time.time()
    task, answer = os.path.join(a.dir, 'task.txt'), os.path.join(a.dir, 'answer.json')
    used = sum(1 for r in json.load(open(answer))['routes'] if r)
    K = a.K if a.K is not None else used - 1
    out = a.out or os.path.join(ROOT, 'research', 'runs', 'proof_' + os.path.basename(os.path.normpath(a.dir)))
    os.makedirs(out, exist_ok=True)
    prefix = os.path.join(out, f'K{K}')
    if not a.skip_tree:
        cmd = [os.path.join(ROOT, 'solver', 'bin', 'fleet_proof'), task, answer, str(K), str(a.seconds), prefix]
        subprocess.run(cmd + (['--common'] if a.common else []) + [f'--ng={a.ng}'], check=True)
    lines = [json.loads(x) for x in open(prefix + '_tree.jsonl', encoding='utf-8')]
    head, nodes = lines[0], lines[1:]
    served = head['served']
    leaves = [n for n in nodes if not n['closed']]
    print(f'план {used} бригад, проверяем K = {K}: узлов {len(nodes)}, закрыто ЛП {len(nodes) - len(leaves)}, '
          f'листов {len(leaves)}')
    ok = True
    for n in sorted(leaves, key=lambda n: os.path.getsize(n['file'])):
        if not n['complete']:
            print(f'  {n["cap"]}: перебор неполный (ЛП {n["L"]:.3f}) — доказательства нет')
            ok = False
            continue
        leaf = json.load(open(n['file']))
        r = cover(served, leaf['cols'], leaf['cap'], a.cpsat_tl)
        print(f'  {n["cap"]}: ЛП {n["L"]:.3f}, маршрутов {len(leaf["cols"])}, CP-SAT {r} ({time.time() - t0:.0f} с)',
              flush=True)
        ok &= r == 'INFEASIBLE'
        if r in ('FEASIBLE', 'OPTIMAL'):
            print(f'  есть план на {sum(leaf["cap"])} бригад')
    print(f'ДОКАЗАНО: на {K} бригадах эти заявки не выполнить, минимум — {K + 1}' if ok else 'не доказано')
    return 0 if ok else 1


def cover(served, cols, cap, tl):
    """Точное покрытие в листе: заявка ровно в одном маршруте, у типа t ровно cap[t] маршрутов."""
    m = cp_model.CpModel()
    x = [m.NewBoolVar('') for _ in cols]
    by_order = {k: [] for k in served}
    by_type = {}
    for i, (t, r) in enumerate(cols):
        by_type.setdefault(t, []).append(x[i])
        for k in r:
            by_order[k].append(x[i])
    if any(not v for v in by_order.values()):
        return 'INFEASIBLE'
    for vs in by_order.values():
        m.AddExactlyOne(vs)
    for t, c in enumerate(cap):
        vs = by_type.get(t, [])
        if len(vs) < c:
            return 'INFEASIBLE'
        if vs:
            m.Add(sum(vs) == c)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = tl
    s.parameters.num_workers = 8
    return s.StatusName(s.Solve(m))


if __name__ == '__main__':
    sys.exit(main())
