"""Границы парка в пересчёте deck_v2 с памятью ng 24 (продукт перешёл с 8 на 24, см. docs/SOLVER_IO.md): для задач,
где fleet_bound (ng 8) ниже числа бригад лучшего плана, тот же fleet_bound 60 с на тех же ответах, ng 24. Более высокая
граница записывается в задачу (прежняя — в bound_ng8), затем `deck_v2.py report` пересобирает отчёт.
Запуск из корня: .venv/bin/python research/deck_v2_ng24.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck_v2 as D  # noqa: E402

NG = 24


def stronger(task, answer, old):
    new = D.run_json([os.path.join(D.BIN, 'fleet_bound'), task, answer, D.BOUND_SEC, NG], timeout=D.BOUND_SEC * 3 + 60)
    if (new.get('fleet_lower_bound') or 0) > (old.get('fleet_lower_bound') or 0):
        return dict(new, ng=NG, sec=round(new.get('ms', 0) / 1000, 2)), True
    return old, False


def main():
    tasks = os.path.join(D.OUT, 'tasks')
    for f in sorted(os.listdir(tasks)):
        path = os.path.join(tasks, f)
        d = json.load(open(path, encoding='utf-8'))
        name = f[:-5]
        wd = os.path.join(D.OUT, 'work', name)
        b = d['plans'][d['best']]
        changed = False
        if (d['bound'].get('fleet_lower_bound') or 0) < b['used']:
            seed = b.get('seed', D.SEEDS[d['best']])
            nb, up = stronger(os.path.join(wd, 'task.txt'), os.path.join(wd, f'plan_s{seed}.json'), d['bound'])
            if up:
                d['bound_ng8'], d['bound'], changed = d['bound'], nb, True
            print(f"{name}: план {b['used']}, граница ng8 {d.get('bound_ng8', d['bound']).get('fleet_lower_bound')} → "
                  f"{d['bound'].get('fleet_lower_bound')}", flush=True)
        lg = d.get('long')
        if lg and (lg['bound'].get('fleet_lower_bound') or 0) < lg['runs'][lg['best']]['used']:
            seed = lg['runs'][lg['best']]['seed']
            nb, up = stronger(os.path.join(wd, 'task.txt'), os.path.join(wd, f'long_s{seed}.json'), lg['bound'])
            if up:
                lg['bound_ng8'], lg['bound'], changed = lg['bound'], nb, True
            print(f"{name} (30 с): план {lg['runs'][lg['best']]['used']}, граница → {lg['bound'].get('fleet_lower_bound')}",
                  flush=True)
        if changed:
            json.dump(d, open(path, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
