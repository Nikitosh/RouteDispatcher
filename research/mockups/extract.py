"""Данные для макетов интерфейса: Восток, mix2_inf, лучшее решение и базовый вариант ТЗ."""
import json, subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')
from data import load
INST = 'instances_control_road/control_vostok_mix2_inf.txt'
office, orders = load('Восток')
L = open(INST).read().split('\n')
n, v, S = map(int, L[1].split()); M = S + n; MODES = ['car', 'pt', 'bike', 'foot']
ords = [l.split() for l in L[2:2 + n]]
br = [list(map(int, l.split())) for l in L[2 + n:2 + n + v]]
mat = {}; p = 2 + n + v
for m in MODES:
    T = [list(map(float, L[p + i].split())) for i in range(M)]; p += M
    D = [list(map(float, L[p + i].split())) for i in range(M)]; p += M
    mat[m] = (T, D)
def routes_of(lines):
    r = {}
    for l in lines:
        if l.startswith('ROUTE'):
            a = list(map(int, l.split()[1:]))
            if len(a) > 1: r[a[0]] = a[1:]
    return r
def simulate(vv, r, t0=0):
    st, mode, mask = br[vv]; T, D = mat[MODES[mode]]; t = t0; prev = st; km = 0; stops = []; ok = True
    for k in r:
        nd = S + k; arr = t + T[prev][nd]; a, b, svc = int(ords[k][2]), int(ords[k][3]), int(ords[k][1])
        beg = max(arr, a); km += D[prev][nd]
        if beg > b + 1e-6: ok = False
        stops.append(dict(k=k, arr=round(arr), beg=round(beg), end=round(beg + svc)))
        t = beg + svc; prev = nd
    return dict(v=vv, mode=MODES[mode], mask=mask, km=round(km, 1), stops=stops), ok, km
best = routes_of(open('best/instances_control_road/control_vostok_mix2_inf.out'))
base_out = subprocess.run(['bin/s00_tz_greedy', INST, '1', '1'], capture_output=True, text=True).stdout.split('\n')
base = routes_of(base_out)
plan = [simulate(vv, r)[0] for vv, r in sorted(best.items())]
basep = [simulate(vv, r)[0] for vv, r in sorted(base.items())]
served = {k for r in base.values() for k in r}
# Объяснение для одной аварии: кто ещё мог бы её взять
def explain(k):
    owner = next(vv for vv, r in best.items() if k in r)
    base_km = {vv: simulate(vv, r)[2] for vv, r in best.items()}
    rows = []
    for vv in range(v):
        if vv == owner: continue
        if not (br[vv][2] >> int(ords[k][5])) & 1:
            rows.append(dict(v=vv, why='skill')); continue
        if vv not in best:
            rows.append(dict(v=vv, why='idle')); continue
        cand = []
        r = best[vv]
        for pos in range(len(r) + 1):
            rr = r[:pos] + [k] + r[pos:]
            _, ok, km = simulate(vv, rr)
            if ok and (simulate(vv, rr)[0]['stops'][-1]['end'] <= 720): cand.append(km - base_km[vv])
        rows.append(dict(v=vv, why='ok', dkm=round(min(cand), 1)) if cand else dict(v=vv, why='time'))
    r = best[owner]; i = r.index(k)
    _, _, km_wo = simulate(owner, r[:i] + r[i + 1:])
    return dict(k=k, owner=owner, dkm=round(base_km[owner] - km_wo, 1), others=rows)
