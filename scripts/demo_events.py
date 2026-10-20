"""События демо по свежему плану Востока: недоступна бригада, отмена, работа затянулась, клиент перенёс окно,
сломалась машина. Решатель многопоточный, и план от запуска к запуску немного разный, поэтому заявку и бригаду для
каждого события берём из плана: сначала ту, что в документации, а если она к моменту события уже начата или не
подходит — первую подходящую.

Запуск из корня: .venv/bin/python scripts/demo_events.py out/Восток   (пишет out/Восток_недоступна и т. д.)
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dispatch.files import document                                   # noqa: E402

AT = {'unavailable': 240, 'cancel': 180, 'finish': 150, 'window': 180, 'breakdown': 180}   # минуты от 10:00


def hm(m):
    return f'{10 + int(m) // 60}:{int(m) % 60:02d}'


def stops(doc):
    """{бригада: [(заявка, начало, конец)]} по плану."""
    return {r['v']: [(doc['orders'][s['k']]['id'], s['beg'], s['end']) for s in r['stops']] for r in doc['plan']['routes']}


def pick(doc):
    B, S = doc['brigades'], stops(doc)
    name = {v: B[v]['name'] for v in S}
    after = lambda v, at: [x for x in S[v] if x[1] >= at]
    out = {}
    # недоступна в 14:00: Бригада 4, иначе бригада с наибольшим числом неначатых заявок
    v = next((v for v in S if name[v] == 'Бригада 4' and after(v, AT['unavailable'])), None)
    v = v if v is not None else max(S, key=lambda v: len(after(v, AT['unavailable'])))
    out['unavailable'] = ['--unavailable', name[v]]
    # отмена в 13:00: заявка 74198, иначе первая неначатая
    free = [x[0] for v in S for x in after(v, AT['cancel'])]
    out['cancel'] = ['--cancel', '74198' if '74198' in free else free[0]]
    # работа затянулась в 12:30: последняя начатая заявка бригады, конец в 14:00 (или на час позже плана)
    started = [(v, x) for v in S for x in S[v] if x[1] < AT['finish'] and not any(y[1] < AT['finish'] for y in S[v] if y[1] > x[1])]
    v, x = next(((v, x) for v, x in started if x[0] == '77563'), max(started, key=lambda p: p[1][2]))
    out['finish'] = ['--finish', x[0], hm(240 if x[2] < 230 else min(720, x[2] + 60))]   # 14:00, как в документации
    # клиент перенёс окно в 13:00 на 16:00–18:00: заявка 86160, иначе первая неначатая с другим окном
    orders = {o['id']: o for o in doc['orders']}
    free = [x[0] for v in S for x in after(v, AT['window']) if orders[x[0]]['window'] != [360, 480]]
    out['window'] = ['--reschedule', '86160' if '86160' in free else free[0], '16:00-18:00']
    # сломалась машина в 13:00: Бригада 1, иначе бригада на машине с неначатыми заявками
    cars = [v for v in S if B[v]['mode'] == 'car' and after(v, AT['breakdown'])]
    v = next((v for v in cars if name[v] == 'Бригада 1'), cars[0] if cars else None)
    if v is not None:
        out['breakdown'] = ['--breakdown', name[v], '--to', 'pt']
    return out


DIRS = {'unavailable': 'недоступна', 'cancel': 'отмена', 'finish': 'задержка', 'window': 'перенос_окна', 'breakdown': 'поломка'}


def main(argv):
    plan = argv[0] if argv else 'out/Восток'
    events = pick(document.load(plan))
    failed = 0
    for kind, args in events.items():
        out = f'{plan}_{DIRS[kind]}'
        cmd = [sys.executable, '-m', 'dispatch', 'replan', plan, '--at', hm(AT[kind]), *args, '-o', out]
        print('$', ' '.join(cmd[1:]), flush=True)
        if subprocess.run(cmd).returncode:
            failed += 1
            print(f'Событие {kind} не посчитано — на странице его не будет', flush=True)
    return 1 if failed == len(events) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
