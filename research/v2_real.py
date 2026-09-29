"""Реальный план диспетчера (контрольные файлы) в модели продукта: матрицы из instances_control_v2 (машина × 1,3,
подход 5/3 мин). Как в control.eval_real: у каждой бригады её реальные заявки в лучшем порядке (перебор внутри групп
с одинаковым началом окна). Считаем км, опоздания (начало после окна) и бригады, закончившие после 22:00.
Требование «только машина» здесь не проверяем: реальный транспорт бригад неизвестен (варианты «все pt» и «все car»)."""
import csv, glob, itertools, json
from collections import Counter, defaultdict
from validate import load_instance
TR = {'Восток': 'vostok', 'Югоцентр': 'yugocentr', 'Юго-восток': 'yugovostok'}
out = {}
for region, tr in TR.items():
    f = glob.glob(f'utf8/{region} Контрольное*.csv')[0]
    rows = list(csv.reader(open(f, encoding='utf-8'), delimiter=';')); h = rows[0]; d = [r for r in rows[1:] if r and r[0]]
    bi = h.index('Бригада'); names = [b for b, _ in Counter(r[bi] for r in d if r[bi]).most_common()]
    plan = {v: [k for k, r in enumerate(d) if r[bi] == b] for v, b in enumerate(names)}
    for mode in ('pt', 'car'):
        I = load_instance(f'instances_control_v2/control_{tr}_{mode}_inf.txt'); S = I['S']; m = I['veh'][0]['mode']
        T, D = I['T'][m], I['D'][m]
        tot = dict(km=0, late=0, late_min=0, after22=0, used=0, unassigned=I['N'] - sum(len(ks) for ks in plan.values()))
        for v, ks in plan.items():
            tot['used'] += 1; node = I['veh'][v]['start']; t = 0; groups = defaultdict(list)
            for k in ks: groups[I['ords'][k]['a']].append(k)
            for a in sorted(groups):
                best = None
                for perm in itertools.permutations(groups[a]):
                    tt, nd, km, late, lm = t, node, 0, 0, 0
                    for k in perm:
                        o = I['ords'][k]; n = S + k; beg = max(tt + T[nd][n], o['a'])
                        if beg > o['b'] + 1e-6: late += 1; lm += beg - o['b']
                        km += D[nd][n]; tt = beg + o['svc']; nd = n
                    if best is None or (late, lm, km) < best[0]: best = ((late, lm, km), tt, nd)
                (late, lm, km), t, node = best; tot['km'] += km; tot['late'] += late; tot['late_min'] += lm
            if t > 720 + 1e-6: tot['after22'] += 1
        out[f'{tr}_{mode}'] = {k: round(x, 1) for k, x in tot.items()}
        print(region, mode, out[f'{tr}_{mode}'])
json.dump(out, open('real_plan_eval_v2.json', 'w'), ensure_ascii=False, indent=1)