def unavailable(gone, t0):
    """Бригада gone недоступна с t0: её неначатые заявки вставляем к другим после t0 (дешёвая вставка),
    не влезшие — в свободную бригаду, выходящую из офиса в t0."""
    cur = {vv: list(r) for vv, r in best.items()}
    st = simulate(gone, cur[gone])[0]['stops']
    moved = [s_['k'] for s_ in st if s_['beg'] >= t0]
    cur[gone] = [s_['k'] for s_ in st if s_['beg'] < t0]
    old = {s_['k']: s_['beg'] for s_ in st}
    changes = []
    for k in sorted(moved, key=lambda k: int(ords[k][3])):
        best_c = None
        for vv, r in cur.items():
            if vv == gone or not (br[vv][2] >> int(ords[k][5])) & 1: continue
            sim = simulate(vv, r)[0]['stops']
            first = next((i for i, s_ in enumerate(sim) if s_['beg'] >= t0), len(r))
            km0 = simulate(vv, r)[2]
            for pos in range(first, len(r) + 1):
                rr = r[:pos] + [k] + r[pos:]
                res, ok, km = simulate(vv, rr)
                if ok and res['stops'][-1]['end'] <= 720 and (best_c is None or km - km0 < best_c[0]):
                    best_c = (km - km0, vv, rr, res['stops'][pos]['beg'])
        if best_c:
            cur[best_c[1]] = best_c[2]
            changes.append(dict(k=k, was=gone, old=old[k], now=best_c[1], new=best_c[3], dkm=round(best_c[0], 1)))
        else:
            changes.append(dict(k=k, was=gone, old=old[k], now=None))
    # не влезшие — свободной бригаде с нужными навыками, выход из офиса в t0
    left = [c for c in changes if c['now'] is None]
    extra = {}
    for c in left:
        k = c['k']; placed = None
        for vv in list(extra) + [u for u in range(v) if u not in cur and u not in extra]:
            if not (br[vv][2] >> int(ords[k][5])) & 1: continue
            r = extra.get(vv, []); km0 = simulate(vv, r, t0)[2] if r else 0
            for pos in range(len(r) + 1):
                rr = r[:pos] + [k] + r[pos:]; res, ok, km = simulate(vv, rr, t0)
                if ok and res['stops'][-1]['end'] <= 720 and (placed is None or km - km0 < placed[0]):
                    placed = (km - km0, vv, rr, res['stops'][pos]['beg'])
            if placed: break
        if placed:
            extra[placed[1]] = placed[2]; c.update(now=placed[1], new=placed[3], dkm=round(placed[0], 1), fresh=True)
    for c in changes:
        vv = c['now']
        if vv is not None:
            r = cur.get(vv) or extra[vv]; t = 0 if vv in cur else t0
            c['new'] = next(s_['beg'] for s_ in simulate(vv, r, t)[0]['stops'] if s_['k'] == c['k'])
    after = [dict(simulate(vv, r)[0], t0=0) for vv, r in sorted(cur.items()) if r]
    after += [dict(simulate(vv, r, t0)[0], t0=t0) for vv, r in sorted(extra.items())]
    return dict(gone=gone, t0=t0, changes=changes, after=after)
TYPE = {'Подключение': 'Подключение', 'Локальная заявка': 'Ремонт', 'Дозаказ': 'Дозаказ', 'Глобальная проблема': 'Авария'}
out = dict(
    office=[office['lat'], office['lon']],
    orders=[dict(id=o['id'], lat=o['lat'], lon=o['lon'], district=o['district'], type=TYPE[o['bk']],
                 addr=o['addr'].replace('Город Москва, ', ''), a=int(ords[k][2]), b=int(ords[k][3]), wb=o['b'], svc=int(ords[k][1]))
            for k, o in enumerate(orders)],
    brigades=[dict(v=vv, mode=MODES[b[1]], mask=b[2]) for vv, b in enumerate(br)],
    plan=plan, base=basep, base_unserved=[k for k in range(n) if k not in served],
    explain={k: explain(k) for k in range(n) if any(k in r for r in best.values())},
    event=unavailable(3, 240),
)
json.dump(out, open('mockups/data.json', 'w'), ensure_ascii=False)
print('plan', len(plan), sum(r['km'] for r in plan), 'base', len(basep), sum(r['km'] for r in basep), 'unserved', len(out['base_unserved']))
for e in out['explain']: print(e)
