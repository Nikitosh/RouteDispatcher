"""Cheap lower bounds for comparison with CG.  Usage: cg_cheap.py <dir> [...]
vehicles: max of (a) time-window load bound over all intervals, for all orders and per skill group,
          (b) greedy clique of pairwise route-incompatible orders.
km (with <= Kbest vehicles, all orders served): assignment bound (each order gets exactly one predecessor:
          another order or one of K depot slots; arc cost = min over vehicle types, time-infeasible arcs forbidden).
Writes lb/<dir>/_cheap.json"""
import sys, os, glob, json, math
import numpy as np
from scipy.optimize import linear_sum_assignment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance

def types_of(I):
    ty = {}
    for v in I['veh']: ty[(v['start'], v['mode'], v['mask'])] = ty.get((v['start'], v['mode'], v['mask']), 0) + 1
    return ty

def can_follow(I, ty, i, j):
    """some type can do j right after i (earliest)"""
    oi, oj = I['ords'][i], I['ords'][j]; S = I['S']
    for (s, m, mask) in ty:
        if not ((mask >> oi['skill']) & 1 and (mask >> oj['skill']) & 1): continue
        st_i = max(I['T'][m][s][S + i], oi['a'])
        if st_i > oi['b'] + 1e-6: continue
        st_j = max(st_i + oi['svc'] + I['T'][m][S + i][S + j], oj['a'])
        if st_j <= oj['b'] + 1e-6 and st_j + oj['svc'] <= 720 + 1e-6: return True
    return False

def load_bound(I, ks):
    if not ks: return 0
    pts = sorted(set([I['ords'][k]['a'] for k in ks] + [I['ords'][k]['b'] + I['ords'][k]['svc'] for k in ks] + [I['ords'][k]['b'] for k in ks] + [I['ords'][k]['a'] + I['ords'][k]['svc'] for k in ks]))
    best = 0
    for x in range(len(pts)):
        for y in range(x + 1, len(pts)):
            s, e = pts[x], pts[y]; tot = 0.0
            for k in ks:
                o = I['ords'][k]; p = o['svc']
                ov = lambda st: max(0.0, min(e, st + p) - max(s, st))
                tot += min(ov(o['a']), ov(o['b']))
            best = max(best, math.ceil(tot / (e - s) - 1e-9))
    return best

def main():
    for d in sys.argv[1:]:
        out = {}
        for p in sorted(glob.glob(f'{d}/*.txt')):
            name = os.path.basename(p)[:-4]; I = load_instance(p); N = I['N']; ty = types_of(I)
            ks = list(range(N))
            lb_load = load_bound(I, ks)
            for sk in set(o['skill'] for o in I['ords']):
                lb_load = max(lb_load, load_bound(I, [k for k in ks if I['ords'][k]['skill'] == sk]))
            comp = [[i != j and (can_follow(I, ty, i, j) or can_follow(I, ty, j, i)) for j in range(N)] for i in range(N)]
            # greedy clique of mutually incompatible orders, several starts
            clique = 0
            order = sorted(ks, key=lambda k: -sum(1 for j in ks if j != k and not comp[k][j]))
            for st in order[:N]:
                C = [st]
                for k in order:
                    if k != st and all(not comp[k][c] for c in C): C.append(k)
                clique = max(clique, len(C))
            meta = json.load(open(f'best/{d}/_meta.json')).get(name) if os.path.exists(f'best/{d}/_meta.json') else None
            K = meta['used'] if meta else I['V']
            # assignment bound
            S = I['S']; BIG = 1e7
            C = np.full((N, N + K), BIG)
            for j in range(N):
                oj = I['ords'][j]
                for i in range(N):
                    if i != j and can_follow(I, ty, i, j):
                        C[j, i] = min(I['D'][m][S + i][S + j] for (s, m, mask) in ty if (mask >> oj['skill']) & 1 and (mask >> I['ords'][i]['skill']) & 1)
                dep = BIG
                for (s, m, mask) in ty:
                    if (mask >> oj['skill']) & 1 and max(I['T'][m][s][S + j], oj['a']) <= oj['b'] + 1e-6: dep = min(dep, I['D'][m][s][S + j])
                C[j, N:] = dep
            r, c = linear_sum_assignment(C); akm = float(C[r, c].sum())
            out[name] = dict(lb_used_load=lb_load, lb_used_clique=clique, lb_used_cheap=max(lb_load, clique), K=K,
                             lb_km_assign=round(akm, 3) if akm < BIG else None)
            print(name, out[name], flush=True)
        os.makedirs(f'lb/{d}', exist_ok=True); json.dump(out, open(f'lb/{d}/_cheap.json', 'w'), indent=1)

if __name__ == '__main__':
    main()
