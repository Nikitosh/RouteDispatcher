"""cgm: MERGE of the cgx engine (fast B&P: big-M artificials dropped, Lagrangian pruning, lm-SRC <= 512, column purge,
node enumeration with SRC-aware A-mask completion bounds + exact pool) and the cgc engine (rounded FLEET CUTS
sum_{r visits S} x_r >= k(S), route-count row = K, vehicle-level / set branching).  Pricer: bin/cgm_price.
(original cgx doc:) km-stage column generation / branch-and-price with the cgx pricer (solvers/cgx_price.cpp).
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
SCR = os.environ.get('CGM_TMP', '/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/d8f727be-c46e-4a0a-ac5e-5e8ed1e794e5/scratchpad/cgm_tmp')
os.makedirs(SCR, exist_ok=True); os.environ.setdefault('CGC_TMP', SCR)
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
        self.forb = []; self.fl = []; self.tsum = 0.0; self.ncalls = 0

    def _send(self, head, alpha, pi, cuts):
        cuts = [c for c in cuts if c[3] < -1e-9]
        self.p.stdin.write(head + '\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
                           + f'{len(cuts)}\n' + ''.join(f'{a} {b} {c} {sg:.10f} ' + ('-1' if mem is None else f'{len(mem)} ' + ' '.join(map(str, mem))) + '\n' for a, b, c, sg, mem in cuts)
                           + fleet_lines(self.fl)
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


def fleet_lines(fl):
    fl = [f for f in fl if abs(f[0]) > 1e-9]
    return f'{len(fl)}\n' + ''.join(f'{g:.10f} {len(S)} ' + ' '.join(map(str, sorted(S))) + f' {len(ty)} ' + ' '.join(map(str, sorted(ty))) + '\n' for g, S, ty in fl)


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
        self.fl = []; self.big = big

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
        rs = set(r)
        for f in self.fl:
            if (not f['ty'] or t in f['ty']) and not rs.isdisjoint(f['S']): idx.append(f['row']); vals.append(1.0)
        self.h.addCol(km, 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        return len(self.cols) - 1

    def add_frow(self, S, ty, lo, up):
        """fleet row: sum over routes of applicable types (ty empty = all) visiting S, bounds [lo, up]; +-1 artificials"""
        S = frozenset(S); ty = frozenset(ty or ()); f = dict(S=S, ty=ty, glo=lo, gup=up)
        idx = [self.hidx[j] for j, (t, km, r) in enumerate(self.cols) if (not ty or t in ty) and not S.isdisjoint(r)]
        f['row'] = self.h.getNumRow()
        self.h.addRow(lo, up, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
        self.rlo.append(lo); self.rup.append(up)
        for sg in (1.0, -1.0):
            self.art.append(self.h.getNumCol()); self.h.addCol(self.big, 0.0, INF, 1, np.array([f['row']], dtype=np.int32), np.array([sg]))
        self.fl.append(f)
        return len(self.fl) - 1

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
        dele = np.array(sorted(self.hidx[j] for j in range(n) if not keep[j]), dtype=np.int32)
        self.h.deleteCols(len(dele), dele)
        remap = lambda c: c - int(np.searchsorted(dele, c))
        self.cols = [self.cols[j] for j in range(n) if keep[j]]
        self.hidx = [remap(self.hidx[j]) for j in range(n) if keep[j]]
        self.art = [remap(c) for c in self.art]
        assert self.h.getNumCol() == len(self.hidx) + len(self.art)
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


def extra_cands(I, cand, log):
    """cgm: extra fleet-cut candidate families: ALL orders (and each pair of depot clusters) restricted to window-start
    ranges [lo,hi) and window-end ranges; window-start range x skill subset."""
    from cgc_master import clusters_of
    N = I['N']; used, near = clusters_of(I); n0 = len(cand)
    starts = sorted(set(o['a'] for o in I['ords'])) + [1e9]; ends = sorted(set(o['b'] for o in I['ords'])) + [1e9]
    skills = sorted(set(o['skill'] for o in I['ords']))
    sks = [frozenset(c) for rr in range(1, len(skills)) for c in itertools.combinations(skills, rr)]
    bases = [(frozenset(range(N)), 'all')] + [(frozenset(k for k in range(N) if near[k] in pr_), f'd{"".join(map(str, pr_))}')
                                              for rr in (1, 2) for pr_ in itertools.combinations(used, rr) if rr < len(used)]
    for B, nm in bases:
        for i1 in range(len(starts) - 1):
            for i2 in range(i1 + 1, len(starts)):
                S = frozenset(k for k in B if starts[i1] <= I['ords'][k]['a'] < starts[i2])
                if 0 < len(S) < N: cand.setdefault(S, f'x{nm}a[{starts[i1]:g},{starts[i2]:g})')
                for sk in sks:
                    S2 = frozenset(k for k in S if I['ords'][k]['skill'] in sk)
                    if 0 < len(S2) < N: cand.setdefault(S2, f'x{nm}a[{starts[i1]:g},{starts[i2]:g})sk' + ''.join(map(str, sorted(sk))))
        for i1 in range(len(ends) - 1):
            for i2 in range(i1 + 1, len(ends)):
                S = frozenset(k for k in B if ends[i1] <= I['ords'][k]['b'] < ends[i2])
                if 0 < len(S) < N: cand.setdefault(S, f'x{nm}b[{ends[i1]:g},{ends[i2]:g})')
    log(f'extra candidate families: {len(cand) - n0} new sets (total {len(cand)})')


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
    ap.add_argument('--pricer', default='bin/cgm_price'); ap.add_argument('--pargs', default='')
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
    ap.add_argument('--fleet', type=int, default=1, help='rounded fleet cuts (cgc)'); ap.add_argument('--kstl', type=float, default=300)
    ap.add_argument('--maxfleet', type=int, default=40); ap.add_argument('--vbr', type=int, default=1, help='vehicle-level/set branching')
    ap.add_argument('--nodefleet', type=int, default=1); ap.add_argument('--check', action='store_true')
    ap.add_argument('--slowprice', type=float, default=6.0); ap.add_argument('--enum-tl', type=float, default=60)
    ap.add_argument('--xcands', type=int, default=0); ap.add_argument('--small-first', type=int, default=0)
    ap.add_argument('--pb-every', type=float, default=1200); ap.add_argument('--pb-tl', type=float, default=300); ap.add_argument('--pb-final', type=float, default=1200)
    ap.add_argument('--lpstrat', type=int, default=0); ap.add_argument('--root-mip', type=float, default=60)
    a = ap.parse_args(); t0 = time.time()
    ovf = os.path.join(SCR, 'cgm_overrides.json')   # queue workers pick up changed defaults for the next instance
    if os.path.exists(ovf):
        for k_, v_ in json.load(open(ovf)).items():
            setattr(a, k_, v_)
        log0 = f'overrides {json.load(open(ovf))}'
    else: log0 = ''
    inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    Rb, _ = parse_output(open(os.path.join(HERE, 'best', d, name + '.out')).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    Pb, Kb, UB = score(I, Rb)
    assert Pb == 0, 'penalty stage not handled'
    K = a.K or Kb
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    lbf = os.path.join(HERE, 'lb', d, name + '.json'); L0 = json.load(open(lbf)) if os.path.exists(lbf) else {}
    log(name, 'N', N, 'V', I['V'], 'best', (Pb, Kb, round(UB, 4)), 'K', K, 'stored lb_km', L0.get('lb_km'), 'lb_used', L0.get('lb_used'), log0)
    os.environ['CGM_ENUM_TL'] = str(a.enum_tl)
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
    # ---------------- cgc part: route-count row = K, rounded fleet cuts, vehicle-level branching sets
    eqK = L0.get('lb_used') == K and L0.get('lb_pen', 0) >= Pb and K == Kb
    if eqK:
        M.set_row(N + T, float(K), float(K))
        for sg in (1.0, -1.0): M.art.append(M.h.getNumCol()); M.h.addCol(big, 0.0, INF, 1, np.array([N + T], dtype=np.int32), np.array([sg]))
    use_fleet = bool(a.fleet) and eqK
    if use_fleet:
        from cgc_tri import check as tri_check
        tri = tri_check(inst)
        if tri > 1e-9: log('triangle condition violated -> no fleet cuts', tri); use_fleet = False
    cand = {}; bsets = []; tgroups = []; kcache = {}; ks_time = [0.0]; kseed = [None]
    kfile = os.path.join(SCR, f'kcache_{d}_{name}.json')
    if use_fleet:
        from cgc_master import build_cands
        from cgc_sub import kS
        cand = build_cands(I, log)
        if a.xcands: extra_cands(I, cand, log)
        bsets = [S for S, nm in cand.items() if '[' not in nm and 'sk' not in nm]
        starts_cnt = {}
        for ty in types: starts_cnt[ty['start']] = starts_cnt.get(ty['start'], 0) + ty['count']
        main_dep = max(starts_cnt, key=lambda s_: starts_cnt[s_])
        far_t = [t for t in range(T) if types[t]['start'] != main_dep]
        for sdep in sorted(starts_cnt):
            if sdep == main_dep: continue
            g = frozenset(t for t in range(T) if types[t]['start'] == sdep); tgroups.append(g)
            for t in g:
                if frozenset([t]) not in tgroups: tgroups.append(frozenset([t]))
        if far_t and frozenset(far_t) not in tgroups: tgroups.append(frozenset(far_t))
        for kf in (os.path.join(HERE, 'results', 'cgc_tmp', f'kcache_{d}_{name}.json'), kfile):
            if os.path.exists(kf):
                for kk, v in json.load(open(kf)).items(): kcache[frozenset(map(int, kk.split(',')))] = int(v)
        log(f'fleet: {len(cand)} candidate sets, {len(bsets)} base sets, type groups {[sorted(g) for g in tgroups]}, {len(kcache)} cached k')
    clist = list(cand); cidx = {S: i for i, S in enumerate(clist)}
    Cm = np.zeros((len(clist), N), dtype=np.float32)
    for i, S in enumerate(clist): Cm[i, list(S)] = 1.0

    def kval(S, allow_new=True):
        if S not in kcache:
            if not allow_new or ks_time[0] > a.kstl: return None
            ts = time.time(); v, conv = kS(inst, S, 10, seed_cols=kseed[0]); ks_time[0] += time.time() - ts
            kcache[S] = int(math.ceil(v - 1e-4)) if v > -1e17 else 0
            log(f'   k(S) {cand.get(S, "?")} |S|={len(S)}: LP {v:.4f} conv={conv} -> {kcache[S]} ({time.time()-ts:.1f}s)')
            try: json.dump({','.join(map(str, sorted(k))): v for k, v in kcache.items()}, open(kfile, 'w'))
            except Exception: pass
        return kcache[S]

    def support(x, ty=None):
        return [(M.cols[j], x[M.hidx[j]]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-9 and (ty is None or M.cols[j][0] in ty)]

    def lhs_vec(sup, rows=None):
        """lhs of candidate sets: sum of x over support routes visiting S (numpy)"""
        if not sup or not clist: return np.zeros(len(clist) if rows is None else len(rows))
        R = np.zeros((N, len(sup)), dtype=np.float32); xv = np.array([v for c, v in sup])
        for i, ((t, km, r), v) in enumerate(sup): R[list(r), i] = 1.0
        C = Cm if rows is None else Cm[rows]
        return ((C @ R) > 0.5).astype(np.float64) @ xv

    fl_have = set()

    def separate_fleet(x, allow_new=True, maxn=8):
        sup = support(x); kseed[0] = [(c[0], c[2]) for c, v in sup if v > 1e-6]
        L = lhs_vec(sup); viol = []
        # cgm: cached sets first, then uncached SMALL sets first (cheap k(S), and small time-window sets gave the
        # strongest cuts); integral lhs is skipped only for large uncached sets (k can exceed an integral lhs)
        order = sorted(range(len(clist)), key=lambda i: (clist[i] not in kcache, len(clist[i]) if a.small_first else -len(clist[i])))
        for i in order:
            S = clist[i]; v = L[i]
            if S in fl_have: continue
            fr = v - math.floor(v + 1e-6)
            if fr < 0.02 and not (S in kcache and kcache[S] > v + 1e-3) and not (a.small_first and len(S) <= 30 and S not in kcache): continue
            if S not in kcache:
                if not allow_new or ks_time[0] > a.kstl: continue
                if any(kv2 <= v + 1e-3 and S <= S2 for S2, kv2 in kcache.items()): continue   # k monotone in S
            k = kval(S, allow_new)
            if k is not None and k > v + 1e-3: viol.append((k - v, S, k))
        viol.sort(key=lambda z_: (-round(z_[0], 3), len(z_[1]))); out = []; picked = []
        for g, S, k in viol:
            if len(out) >= max(0, min(maxn, a.maxfleet - sum(1 for f in M.fl if f['glo'] > 0))): break
            if len(M.fl) >= 240: break
            if any(P <= S and kp >= k for P, kp in picked): continue
            picked.append((S, k))
            fid = M.add_frow(S, None, float(k), INF); fl_have.add(S); out.append((S, k, g))
            M.fl[fid]['name'] = cand.get(S, '?'); M.fl[fid]['k'] = k
        return out

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
        fb = node.get('fb', {})
        for i, f in enumerate(M.fl):
            l, u = fb.get(i, (f['glo'], f['gup'])); M.set_row(f['row'], float(l), float(u))
        for c in M.art: M.h.changeColBounds(c, 0.0, INF)
        M.art_on = True
        return fs

    stats = dict(price_calls=0); nodes_done = [0]

    def cg_node(node, tl, allow_cuts=True):
        """returns (lp value if converged else None, best Lagrangian bound, x, duals)"""
        fs = apply(node); ts = time.time(); level = 1; hist = []; bestL = -1e18; it = 0
        while True:
            it += 1; tl0 = time.time()
            if a.lpstrat: M.h.setOptionValue('simplex_strategy', 1 if it == 1 else a.lpstrat)   # dual after bound changes, primal after adding columns
            z, du, x = M.solve()
            if M.art_on and all(x[c] < 1e-9 for c in M.art):
                # artificials unused: drop them, otherwise degenerate duals carry the big-M cost into pricing
                for c in M.art: M.h.changeColBounds(c, 0.0, 0.0)
                M.art_on = False; z, du, x = M.solve()
            stats['tlp'] = stats.get('tlp', 0) + time.time() - tl0
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            cuts = [(S[0], S[1], S[2], du[row], mem) for S, row, mem in zip(M.cuts, M.cutrow, M.cutmem)]
            alpha = [-mu[t] - lam for t in range(T)]
            pr.fl = [(du[f['row']], f['S'], f['ty']) for f in M.fl]
            cols, mins, comp = pr.price(level, 60 if level == 1 else 300, alpha, pi, cuts, a.l1cap if level == 1 else a.labcap)
            stats['price_calls'] += 1
            if level == 2 and pr.last > a.slowprice and not stats.get('srccap'):
                stats['srccap'] = True; log(f'    slow exact pricing ({pr.last:.1f}s) with {len(M.cuts)} SRC: no more SRC')
            if a.check and cols:
                mx = 0.0
                for (t, km, rc, r) in cols:
                    rcm = km - sum(pi[o] for o in r) - mu[t] - lam
                    for S_, row_, mem_ in zip(M.cuts, M.cutrow, M.cutmem): rcm -= du[row_] * lmcoef(r, S_, mem_)
                    for f in M.fl:
                        if (not f['ty'] or t in f['ty']) and not f['S'].isdisjoint(r): rcm -= du[f['row']]
                    mx = max(mx, abs(rcm - rc))
                stats['chk'] = max(stats.get('chk', 0), mx)
                if mx > 1e-5: log(f'    RC MISMATCH {mx:.2e}')
            # Lagrangian bound: dual objective + sum_t up_t * min(0, minrc_t)  (mins valid lower bounds)
            Lg = M.dual_obj(du) + sum(node['up'][t] * min(0.0, mins[t]) for t in range(T))   # valid for any sign-correct duals
            bestL = max(bestL, Lg)
            added = 0; ta0 = time.time()
            for (t, km, rc, r) in cols:
                j = M.add(t, km, r)
                if j >= 0:
                    added += 1
                    if not colok((t, km, r), fs): M.h.changeColBounds(M.hidx[j], 0.0, 0.0); M.allowed[M.hidx[j]] = False
            stats['tadd'] = stats.get('tadd', 0) + time.time() - ta0
            if a.verbose_cg if hasattr(a, 'verbose_cg') else (it % 10 == 1 or not added):
                log(f'    it{it} lvl{level} z={z:.4f} L={bestL:.4f} add={added} cols={len(M.cols)} cuts={len(M.cuts)} tprice={pr.last:.2f}s comp={comp}')
            if bestL >= best[0] - 1e-6: return bestL, bestL, x, du
            if not added:
                if level == 1: level = 2; continue
                if not comp: return None, bestL, x, du
                bestL = max(bestL, z)
                hist.append(z)
                if not allow_cuts or z >= best[0] - 1e-6 or len(M.cuts) >= a.maxcuts or (stats.get('srccap') and not use_fleet) or time.time() - ts > tl \
                        or (len(hist) >= 3 and hist[-1] - hist[-3] < 2e-4 * abs(z)):
                    return z, bestL, x, du
                if use_fleet and (nodes_done[0] == 0 or a.nodefleet):
                    newf = separate_fleet(x, allow_new=nodes_done[0] == 0)
                    if newf:
                        log(f'    LP {z:.4f}: +{len(newf)} fleet cuts ' + ', '.join(f'{cand.get(S_, "?")}>={k_} (viol {g_:.2f})' for S_, k_, g_ in newf))
                        level = 1; hist = []; continue
                if stats.get('srccap'): return z, bestL, x, du
                naug = M.augment(x) if a.lm else 0
                new = M.separate(x, maxcuts=min(30, a.maxcuts - len(M.cuts)))
                if not new and not naug: return z, bestL, x, du
                if naug: log(f'    memory augmented for {naug} cuts')
                for S in new: M.add_cut(S, M.memory_for(S, x) if a.lm else None)
                log(f'    +{len(new)} cuts (total {len(M.cuts)}), LP was {z:.4f}')
                level = 1; continue
            if time.time() - ts > tl: return None, bestL, x, du
            if level == 2 and added < 5: level = 1

    def vehicle_branch(node, x):
        """cgc branching: number of routes of a far-home type group entering a base cluster union, else number of
        routes visiting a candidate set (most fractional; fleet rows with branching bounds, capped at 63 rows)"""
        best_ty = None
        bidx = [cidx[S] for S in bsets]
        for ty in tgroups:
            sup = support(x, ty)
            if not sup: continue
            L = lhs_vec(sup, bidx)
            for S, v in zip(bsets, L):
                fr = v - math.floor(v)
                if 0.1 < fr < 0.9:
                    sc = min(fr, 1 - fr) + 0.001 * len(S) / N
                    if best_ty is None or sc > best_ty[0]: best_ty = (sc, S, ty, v)
        cands = []
        if best_ty: cands.append((best_ty[1], best_ty[2], best_ty[3], 'vset'))
        L = lhs_vec(support(x)); best_s = None
        for S, v in zip(clist, L):
            fr = v - math.floor(v)
            if 0.05 < fr < 0.95:
                sc = min(fr, 1 - fr) + 0.001 * len(S) / N
                if best_s is None or sc > best_s[0]: best_s = (sc, S, v)
        if best_s: cands.append((best_s[1], frozenset(), best_s[2], 'set'))
        for S, ty, v, kind in cands:
            fid = next((i for i, f in enumerate(M.fl) if f['S'] == S and f['ty'] == ty), None)
            if fid is None:
                if len(M.fl) >= 250: continue
                fid = M.add_frow(S, ty, 0.0, INF); M.fl[fid]['name'] = cand.get(S, '?') + (f'@{sorted(ty)}' if ty else '')
            f = M.fl[fid]; l0, u0 = node.get('fb', {}).get(fid, (f['glo'], f['gup']))
            c1 = dict(node); c1['fb'] = dict(node.get('fb', {})); c1['fb'][fid] = (l0, math.floor(v))
            c2 = dict(node); c2['fb'] = dict(node.get('fb', {})); c2['fb'][fid] = (max(l0, math.ceil(v)), u0)
            return c1, c2, f'{kind} {f["name"]} |S|={len(S)} {v:.3f}'
        return None

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
        od = os.path.join(HERE, 'results', d, 'cgpb'); os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, name + '.out'), 'w') as f:
            f.write('SOLVER cgm_bp\n')
            for v, r in enumerate(R): f.write(f"ROUTE {v} {' '.join(map(str, r))}\n")

    def enum_node(node, du, gap, zn):
        """enumerate all routes with rc <= gap at this node and solve the node exactly over the pool.
        returns the node's proven lower bound (>= UB means closed) or None if enumeration incomplete"""
        from cgx_pool import Pool
        pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        cuts = [(S[0], S[1], S[2], du[row], mem) for S, row, mem in zip(M.cuts, M.cutrow, M.cutmem)]
        alpha = [-mu[t] - lam for t in range(T)]
        pr.fl = [(du[f['row']], f['S'], f['ty']) for f in M.fl]
        pr.forb = list(node['forb']); te = time.time()
        if os.environ.get('CGX_TESTGAP'): gap = float(os.environ['CGX_TESTGAP'])
        routes, rcs, comp = pr.enum(gap + 1e-6, a.enum_max, os.path.join(SCR, f'enum_{os.getpid()}.txt'), alpha, pi, cuts)
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

    ARCH = {}
    def arch_add():
        for (t, km, r) in M.cols:
            k = (t, tuple(r))
            if k not in ARCH or ARCH[k] > km: ARCH[k] = km
    def col_mip(tl):
        """price-and-branch heuristic: set partitioning MIP over all generated columns (архив всех столбцов дерева)"""
        arch_add()
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1)
        h.setOptionValue('time_limit', float(tl)); h.setOptionValue('mip_rel_gap', 0.0)
        for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
        for t in range(T): h.addRow(0.0, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
        h.addRow(-INF, float(K), 0, np.array([], dtype=np.int32), np.array([]))
        ok = [(t, km, list(r)) for (t, r), km in ARCH.items() if len(set(r)) == len(r)]
        log(f'  col MIP over {len(ok)} archived columns, tl {tl}')
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

    root = dict(lo=[0] * T, up=[ty['count'] for ty in types], forb=(), fb={}, bound=-1e18, depth=0)
    heap = [(-1e18, 0, root)]; cnt = 1; nodes = 0; rootlb = None; hardLB = []  # bounds of nodes dropped unsolved
    last_pb = time.time()
    while heap and time.time() - t0 < a.tl:
        if time.time() - last_pb > a.pb_every: col_mip(a.pb_tl); last_pb = time.time()
        if nodes % 5 == 0: arch_add()
        bnd, _, node = heapq.heappop(heap)
        if bnd >= best[0] - 1e-6: continue
        tn = time.time()
        z, Lg, x, du = cg_node(node, min(a.node_tl, max(10, a.tl - (time.time() - t0))) if nodes else a.tl)
        nodes += 1; nodes_done[0] = nodes
        if len(x) < M.h.getNumCol():
            M.h.run(); x = list(M.h.getSolution().col_value)
        nb = max(node['bound'], Lg) if z is None else max(z, node['bound'])
        if nodes == 1:
            rootlb = nb; log(f'ROOT: lp={z} lagr={Lg:.4f} bound={nb:.4f} UB={best[0]:.4f} cuts={len(M.cuts)} cols={len(M.cols)} price {pr.ncalls} calls {pr.tsum:.1f}s')
            if a.root_mip > 0 and best[0] - nb > 0.03 * best[0]: col_mip(a.root_mip)   # price-and-branch incumbent
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
        if any(x[c] > 1e-6 for c in M.art):   # artificial used at LP optimum below UB: keep bound (valid), do not branch
            hardLB.append(nb); log(f'  node {nodes} uses artificials, bound {nb:.4f} kept'); continue
        if z is None:
            # cgm: unconverged node (time limit / label cap). Its bound nb (parent or Lagrangian) is valid; closing is not
            # allowed, but branching on the restricted-master solution is (children inherit nb).
            integ = all(x[M.hidx[j]] < 1e-6 or x[M.hidx[j]] > 1 - 1e-6 for j in range(len(M.cols)))
            if integ:
                try_incumbent(x); hardLB.append(nb); log(f'  node {nodes} unconverged + integral RMP, bound {nb:.4f} kept'); continue
            log(f'  node {nodes} unconverged (bound {nb:.4f}): branching on the RMP solution'); stats['unconv'] = stats.get('unconv', 0) + 1
        elif try_incumbent(x) or all(x[M.hidx[j]] < 1e-6 or x[M.hidx[j]] > 1 - 1e-6 for j in range(len(M.cols))): continue
        if z is not None and a.enum_gap > 0 and best[1] is not None or (a.enum_gap > 0 and best[0] < 1e8):
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
        elif use_fleet and a.vbr and (vb := vehicle_branch(node, x)) is not None:
            c1, c2, desc = vb
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
    if a.check: log(f'max |rc pricer - rc master| = {stats.get("chk", 0):.2e}')
    log(f'DONE nodes {nodes} LB {openb:.4f} UB {best[0]:.4f} gap {(best[0]/openb-1)*100 if openb>0 else 999:.3f}% exhausted {exhausted} price {pr.ncalls} calls {pr.tsum:.1f}s sb {stats.get("sb", 0):.1f}s enum {stats.get("enum", 0)} tlp {stats.get("tlp", 0):.1f}s tadd {stats.get("tadd", 0):.1f}s')
    pr.close()
    res = dict(lb=openb, root=rootlb, ub=best[0], nodes=nodes, exhausted=exhausted, K=K, seconds=round(time.time() - t0, 1))
    try: col_mip(a.pb_final)
    except Exception as e: log(f'final col MIP failed {e}')
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
        L['lb_km'] = newv; L['km_K'] = K; L['method'] = (L.get('method', '') + ' + cgm B&P (cgx engine + cgc fleet cuts)').strip(' +')
        if exhausted: L['proven_optimal'] = True
        L['cgm'] = dict(lb=lb, ub=ub, nodes=nodes, exhausted=exhausted, seconds=round(secs, 1), old_lb_km=old)
        L['seconds'] = round(L.get('seconds', 0) + secs, 1)
        json.dump(L, open(lbf, 'w'), indent=1); log(f'lb json updated {old} -> {newv}')
    else:
        log(f'lb json not updated (old {old}, new {newv})')


if __name__ == '__main__':
    main()
