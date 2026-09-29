"""Branch-and-price for the first two lexicographic levels (agent cgp).
  --stage 1 : min priority penalty (fleet = all vehicles). Proves lb_pen = Pbest by showing no integer solution
              with penalty <= Pbest-10 (penalties are multiples of 10): prune node if LP > Pbest-10.
  --stage 2 : min #vehicles s.t. penalty <= Pbest. Proves lb_used = K by showing no solution with <= K-1 vehicles.
Master: set partitioning LP (HiGHS), order rows =1 (y_k unserved slack), type rows lo..up, penalty row, 3-SRC cuts.
Pricing: bin/cgp_price (ng-route labeling, per-type forbidden nodes/arcs), exact => valid node bounds.
Unconverged node -> Lagrangian bound (valid) or parent bound.
Branching: y_k (stage 1), vehicles per type, order-to-type assignment, arc flow.
Usage: cgp_bp.py <inst.txt> --stage 1|2 [--tl 3600] [--node-tl 300]"""
import sys, os, json, time, math, heapq, argparse, subprocess, itertools
from collections import defaultdict
import numpy as np
import highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output

PEN = {1: 100, 2: 50, 3: 20}
HERE = os.path.dirname(os.path.abspath(__file__)); INF = highspy.kHighsInf
TIM = defaultdict(float)


class Pricer:
    def __init__(self, inst, ng, binary='bin/cgp_price'):
        self.p = subprocess.Popen([os.path.join(HERE, binary), inst, str(ng)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        T = int(self.p.stdout.readline().split()[1]); self.types = []
        for _ in range(T):
            a = list(map(int, self.p.stdout.readline().split()))
            self.types.append(dict(count=a[0], start=a[1], mode=a[2], mask=a[3], vehs=a[4:]))
        self.forb = []; self.r1 = 'price5' in binary

    def _send(self, head, alpha, pi, cuts):
        cuts = [c for c in cuts if c[-1] < -1e-9]
        if self.r1: ctext = ''.join(f'{len(c) - 1} ' + ' '.join(map(str, c[:-1])) + f' {c[-1]:.10f}\n' for c in cuts)
        else: ctext = ''.join(f'{a} {b} {c} {sg:.10f}\n' for a, b, c, sg in cuts)
        self.p.stdin.write(head + '\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
                           + f'{len(cuts)}\n' + ctext
                           + f'{len(self.forb)}\n' + ''.join(f'{t} {i} {j}\n' for t, i, j in self.forb))
        self.p.stdin.flush()

    def price(self, level, maxcols, beta, alpha, pi, labcap=3e6, cuts=()):
        _t = time.time(); self._send(f'PRICE {level} {maxcols} {beta} {labcap}', alpha, pi, cuts)
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2])
        mins = list(map(float, self.p.stdout.readline().split()[1:]))
        cols = []
        for _ in range(n):
            a = self.p.stdout.readline().split()
            cols.append((int(a[0]), float(a[1]), float(a[2]), tuple(int(x) for x in a[4:])))
        TIM['price'] += time.time() - _t
        return cols, mins, comp

    def enum(self, gap, beta, maxroutes, fn, alpha, pi, cuts=()):
        self._send(f'ENUM {gap:.10f} {beta} {maxroutes} {fn}', alpha, pi, cuts)
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2])
        routes = []
        for line in open(fn):
            a = line.split(); routes.append((int(a[0]), float(a[1]), tuple(int(x) for x in a[4:])))
        os.remove(fn)
        return routes, comp

    def close(self):
        try: self.p.stdin.write('QUIT\n'); self.p.stdin.flush(); self.p.wait(5)
        except Exception: self.p.kill()


def arcs_of(r):
    return [(-1, r[0])] + [(r[i], r[i + 1]) for i in range(len(r) - 1)] + [(r[-1], -1)]


