"""Branch-and-price (best-first) for the km stage (penalty <= Pbest, vehicles <= K), to tighten lb_km on hard
instances.  Branching: (1) vehicles per type (row bounds), (2) arc flow i->j (forbid / force).
Node bound = converged CG LP (exact ng pricing + global 3-SRC cuts); unconverged node -> parent bound.
Usage: cg_bp.py <instance> [--tl 1200]   (updates lb/<dir>/<inst>.json if lb_km improves; saves better solutions)"""
import sys, os, json, time, math, heapq, argparse
import numpy as np
import highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output
import cg_master as CM

HERE = os.path.dirname(os.path.abspath(__file__)); INF = highspy.kHighsInf


class BPPricer(CM.Pricer):
    forb = []
    def _send(self, head, alpha, pi, cuts=()):
        cuts = [c for c in cuts if c[3] < -1e-9]
        self.p.stdin.write(head + '\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
                           + f'{len(cuts)}\n' + ''.join(f'{a} {b} {c} {sg:.10f}\n' for a, b, c, sg in cuts)
                           + f'{len(self.forb)}\n' + ''.join(f'{i} {j}\n' for i, j in self.forb))
        self.p.stdin.flush()


def arcs_of(r):
    out = [(-1, r[0])] + [(r[i], r[i + 1]) for i in range(len(r) - 1)] + [(r[-1], -1)]
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('--tl', type=float, default=1200)
    ap.add_argument('--ng', type=int, default=10); ap.add_argument('--node-tl', type=float, default=120)
    a = ap.parse_args(); t0 = time.time()
    inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    Rb, _ = parse_output(open(os.path.join(HERE, 'best', d, name + '.out')).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    Pbest, K, UB = CM.score(I, Rb)
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    lbf = os.path.join(HERE, 'lb', d, name + '.json'); L0 = json.load(open(lbf)) if os.path.exists(lbf) else {}
    log(name, 'best', (Pbest, K, UB), 'current lb_km', L0.get('lb_km'))
    pr = BPPricer.__new__(BPPricer)
    os.environ['CG_PRICER'] = 'bin/cg_price_bp'; CM.Pricer.__init__(pr, inst, a.ng); pr.forb = []
    types = pr.types; T = len(types)
    vtype = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
    M = CM.Master(I, types); M.stage = 3
    for v, r in enumerate(Rb):
        if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
    for t in range(T):
        for k in range(N):
            c = check(I, [[]] * types[t]['vehs'][0] + [[k]])
            if c['ok']: M.add(t, c['km'], (k,))
    M.set_stage(3, Pbest, K)
    big = 10.0 * UB
    M.set_types([0] * T, [ty['count'] for ty in types], big)   # adds artificial columns (keep LP feasible)
    best = [UB, None]

    def colok(col, forb_set):
        t, km, r = col
        for (i, j) in arcs_of(r):
            if (i, j) in forb_set: return False
        return True

    def apply(node):
        lo, up, forb = node['lo'], node['up'], node['forb']
        for t in range(T): M.h.changeRowBounds(N + t, float(lo[t]), float(up[t]))
        fs = set(forb)
        for j, col in enumerate(M.cols): M.h.changeColBounds(M.hidx[j], 0.0, INF if colok(col, fs) else 0.0)
        pr.forb = list(forb)
        return fs

    def solve_node(node, tl):
        fs = apply(node); ts = time.time(); level = 1; hist = []
        while True:
            z, du, x = M.solve()
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            cuts = [(S[0], S[1], S[2], du[row]) for S, row in zip(M.cuts, M.cutrow)]
            alpha = [0.0 - mu[t] - lam for t in range(T)]
            cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 1.0, alpha, pi, cuts=cuts)
            added = 0
            for (t, km, rc, r) in cols:
                if M.add(t, km, r):
                    added += 1
                    if not colok((t, km, r), fs): M.h.changeColBounds(M.hidx[-1], 0.0, 0.0)
            if not added:
                if level == 1: level = 2; continue
                if not comp: return None, x
                hist.append(z)
                if z >= best[0] - 1e-6 or len(M.cuts) >= 150 or (len(hist) >= 3 and hist[-1] - hist[-3] < 1e-3):
                    return z, x
                new = M.separate(x, maxcuts=min(30, 150 - len(M.cuts)))
                if not new: return z, x
                for S in new: M.add_cut(S)
                level = 1; continue
            if time.time() - ts > tl: return None, x
            if level == 2 and added < 5: level = 1

    root = dict(lo=[0] * T, up=[ty['count'] for ty in types], forb=(), bound=-1e18, depth=0)
    heap = [(-1e18, 0, root)]; cnt = 1; nodes = 0; globalLB = -1e18
    while heap and time.time() - t0 < a.tl:
        bnd, _, node = heapq.heappop(heap)
        if bnd >= best[0] - 1e-6: continue
        z, x = solve_node(node, a.node_tl); nodes += 1
        if x is None or len(x) < M.h.getNumCol():   # устаревшее решение: пересчитать LP
            M.h.run(); x = list(M.h.getSolution().col_value)
        if z is None: z = node['bound']  # unconverged: inherit parent bound (still valid)
        if z >= best[0] - 1e-6: continue
        # artificial in solution -> LP infeasible for this node
        if any(x[c] > 1e-6 for c in M.art): continue
        # branching candidates
        tval = [0.0] * T; arc = {}
        integral = True
        for j, (t, km, r) in enumerate(M.cols):
            v = x[M.hidx[j]]
            if v < 1e-7: continue
            if v < 1 - 1e-6: integral = False
            tval[t] += v
            for e in arcs_of(r):
                if e[0] >= 0 and e[1] >= 0: arc[e] = arc.get(e, 0.0) + v
        if integral:
            sol = [(t, km, r) for j, (t, km, r) in enumerate(M.cols) if x[M.hidx[j]] > 0.5]
            R = CM.routes_to_vehicles(types, sol, I['V']); s = CM.score(I, R)
            if s and s[0] <= Pbest and s[1] <= K and s[2] < best[0] - 1e-6:
                best[0] = s[2]; best[1] = R; log(f'  NEW incumbent {s}')
            continue
        ft = [(abs(tval[t] - round(tval[t])), t) for t in range(T) if abs(tval[t] - round(tval[t])) > 1e-4]
        children = []
        if ft:
            _, t = max(ft); v = tval[t]
            c1 = dict(node); c1['up'] = list(node['up']); c1['up'][t] = math.floor(v)
            c2 = dict(node); c2['lo'] = list(node['lo']); c2['lo'][t] = math.ceil(v)
            children = [c1, c2]; desc = f'type {t} {v:.3f}'
        else:
            fa = [(-abs(v - 0.5), e) for e, v in arc.items() if 1e-4 < v < 1 - 1e-4]
            if not fa:
                log('  no fractional arc but non-integral solution; skipping node (bound kept)'); globalLB = min(globalLB, z) if globalLB > -1e17 else z; continue
            _, (i, j) = max(fa)
            c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((i, j),)
            force = [(i, k) for k in range(N) if k != j] + [(k, j) for k in range(N) if k != i] + [(-1, j), (i, -1)]
            c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple(force)
            children = [c1, c2]; desc = f'arc {i}->{j} {arc[(i, j)]:.3f}'
        for c in children:
            c['bound'] = z; c['depth'] = node['depth'] + 1
            heapq.heappush(heap, (z, cnt, c)); cnt += 1
        if nodes % 40 == 0:   # price-and-branch heuristic over current columns for incumbents
            st, obj, chosen, db = CM.mip(I, types, M.cols, 3, Pbest, K, 15)
            if chosen and obj is not None and obj < best[0] - 1e-6:
                R = CM.routes_to_vehicles(types, chosen, I['V']); s = CM.score(I, R)
                if s and s[0] <= Pbest and s[1] <= K and s[2] < best[0] - 1e-6: best[0] = s[2]; best[1] = R; log(f'  NEW incumbent (MIP) {s}')
        openb = min([h[0] for h in heap] + [best[0]])
        if nodes % 5 == 1 or len(heap) < 3:
            log(f'  node {nodes} depth {node["depth"]} z={z:.4f} branch {desc}; open {len(heap)} LB={openb:.4f} UB={best[0]:.4f} cuts={len(M.cuts)} cols={len(M.cols)}')
    openb = min([h[0] for h in heap if h[0] < best[0]] + [best[0]])
    if globalLB > -1e17: openb = min(openb, globalLB)
    exhausted = not any(h[0] < best[0] - 1e-6 for h in heap)
    log(f'B&P done: nodes {nodes}, LB {openb:.4f}, UB {best[0]:.4f}, exhausted {exhausted}')
    pr.close()
    if best[1] is not None:
        od = os.path.join(HERE, 'results', d, 'cg'); os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, name + '.out'), 'w') as f:
            f.write('SOLVER cg_bp\n')
            for v, r in enumerate(best[1]): f.write(f"ROUTE {v} {' '.join(map(str, r))}\n")
    L = json.load(open(lbf)) if os.path.exists(lbf) else {}
    if L and L.get('lb_used') == K and openb - 1e-4 > (L.get('lb_km') or -1):
        L['lb_km'] = round(openb - 1e-4, 4); L['method'] = L.get('method', '') + ' + B&P'
        L['bp'] = dict(nodes=nodes, lb=openb, ub=best[0], exhausted=exhausted, seconds=round(time.time() - t0, 1))
        if exhausted: L['proven_optimal'] = bool(L.get('lb_pen', 0) >= Pbest)
        json.dump(L, open(lbf, 'w'), indent=1); log('lb json updated', L['lb_km'])


if __name__ == '__main__':
    main()
