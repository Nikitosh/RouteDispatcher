"""cgx: km-stage column generation / branch-and-price with the cgx pricer (solvers/cgx_price.cpp).
Stage: penalty 0 (all orders served), vehicles <= K (K = best known), min km.
Usage: .venv/bin/python cgx_bp.py <instance.txt> [--tl 1800] [--node-tl 300] [--K k] [--write]
Writes lb/<dir>/<inst>.json only with --write and only if the bound is higher than the stored one (and valid).
Better solutions -> runs/results/<dir>/cgx/<inst>.out"""
import sys, os, json, time, math, heapq, argparse, itertools, subprocess
from collections import defaultdict
import numpy as np
import highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output

HERE = os.path.dirname(os.path.abspath(__file__)); INF = highspy.kHighsInf
PEN = {1: 100, 2: 50, 3: 20}


def score(I, R):
    c = check(I, R)
    if not c['ok']: return None
    served = {k for r in R for k in r}
    return (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served), c['used'], c['km'])


class Pricer:
    def __init__(self, inst, args):
        self.p = subprocess.Popen([os.path.join(HERE, args.pricer), inst] + args.pargs.split(), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        T = int(self.p.stdout.readline().split()[1]); self.types = []
        for _ in range(T):
            a = list(map(int, self.p.stdout.readline().split()))
            self.types.append(dict(count=a[0], start=a[1], mode=a[2], mask=a[3], vehs=a[4:]))
        self.forb = []; self.tsum = 0.0; self.ncalls = 0

    def _send(self, head, alpha, pi, cuts):
        cuts = [c for c in cuts if c[3] < -1e-9]
        self.p.stdin.write(head + '\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
                           + f'{len(cuts)}\n' + ''.join(f'{a} {b} {c} {sg:.10f} ' + ('-1' if mem is None else f'{len(mem)} ' + ' '.join(map(str, mem))) + '\n' for a, b, c, sg, mem in cuts)
                           + f'{len(self.forb)}\n' + ''.join(f'{i} {j}\n' for i, j in self.forb))
        self.p.stdin.flush()

    def price(self, level, maxcols, alpha, pi, cuts, labcap=5e6):
        t0 = time.time()
        self._send(f'PRICE {level} {maxcols} 1.0 {labcap}', alpha, pi, cuts)
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2])
        mins = list(map(float, self.p.stdout.readline().split()[1:]))
        cols = []
        for _ in range(n):
            a = self.p.stdout.readline().split()
            cols.append((int(a[0]), float(a[1]), float(a[2]), tuple(int(x) for x in a[4:])))
        self.tsum += time.time() - t0; self.ncalls += 1; self.last = time.time() - t0
        return cols, mins, comp

    def enum(self, gap, maxroutes, fn, alpha, pi, cuts):
        self._send(f'ENUM {gap:.10f} 1.0 {maxroutes} {fn}', alpha, pi, cuts)
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2])
        routes = []; rcs = []
        for line in open(fn):
            a = line.split(); routes.append((int(a[0]), float(a[1]), tuple(int(x) for x in a[4:]))); rcs.append(float(a[2]))
        os.remove(fn)
        return routes, rcs, comp

    def close(self):
        try: self.p.stdin.write('QUIT\n'); self.p.stdin.flush(); self.p.wait(5)
        except Exception: self.p.kill()


def lmcoef(r, S, mem):
    if mem is None: return sum(1 for k in r if k in S) // 2
    st = 0; c = 0
    for k in r:
        if k not in mem: st = 0; continue
        if k in S:
            st += 1
            if st == 2: c += 1; st = 0
    return c


def arcs_of(r):
    return [(-1, r[0])] + [(r[i], r[i + 1]) for i in range(len(r) - 1)] + [(r[-1], -1)]


