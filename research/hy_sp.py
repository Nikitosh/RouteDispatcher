"""Разбиение на множества (CP-SAT) по пулу маршрутов: все известные решения (best.py-источники + hy_runs) +
столбцы COL из s40_hy (COLS-режим, разбиения пар) + подмаршруты без одной заявки. Бригады группируются по типу
(старт, режим, навыки): на тип не больше числа бригад этого типа.
python3 hy_sp.py <instdir> <inst> [tl=20] [colfile ...]   -> печать ROUTE, запись в runs/results/<instdir>/hy/<inst>.out если лучше"""
import glob, os, sys, time
from collections import defaultdict
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
def feas(I, v, rt):
    ve = I['veh'][v]; m = ve['mode']; t = 0; prev = ve['start']; km = 0; T = I['T'][m]; D = I['D'][m]; S = I['S']
    for k in rt:
        o = I['ords'][k]
        if not (ve['mask'] >> o['skill']) & 1: return None
        n = S + k; beg = max(t + T[prev][n], o['a'])
        if beg > o['b'] + 1e-6: return None
        t = beg + o['svc']
        if t > 720 + 1e-6: return None
        km += D[prev][n]; prev = n
    return km
def score(I, routes):
    c = check(I, routes)
    if not c['ok']: return None
    served = {k for r in routes for k in r}
    return (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served), c['used'], round(c['km'], 3))
_COL = {}
def solutions(d, inst):
    import best
    if d not in _COL: _COL[d] = best.collect(d)
    out = [r for r, tag in _COL[d].get(inst, [])]
    for f in glob.glob(f'runs/hy_runs/{d}/*/{inst}.out'):
        r, _ = parse_output(open(f).read())
        if r: out.append(r)
    return out
def solve(d, inst, tl=20, colfiles=(), extra_sols=(), subroutes=True, log=True):
    from ortools.sat.python import cp_model
    I = load_instance(f'{d}/{inst}.txt'); N, V = I['N'], I['V']
    rep = []
    for v, ve in enumerate(I['veh']):
        key = (ve['start'], ve['mode'], ve['mask'])
        rep.append(next((u for u in range(v) if (I['veh'][u]['start'], I['veh'][u]['mode'], I['veh'][u]['mask']) == key), v))
    types = sorted(set(rep)); cnt = {t: rep.count(t) for t in types}
    sols = solutions(d, inst) + list(extra_sols)
    sols = [(r + [[]] * V)[:V] for r in sols]
    scored = [(score(I, r), r) for r in sols]; scored = [x for x in scored if x[0]]
    scored.sort(key=lambda x: x[0]); best0 = scored[0] if scored else None
    cand = {}   # (type, tuple(set)) -> (km, seq)
    def add(t, seq, km=None):
        if km is None:
            km = feas(I, t, seq)
            if km is None: return
        key = (t, frozenset(seq))
        if key not in cand or cand[key][0] > km + 1e-9: cand[key] = (km, tuple(seq))
    raw = set()
    for sc, r in scored:
        for v, rt in enumerate(r):
            if rt: raw.add(tuple(rt))
    for rt in raw:
        for t in types:
            add(t, rt)
            if subroutes:
                for i in range(len(rt)): 
                    if len(rt) > 1: add(t, rt[:i] + rt[i + 1:])
    for cf in colfiles:
        for line in open(cf):
            p = line.split()
            if p and p[0] == 'COL': add(int(p[1]), [int(x) for x in p[3:]], float(p[2]))
    items = list(cand.items())
    m = cp_model.CpModel(); x = [m.NewBoolVar('') for _ in items]
    byk = defaultdict(list); byt = defaultdict(list)
    for i, ((t, s), (km, seq)) in enumerate(items):
        byt[t].append(x[i]); [byk[k].append(x[i]) for k in seq]
    served = [m.NewBoolVar('') for _ in range(N)]
    for k in range(N): m.Add(sum(byk[k]) == served[k])
    for t in types: m.Add(sum(byt[t]) <= cnt[t])
    m.Minimize(sum(int(PEN[I['ords'][k]['pri']] * 1e9) * (1 - served[k]) for k in range(N)) +
               sum((10_000_000 + int(round(km * 1000))) * x[i] for i, (_, (km, seq)) in enumerate(items)))
    if best0:
        hint = defaultdict(int)
        for v, rt in enumerate(best0[1]):
            if rt: hint[(rep[v], frozenset(rt))] += 1
        for i, ((t, s), _) in enumerate(items): m.AddHint(x[i], 1 if hint.get((t, s)) else 0)
    sv = cp_model.CpSolver(); sv.parameters.max_time_in_seconds = tl; sv.parameters.num_workers = int(os.environ.get('SPW', 2))
    t0 = time.time(); st = sv.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE): return best0, None, len(items)
    routes = [[] for _ in range(V)]; free = defaultdict(list)
    for v in range(V): free[rep[v]].append(v)
    for i, ((t, s), (km, seq)) in enumerate(items):
        if sv.Value(x[i]): routes[free[t].pop(0)] = list(seq)
    sc = score(I, routes)
    if log: print(f"{inst:34} pool={len(items):7d} src={best0[0] if best0 else None} sp={sc} {time.time()-t0:5.1f}s {'OPT' if st == cp_model.OPTIMAL else ''}", flush=True)
    return best0, (sc, routes), len(items)
def save(d, inst, sc, routes, tag='hy_sp'):
    I = load_instance(f'{d}/{inst}.txt')
    os.makedirs(f'runs/results/{d}/hy', exist_ok=True); f = f'runs/results/{d}/hy/{inst}.out'
    if os.path.exists(f):
        r0, _ = parse_output(open(f).read()); s0 = score(I, (r0 + [[]] * I['V'])[:I['V']]) if r0 else None
        if s0 and s0 <= sc: return False
    with open(f, 'w') as fo:
        fo.write(f"SOLVER {tag}\n"); [fo.write(f"ROUTE {v} {' '.join(map(str, r))}\n") for v, r in enumerate(routes)]
    return True
if __name__ == '__main__':
    d, inst = sys.argv[1], sys.argv[2]; tl = float(sys.argv[3]) if len(sys.argv) > 3 else 20
    b0, res, n = solve(d, inst, tl, sys.argv[4:])
    if res and res[0]: print('saved' if save(d, inst, *res) else 'not better than saved')
