"""Column generation lower bounds + route enumeration exactness for the open heterogeneous VRPTW.
Usage: .venv/bin/python cg_master.py <instance.txt> [--ng 10] [--tl 600] [--enum-max 3000000]
Writes lb/<instdir>/<inst>.json and (if an improved solution is found) runs/results/<instdir>/cg/<inst>.out
Stages: 1) penalty LP (only if best known penalty > 0), 2) vehicles LP s.t. penalty <= Pbest,
        3) km LP s.t. penalty <= Pbest, vehicles <= Kbest, then enumeration of all elementary routes with
        reduced cost <= UB-LB and exact set-partitioning MIP (HiGHS) over them."""
import sys, os, json, time, math, subprocess, argparse
import numpy as np
import highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output
if os.environ.get('CG_POOL', '2') == '2': from cg_pool2 import Pool
else: from cg_pool import Pool

PEN = {1: 100, 2: 50, 3: 20}
HERE = os.path.dirname(os.path.abspath(__file__))
INF = highspy.kHighsInf


class Pricer:
    def __init__(self, inst, ng):
        self.p = subprocess.Popen([os.path.join(HERE, os.environ.get('CG_PRICER', 'bin/cg_price')), inst, str(ng)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        T = int(self.p.stdout.readline().split()[1]); self.types = []
        for _ in range(T):
            a = list(map(int, self.p.stdout.readline().split()))
            self.types.append(dict(count=a[0], start=a[1], mode=a[2], mask=a[3], vehs=a[4:]))

    def _send(self, head, alpha, pi, cuts=()):
        cuts = [c for c in cuts if c[3] < -1e-9]
        self.p.stdin.write(head + '\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
                           + f'{len(cuts)}\n' + ''.join(f'{a} {b} {c} {sg:.10f}\n' for a, b, c, sg in cuts))
        self.p.stdin.flush()

    def price(self, level, maxcols, beta, alpha, pi, labcap=3e6, cuts=()):
        self._send(f'PRICE {level} {maxcols} {beta} {labcap}', alpha, pi, cuts)
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2])
        mins = list(map(float, self.p.stdout.readline().split()[1:]))
        cols = []
        for _ in range(n):
            a = self.p.stdout.readline().split()
            cols.append((int(a[0]), float(a[1]), float(a[2]), tuple(int(x) for x in a[4:])))
        return cols, mins, comp

    def enum(self, gap, beta, maxroutes, fn, alpha, pi, cuts=()):
        self._send(f'ENUM {gap:.10f} {beta} {maxroutes} {fn}', alpha, pi, cuts)
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2])
        routes = []; self.last_rc = []
        for line in open(fn):
            a = line.split(); routes.append((int(a[0]), float(a[1]), tuple(int(x) for x in a[4:]))); self.last_rc.append(float(a[2]))
        os.remove(fn)
        return routes, comp

    def close(self):
        try: self.p.stdin.write('QUIT\n'); self.p.stdin.flush(); self.p.wait(5)
        except Exception: self.p.kill()