class Master:
    """rows: 0..N-1 orders (=1), N..N+T-1 type rows [lo,up], N+T total vehicles (<=K), then cut rows (<=1)"""
    def __init__(self, I, types, K, big):
        self.I, self.types = I, types; N, T = I['N'], len(types); self.N, self.T, self.K = N, T, K
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1); self.h = h
        lo = [1.0] * N + [0.0] * T + [-INF]; up = [1.0] * N + [float(t['count']) for t in types] + [float(K)]
        self.rlo, self.rup = lo[:], up[:]
        for i in range(N + T + 1): h.addRow(lo[i], up[i], 0, np.array([], dtype=np.int32), np.array([], dtype=np.float64))
        self.art = []
        for k in range(N):
            self.art.append(h.getNumCol()); h.addCol(big, 0.0, INF, 1, np.array([k], dtype=np.int32), np.array([1.0]))
        for t in range(T):   # for lower bounds on type rows
            self.art.append(h.getNumCol()); h.addCol(big, 0.0, INF, 1, np.array([N + t], dtype=np.int32), np.array([1.0]))
        self.cols = []; self.key = {}; self.hidx = []; self.cuts = []; self.cutrow = []; self.cutmem = []; self.cutsof = {}

    def add(self, t, km, r):
        k = (t, r)
        if k in self.key: return -1
        self.key[k] = len(self.cols); self.cols.append((t, km, r)); self.hidx.append(self.h.getNumCol())
        cnt = defaultdict(int)
        for x in r: cnt[x] += 1
        idx = sorted(cnt); vals = [float(cnt[i]) for i in idx]
        idx += [self.N + t, self.N + self.T]; vals += [1.0, 1.0]
        hits = defaultdict(int)
        for x in cnt:
            for ci in self.cutsof.get(x, ()): hits[ci] += cnt[x]
        for ci, hc in hits.items():
            if hc < 2: continue
            c = lmcoef(r, self.cuts[ci], self.cutmem[ci])
            if c: idx.append(self.cutrow[ci]); vals.append(float(c))
        self.h.addCol(km, 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        return len(self.cols) - 1

    def add_cut(self, S, mem=None):
        S = tuple(sorted(S)); idx = []; vals = []
        for j, (t, km, r) in enumerate(self.cols):
            c = lmcoef(r, S, mem)
            if c: idx.append(self.hidx[j]); vals.append(float(c))
        row = self.h.getNumRow()
        self.h.addRow(-INF, 1.0, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        for x in S: self.cutsof.setdefault(x, []).append(len(self.cuts))
        self.cuts.append(S); self.cutrow.append(row); self.cutmem.append(mem); self.rlo.append(-INF); self.rup.append(1.0)

    def augment(self, x):
        """enlarge memories of cuts violated in full-memory form but not in lm form; returns number of cuts changed"""
        sup = [(x[self.hidx[j]], j) for j in range(len(self.cols)) if x[self.hidx[j]] > 1e-6]
        nch = 0
        for ci, (S, row, mem) in enumerate(zip(self.cuts, self.cutrow, self.cutmem)):
            if mem is None: continue
            Ss = set(S); full = 0.0; lm = 0.0
            for v, j in sup:
                r = self.cols[j][2]
                if sum(1 for k in r if k in Ss) >= 2:
                    full += v; lm += v * lmcoef(r, S, mem)
            if full > 1 + 1e-3 and full - lm > 1e-3:
                nm = set(mem)
                for v, j in sup:
                    r = self.cols[j][2]; pos = [i for i, k in enumerate(r) if k in Ss]
                    if len(pos) >= 2: nm.update(r[pos[0]:pos[-1] + 1])
                nm = frozenset(nm); self.cutmem[ci] = nm; nch += 1
                for j, (t, km, r) in enumerate(self.cols):
                    c0 = lmcoef(r, S, mem); c1 = lmcoef(r, S, nm)
                    if c0 != c1: self.h.changeCoeff(row, self.hidx[j], float(c1))
        return nch

    def memory_for(self, S, x):
        """limited memory: S plus nodes between the S-visits of every support route touching S at least twice"""
        mem = set(S); Ss = set(S)
        for j in range(len(self.cols)):
            if x[self.hidx[j]] < 1e-6: continue
            r = self.cols[j][2]; pos = [i for i, k in enumerate(r) if k in Ss]
            if len(pos) >= 2: mem.update(r[pos[0]:pos[-1] + 1])
        return frozenset(mem)

    def separate(self, x, maxcuts=30, per_order=5):
        acc = defaultdict(float); N = self.N
        for j in range(len(self.cols)):
            v = x[self.hidx[j]]
            if v < 1e-6: continue
            ks = sorted(set(self.cols[j][2])); seen = set()
            for a, b in itertools.combinations(ks, 2):
                for c in range(N):
                    if c == a or c == b: continue
                    seen.add(tuple(sorted((a, b, c))))
            for key in seen: acc[key] += v
        have = set(self.cuts)
        viol = sorted(((v, k) for k, v in acc.items() if v > 1 + 1e-3 and k not in have), reverse=True)
        out = []; used = defaultdict(int)
        for v, k in viol:
            if len(out) >= maxcuts: break
            if any(used[o] >= per_order for o in k): continue
            out.append(k)
            for o in k: used[o] += 1
        return out

    def purge(self, maxcols, nseed):
        """delete the columns with the largest reduced cost (they can be regenerated by pricing)"""
        n = len(self.cols)
        if n <= maxcols: return 0
        sol = self.h.getSolution(); cd = sol.col_dual; cv = sol.col_value
        rc = np.array([cd[h] for h in self.hidx]); xv = np.array([cv[h] for h in self.hidx])
        keep = np.zeros(n, dtype=bool); keep[np.argsort(rc)[:int(maxcols * 0.6)]] = True
        keep |= xv > 1e-9; keep[:nseed] = True
        dele = np.array([self.hidx[j] for j in range(n) if not keep[j]], dtype=np.int32)
        self.h.deleteCols(len(dele), dele)
        base = min(self.hidx)
        self.cols = [self.cols[j] for j in range(n) if keep[j]]
        self.hidx = list(range(base, base + len(self.cols)))
        self.key = {(t, r): j for j, (t, km, r) in enumerate(self.cols)}
        return len(dele)

    def solve(self):
        self.h.run(); st = self.h.getModelStatus()
        if st != highspy.HighsModelStatus.kOptimal:
            raise RuntimeError(f'LP status {st}')
        s = self.h.getSolution()
        return self.h.getInfo().objective_function_value, list(s.row_dual), list(s.col_value)

    def dual_obj(self, du):
        """sum_i dual_i * active bound_i (lo if dual>0 else up)"""
        v = 0.0
        for i, d in enumerate(du):
            if abs(d) < 1e-12: continue
            b = self.rlo[i] if d > 0 else self.rup[i]
            if abs(b) >= INF: return -1e18
            v += d * b
        return v

    def set_row(self, i, lo, up):
        self.rlo[i], self.rup[i] = lo, up; self.h.changeRowBounds(i, lo, up)


def load_pool_solutions(I, d, name, vtype, maxfiles=400):
    """routes from all known solutions of this instance (warm start columns)"""
    import glob
    out = set()
    fs = glob.glob(os.path.join(HERE, 'results', d, '**', name + '.out'), recursive=True)
    for f in fs[:maxfiles]:
        try: R, _ = parse_output(open(f).read())
        except Exception: continue
        if len(R) > I['V']: continue
        for v, r in enumerate(R):
            if r: out.add((vtype[v], tuple(r), v))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('--tl', type=float, default=1800)
    ap.add_argument('--node-tl', type=float, default=300); ap.add_argument('--K', type=int, default=None)
    ap.add_argument('--pricer', default='bin/cgx_price'); ap.add_argument('--pargs', default='')
    ap.add_argument('--maxcuts', type=int, default=150); ap.add_argument('--write', action='store_true')
    ap.add_argument('--root-only', action='store_true'); ap.add_argument('--enum-gap', type=float, default=0.0)
    ap.add_argument('--enum-max', type=int, default=3000000); ap.add_argument('--pool-tl', type=float, default=600)
    ap.add_argument('--warm', type=int, default=1); ap.add_argument('--labcap', type=float, default=5e6)
    ap.add_argument('--tb', type=int, default=1, help='branch on type counts first')
    ap.add_argument('--lm', type=int, default=1, help='limited-memory subset-row cuts')
    ap.add_argument('--maxcols', type=int, default=14000)
    ap.add_argument('--root-enum', action='store_true'); ap.add_argument('--sb-iter', type=int, default=150)
    ap.add_argument('--sb', type=int, default=8, help='strong branching candidates');
    ap.add_argument('--l1cap', type=float, default=3e5); ap.add_argument('--mip-tl', type=float, default=30)
    a = ap.parse_args(); t0 = time.time()
    inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    Rb, _ = parse_output(open(os.path.join(HERE, 'best', d, name + '.out')).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    Pb, Kb, UB = score(I, Rb)
    assert Pb == 0, 'penalty stage not handled'
    K = a.K or Kb
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    lbf = os.path.join(HERE, 'lb', d, name + '.json'); L0 = json.load(open(lbf)) if os.path.exists(lbf) else {}
    log(name, 'N', N, 'V', I['V'], 'best', (Pb, Kb, round(UB, 4)), 'K', K, 'stored lb_km', L0.get('lb_km'), 'lb_used', L0.get('lb_used'))
    pr = Pricer(inst, a); types = pr.types; T = len(types)
    vtype = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
    if K != Kb: UB = 1e9  # no incumbent with K vehicles known
    big = 10.0 * min(UB, 1e4)
    M = Master(I, types, K, big)
    best = [UB, None]
    for v, r in enumerate(Rb):
        if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
    for t in range(T):
        for k in range(N):
            c = check(I, [[]] * types[t]['vehs'][0] + [[k]])
            if c['ok']: M.add(t, c['km'], (k,))
    if a.warm:
        ws = load_pool_solutions(I, d, name, vtype)
        for (t, r, v) in ws:
            c = check(I, [[]] * v + [list(r)])
            if c['ok']: M.add(t, c['km'], r)
        log('warm columns', len(M.cols))
    nseed = len(M.cols)

    def colok(col, fs):
        if not fs: return True
        for e in arcs_of(col[2]):
            if e in fs: return False
        return True

    colarcs = {'n': 0}
    def apply(node):
        for t in range(T): M.set_row(N + t, float(node['lo'][t]), float(node['up'][t]))
        fs = set(node['forb'])
        M.allowed = {}
        while colarcs['n'] < len(M.cols):   # incremental arc -> columns index
            j = colarcs['n']
            for e in arcs_of(M.cols[j][2]): colarcs.setdefault(e, []).append(j)
            colarcs['n'] += 1
        bad = set()
        for e in fs: bad.update(colarcs.get(e, ()))
        n = len(M.cols); idx = np.array(M.hidx, dtype=np.int32); up = np.full(n, INF)
        for j in bad: up[j] = 0.0; M.allowed[M.hidx[j]] = False
        M.h.changeColsBounds(n, idx, np.zeros(n), up)
        pr.forb = list(node['forb'])
        for c in M.art: M.h.changeColBounds(c, 0.0, INF)
        M.art_on = True
        return fs

    stats = dict(price_calls=0)

    def cg_node(node, tl, allow_cuts=True):
        """returns (lp value if converged else None, best Lagrangian bound, x, duals)"""
        fs = apply(node); ts = time.time(); level = 1; hist = []; bestL = -1e18; it = 0
        while True:
            it += 1
            z, du, x = M.solve()
            if M.art_on and all(x[c] < 1e-9 for c in M.art):
                # artificials unused: drop them, otherwise degenerate duals carry the big-M cost into pricing
                for c in M.art: M.h.changeColBounds(c, 0.0, 0.0)
                M.art_on = False; z, du, x = M.solve()
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            cuts = [(S[0], S[1], S[2], du[row], mem) for S, row, mem in zip(M.cuts, M.cutrow, M.cutmem)]
            alpha = [-mu[t] - lam for t in range(T)]
            cols, mins, comp = pr.price(level, 60 if level == 1 else 300, alpha, pi, cuts, a.l1cap if level == 1 else a.labcap)
            stats['price_calls'] += 1
            # Lagrangian bound: dual objective + sum_t up_t * min(0, minrc_t)  (mins valid lower bounds)
            Lg = M.dual_obj(du) + sum(node['up'][t] * min(0.0, mins[t]) for t in range(T))   # valid for any sign-correct duals
            bestL = max(bestL, Lg)
            added = 0
            for (t, km, rc, r) in cols:
                j = M.add(t, km, r)
                if j >= 0:
                    added += 1
                    if not colok((t, km, r), fs): M.h.changeColBounds(M.hidx[j], 0.0, 0.0); M.allowed[M.hidx[j]] = False
            if a.verbose_cg if hasattr(a, 'verbose_cg') else (it % 10 == 1 or not added):
                log(f'    it{it} lvl{level} z={z:.4f} L={bestL:.4f} add={added} cols={len(M.cols)} cuts={len(M.cuts)} tprice={pr.last:.2f}s comp={comp}')
            if bestL >= best[0] - 1e-6: return bestL, bestL, x, du
            if not added:
                if level == 1: level = 2; continue
                if not comp: return None, bestL, x, du
                bestL = max(bestL, z)
                hist.append(z)
                if not allow_cuts or z >= best[0] - 1e-6 or len(M.cuts) >= a.maxcuts or time.time() - ts > tl \
                        or (len(hist) >= 3 and hist[-1] - hist[-3] < 2e-4 * abs(z)):
                    return z, bestL, x, du
                naug = M.augment(x) if a.lm else 0
                new = M.separate(x, maxcuts=min(30, a.maxcuts - len(M.cuts)))
                if not new and not naug: return z, bestL, x, du
                if naug: log(f'    memory augmented for {naug} cuts')
                for S in new: M.add_cut(S, M.memory_for(S, x) if a.lm else None)
                log(f'    +{len(new)} cuts (total {len(M.cuts)}), LP was {z:.4f}')
                level = 1; continue
            if time.time() - ts > tl: return None, bestL, x, du
            if level == 2 and added < 5: level = 1

    def try_incumbent(x):
        sol = [M.cols[j] for j in range(len(M.cols)) if x[M.hidx[j]] > 0.5]
        if any(1e-6 < x[M.hidx[j]] < 1 - 1e-6 for j in range(len(M.cols))): return False
        used = defaultdict(int); R = [[] for _ in range(I['V'])]
        for (t, km, r) in sol:
            if used[t] >= len(types[t]['vehs']): return False
            R[types[t]['vehs'][used[t]]] = list(r); used[t] += 1
        s = score(I, R)
        if s and s[0] == 0 and s[1] <= K and s[2] < best[0] - 1e-6:
            best[0] = s[2]; best[1] = R; log(f'  NEW incumbent {s}'); save_sol(R); return True
        return False

    def save_sol(R):
        od = os.path.join(HERE, 'results', d, 'cgx'); os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, name + '.out'), 'w') as f:
            f.write('SOLVER cgx_bp\n')
            for v, r in enumerate(R): f.write(f"ROUTE {v} {' '.join(map(str, r))}\n")

    def enum_node(node, du, gap, zn):
        """enumerate all routes with rc <= gap at this node and solve the node exactly over the pool.
        returns the node's proven lower bound (>= UB means closed) or None if enumeration incomplete"""
        from cgx_pool import Pool
        pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        cuts = [(S[0], S[1], S[2], du[row], mem) for S, row, mem in zip(M.cuts, M.cutrow, M.cutmem)]
        alpha = [-mu[t] - lam for t in range(T)]
        pr.forb = list(node['forb']); te = time.time()
        if os.environ.get('CGX_TESTGAP'): gap = float(os.environ['CGX_TESTGAP'])
        routes, rcs, comp = pr.enum(gap + 1e-6, a.enum_max, f'/tmp/cgx_enum_{os.getpid()}.txt', alpha, pi, cuts)
        log(f'  enum node d{node["depth"]} gap={gap:.4f} routes={len(routes)} complete={comp} {time.time()-te:.1f}s')
        stats['enum'] = stats.get('enum', 0) + 1
        if not comp: stats['enum_fail'] = min(stats.get('enum_fail', 1e18), gap); return None
        P = Pool(I, types, routes, 3, 0, K, log=log); P.cuts = [frozenset(S) for S in M.cuts]
        P.tlo = list(node['lo']); P.tup = list(node['up'])
        r2 = P.run(best[0], time_budget=a.pool_tl, mip_tl=a.pool_tl * 0.7, rc0=rcs)
        log('  node pool', {k: v for k, v in r2.items() if k != 'chosen'})
        if r2.get('chosen') and r2['obj'] < best[0] - 1e-4:
            used = defaultdict(int); R = [[] for _ in range(I['V'])]
            for (t, km, rr) in r2['chosen']: R[types[t]['vehs'][used[t]]] = list(rr); used[t] += 1
            s2 = score(I, R)
            if s2 and s2[0] == 0 and s2[1] <= K and s2[2] < best[0] - 1e-6: best[0] = s2[2]; best[1] = R; log(f'  NEW incumbent (pool) {s2}'); save_sol(R)
        if r2['status'] in ('optimal', 'infeasible', 'lp_closed'): return best[0]
        stats['enum_fail'] = min(stats.get('enum_fail', 1e18), 0.8 * gap)
        return max(zn, r2['lb'])

    def sb_eval(fset):
        """strong branching: restricted master LP value with columns using arcs in fset removed (no pricing)"""
        ch = []
        while colarcs['n'] < len(M.cols):   # incremental arc -> columns index
            j = colarcs['n']
            for e in arcs_of(M.cols[j][2]): colarcs.setdefault(e, []).append(j)
            colarcs['n'] += 1
        js = set()
        for e in fset: js.update(colarcs.get(e, ()))
        for j in js:
            if M.allowed.get(M.hidx[j], True):
                ch.append(M.hidx[j]); M.h.changeColBounds(M.hidx[j], 0.0, 0.0)
        M.h.setOptionValue('simplex_iteration_limit', a.sb_iter)
        M.h.run(); st = M.h.getModelStatus()
        M.h.setOptionValue('simplex_iteration_limit', 2**31 - 1)
        if st == highspy.HighsModelStatus.kInfeasible: v = 1e9
        else: v = M.h.getInfo().objective_function_value   # dual simplex: partial objective is still an estimate
        # restore (columns were allowed at this node: only allowed columns can carry the fractional arcs we test,
        # but ch may contain columns already forbidden at this node -> restore to node state)
        for c in ch: M.h.changeColBounds(c, 0.0, INF if M.allowed.get(c, True) else 0.0)
        return v

    def col_mip(tl):
        """price-and-branch heuristic: set partitioning MIP over all generated columns"""
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1)
        h.setOptionValue('time_limit', float(tl)); h.setOptionValue('mip_rel_gap', 0.0)
        for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
        for t in range(T): h.addRow(0.0, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
        h.addRow(-INF, float(K), 0, np.array([], dtype=np.int32), np.array([]))
        ok = [c for c in M.cols if len(set(c[2])) == len(c[2])]
        for (t, km, r) in ok:
            idx = sorted(r) + [N + t, N + T]
            h.addCol(km, 0.0, 1.0, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
        h.changeColsIntegrality(len(ok), np.arange(len(ok), dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * len(ok)))
        if best[0] < 1e8: h.setOptionValue('objective_bound', float(best[0]))
        h.run(); info = h.getInfo()
        if info.primal_solution_status != 2: log('  col MIP: no solution'); return
        xv = h.getSolution().col_value; used = defaultdict(int); R = [[] for _ in range(I['V'])]
        for j, (t, km, r) in enumerate(ok):
            if xv[j] > 0.5: R[types[t]['vehs'][used[t]]] = list(r); used[t] += 1
        s2 = score(I, R); log(f'  col MIP: {s2} status {h.getModelStatus()}')
        if s2 and s2[0] == 0 and s2[1] <= K and s2[2] < best[0] - 1e-6: best[0] = s2[2]; best[1] = R; log(f'  NEW incumbent (col MIP) {s2}'); save_sol(R)

    root = dict(lo=[0] * T, up=[ty['count'] for ty in types], forb=(), bound=-1e18, depth=0)
    heap = [(-1e18, 0, root)]; cnt = 1; nodes = 0; rootlb = None; hardLB = []  # bounds of nodes dropped unsolved
    while heap and time.time() - t0 < a.tl:
        bnd, _, node = heapq.heappop(heap)
        if bnd >= best[0] - 1e-6: continue
        tn = time.time()
        z, Lg, x, du = cg_node(node, min(a.node_tl, max(10, a.tl - (time.time() - t0))) if nodes else a.tl)
        nodes += 1
        if len(x) < M.h.getNumCol():
            M.h.run(); x = list(M.h.getSolution().col_value)
        nb = max(node['bound'], Lg) if z is None else max(z, node['bound'])
        if nodes == 1:
            rootlb = nb; log(f'ROOT: lp={z} lagr={Lg:.4f} bound={nb:.4f} UB={best[0]:.4f} cuts={len(M.cuts)} cols={len(M.cols)} price {pr.ncalls} calls {pr.tsum:.1f}s')
            if a.root_enum and z is not None:
                r = enum_node(node, du, best[0] - nb, nb); log('root enum result', r)
            if a.root_only:
                sup = [(x[M.hidx[j]], M.cols[j]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-6]
                tv = defaultdict(float)
                for v, (t, km, r) in sup: tv[t] += v
                log('support', len(sup), 'nonelem', sum(1 for v, c in sup if len(set(c[2])) < len(c[2])), 'types', {t: round(v, 3) for t, v in tv.items()},
                    'avg len', round(sum(v * len(c[2]) for v, c in sup) / max(1e-9, sum(v for v, c in sup)), 2))
                heap.append((nb, 0, dict(node, bound=nb))); break
        if nb >= best[0] - 1e-6: continue
        if z is None:
            # unconverged: cannot branch reliably; keep its bound as a hard floor
            hardLB.append(nb); log(f'  node {nodes} unconverged, bound {nb:.4f} kept'); continue
        if any(x[c] > 1e-6 for c in M.art):   # artificial used at LP optimum below UB: keep bound (valid), do not branch
            hardLB.append(nb); log(f'  node {nodes} uses artificials, bound {nb:.4f} kept'); continue
        if try_incumbent(x) or all(x[M.hidx[j]] < 1e-6 or x[M.hidx[j]] > 1 - 1e-6 for j in range(len(M.cols))): continue
        if a.enum_gap > 0 and best[1] is not None or (a.enum_gap > 0 and best[0] < 1e8):
            if best[0] - nb <= a.enum_gap * best[0] and best[0] - nb < 0.7 * stats.get('enum_fail', 1e18) and time.time() - t0 < a.tl - 30:
                r = enum_node(node, du, best[0] - nb, nb)
                if r is not None:
                    if r >= best[0] - 1e-6: continue          # node closed by enumeration + pool MIP
                    nb = max(nb, r)                           # pool bound; keep branching below this node
        tval = [0.0] * T; arc = defaultdict(float)
        for j, (t, km, r) in enumerate(M.cols):
            v = x[M.hidx[j]]
            if v < 1e-7: continue
            tval[t] += v
            for e in arcs_of(r): arc[e] += v
        ft = [(abs(tval[t] - round(tval[t])), t) for t in range(T) if abs(tval[t] - round(tval[t])) > 1e-4] if a.tb else []
        if ft:
            _, t = max(ft); v = tval[t]
            c1 = dict(node); c1['up'] = list(node['up']); c1['up'][t] = math.floor(v)
            c2 = dict(node); c2['lo'] = list(node['lo']); c2['lo'][t] = math.ceil(v)
            desc = f'type {t} {v:.3f}'
        else:
            fa = [(-abs(v - 0.5), e) for e, v in arc.items() if 1e-4 < v < 1 - 1e-4 and e[0] >= 0 and e[1] >= 0]
            if not fa: fa = [(-abs(v - 0.5), e) for e, v in arc.items() if 1e-4 < v < 1 - 1e-4]
            if not fa: hardLB.append(nb); log('  no branching candidate'); continue
            def force_of(i, j):
                if i >= 0 and j >= 0: return [(i, k) for k in range(N) if k != j] + [(k, j) for k in range(N) if k != i] + [(-1, j), (i, -1)]
                if i < 0: return [(k, j) for k in range(N)]
                return [(i, k) for k in range(N)]
            fa.sort(reverse=True)
            if a.sb > 1 and len(fa) > 1:
                bestsc = None; tsb = time.time()
                for _, (i, j) in fa[:a.sb]:
                    d1 = sb_eval({(i, j)}) - nb; d2 = sb_eval(set(force_of(i, j))) - nb
                    sc = max(d1, 1e-4) * max(d2, 1e-4)
                    if bestsc is None or sc > bestsc[0]: bestsc = (sc, (i, j), d1, d2)
                M.h.run(); x = list(M.h.getSolution().col_value)
                i, j = bestsc[1]; stats['sb'] = stats.get('sb', 0) + time.time() - tsb
            else:
                _, (i, j) = fa[0]
            c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((i, j),)
            force = force_of(i, j)
            c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple(force)
            desc = f'arc {i}->{j} {arc[(i, j)]:.3f}'
        for c in (c1, c2):
            c['bound'] = nb; c['depth'] = node['depth'] + 1
            heapq.heappush(heap, (nb, cnt, c)); cnt += 1
        if a.maxcols and len(M.cols) > a.maxcols:
            nd = M.purge(a.maxcols, nseed); colarcs.clear(); colarcs['n'] = 0; M.allowed = {}
            log(f'  purged {nd} columns -> {len(M.cols)}')
        openb = min([h[0] for h in heap] + [best[0]] + hardLB)
        if a.write and time.time() - stats.get('lastw', t0) > 300:   # checkpoint the bound (runs may be killed)
            stats['lastw'] = time.time(); write_lb(d, name, K, openb, False, best[0], nodes, time.time() - t0, log)
        log(f'  node {nodes} d{node["depth"]} z={nb:.4f} {desc} open {len(heap)} LB={openb:.4f} UB={best[0]:.4f} cuts={len(M.cuts)} cols={len(M.cols)} {time.time()-tn:.1f}s')
    if a.mip_tl > 0 and heap:
        col_mip(a.mip_tl)
    openb = min([h[0] for h in heap if h[0] < best[0] - 1e-6] + [best[0]] + hardLB)
    exhausted = not any(h[0] < best[0] - 1e-6 for h in heap) and not hardLB and not a.root_only
    log(f'DONE nodes {nodes} LB {openb:.4f} UB {best[0]:.4f} gap {(best[0]/openb-1)*100 if openb>0 else 999:.3f}% exhausted {exhausted} price {pr.ncalls} calls {pr.tsum:.1f}s sb {stats.get("sb", 0):.1f}s enum {stats.get("enum", 0)}')
    pr.close()
    res = dict(lb=openb, root=rootlb, ub=best[0], nodes=nodes, exhausted=exhausted, K=K, seconds=round(time.time() - t0, 1))
    print('RESULT', json.dumps(res), flush=True)
    if a.write: write_lb(d, name, K, openb, exhausted, best[0], nodes, time.time() - t0, log)


def write_lb(d, name, K, lb, exhausted, ub, nodes, secs, log):
    lbf = os.path.join(HERE, 'lb', d, name + '.json')
    L = json.load(open(lbf)) if os.path.exists(lbf) else {}
    if L.get('lb_used') is None or L['lb_used'] < K:
        log('lb_used not proven = K; lb_km for K is still valid only as bound for K vehicles');
        if L.get('lb_used') != K: return
    old = L.get('lb_km') if L.get('km_K', K) == K else None
    newv = round(lb - 1e-4, 4)
    if exhausted: newv = round(min(lb, ub) - 1e-4, 4) if ub < 1e8 else newv
    if old is None or newv > old + 1e-4:
        L['lb_km'] = newv; L['km_K'] = K; L['method'] = (L.get('method', '') + ' + cgx B&P').strip(' +')
        L['cgx'] = dict(lb=lb, ub=ub, nodes=nodes, exhausted=exhausted, seconds=round(secs, 1), old_lb_km=old)
        L['seconds'] = round(L.get('seconds', 0) + secs, 1)
        json.dump(L, open(lbf, 'w'), indent=1); log(f'lb json updated {old} -> {newv}')
    else:
        log(f'lb json not updated (old {old}, new {newv})')


if __name__ == '__main__':
    main()
