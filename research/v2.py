"""Наборы задач под модель продукта (lct plan, 2026-09-28): время в пути = OSRM × 1,3 для машины + подход к заявке
(машина 5 мин, общ. транспорт, велосипед и пешком 3 мин) и требование «только машина».

Требование: настоящая авария на узле связи («Глобальная проблема / Авария») и подключение с заказом оборудования
(«Подключение / Заказ подключения…»). В контрольных и исходных задачах номера заявок настоящие — правило точное.
В сгенерированных типа HD нет: требование ставим детерминированно по номеру с той же частотой, что в реальных данных
(70% аварий, 11% подключений). Требование снимается, если на заявку нет ни одной машины с нужным навыком —
иначе вариант «все на общ. транспорте» мерил бы не оптимизацию, а невыполнимость.
Кодирование как в lct/model.py: навык заявки s + 3·c (c = 1 — только машина), бригада на машине получает биты s + 3.

python3 v2.py  →  instances_control_v2/, instances_road_v2/, instances_gen_v2/"""
import csv, glob, os, zlib
# исходные задачи v1 (реальные дороги) перенесены в archive_v1/ (29.09)
SRC = {'archive_v1/instances_control_road': 'instances_control_v2', 'archive_v1/instances_road': 'instances_road_v2', 'archive_v1/instances_gen_road': 'instances_gen_v2'}
ALPHA = {0: 1.3}; BETA = {0: 5.0, 1: 3.0, 2: 3.0, 3: 3.0}      # виды: 0 car, 1 pt, 2 bike, 3 foot
REG = [('yugovostok', 'Юго-восток'), ('yugocentr', 'Югоцентр'), ('vostok', 'Восток')]

TYPES = {}
for f in glob.glob('utf8/* Синтетические*.csv'):
    reg = os.path.basename(f).split(' Синтетические')[0]
    for r in csv.reader(open(f, encoding='utf-8'), delimiter=';'):
        if len(r) > 2: TYPES[(reg, r[0])] = (r[1], r[2])


def needs_car(name, oid, skill, svc):
    reg = next((ru for en, ru in REG if en in name), None)
    t = TYPES.get((reg, oid))
    if t:
        bk, hd = t
        return (bk == 'Глобальная проблема' and hd == 'Авария') or (bk == 'Подключение' and hd.startswith('Заказ подключения'))
    r = zlib.crc32(f'{name}|{oid}'.encode()) / 2 ** 32
    return (skill == 2 and r < 0.70) or (skill == 1 and svc == 70 and r < 0.11)


def convert(src, dst):
    L = open(src).read().split('\n'); name = L[0]
    N, V, S = map(int, L[1].split()); M = S + N
    ords = [l.split() for l in L[2:2 + N]]; veh = [list(map(int, l.split())) for l in L[2 + N:2 + N + V]]
    car_skills = {s for st, mode, mask in veh if mode == 0 for s in range(3) if mask >> s & 1}
    need = [needs_car(name, o[0], int(o[5]), int(o[1])) and int(o[5]) in car_skills for o in ords]
    out = [name, f'{N} {V} {S}']
    out += [' '.join(o[:5] + [str(int(o[5]) + 3 * need[k])]) for k, o in enumerate(ords)]
    out += [f'{st} {mode} {mask | ((mask & 7) << 3 if mode == 0 else 0)}' for st, mode, mask in veh]
    p = 2 + N + V
    for m in range(4):
        a, b = ALPHA.get(m, 1.0), BETA[m]
        for i in range(M):
            row = L[p + i].split()
            out.append(' '.join(f'{(a * float(x) + (b if j >= S else 0.0)) if i != j else 0.0:.3f}' for j, x in enumerate(row)))
        out += L[p + M:p + 2 * M]; p += 2 * M
    open(dst, 'w').write('\n'.join(out) + '\n')
    return sum(need)


if __name__ == '__main__':
    for s, d in SRC.items():
        os.makedirs(d, exist_ok=True); tot = 0; files = sorted(glob.glob(f'{s}/*.txt'))
        for f in files: tot += convert(f, f'{d}/{os.path.basename(f)}')
        print(f'{d}: {len(files)} задач, заявок «только машина» всего {tot}')