class Master:
    """rows: 0..N-1 order partition (=1), N..N+T-1 type count (<=), N+T total vehicles (<=K), N+T+1 penalty (<=P)"""
    def __init__(self, I, types):
        self.I, self.types = I, types; N, T = I['N'], len(types); self.N, self.T = N, T
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1); self.h = h
        lo = [1.0] * N + [-INF] * T + [-INF, -INF]; up = [1.0] * N + [float(t['count']) for t in types] + [INF, INF]
        for i in range(N + T + 2): h.addRow(lo[i], up[i], 0, np.array([], dtype=np.int32), np.array([], dtype=np.float64))
        self.pk = [PEN[o['pri']] for o in I['ords']]
        for k in range(N):  # y_k columns 0..N-1
            h.addCol(0.0, 0.0, 1.0, 2, np.array([k, N + T + 1], dtype=np.int32), np.array([1.0, float(self.pk[k])]))
        self.cols = []; self.key = set()   # (t, km, route)
        self.stage = 2; self.cuts = []; self.cutrow = []   # cuts: tuple(sorted triple)

    def add(self, t, km, r):
        k = (t, r)
        if k in self.key: return False
        self.key.add(k); self.cols.append((t, km, r))
        cnt = {}
        for x in r: cnt[x] = cnt.get(x, 0) + 1
        idx = sorted(cnt); vals = [float(cnt[i]) for i in idx]
        idx += [self.N + t, self.N + self.T]; vals += [1.0, 1.0]
        for S, row in zip(self.cuts, self.cutrow):
            c = sum(cnt.get(x, 0) for x in S) // 2
            if c: idx.append(row); vals.append(float(c))
        self.h.addCol(self.colcost(km), 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        return True

    def add_cut(self, S):
        S = tuple(sorted(S)); idx = []; vals = []
        for j, (t, km, r) in enumerate(self.cols):
            c = sum(r.count(x) for x in S) // 2
            if c: idx.append(self.N + j); vals.append(float(c))
        row = self.h.getNumRow()
        self.h.addRow(-INF, 1.0, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        self.cuts.append(S); self.cutrow.append(row)

    def separate(self, x, maxcuts=30):
        from collections import defaultdict
        import itertools
        acc = defaultdict(float); N = self.N
        for j in range(len(self.cols)):
            v = x[N + j]
            if v < 1e-6: continue
            r = self.cols[j][2]; cnt = {}
            for o in r: cnt[o] = cnt.get(o, 0) + 1
            ks = sorted(cnt); seen = set()
            for a, b in itertools.combinations(ks, 2):
                for c in range(N):
                    if c == a or c == b: continue
                    seen.add(tuple(sorted((a, b, c))))
            for key in seen:
                cc = sum(cnt.get(o, 0) for o in key) // 2
                if cc: acc[key] += v * cc
        have = set(self.cuts)
        viol = sorted(((v, k) for k, v in acc.items() if v > 1 + 1e-3 and k not in have and len(set(k)) == 3), reverse=True)
        out = []; used = defaultdict(int)
        for v, k in viol:
            if len(out) >= maxcuts: break
            if any(used[o] >= 5 for o in k): continue
            out.append(k)
            for o in k: used[o] += 1
        return out

    def colcost(self, km):
        return {1: 0.0, 2: 1.0, 3: km}[self.stage]

    def set_stage(self, stage, Pbest, K):
        self.stage = stage; h = self.h; N, T = self.N, self.T
        for k in range(N): h.changeColCost(k, float(self.pk[k]) if stage == 1 else 0.0)
        for j, (t, km, r) in enumerate(self.cols): h.changeColCost(N + j, self.colcost(km))
        h.changeRowBounds(N + T + 1, -INF, INF if stage == 1 else float(Pbest))
        h.changeRowBounds(N + T, -INF, float(K) if stage == 3 else INF)

    def solve(self):
        self.h.run(); st = self.h.getModelStatus()
        if st != highspy.HighsModelStatus.kOptimal: raise RuntimeError(f'LP status {st}')
        s = self.h.getSolution(); d = list(s.row_dual)
        return self.h.getInfo().objective_function_value, d, list(s.col_value)


def mip(I, types, cols, stage, Pbest, K, tl, extra_y=True):
    """exact set partitioning over the given columns; returns (status, obj, chosen cols, y served flags)"""
    N, T = I['N'], len(types); h = highspy.Highs(); h.setOptionValue('output_flag', False)
    h.setOptionValue('time_limit', float(tl)); h.setOptionValue('mip_rel_gap', 0.0); h.setOptionValue('threads', 1)
    for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
    for t in range(T): h.addRow(-INF, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
    h.addRow(-INF, float(K) if stage == 3 else INF, 0, np.array([], dtype=np.int32), np.array([]))
    h.addRow(-INF, float(Pbest), 0, np.array([], dtype=np.int32), np.array([]))
    pk = [PEN[o['pri']] for o in I['ords']]
    for k in range(N): h.addCol(0.0, 0.0, 1.0 if Pbest > 0 else 0.0, 2, np.array([k, N + T + 1], dtype=np.int32), np.array([1.0, float(pk[k])]))
    ok = []
    for (t, km, r) in cols:
        if len(set(r)) != len(r): continue  # non-elementary
        idx = sorted(r) + [N + t, N + T]
        h.addCol(1.0 if stage == 2 else km, 0.0, 1.0, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
        ok.append((t, km, r))
    nc = N + len(ok)
    h.changeColsIntegrality(nc, np.arange(nc, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * nc))
    rs = h.run(); st = h.getModelStatus()
    if str(rs) != 'HighsStatus.kOk': print('MIP run status', rs, st, flush=True)
    try: sol = list(h.getSolution().col_value)
    except Exception: sol = None
    info = h.getInfo()
    if sol is None or info.primal_solution_status != 2:
        return str(st), None, None, info.mip_dual_bound
    chosen = [ok[j] for j in range(len(ok)) if sol[N + j] > 0.5]
    return str(st), info.objective_function_value, chosen, info.mip_dual_bound


def routes_to_vehicles(types, chosen, V):
    R = [[] for _ in range(V)]; used = {t: 0 for t in range(len(types))}
    for (t, km, r) in chosen:
        v = types[t]['vehs'][used[t]]; used[t] += 1; R[v] = list(r)
    return R


def score(I, R):
    c = check(I, R)
    if not c['ok']: return None
    served = {k for r in R for k in r}
    return (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served), c['used'], c['km'])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('--ng', type=int, default=10)
    ap.add_argument('--tl', type=float, default=900); ap.add_argument('--enum-max', type=int, default=3000000)
    ap.add_argument('--mip-tl', type=float, default=120); ap.add_argument('--best', default=None)
    ap.add_argument('--out', default=None); ap.add_argument('--no-enum', action='store_true'); ap.add_argument('--pool-tl', type=float, default=600); ap.add_argument('--cuts', type=int, default=1)
    a = ap.parse_args()
    t0 = time.time(); inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    bestf = a.best or os.path.join(HERE, 'best', d, name + '.out')
    Rb, _ = parse_output(open(bestf).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    sb = score(I, Rb); assert sb, 'best solution invalid'
    Pbest, Kbest, UBkm = sb
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    log(name, 'N', N, 'V', I['V'], 'best', sb)
    pr = Pricer(inst, a.ng); types = pr.types; T = len(types)
    vtype = {}
    for t, ty in enumerate(types):
        for v in ty['vehs']: vtype[v] = t
    M = Master(I, types)
    res = dict(inst=name, dir=d, best_pen=Pbest, best_used=Kbest, best_km=round(UBkm, 3), types=T)

    def seed():
        for v, r in enumerate(Rb):
            if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
        for t in range(T):  # singletons
            for k in range(N):
                rr = [[]] * types[t]['vehs'][0] + [[k]]
                c = check(I, rr)
                if c['ok']: M.add(t, c['km'], (k,))

    def cg(stage, K, tl, cut_target=None, max_cuts=150):
        """returns (valid lower bound, lp value, converged flag, duals). If cut_target is given, 3-SRC cuts are
        separated after convergence until LP >= cut_target, no violated cut, stall, or time."""
        M.set_stage(stage, Pbest, K)
        beta = 1.0 if stage == 3 else 0.0; aobj = 0.0 if stage == 1 else (1.0 if stage == 2 else 0.0)
        level = 1; it = 0; bestLB = -1e18; ts = time.time(); hist = []
        while True:
            it += 1
            z, du, x = M.solve()
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]; sig = du[N + T + 1]
            cuts = [(S[0], S[1], S[2], du[row]) for S, row in zip(M.cuts, M.cutrow)]
            alpha = [aobj - mu[t] - lam for t in range(T)]
            cols, mins, comp = pr.price(level, 60 if level == 1 else 200, beta, alpha, pi, cuts=cuts)
            # Lagrangian bound (valid whenever mins are valid lower bounds, i.e. always: exact or F-bound)
            ycost = [(M.pk[k] if stage == 1 else 0.0) for k in range(N)]
            L = sum(pi) + lam * (K if stage == 3 else 0) + sig * (Pbest if stage != 1 else 0) + sum(c[3] for c in cuts)
            L += sum(types[t]['count'] * min(0.0, mins[t] + mu[t]) for t in range(T))
            L += sum(min(0.0, ycost[k] - pi[k] - sig * M.pk[k]) for k in range(N))
            bestLB = max(bestLB, L)
            added = sum(M.add(t, km, r) for (t, km, rc, r) in cols)
            if it % 10 == 1 or not added:
                log(f'  st{stage} it{it} lvl{level} z={z:.4f} L={L:.4f} best={bestLB:.4f} add={added} ncols={len(M.cols)} cuts={len(M.cuts)}')
            dual = (pi, mu, lam, sig, alpha, cuts)
            if not added:
                if level == 1: level = 2; continue
                if not comp: return bestLB, z, False, dual
                bestLB = max(bestLB, z)
                hist.append(z)
                if cut_target is None or z >= cut_target - 1e-6 or len(M.cuts) >= max_cuts or time.time() - ts > tl \
                        or (len(hist) >= 4 and hist[-1] - hist[-4] < 1e-3 * max(1.0, abs(z)) * 0.01):
                    return bestLB, z, True, dual
                new = M.separate(x, maxcuts=min(30, max_cuts - len(M.cuts)))
                if not new: return bestLB, z, True, dual
                for S in new: M.add_cut(S)
                log(f'  st{stage} +{len(new)} cuts (total {len(M.cuts)}), LP was {z:.4f}')
                level = 1; continue
            if time.time() - ts > tl:
                return bestLB, z, False, dual
            if level == 2 and added < 5: level = 1

    seed()
    # ---- stage 1: penalty
    if Pbest > 0:
        lb1, z1, conv1, _ = cg(1, 0, a.tl / 3)
        lb_pen = int(math.ceil(lb1 / 10 - 1e-6) * 10)
        res.update(lb_pen=lb_pen, lp_pen=z1, pen_conv=conv1)
        log('stage1 penalty LB', lb1, '->', lb_pen, 'best', Pbest)
    else:
        res.update(lb_pen=0)
    # ---- stage 2: vehicles
    lb2, z2, conv2, du2 = cg(2, 0, a.tl / 3, cut_target=(Kbest - 1 + 1e-4) if a.cuts else None)
    lb_used = int(math.ceil(lb2 - 1e-6))
    res.update(lb_used=lb_used, lp_used=z2, used_conv=conv2)
    log('stage2 vehicles LB', lb2, '->', lb_used, 'best', Kbest)
    newbest = None
    if lb_used < Kbest:
        st, obj, chosen, db = mip(I, types, M.cols, 2, Pbest, Kbest, a.mip_tl)
        log('  vehicle MIP over columns', st, obj, db)
        if chosen and obj is not None and round(obj) < Kbest:
            R = routes_to_vehicles(types, chosen, I['V']); s = score(I, R)
            log('  NEW vehicle count', s); newbest = (R, s)
        # enumeration to prove Kbest optimal: routes with rc <= (Kbest-1) - LP
        if conv2 and not a.no_enum and newbest is None:
            pi, mu, lam, sig, alpha, cuts = du2
            gap = (Kbest - 1) - z2 + 1e-6
            routes, comp = pr.enum(gap, 0.0, a.enum_max, f'/tmp/cg_enum_{os.getpid()}.txt', alpha, pi, cuts)
            log(f'  enum vehicles gap={gap:.4f} routes={len(routes)} complete={comp}')
            if comp:
                st, obj, chosen, db = mip(I, types, routes, 2, Pbest, Kbest, a.mip_tl * 2)
                log('  enum vehicle MIP', st, obj, db)
                if 'Infeasible' in st or (obj is not None and obj > Kbest - 1 + 1e-6):
                    lb_used = Kbest; res.update(lb_used=lb_used, used_proof='enum')
                elif chosen and obj is not None:
                    R = routes_to_vehicles(types, chosen, I['V']); s = score(I, R); log('  NEW vehicle count (enum)', s); newbest = (R, s)
    # ---- stage 3: km with K = Kbest
    K = Kbest if newbest is None else newbest[1][1]
    lb3, z3, conv3, du3 = cg(3, K, a.tl / 3, cut_target=UBkm if a.cuts else None)
    res.update(lb_km=round(lb3 - 1e-6, 4), lp_km=z3, km_conv=conv3, km_K=K)
    log('stage3 km LB', lb3, 'LP', z3, 'best', UBkm)
    UB = UBkm if newbest is None else newbest[1][2]
    st, obj, chosen, db = mip(I, types, M.cols, 3, Pbest, K, a.mip_tl)
    log('  km MIP over columns', st, obj, db)
    if chosen and obj is not None and obj < UB - 1e-4:
        R = routes_to_vehicles(types, chosen, I['V']); s = score(I, R); log('  NEW km', s)
        if s and s[0] <= Pbest and s[1] <= K: newbest = (R, s); UB = s[2]
    proven_km = False
    if conv3 and not a.no_enum and UB - lb3 > 1e-6:
        pi, mu, lam, sig, alpha, cuts = du3
        gap = UB - z3 + 1e-6
        routes, comp = pr.enum(gap, 1.0, a.enum_max, f'/tmp/cg_enum_{os.getpid()}.txt', alpha, pi, cuts)
        log(f'  enum km gap={gap:.4f} routes={len(routes)} complete={comp}')
        res.update(enum_routes=len(routes), enum_complete=bool(comp))
        if comp:
            P = Pool(I, types, routes, 3, Pbest, K, log=log); P.cuts = [frozenset(S) for S in M.cuts]
            pr_res = P.run(UB, time_budget=a.pool_tl, mip_tl=a.pool_tl * 0.7, **({'rc0': pr.last_rc} if 'rc0' in P.run.__code__.co_varnames else {}))
            log('  pool result', {k: v for k, v in pr_res.items() if k != 'chosen'})
            res.update(lb_km=round(max(res['lb_km'], pr_res['lb'] - 1e-4), 4), pool_status=pr_res['status'], pool_cuts=pr_res.get('cuts'), pool_lp=pr_res.get('lp'))
            if pr_res['status'] in ('optimal', 'infeasible', 'lp_closed'): proven_km = True
            if pr_res.get('chosen') and pr_res['obj'] < UB - 1e-4:
                R = routes_to_vehicles(types, pr_res['chosen'], I['V']); s = score(I, R); log('  NEW km (pool)', s)
                if s and s[0] <= Pbest and s[1] <= K: newbest = (R, s); UB = s[2]
            if pr_res['status'] == 'optimal': res.update(lb_km=round(min(UB, pr_res['lb']) - 1e-4, 4))
    elif conv3 and UB - lb3 <= 1e-6:
        proven_km = True
    if newbest:
        R, s = newbest
        od = a.out or os.path.join(HERE, 'results', d, 'cg'); os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, name + '.out'), 'w') as f:
            f.write('SOLVER cg\n')
            for v, r in enumerate(R): f.write(f"ROUTE {v} {' '.join(map(str, r))}\n")
        res.update(new_solution=list(s))
    pen_ok = res['lb_pen'] >= (newbest[1][0] if newbest else Pbest)
    used_ok = res['lb_used'] >= K
    res.update(proven_optimal=bool(pen_ok and used_ok and proven_km), method=f'CG ng{a.ng} + enum/MIP', seconds=round(time.time() - t0, 1))
    pr.close()
    od = os.path.join(HERE, 'lb', d); os.makedirs(od, exist_ok=True)
    json.dump(res, open(os.path.join(od, name + '.json'), 'w'), indent=1)
    log('RESULT', json.dumps(res))


if __name__ == '__main__':
    main()