class Master:
    """rows: 0..N-1 orders (=1), N..N+T-1 types (lo..up), N+T penalty (<= Pmax), then cuts (<=1).
    cols: y_k (0..N-1), artificial (N order + T type), then routes."""
    def __init__(self, I, types, stage, Pmax, big):
        self.I, self.types, self.stage = I, types, stage; N, T = I['N'], len(types); self.N, self.T = N, T
        self.Pmax = Pmax
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1); self.h = h
        self.pk = [PEN[o['pri']] for o in I['ords']]
        for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
        for t in range(T): h.addRow(0.0, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
        h.addRow(-INF, float(Pmax), 0, np.array([], dtype=np.int32), np.array([]))
        for k in range(N):
            h.addCol(float(self.pk[k]) if stage == 1 else 0.0, 0.0, 1.0, 2, np.array([k, N + T], dtype=np.int32), np.array([1.0, float(self.pk[k])]))
        self.art = []; self.big = big
        for k in range(N):
            self.art.append(h.getNumCol()); h.addCol(big, 0.0, INF, 1, np.array([k], dtype=np.int32), np.array([1.0]))
        for t in range(T):
            self.art.append(h.getNumCol()); h.addCol(big, 0.0, INF, 1, np.array([N + t], dtype=np.int32), np.array([1.0]))
        self.cols = []; self.key = {}; self.hidx = []; self.cuts = []; self.cutrow = []

    phase1 = False
    simplex = -1

    def rebuild(self, keep):
        cols = [self.cols[j] for j in keep]; cuts = list(self.cuts)
        self.__init__(self.I, self.types, self.stage, self.Pmax, self.big)
        if self.simplex >= 0: self.h.setOptionValue('simplex_strategy', self.simplex)
        for S in cuts: self.add_cut(S)
        for (t, km, r) in cols: self.add(t, km, r)

    def purge(self, maxpool, keepn, protect):
        if len(self.cols) <= maxpool: return False
        try: rc = list(self.h.getSolution().col_dual)
        except Exception: return False
        if len(rc) < self.h.getNumCol(): return False
        order = sorted(range(len(self.cols)), key=lambda j: rc[self.hidx[j]])
        keep = set(order[:keepn]) | set(protect)
        self.rebuild(sorted(keep)); return True
    def ccost(self): return 0.0 if (self.stage == 1 or self.phase1) else 1.0

    def set_phase1(self, on):
        self.phase1 = on; h = self.h; N = self.N
        for k in range(N): h.changeColCost(k, 0.0 if on else (float(self.pk[k]) if self.stage == 1 else 0.0))
        for c in self.art: h.changeColCost(c, 1.0 if on else self.big)
        cc = self.ccost()
        for j in self.hidx: h.changeColCost(j, cc)

    def add(self, t, km, r):
        k = (t, r)
        if k in self.key: return -1
        self.key[k] = len(self.cols); self.cols.append((t, km, r)); self.hidx.append(self.h.getNumCol())
        cnt = {}
        for x in r: cnt[x] = cnt.get(x, 0) + 1
        idx = sorted(cnt); vals = [float(cnt[i]) for i in idx]
        idx.append(self.N + t); vals.append(1.0)
        for S, row in zip(self.cuts, self.cutrow):
            c = sum(cnt.get(x, 0) for x in S) // (2 if len(S) == 3 else 3)
            if c: idx.append(row); vals.append(float(c))
        self.h.addCol(self.ccost(), 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        return len(self.cols) - 1

    def add_cut(self, S):
        S = tuple(sorted(S)); idx = []; vals = []
        for j, (t, km, r) in enumerate(self.cols):
            c = sum(r.count(x) for x in S) // (2 if len(S) == 3 else 3)
            if c: idx.append(self.hidx[j]); vals.append(float(c))
        row = self.h.getNumRow()
        self.h.addRow(-INF, 1.0, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        self.cuts.append(S); self.cutrow.append(row)

    def separate(self, x, maxcuts=30):
        N = self.N; fr = [j for j in range(len(self.cols)) if x[self.hidx[j]] > 1e-6]
        if not fr: return []
        A = np.zeros((len(fr), N), dtype=np.int16); v = np.array([x[self.hidx[j]] for j in fr])
        for i, j in enumerate(fr):
            for o in self.cols[j][2]: A[i, o] += 1
        have = set(self.cuts); cand = []
        act = np.nonzero(A.sum(axis=0))[0]
        for ia, a_ in enumerate(act):
            for ib in range(ia + 1, len(act)):
                b_ = act[ib]; sab = A[:, a_] + A[:, b_]
                if not np.any(sab >= 2) and not np.any(sab >= 1): continue
                rest = act[ib + 1:]
                if len(rest) == 0: continue
                S = (sab[:, None] + A[:, rest]) // 2
                val = v @ S
                for ic in np.nonzero(val > 1 + 1e-3)[0]:
                    k = (int(a_), int(b_), int(rest[ic]))
                    if k not in have: cand.append((float(val[ic]), k))
        cand.sort(reverse=True)
        out = []; used = defaultdict(int)
        for val, k in cand:
            if len(out) >= maxcuts: break
            if any(used[o] >= 5 for o in k): continue
            out.append(k)
            for o in k: used[o] += 1
        return out

    def separate5(self, x, maxcuts=30, xmin=0.15):
        """heuristic separation of rank-1 cuts on 5 orders with multiplier 1/3: sum_r floor(|r cap S|/3) x_r <= 1"""
        N = self.N; fr = [j for j in range(len(self.cols)) if x[self.hidx[j]] > 1e-6]
        if not fr: return []
        A = np.zeros((len(fr), N), dtype=np.int16); v = np.array([x[self.hidx[j]] for j in fr])
        for i, j in enumerate(fr):
            for o in self.cols[j][2]: A[i, o] += 1
        big = [i for i in range(len(fr)) if v[i] >= xmin and len(set(self.cols[fr[i]][2])) >= 3]
        sets = [set(self.cols[fr[i]][2]) for i in big]
        have = set(self.cuts); cand = {}
        tested = set()
        for p1 in range(len(big)):
            for p2 in range(p1 + 1, len(big)):
                i12 = sets[p1] & sets[p2]
                if not i12: continue
                for p3 in range(p2 + 1, len(big)):
                    if not (sets[p3] & sets[p1]) or not (sets[p3] & sets[p2]): continue
                    cnt = defaultdict(int)
                    for q in (p1, p2, p3):
                        for o in sets[q]: cnt[o] += 1
                    multi = [o for o, c in cnt.items() if c >= 2]
                    if len(multi) < 3: continue
                    pool = sorted(multi, key=lambda o: -cnt[o])[:8]
                    if len(pool) < 5:
                        extra = sorted((o for o, c in cnt.items() if c == 1), key=lambda o: -A[:, o].astype(float) @ v)[:5 - len(pool) + 2]
                        pool = pool + extra
                    if len(pool) < 5: continue
                    for S in itertools.combinations(sorted(pool), 5):
                        if S in tested: continue
                        tested.add(S)
                        val = float(v @ (A[:, list(S)].sum(axis=1) // 3))
                        if val > 1 + 1e-3 and S not in have: cand[S] = val
                    if len(tested) > 200000: break
                if len(tested) > 200000: break
            if len(tested) > 200000: break
        # randomized local search (swap moves) from seeds inside pairs of overlapping fractional routes
        rng = np.random.default_rng(len(self.cuts))
        act = np.nonzero(A.sum(axis=0))[0]
        def val_of(S): return float(v @ (A[:, list(S)].sum(axis=1) // 3))
        nb = len(big)
        for it in range(400 if nb >= 2 else 0):
            p1, p2 = rng.choice(nb, 2, replace=False)
            U = list(sets[p1] | sets[p2])
            if len(U) < 5: continue
            S = list(rng.choice(U, 5, replace=False)); cur = val_of(S)
            improved = True
            while improved:
                improved = False
                for pos in range(5):
                    bestv, besto = cur, None
                    for o in act:
                        if o in S: continue
                        T2 = S[:pos] + [int(o)] + S[pos + 1:]
                        vv = val_of(T2)
                        if vv > bestv + 1e-9: bestv, besto = vv, int(o)
                    if besto is not None: S[pos] = besto; cur = bestv; improved = True
            key = tuple(sorted(int(o) for o in S))
            if cur > 1 + 1e-3 and key not in have: cand[key] = cur
        out = []; used = defaultdict(int)
        for S, val in sorted(cand.items(), key=lambda kv: -kv[1]):
            if len(out) >= maxcuts: break
            if any(used[o] >= 3 for o in S): continue
            out.append(S)
            for o in S: used[o] += 1
        return out

    def solve(self):
        _t = time.time(); self.h.run(); TIM['lp'] += time.time() - _t; st = self.h.getModelStatus()
        if st != highspy.HighsModelStatus.kOptimal: raise RuntimeError(f'LP status {st}')
        s = self.h.getSolution()
        return self.h.getInfo().objective_function_value, list(s.row_dual), list(s.col_value)


def colok(col, fnode, farc):
    t, km, r = col
    if any((t, k) in fnode for k in r): return False
    if farc:
        for e in arcs_of(r):
            if (t,) + e in farc: return False
    return True


def expand_forb(forb, T):
    """forb: tuples (t,i,j), t=-1 all types; i==j node forbid. -> sets of (t,k) and (t,i,j)"""
    fnode = set(); farc = set()
    for (t, i, j) in forb:
        ts = range(T) if t < 0 else [t]
        for tt in ts:
            if i == j: fnode.add((tt, i))
            else: farc.add((tt, i, j))
    return fnode, farc


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('--stage', type=int, required=True)
    ap.add_argument('--tl', type=float, default=3600); ap.add_argument('--node-tl', type=float, default=300)
    ap.add_argument('--ng', type=int, default=10); ap.add_argument('--maxcuts', type=int, default=150)
    ap.add_argument('--labcap', type=float, default=3e6); ap.add_argument('--labcap-small', type=float, default=2e5); ap.add_argument('--target', type=float, default=None)
    ap.add_argument('--dfs', type=int, default=0); ap.add_argument('--nowrite', action='store_true')
    ap.add_argument('--best', default=None); ap.add_argument('--nolagr', action='store_true'); ap.add_argument('--pricer', default='bin/cgp_price'); ap.add_argument('--simplex', type=int, default=-1); ap.add_argument('--rootonly', action='store_true'); ap.add_argument('--maxpool', type=int, default=30000); ap.add_argument('--maxrounds', type=int, default=8); ap.add_argument('--dive', action='store_true'); ap.add_argument('--divewidth', type=int, default=2); ap.add_argument('--strong', type=int, default=0); ap.add_argument('--stall', type=float, default=1e-3); ap.add_argument('--checkrc', action='store_true'); ap.add_argument('--enum', action='store_true'); ap.add_argument('--enum-max', type=int, default=3000000); ap.add_argument('--enum-mip-tl', type=float, default=1800); ap.add_argument('--rootrounds', type=int, default=30); ap.add_argument('--sb-tl', type=float, default=60); ap.add_argument('--keeppool', type=int, default=12000); ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args(); t0 = time.time()
    inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    bestf = a.best or os.path.join(HERE, 'best', d, name + '.out')
    Rb, _ = parse_output(open(bestf).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    c = check(I, Rb); assert c['ok']
    served = {k for r in Rb for k in r}
    Pbest = sum(PEN[I['ords'][k]['pri']] for k in range(N) if k not in served); K = c['used']
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    stage = a.stage
    if stage == 1:
        UB = Pbest; thr = Pbest - 10   # prove: no solution with penalty <= thr
        Pmax = Pbest
    else:
        UB = K; thr = K - 1; Pmax = Pbest
    if a.target is not None: thr = a.target
    log(name, 'stage', stage, 'best', (Pbest, K, round(c['km'], 3)), 'prove no solution with obj <=', thr)
    pr = Pricer(inst, a.ng, a.pricer); types = pr.types; T = len(types)
    vtype = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
    big = 1000.0 if stage == 1 else 100.0
    M = Master(I, types, stage, Pmax, big)
    if a.simplex >= 0: M.h.setOptionValue('simplex_strategy', a.simplex); M.simplex = a.simplex
    for v, r in enumerate(Rb):
        if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
    for t in range(T):
        for k in range(N):
            cc = check(I, [[]] * types[t]['vehs'][0] + [[k]])
            if cc['ok']: M.add(t, cc['km'], (k,))
    nseed = len(M.cols)
    found = []   # improved solutions

    def save_solution(R, s):
        od = os.path.join(HERE, 'runs', 'results', d, 'cgp'); os.makedirs(od, exist_ok=True)
        fn = os.path.join(od, name + '.out')
        with open(fn, 'w') as f:
            f.write('SOLVER cgp_bp\n')
            for v, r in enumerate(R): f.write(f"ROUTE {v} {' '.join(map(str, r))}\n")
        log('  saved', fn, s)

    def score(R):
        cc = check(I, R)
        if not cc['ok']: return None
        sv = {k for r in R for k in r}
        return (sum(PEN[I['ords'][k]['pri']] for k in range(N) if k not in sv), cc['used'], cc['km'])

    def to_routes(sol):
        R = [[] for _ in range(I['V'])]; used = defaultdict(int)
        for (t, km, r) in sol:
            if used[t] >= len(types[t]['vehs']): return None
            R[types[t]['vehs'][used[t]]] = list(r); used[t] += 1
        return R

    def objval(s): return s[0] if stage == 1 else s[1]

    def try_integer(sol):
        nonlocal thr
        R = to_routes(sol)
        if R is None: return False
        s = score(R)
        if s is None or s[0] > Pmax: return False
        if objval(s) <= thr + 1e-9:
            log('  *** IMPROVED SOLUTION', s); found.append(s); save_solution(R, s)
            thr = objval(s) - (10 if stage == 1 else 1)
            return True
        return False

    def lagr_bound(du, mins, node):
        pi = du[:N]; mu = du[N:N + T]; sig = min(0.0, du[N + T])
        L = sum(pi) + sig * Pmax
        for t in range(T): L += mu[t] * (node['up'][t] if mu[t] < 0 else node['lo'][t])
        for row in M.cutrow: L += min(0.0, du[row])
        for t in range(T): L += node['up'][t] * min(0.0, mins[t])
        for k in range(N):
            lo = 1.0 if node['y'].get(k) == 1 else 0.0; up = 0.0 if node['y'].get(k) == 0 else 1.0
            rc = (M.pk[k] if stage == 1 else 0.0) - pi[k] - sig * M.pk[k]
            L += rc * (up if rc < 0 else lo)
            if big - pi[k] < -1e-9: return -1e18
        for t in range(T):
            if big - mu[t] < -1e-9: return -1e18
        return L

    def apply(node):
        for t in range(T): M.h.changeRowBounds(N + t, float(node['lo'][t]), float(node['up'][t]))
        fixed = node.get('fix', ())
        for (t, km, r) in fixed:
            if (t, r) not in M.key: M.add(t, km, r)
        forb = list(node['forb']) + [(-1, k, k) for k, v in node['y'].items() if v == 1] + [(-1, k, k) for (t, km, r) in fixed for k in r]
        fnode, farc = expand_forb(forb, T)
        for k in range(N):
            v = node['y'].get(k)
            M.h.changeColBounds(k, 1.0 if v == 1 else 0.0, 0.0 if v == 0 else 1.0)
        fixidx = {M.key[(t, r)] for (t, km, r) in fixed}
        for j, col in enumerate(M.cols):
            if j in fixidx: M.h.changeColBounds(M.hidx[j], 1.0, 1.0)
            else: M.h.changeColBounds(M.hidx[j], 0.0, INF if colok(col, fnode, farc) else 0.0)
        pr.forb = forb
        return fnode, farc

    def solve_node(node, tl, maxcuts):
        if M.purge(a.maxpool, a.keeppool, range(nseed)): log(f'  purged column pool -> {len(M.cols)}')
        fnode, farc = apply(node); ts = time.time(); level = 1; hist = []; bestL = -1e18; it = 0; rounds = 0
        while True:
            it += 1
            if it == 1 and a.simplex >= 0: M.h.setOptionValue('simplex_strategy', 1)
            z, du, x = M.solve()
            if it == 1 and a.simplex >= 0: M.h.setOptionValue('simplex_strategy', a.simplex)
            pi = du[:N]; mu = du[N:N + T]
            cuts = [tuple(S) + (du[row],) for S, row in zip(M.cuts, M.cutrow)]
            alpha = [M.ccost() - mu[t] for t in range(T)]
            cols, mins, comp = pr.price(min(level, 2), 60 if level == 1 else 300, 0.0, alpha, pi, labcap=(a.labcap if level == 3 else a.labcap_small), cuts=cuts)
            if level == 2 and comp: level = 3
            if a.checkrc and cols:
                for (t, km, rc, r) in cols[:50]:
                    cnt = defaultdict(int)
                    for o in r: cnt[o] += 1
                    rc2 = alpha[t] - sum(pi[o] for o in r) - sum(c[-1] * (sum(cnt.get(e, 0) for e in c[:-1]) // (2 if len(c) == 4 else 3)) for c in cuts if c[-1] < -1e-9)
                    if abs(rc2 - rc) > 1e-6: stats['rc_mismatch'] += 1; log(f'  RC MISMATCH {rc} vs {rc2} route {r}')
            L = lagr_bound(du, mins, node); bestL = max(bestL, L)
            if bestL > thr + 1e-6 and not a.nolagr: return bestL, x, 'lagr'
            added = 0
            for (t, km, rc, r) in cols:
                j = M.add(t, km, r)
                if j >= 0:
                    added += 1
                    if not colok((t, km, r), fnode, farc): M.h.changeColBounds(M.hidx[j], 0.0, 0.0)
            if not added:
                if level < 3: level += 1; continue
                if not comp: return bestL, None, 'incomplete'
                hist.append(z)
                if z > thr + 1e-6: return z, x, 'lp'
                if any(x[cidx] > 1e-6 for cidx in M.art): return z, x, 'lp'
                rounds += 1
                pinned = z > thr - 1e-4
                if maxcuts == 0: return z, x, 'lp'
                if rounds > (a.maxrounds if node['depth'] > 0 else a.rootrounds) or (not pinned and len(hist) >= 3 and hist[-1] - hist[-3] < a.stall) or time.time() - ts > tl:
                    return z, x, 'lp'
                _t = time.time(); new = M.separate(x, maxcuts=30)
                if not new and pr.r1:
                    new = M.separate5(x, maxcuts=30); stats['r1_5'] += len(new)
                if not new: return z, x, 'lp'
                if len(M.cuts) + len(new) > maxcuts:
                    slack = [i for i, row in enumerate(M.cutrow) if abs(du[row]) < 1e-9]
                    if len(M.cuts) - len(slack) + len(new) > maxcuts: new = new[:max(0, maxcuts - len(M.cuts) + len(slack))]
                    if not new: return z, x, 'lp'
                    drop = set(slack[:len(M.cuts) + len(new) - maxcuts]) if len(M.cuts) + len(new) > maxcuts else set()
                    if drop:
                        M.cuts = [S for i, S in enumerate(M.cuts) if i not in drop]
                        M.rebuild(list(range(len(M.cols)))); fnode, farc = apply(node); stats['cutpurge'] += 1
                for S in new: M.add_cut(S)
                TIM['sep'] += time.time() - _t
                level = 1; continue
            if time.time() - ts > tl: return bestL, None, 'timeout'
            if level >= 2 and added < 5: level = 1

    def phase1_infeasible(node, tl=600):
        """rigorous infeasibility test of the node LP: min sum(artificials) by CG; True iff proven > 0"""
        M.set_phase1(True); fnode, farc = apply(node); ts = time.time(); level = 1; res = False
        try:
            while time.time() - ts < tl:
                z, du, x = M.solve()
                pi = du[:N]; mu = du[N:N + T]; sig = du[N + T]
                cuts = [tuple(S) + (du[row],) for S, row in zip(M.cuts, M.cutrow)]
                alpha = [0.0 - mu[t] for t in range(T)]
                cols, mins, comp = pr.price(min(level, 2), 60 if level == 1 else 300, 0.0, alpha, pi, labcap=(a.labcap if level == 3 else a.labcap_small), cuts=cuts)
                if level == 2 and comp: level = 3
                L = sum(pi) + sig * Pmax + sum(du[row] for row in M.cutrow)
                for t in range(T): L += mu[t] * (node['up'][t] if mu[t] < 0 else node['lo'][t]) + node['up'][t] * min(0.0, mins[t])
                ok = all(1.0 - pi[k] >= -1e-9 for k in range(N)) and all(1.0 - mu[t] >= -1e-9 for t in range(T))
                for k in range(N):
                    lo = 1.0 if node['y'].get(k) == 1 else 0.0; up = 0.0 if node['y'].get(k) == 0 else 1.0
                    rc = -pi[k] - sig * M.pk[k]; L += rc * (up if rc < 0 else lo)
                if ok and L > 1e-6: res = True; break
                added = 0
                for (t, km, rc, r) in cols:
                    j = M.add(t, km, r)
                    if j >= 0:
                        added += 1
                        if not colok((t, km, r), fnode, farc): M.h.changeColBounds(M.hidx[j], 0.0, 0.0)
                if not added:
                    if level < 3: level += 1; continue
                    if comp and z > 1e-6: res = True
                    break
                if level >= 2 and added < 5: level = 1
        finally:
            M.set_phase1(False)
        return res

    def try_mip(tl=20):
        """price-and-branch heuristic over all generated columns (stage objective)"""
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('time_limit', float(tl)); h.setOptionValue('threads', 1)
        for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
        for t in range(T): h.addRow(-INF, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
        h.addRow(-INF, float(Pmax), 0, np.array([], dtype=np.int32), np.array([]))
        for k in range(N): h.addCol(float(M.pk[k]) if stage == 1 else 0.0, 0.0, 1.0, 2, np.array([k, N + T], dtype=np.int32), np.array([1.0, float(M.pk[k])]))
        ok = [col for col in M.cols if len(set(col[2])) == len(col[2])]
        for (t, km, r) in ok:
            idx = sorted(r) + [N + t]
            h.addCol(0.0 if stage == 1 else 1.0, 0.0, 1.0, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
        nc = N + len(ok)
        h.changeColsIntegrality(nc, np.arange(nc, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * nc))
        h.setOptionValue('objective_bound', float(thr) + 0.5)
        h.run()
        try:
            info = h.getInfo()
            if info.primal_solution_status != 2: return
            xs = list(h.getSolution().col_value)
        except Exception: return
        sol = [ok[j] for j in range(len(ok)) if xs[N + j] > 0.5]
        try_integer(sol)

    root = dict(lo=[0] * T, up=[ty['count'] for ty in types], forb=(), y={}, bound=-1e18, depth=0)
    if a.dive:
        dstat = defaultdict(int)
        def dive(node, depth):
            if time.time() - t0 > a.tl: return False
            z, x, how = solve_node(node, a.node_tl, a.maxcuts); dstat['nodes'] += 1
            if how in ('incomplete', 'timeout') or x is None or len(x) < M.h.getNumCol():
                M.h.run(); x = list(M.h.getSolution().col_value)
                if how in ('incomplete', 'timeout'): z = M.h.getInfo().objective_function_value
            art = sum(x[c] for c in M.art)
            fx = {(t, r) for (t, km, r) in node.get('fix', ())}
            frac = sorted(((x[M.hidx[j]], j) for j in range(len(M.cols)) if 1e-6 < x[M.hidx[j]] and M.cols[j][:1] + M.cols[j][2:] not in fx), reverse=True)
            integ = all(v > 1 - 1e-6 for v, j in frac) and all(x[k] < 1e-6 or x[k] > 1 - 1e-6 for k in range(N))
            log(f'  dive d{depth} z={z:.4f} {how} art={art:.3f} fixed={len(fx)} frac={sum(1 for v, j in frac if v < 1 - 1e-6)} top={[round(v, 3) for v, j in frac[:4]]}')
            if z > thr + 1e-6 or art > 1e-6: return False
            if integ:
                sol = [M.cols[j] for v, j in frac] + list(node.get('fix', ()))
                return try_integer(sol)
            ones = [M.cols[j] for v, j in frac if v > 0.99]
            cands = [[c] for c in ones[:1]] if ones else []
            if ones and len(ones) > 1: cands = [ones]
            if not cands: cands = [[M.cols[j]] for v, j in frac[:a.divewidth]]
            for c in cands:
                ch = dict(node); ch['fix'] = tuple(node.get('fix', ())) + tuple(c); ch['depth'] = depth + 1
                if dive(ch, depth + 1): return True
                if time.time() - t0 > a.tl: return False
            return False
        ok = dive(dict(root), 0)
        log(f'dive done: success={ok} found={found} {dict(dstat)}')
        pr.close(); print('RESULT', json.dumps(dict(inst=name, dive=True, found=found, seconds=round(time.time() - t0, 1))), flush=True)
        return
    heap = [(-1e18, 0, root)]; cnt = 1; nodes = 0; stats = defaultdict(int); maxdepth = 0
    rootz = None; enum_proof = False
    while heap and time.time() - t0 < a.tl:
        if a.dfs: heap.sort(key=lambda h: (-h[2]['depth'], h[0])); bnd, _, node = heap.pop(0)
        else: bnd, _, node = heapq.heappop(heap)
        if bnd > thr + 1e-6: continue
        z, x, how = solve_node(node, a.node_tl, a.maxcuts); nodes += 1; stats[how] += 1
        maxdepth = max(maxdepth, node['depth'])
        if a.verbose: log(f'   node {nodes} d{node["depth"]} y={node["y"]} lo={node["lo"]} up={node["up"]} nforb={len(node["forb"])} -> z={z:.4f} {how}')
        if rootz is None:
            rootz = z; log(f'root: z={z:.4f} ({how}) cols={len(M.cols)} cuts={len(M.cuts)} tim={dict(TIM)}')
            if a.enum and how == 'lp' and z <= thr + 1e-6:
                du = list(M.h.getSolution().row_dual)
                pi = du[:N]; mu = du[N:N + T]
                cuts = [tuple(S) + (du[row],) for S, row in zip(M.cuts, M.cutrow)]
                alpha = [M.ccost() - mu[t] for t in range(T)]
                gap = thr - z + 1e-6
                fn = os.path.join(os.environ.get('CGP_TMP', '/tmp'), f'cgp_enum_{os.getpid()}.txt')
                routes, comp = pr.enum(gap, 0.0, a.enum_max, fn, alpha, pi, cuts)
                log(f'  ENUM gap={gap:.5f} routes={len(routes)} complete={comp}')
                if comp:
                    h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('time_limit', float(a.enum_mip_tl)); h.setOptionValue('threads', 1)
                    for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
                    for t in range(T): h.addRow(-INF, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
                    h.addRow(-INF, float(Pmax), 0, np.array([], dtype=np.int32), np.array([]))
                    for k in range(N): h.addCol(float(M.pk[k]) if stage == 1 else 0.0, 0.0, 1.0 if Pmax > 0 else 0.0, 2, np.array([k, N + T], dtype=np.int32), np.array([1.0, float(M.pk[k])]))
                    ok = [(t, km, r) for (t, km, r) in routes if len(set(r)) == len(r)]
                    for (t, km, r) in ok:
                        idx = sorted(r) + [N + t]
                        h.addCol(0.0 if stage == 1 else 1.0, 0.0, 1.0, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
                    nc = N + len(ok)
                    h.changeColsIntegrality(nc, np.arange(nc, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * nc))
                    h.run(); st = str(h.getModelStatus()); info = h.getInfo()
                    log(f'  ENUM MIP status {st} obj {info.objective_function_value if info.primal_solution_status == 2 else None} dual {info.mip_dual_bound}')
                    if info.primal_solution_status == 2:
                        xs = list(h.getSolution().col_value)
                        try_integer([ok[j] for j in range(len(ok)) if xs[N + j] > 0.5])
                    if 'Infeasible' in st or (('Optimal' in st) and info.objective_function_value > thr + 1e-6) or info.mip_dual_bound > thr + 1e-6:
                        log('  ENUM PROOF: no integer solution with obj <= thr'); heap = [(1e18, 0, node)]; enum_proof = True; break
            if a.rootonly: heap = [(z, 0, node)]; break
        if how in ('incomplete', 'timeout'):
            z = max(z, node['bound'])
            if z > thr + 1e-6: continue
            # cannot branch without an LP solution: re-solve LP on current columns and branch anyway (bound = parent's)
            M.h.run(); x = list(M.h.getSolution().col_value)
        if z > thr + 1e-6: continue
        if x is None or len(x) < M.h.getNumCol():
            M.h.run(); x = list(M.h.getSolution().col_value)
        if any(x[cidx] > 1e-6 for cidx in M.art):
            inf = phase1_infeasible(node)
            if inf: stats['infeas'] += 1; continue
            stats['art_but_feasible'] += 1
            M.h.run(); x = list(M.h.getSolution().col_value)
            if any(x[cidx] > 1e-6 for cidx in M.art):
                log('  WARNING artificial in LP but phase 1 not proven infeasible; node kept open'); stats['stuck'] += 1
                heapq.heappush(heap, (max(z, node['bound']) + 1e-9, cnt, dict(node, bound=1e18))); cnt += 1
                continue
        # candidates
        tval = [0.0] * T; arc = defaultdict(float); asg = defaultdict(float); integral = True; sol = []
        for j, (t, km, r) in enumerate(M.cols):
            v = x[M.hidx[j]]
            if v < 1e-7: continue
            if v < 1 - 1e-6: integral = False
            else: sol.append((t, km, r))
            tval[t] += v
            for k in r: asg[(t, k)] += v
            for e in arcs_of(r):
                if e[0] >= 0 and e[1] >= 0: arc[e] += v
        yv = x[:N]
        if any(1e-6 < v < 1 - 1e-6 for v in yv): integral = False
        if integral:
            if try_integer(sol): continue
            continue   # integral with obj > thr cannot be (z<=thr) -- safety
        children = []; desc = ''
        fy = [(M.pk[k] * min(yv[k], 1 - yv[k]), k) for k in range(N) if 1e-5 < yv[k] < 1 - 1e-5]
        ft = [(abs(tval[t] - round(tval[t])), t) for t in range(T) if abs(tval[t] - round(tval[t])) > 1e-5]
        fa = [(min(v, 1 - v), key) for key, v in asg.items() if 1e-5 < v < 1 - 1e-5]
        if stage == 1 and fy:
            _, k = max(fy)
            c1 = dict(node); c1['y'] = dict(node['y']); c1['y'][k] = 0
            c2 = dict(node); c2['y'] = dict(node['y']); c2['y'][k] = 1
            children = [c1, c2]; desc = f'y{k}={yv[k]:.3f}'
        elif ft:
            _, t = max(ft); v = tval[t]
            c1 = dict(node); c1['up'] = list(node['up']); c1['up'][t] = math.floor(v)
            c2 = dict(node); c2['lo'] = list(node['lo']); c2['lo'][t] = math.ceil(v)
            children = [c1, c2]; desc = f'type{t}={v:.3f}'
        elif fy:
            _, k = max(fy)
            c1 = dict(node); c1['y'] = dict(node['y']); c1['y'][k] = 0
            c2 = dict(node); c2['y'] = dict(node['y']); c2['y'][k] = 1
            children = [c1, c2]; desc = f'y{k}={yv[k]:.3f}'
        elif fa and a.strong > 0:
            far0 = sorted(((min(v, 1 - v), e) for e, v in arc.items() if 1e-5 < v < 1 - 1e-5), reverse=True)
            cand = []
            for _, (i, j) in far0[:a.strong]:
                c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((-1, i, j),)
                force = [(-1, i, k) for k in range(N) if k != j and k != i] + [(-1, k, j) for k in range(N) if k != i and k != j] + [(-1, -1, j), (-1, i, -1)]
                c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple(force)
                cand.append((f'arc {i}->{j}={arc[(i, j)]:.3f}', c1, c2))
            for _, (t, k) in sorted(fa, reverse=True)[:max(1, a.strong // 2)]:
                c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((t, k, k),)
                c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple((u, k, k) for u in range(T) if u != t)
                cand.append((f'asg t{t} k{k}={asg[(t, k)]:.3f}', c1, c2))
            bestc = None; pz = max(z, node['bound'])
            for dsc, c1, c2 in cand:
                zs = [pz, pz]
                for side, ch in ((1, c2), (0, c1)):
                    zc, xc, hw = solve_node(ch, a.sb_tl, 0); stats['sb_eval'] += 1
                    zs[side] = max(zc if zc is not None else -1e18, pz)
                    if zs[side] > thr + 1e-6: break     # probing: other side kept with parent bound, not evaluated
                sc = (min(zs), max(zs))
                if bestc is None or sc > bestc[0]: bestc = (sc, dsc, (c1, c2), zs)
                if max(zs) > thr + 1e-6: break
                if time.time() - t0 > a.tl: break
            _, desc, (c1, c2), zs = bestc
            children = []
            for ch, zc in zip((c1, c2), zs):
                if zc > thr + 1e-6: stats['sb_pruned'] += 1; continue
                ch['bound0'] = zc; children.append(ch)
            desc = 'SB ' + desc + f' zs={[round(v, 4) for v in zs]}'
        elif fa:
            _, (t, k) = max(fa)
            c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((t, k, k),)                       # k not by type t
            c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple((u, k, k) for u in range(T) if u != t)   # k by type t
            children = [c1, c2]; desc = f'asg t{t} k{k}={asg[(t, k)]:.3f}'
        else:
            far = [(min(v, 1 - v), e) for e, v in arc.items() if 1e-5 < v < 1 - 1e-5]
            if not far:
                log('  WARNING fractional but no branching candidate; node bound kept as open'); stats['stuck'] += 1
                heapq.heappush(heap, (max(z, node['bound']) + 1e-9, cnt, dict(node, bound=1e18))); cnt += 1
                continue
            _, (i, j) = max(far)
            c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((-1, i, j),)
            force = [(-1, i, k) for k in range(N) if k != j and k != i] + [(-1, k, j) for k in range(N) if k != i and k != j] + [(-1, -1, j), (-1, i, -1)]
            c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple(force)
            children = [c1, c2]; desc = f'arc {i}->{j}={arc[(i, j)]:.3f}'
        for ch in children:
            ch['bound'] = max(z, node['bound'], ch.pop('bound0', -1e18)); ch['depth'] = node['depth'] + 1
            heapq.heappush(heap, (ch['bound'], cnt, ch)); cnt += 1
        if nodes % 25 == 0:
            try_mip()
        openb = min([h[0] for h in heap] + [thr + 1e-3])
        if nodes % 10 == 1 or len(heap) < 3:
            log(f'  node {nodes} d{node["depth"]} z={z:.4f} {how} br {desc}; open {len(heap)} LB={openb:.4f} thr={thr} cuts={len(M.cuts)} cols={len(M.cols)} {dict(stats)} tim={ {k: round(v) for k, v in TIM.items()} }')

    def _unused(): pass
    openb = min([h[0] for h in heap if h[0] <= thr + 1e-6] + [math.inf])
    exhausted = not any(h[0] <= thr + 1e-6 for h in heap)
    log(f'B&P done: nodes {nodes} maxdepth {maxdepth}, exhausted {exhausted}, open LB {openb}, thr {thr}, found {found}, stats {dict(stats)}')
    pr.close()
    res = dict(inst=name, dir=d, stage=stage, nodes=nodes, exhausted=exhausted, open_lb=openb if openb < math.inf else None,
               thr=thr, root=rootz, found=found, enum_proof=enum_proof, seconds=round(time.time() - t0, 1), cuts=len(M.cuts), cols=len(M.cols))
    print('RESULT', json.dumps(res), flush=True)


if __name__ == '__main__':
    main()
