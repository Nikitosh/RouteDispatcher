"""cgc: branch-price-and-cut for the km stage with ROUNDED FLEET CUTS for far-home instances.
Cut: sum_{r visits S} x_r >= k(S), k(S) = ceil(vehicle-min LP on the sub-instance S) (cgc_sub.py; valid because every
order is served (best pen = 0) and dropping orders keeps routes time-feasible, checked by cgc_tri.py).
Also: route-count row sum x_r = K (lb_used = K), 3-SRC cuts, best-first B&P (branch: vehicles per type, number of routes
entering a cluster set S, arcs).  Node bound = converged LP with exact ng pricing (bin/cgc_price), else parent bound.
Usage: cgc_master.py <inst> [--tl 3600] [--node-tl 300] [--nobranch]
Writes lb/<dir>/<inst>.json only if the bound is higher; log to stdout."""
import sys, os, json, time, math, heapq, argparse, itertools, subprocess
from collections import defaultdict
import numpy as np
import highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output
import cg_master as CM
from cgc_sub import kS
from cgc_tri import check as tri_check

HERE = os.path.dirname(os.path.abspath(__file__)); INF = highspy.kHighsInf


class Pricer(CM.Pricer):
    def __init__(self, inst, ng, lm=False):
        os.environ['CG_PRICER'] = 'bin/cgc_price3' if lm else 'bin/cgc_price'; super().__init__(inst, ng); self.forb = []; self.fl = []; self.lm = lm

    def _send(self, head, alpha, pi, cuts=()):
        cuts = [c for c in cuts if c[3] < -1e-9]
        fl = [f for f in self.fl if abs(f[0]) > 1e-9]
        s = (head + '\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
             + f'{len(cuts)}\n' + ''.join(f'{c_[0]} {c_[1]} {c_[2]} {c_[3]:.10f}' + ((f' {len(c_[4])} ' + ' '.join(map(str, sorted(c_[4]))) if c_[4] else ' 0') if self.lm else '') + '\n' for c_ in cuts)
             + f'{len(fl)}\n' + ''.join(f'{g:.10f} {len(S)} ' + ' '.join(map(str, sorted(S))) + f' {len(ty)} ' + ' '.join(map(str, sorted(ty))) + '\n' for g, S, ty in fl)
             + f'{len(self.forb)}\n' + ''.join(f'{i} {j}\n' for i, j in self.forb))
        self.p.stdin.write(s); self.p.stdin.flush()


class FM(CM.Master):
    """CM.Master + fleet rows (sum over routes of applicable types that visit S) with per-row bounds"""
    def __init__(self, I, types):
        super().__init__(I, types); self.fl = []   # dict(S, ty, row, glo, gup)
        self.mem = []   # per SRC: frozenset memory (None = full)

    @staticmethod
    def lmcoef(r, C, Mem):
        if Mem is None: return sum(r.count(x) for x in C) // 2
        a = 0; st = 0
        for v in r:
            if v not in Mem: st = 0
            if v in C:
                if st: a += 1; st = 0
                else: st = 1
        return a

    def add_cut(self, S, Mem=None):
        S = tuple(sorted(S)); idx = []; vals = []
        for j, (t, km, r) in enumerate(self.cols):
            c = self.lmcoef(r, S, Mem)
            if c: idx.append(self.hidx[j]); vals.append(float(c))
        row = self.h.getNumRow()
        self.h.addRow(-INF, 1.0, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        self.cuts.append(S); self.cutrow.append(row); self.mem.append(Mem)

    def fcoef(self, t, r, f):
        return 1.0 if ((not f['ty']) or t in f['ty']) and any(k in f['S'] for k in r) else 0.0

    def add(self, t, km, r):
        k = (t, r)
        if k in self.key: return False
        self.key.add(k); self.cols.append((t, km, r)); self.hidx.append(self.h.getNumCol())
        cnt = {}
        for x in r: cnt[x] = cnt.get(x, 0) + 1
        idx = sorted(cnt); vals = [float(cnt[i]) for i in idx]
        idx += [self.N + t, self.N + self.T]; vals += [1.0, 1.0]
        for S, row, Mem in zip(self.cuts, self.cutrow, self.mem):
            c = self.lmcoef(r, S, Mem)
            if c: idx.append(row); vals.append(float(c))
        for f in self.fl:
            if self.fcoef(t, r, f): idx.append(f['row']); vals.append(1.0)
        self.h.addCol(self.colcost(km), 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        return True

    def add_frow(self, S, ty, lo, up, big):
        S = frozenset(S); ty = frozenset(ty or ())
        f = dict(S=S, ty=ty, glo=lo, gup=up)
        idx = []; vals = []
        for j, (t, km, r) in enumerate(self.cols):
            if self.fcoef(t, r, f): idx.append(self.hidx[j]); vals.append(1.0)
        f['row'] = self.h.getNumRow()
        self.h.addRow(lo, up, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
        # artificial columns (+1 / -1) keep node LPs feasible under branching bounds
        f['art'] = []
        for sg in (1.0, -1.0):
            f['art'].append(self.h.getNumCol())
            self.h.addCol(big, 0.0, INF, 1, np.array([f['row']], dtype=np.int32), np.array([sg]))
        self.fl.append(f)
        return len(self.fl) - 1


def clusters_of(I):
    S = I['S']; used = sorted(set(v['start'] for v in I['veh']))
    near = [min(used, key=lambda s: I['T'][0][s][S + k]) for k in range(I['N'])]
    return used, near


def geo_clusters(I, thr):
    S = I['S']; N = I['N']; T = I['T'][0]
    d = [[(T[S + i][S + j] + T[S + j][S + i]) / 2 for j in range(N)] for i in range(N)]
    cl = [[i] for i in range(N)]
    D = {}
    while len(cl) > 1:
        best = None
        for x in range(len(cl)):
            for y in range(x + 1, len(cl)):
                v = max(d[i][j] for i in cl[x] for j in cl[y])
                if best is None or v < best[0]: best = (v, x, y)
        if best[0] > thr: break
        v, x, y = best; cl[x] = cl[x] + cl[y]; del cl[y]
    return [frozenset(c) for c in cl]


def build_cands(I, log=print, maxc=6000):
    """candidate order sets: families of base clusters (nearest start depot; complete-linkage geographic clusters at
    35 and 20 min car time), all unions inside a family (<= 7 clusters, else up to triples), and every base cluster
    restricted to window-start ranges [lo,hi), alone and united with the other clusters of its family."""
    N = I['N']
    used, near = clusters_of(I)
    fams = [[frozenset(k for k in range(N) if near[k] == s) for s in used]]
    for thr in (35, 20):
        g = [c for c in geo_clusters(I, thr) if len(c) >= 2]
        fams.append(g)
    cand = {}
    for fi, fam in enumerate(fams):
        fam = [c for c in fam if c]
        keys = list(range(len(fam)))
        mr = len(keys) if len(keys) <= 7 else 3
        for rr in range(1, mr + 1):
            for comb in itertools.combinations(keys, rr):
                S = frozenset().union(*[fam[c] for c in comb])
                if 0 < len(S) < N: cand.setdefault(S, f'f{fi}c{"".join(map(str, comb))}')
    # skill-restricted sets: (union of clusters of family 0/1, or all orders) intersected with a subset of skills
    skills = sorted(set(o['skill'] for o in I['ords']))
    sks = [frozenset(c) for rr in range(1, len(skills)) for c in itertools.combinations(skills, rr)]
    bases = [(frozenset(range(N)), 'all')]
    for fi, fam in enumerate(fams[:2]):
        fam = [c for c in fam if c]
        for rr in range(1, min(len(fam), 3) + 1):
            for comb in itertools.combinations(range(len(fam)), rr):
                bases.append((frozenset().union(*[fam[c] for c in comb]), f'f{fi}c{"".join(map(str, comb))}'))
    for B, nm in bases:
        for sk in sks:
            S = frozenset(k for k in B if I['ords'][k]['skill'] in sk)
            if 0 < len(S) < N: cand.setdefault(S, nm + 'sk' + ''.join(map(str, sorted(sk))))
    starts = sorted(set(o['a'] for o in I['ords'])) + [1e9]
    for fi, fam in enumerate(fams):
        fam = [c for c in fam if c]
        for ci, C in enumerate(fam):
            for i1 in range(len(starts) - 1):
                for i2 in range(i1 + 1, len(starts)):
                    S = frozenset(k for k in C if starts[i1] <= I['ords'][k]['a'] < starts[i2])
                    if not (0 < len(S) < len(C)): continue
                    nm = f'f{fi}c{ci}[{starts[i1]:g},{starts[i2]:g})'
                    cand.setdefault(S, nm)
                    others = [o for o in range(len(fam)) if o != ci]
                    mr = len(others) if len(others) <= 6 else 1
                    for rr in range(1, mr + 1):
                        for comb in itertools.combinations(others, rr):
                            U = S.union(*[fam[o] for o in comb])
                            if len(U) < N: cand.setdefault(U, nm + '+c' + ''.join(map(str, comb)))
                    if len(cand) > maxc: break
    log(f'families {[[len(c) for c in f] for f in fams]}, {len(cand)} candidate sets')
    return cand


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('--tl', type=float, default=3600)
    ap.add_argument('--ng', type=int, default=10); ap.add_argument('--node-tl', type=float, default=300)
    ap.add_argument('--nobranch', action='store_true'); ap.add_argument('--maxsrc', type=int, default=0)
    ap.add_argument('--nofleet', action='store_true'); ap.add_argument('--kstl', type=float, default=400)
    ap.add_argument('--write', type=int, default=1); ap.add_argument('--slowprice', type=float, default=25); ap.add_argument('--dump', action='store_true'); ap.add_argument('--check', action='store_true'); ap.add_argument('--lm', type=int, default=1); ap.add_argument('--enum', type=float, default=0.0); ap.add_argument('--enum-max', type=int, default=2000000); ap.add_argument('--pool-tl', type=float, default=600); ap.add_argument('--nenum', type=float, default=0.0); ap.add_argument('--nenum-max', type=int, default=200000); ap.add_argument('--nenum-tl', type=float, default=60); ap.add_argument('--sb', type=int, default=0); ap.add_argument('--sbtl', type=float, default=20)
    a = ap.parse_args(); t0 = time.time()
    if a.maxsrc <= 0: a.maxsrc = 300 if a.lm else 150
    inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    Rb, _ = parse_output(open(os.path.join(HERE, 'best', d, name + '.out')).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    Pbest, K, UB = CM.score(I, Rb)
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    lbf = os.path.join(HERE, 'lb', d, name + '.json'); L0 = json.load(open(lbf)) if os.path.exists(lbf) else {}
    log(name, 'best', (Pbest, K, round(UB, 4)), 'current lb', L0.get('lb_pen'), L0.get('lb_used'), L0.get('lb_km'))
    assert Pbest == 0 and L0.get('lb_used') == K, 'needs pen 0 and proven fleet size'
    tri = tri_check(inst); assert tri <= 1e-9, f'triangle condition violated {tri}'
    pr = Pricer(inst, a.ng, lm=a.lm); types = pr.types; T = len(types)
    vtype = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
    M = FM(I, types); M.stage = 3
    for v, r in enumerate(Rb):
        if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
    for t in range(T):
        for k in range(N):
            c = check(I, [[]] * types[t]['vehs'][0] + [[k]])
            if c['ok']: M.add(t, c['km'], (k,))
    M.set_stage(3, Pbest, K)
    big = 10.0 * UB
    M.set_types([0] * T, [ty['count'] for ty in types], big)
    # route-count row: exactly K routes (lb_used = K); artificial -1 col keeps it feasible
    M.h.changeRowBounds(N + T, float(K), float(K))
    M.h.addCol(big, 0.0, INF, 1, np.array([N + T], dtype=np.int32), np.array([1.0])); M.art.append(M.h.getNumCol() - 1)
    best = [UB, None]

    # ---------- candidate sets for fleet cuts / branching
    cand = build_cands(I, log)
    bsets = [S for S, nm in cand.items() if '[' not in nm and 'sk' not in nm]
    starts_cnt = {}
    for ty in types: starts_cnt[ty['start']] = starts_cnt.get(ty['start'], 0) + ty['count']
    main_dep = max(starts_cnt, key=lambda s: starts_cnt[s])
    tgroups = []
    far_t = [t for t in range(T) if types[t]['start'] != main_dep]
    for sdep in sorted(starts_cnt):
        if sdep == main_dep: continue
        g = frozenset(t for t in range(T) if types[t]['start'] == sdep)
        tgroups.append(g)
        for t in g:
            if frozenset([t]) not in tgroups: tgroups.append(frozenset([t]))
    if far_t and frozenset(far_t) not in tgroups: tgroups.append(frozenset(far_t))
    log(f'type groups for vehicle-level branching: {[sorted(g) for g in tgroups]}, {len(bsets)} base sets')
    kfile = os.path.join(HERE, 'runs', 'results', 'cgc_tmp', f'kcache_{d}_{name}.json')
    kcache = {}
    if os.path.exists(kfile):
        for kk, v in json.load(open(kfile)).items(): kcache[frozenset(map(int, kk.split(',')))] = v
    ks_time = [0.0]

    def kval(S):
        if S not in kcache:
            if ks_time[0] > a.kstl: return None
            ts = time.time(); v, conv = kS(inst, S, a.ng, seed_cols=kseed[0]); ks_time[0] += time.time() - ts
            kcache[S] = int(math.ceil(v - 1e-4)) if v > -1e17 else 0
            log(f'   k(S) {cand.get(S, "?")} |S|={len(S)}: LP {v:.4f} conv={conv} -> {kcache[S]} ({time.time()-ts:.1f}s)')
            try: json.dump({','.join(map(str, sorted(k))): v for k, v in kcache.items()}, open(kfile, 'w'))
            except Exception: pass
        return kcache[S]

    def lhs_all(x):
        """lhs of every candidate set: sum of x over routes visiting S"""
        act = [(M.cols[j], x[M.hidx[j]]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-9]
        out = {}
        for S in cand:
            out[S] = sum(v for (t, km, r), v in act if any(k in S for k in r))
        return out

    fl_have = set()

    kseed = [None]

    def separate_fleet(x, maxn=8):
        # seed columns for the k(S) sub-LPs: current LP support + cheapest columns (restricted to S inside kS)
        kseed[0] = [(M.cols[j][0], M.cols[j][2]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-6]
        L = lhs_all(x); viol = []
        for S, v in sorted(L.items(), key=lambda kv: (kv[0] not in kcache, -len(kv[0]))):
            if S in fl_have: continue
            fr = v - math.floor(v + 1e-6)
            if fr < 0.02 and not (S in kcache and kcache[S] > v + 1e-3): continue   # integral lhs cannot be cut by a rounded bound unless k > lhs+1: skip for speed
            if S not in kcache and any(kv2 <= v + 1e-3 and S <= S2 for S2, kv2 in kcache.items()): continue  # k monotone
            k = kval(S)
            if k is not None and k > v + 1e-3: viol.append((k - v, S, k))
        viol.sort(key=lambda z: (-round(z[0], 3), len(z[1]))); out = []; picked = []
        for g, S, k in viol:
            if len(out) >= max(0, min(maxn, 52 - len(M.fl))): break
            if any(P <= S and kp >= k for P, kp in picked): continue   # dominated by a smaller set with the same bound
            picked.append((S, k))
            fid = M.add_frow(S, None, float(k), INF, big); fl_have.add(S); out.append((S, k, g))
            M.fl[fid]['name'] = cand.get(S, '?'); M.fl[fid]['k'] = k
        return out

    def colok(col, forb_set):
        t, km, r = col
        for e in [(-1, r[0])] + [(r[i], r[i + 1]) for i in range(len(r) - 1)] + [(r[-1], -1)]:
            if e in forb_set: return False
        return True

    def apply(node):
        lo, up, forb = node['lo'], node['up'], node['forb']
        for t in range(T): M.h.changeRowBounds(N + t, float(lo[t]), float(up[t]))
        fb = node.get('fb', {})
        for i, f in enumerate(M.fl):
            l, u = fb.get(i, (f['glo'], f['gup'])); M.h.changeRowBounds(f['row'], float(l), float(u))
        fs = set(forb)
        if fs or fixed_any[0]:
            for j, col in enumerate(M.cols): M.h.changeColBounds(M.hidx[j], 0.0, INF if colok(col, fs) else 0.0)
            fixed_any[0] = bool(fs)
        pr.forb = list(forb)
        return fs

    srccap = [False]; fixed_any = [False]; chk = [0.0]

    def solve_node(node, tl, root=False):
        fs = apply(node); ts = time.time(); level = 1; hist = []
        while True:
            z, du, x = M.solve()
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            cuts = [(S[0], S[1], S[2], du[row], Mem) for S, row, Mem in zip(M.cuts, M.cutrow, M.mem)]
            pr.fl = [(du[f['row']], f['S'], f['ty']) for f in M.fl]
            alpha = [0.0 - mu[t] - lam for t in range(T)]
            tp = time.time()
            cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 1.0, alpha, pi, cuts=cuts)
            if level == 2 and time.time() - tp > a.slowprice and not srccap[0]:
                srccap[0] = True; log(f'   slow exact pricing ({time.time()-tp:.1f}s) with {len(M.cuts)} SRC: no more SRC')
            if a.check and cols:
                mx = 0.0
                for (t, km, rc, r) in cols:
                    cnt = {}
                    for o in r: cnt[o] = cnt.get(o, 0) + 1
                    rcm = km - sum(pi[o] * c for o, c in cnt.items()) - mu[t] - lam
                    for S3, row, Mem in zip(M.cuts, M.cutrow, M.mem): rcm -= du[row] * M.lmcoef(r, S3, Mem)
                    for f in M.fl:
                        if M.fcoef(t, r, f): rcm -= du[f['row']]
                    mx = max(mx, abs(rcm - rc))
                chk[0] = max(chk[0], mx)
                if mx > 1e-5: log(f'   RC MISMATCH {mx:.2e}')
            added = 0
            for (t, km, rc, r) in cols:
                if M.add(t, km, r):
                    added += 1
                    if fs and not colok((t, km, r), fs): M.h.changeColBounds(M.hidx[-1], 0.0, 0.0); fixed_any[0] = True
            if not added:
                if level == 1: level = 2; continue
                if not comp: log('   pricing incomplete'); return None, x
                hist.append(z)
                if z >= best[0] - 1e-6: return z, x
                if time.time() - ts > tl: return z, x
                # separation: fleet cuts first (root only / or anywhere: they are global valid cuts), then SRC
                new_f = [] if a.nofleet else separate_fleet(x)
                if new_f:
                    if root: log(f'  LP {z:.4f}: +{len(new_f)} fleet cuts ' + ', '.join(f'{cand.get(S,"?")}>={k} (viol {g:.2f})' for S, k, g in new_f))
                    level = 1; hist = []; continue
                if srccap[0] or len(M.cuts) >= a.maxsrc or (len(hist) >= 3 and hist[-1] - hist[-3] < 1e-3): return z, x
                new = M.separate(x, maxcuts=min(30, a.maxsrc - len(M.cuts)))
                if not new: return z, x
                for S in new:
                    Mem = None
                    if a.lm:
                        Mem = set(S)
                        for j in range(len(M.cols)):
                            v = x[M.hidx[j]]
                            if v < 1e-6: continue
                            r = M.cols[j][2]; pos = [i for i, o in enumerate(r) if o in S]
                            if len(pos) >= 2: Mem.update(r[pos[0]:pos[-1] + 1])
                        Mem = frozenset(Mem)
                    M.add_cut(S, Mem)
                if root: log(f'  LP {z:.4f}: +{len(new)} SRC (total {len(M.cuts)}) fleet rows {len(M.fl)} cols {len(M.cols)}')
                level = 1; continue
            if time.time() - ts > tl: return None, x
            if level == 2 and added < 5: level = 1

    root = dict(lo=[0] * T, up=[ty['count'] for ty in types], forb=(), fb={}, bound=-1e18, depth=0)
    z, x = solve_node(root, a.kstl + a.node_tl * 3, root=True)
    log(f'ROOT LP {z} (old lb_km {L0.get("lb_km")}, UB {UB:.4f}); fleet cuts {[(f.get("name"), f.get("k")) for f in M.fl]}; SRC {len(M.cuts)}')
    rootLB = z if z is not None else -1e18
    if a.dump:
        import pickle
        if x is None or len(x) < M.h.getNumCol(): M.h.run(); x = list(M.h.getSolution().col_value)
        pickle.dump(dict(types=types, sol=[(M.cols[j], x[M.hidx[j]]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-6], z=z, UB=UB, K=K),
                    open(os.path.join(HERE, 'runs', 'results', 'cgc_tmp', f'{name}_lp.pkl'), 'wb'))
    res = dict(root=rootLB)
    poolLB = -1e18; proven_pool = False
    if a.enum > 0 and z is not None and 1e-6 < UB - z <= a.enum * UB and not a.nobranch:
        from cg_pool2 import Pool
        apply(root); zz, du, xx = M.solve()
        pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        cuts = [(S[0], S[1], S[2], du[row], Mem) for S, row, Mem in zip(M.cuts, M.cutrow, M.mem)]
        pr.fl = [(du[f['row']], f['S'], f['ty']) for f in M.fl]
        alpha = [0.0 - mu[t] - lam for t in range(T)]
        te = time.time()
        routes, comp = pr.enum(UB - zz + 1e-6, 1.0, a.enum_max, os.path.join(HERE, 'runs', 'results', 'cgc_tmp', f'enum_{os.getpid()}.txt'), alpha, pi, cuts)
        log(f'ENUM gap {UB - zz:.4f}: routes {len(routes)} complete {comp} ({time.time()-te:.1f}s)')
        if comp:
            P = Pool(I, types, routes, 3, Pbest, K, log=log)
            pres = P.run(UB, time_budget=a.pool_tl, mip_tl=a.pool_tl * 0.7, rc0=pr.last_rc)
            log('  pool result', {k: v for k, v in pres.items() if k != 'chosen'})
            poolLB = pres['lb']
            if pres.get('chosen') and pres.get('obj', 1e18) < best[0] - 1e-6:
                R = CM.routes_to_vehicles(types, pres['chosen'], I['V']); s2 = CM.score(I, R)
                if s2 and s2[0] <= Pbest and s2[1] <= K and s2[2] < best[0] - 1e-6: best[0] = s2[2]; best[1] = R; log(f'  NEW incumbent (pool) {s2}')
            if pres['status'] in ('optimal', 'infeasible', 'lp_closed'):
                proven_pool = True; poolLB = min(best[0], max(poolLB, pres['lb']))
    nclosed = [0]

    def node_enum(node):
        """complete enumeration of the routes with rc <= UB - z at a converged node (node duals, forbidden arcs) and
        exact pool MIP with the node's type bounds (fleet-row branching bounds dropped = relaxation, still valid)"""
        from cg_pool2 import Pool
        apply(node); zz, du, xx = M.solve()
        pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        cuts = [(S[0], S[1], S[2], du[row], Mem) for S, row, Mem in zip(M.cuts, M.cutrow, M.mem)]
        pr.fl = [(du[f['row']], f['S'], f['ty']) for f in M.fl]
        alpha = [0.0 - mu[t] - lam for t in range(T)]
        te = time.time()
        routes, comp = pr.enum(best[0] - zz + 1e-6, 1.0, a.nenum_max, os.path.join(HERE, 'runs', 'results', 'cgc_tmp', f'enum_{os.getpid()}.txt'), alpha, pi, cuts)
        if not comp:
            log(f'   node enum gap {best[0]-zz:.3f}: incomplete ({len(routes)} routes, {time.time()-te:.1f}s)'); return False, -1e18
        P = Pool(I, types, routes, 3, Pbest, K, log=lambda *q: None); P.tlo = list(node['lo']); P.tup = list(node['up'])
        pres = P.run(best[0], time_budget=a.nenum_tl, mip_tl=a.nenum_tl * 0.7, rc0=pr.last_rc)
        log(f'   node enum gap {best[0]-zz:.3f}: {len(routes)} routes ({time.time()-te:.1f}s) -> {pres["status"]} lb {pres["lb"]:.4f}')
        if pres.get('chosen') and pres.get('obj', 1e18) < best[0] - 1e-6:
            R = CM.routes_to_vehicles(types, pres['chosen'], I['V']); s2 = CM.score(I, R)
            if s2 and s2[0] <= Pbest and s2[1] <= K and s2[2] < best[0] - 1e-6: best[0] = s2[2]; best[1] = R; log(f'  NEW incumbent (node pool) {s2}')
        if pres['status'] in ('optimal', 'infeasible', 'lp_closed'): return True, best[0]
        return False, pres['lb']

    heap = [(rootLB, 0, root)]; cnt = 1; nodes = 0; globalLB = 1e18
    first = True
    if proven_pool: heap = []
    while heap and time.time() - t0 < a.tl and not a.nobranch:
        bnd, _, node = heapq.heappop(heap)
        if bnd >= best[0] - 1e-6: continue
        if first: first = False
        else:
            z, x = solve_node(node, a.node_tl)
        nodes += 1
        conv_node = z is not None
        if x is None or len(x) < M.h.getNumCol():
            M.h.run(); x = list(M.h.getSolution().col_value)
        if z is None: z = node['bound']
        z = max(z, node['bound'])
        if z >= best[0] - 1e-6: continue
        if a.nenum > 0 and conv_node and 1e-6 < best[0] - z <= a.nenum * best[0]:
            closed, zb = node_enum(node)
            if closed: nclosed[0] += 1; continue
            z = max(z, zb)
            if z >= best[0] - 1e-6: nclosed[0] += 1; continue
            M.h.run(); x = list(M.h.getSolution().col_value)
        tval = [0.0] * T; arc = {}; integral = True
        act = []
        for j, (t, km, r) in enumerate(M.cols):
            v = x[M.hidx[j]]
            if v < 1e-7: continue
            act.append(((t, km, r), v))
            if v < 1 - 1e-6: integral = False
            tval[t] += v
            for e in [(r[i], r[i + 1]) for i in range(len(r) - 1)]: arc[e] = arc.get(e, 0.0) + v
        artpos = any(x[c] > 1e-6 for c in M.art) or any(x[c] > 1e-6 for f in M.fl for c in f['art'])
        if integral and not artpos and not conv_node:
            # restricted master integral but the node LP did not converge (time limit): cannot close the node
            log(f'  integral but unconverged node (bound {z:.4f} kept in global LB)'); globalLB = min(globalLB, z); continue
        if integral and not artpos:
            sol = [c for c, v in act if v > 0.5]
            R = CM.routes_to_vehicles(types, sol, I['V']); s = CM.score(I, R)
            if s and s[0] <= Pbest and s[1] <= K and s[2] < best[0] - 1e-6:
                best[0] = s[2]; best[1] = R; log(f'  NEW incumbent {s}')
            continue
        children = []; desc = ''
        ft = [(abs(tval[t] - round(tval[t])), t) for t in range(T) if abs(tval[t] - round(tval[t])) > 1e-4]
        if ft:
            _, t = max(ft); v = tval[t]
            c1 = dict(node); c1['up'] = list(node['up']); c1['up'][t] = math.floor(v)
            c2 = dict(node); c2['lo'] = list(node['lo']); c2['lo'][t] = math.ceil(v)
            children = [c1, c2]; desc = f'type {t} {v:.3f}'
        if not children:
            # branch on number of routes visiting a candidate set (most fractional, larger sets first)
            best_s = None
            # vehicle-level: routes of a far-home type (group) entering a cluster (union)
            best_ty = None
            for ty in tgroups:
                acts = [(r, v) for (t, km, r), v in act if t in ty]
                if not acts: continue
                for S in bsets:
                    v = sum(vv for r, vv in acts if any(k in S for k in r)); fr = v - math.floor(v)
                    if 0.1 < fr < 0.9:
                        sc = min(fr, 1 - fr) + 0.001 * len(S) / N
                        if best_ty is None or sc > best_ty[0]: best_ty = (sc, S, ty, v)
            if best_ty and (len(M.fl) < 63 or any(f['S'] == best_ty[1] and f['ty'] == best_ty[2] for f in M.fl)):
                _, S, ty, v = best_ty
                fid = next((i for i, f in enumerate(M.fl) if f['S'] == S and f['ty'] == ty), None)
                if fid is None: fid = M.add_frow(S, ty, 0.0, INF, big); M.fl[fid]['name'] = cand.get(S, '?') + f'@{sorted(ty)}'
                f = M.fl[fid]; l0, u0 = node.get('fb', {}).get(fid, (f['glo'], f['gup']))
                c1 = dict(node); c1['fb'] = dict(node.get('fb', {})); c1['fb'][fid] = (l0, math.floor(v))
                c2 = dict(node); c2['fb'] = dict(node.get('fb', {})); c2['fb'][fid] = (math.ceil(v), u0)
                children = [c1, c2]; desc = f'vset {M.fl[fid]["name"]} |S|={len(S)} {v:.3f}'
        if not children:
            best_s = None
            for S, v in lhs_all(x).items():
                fr = v - math.floor(v)
                if 0.05 < fr < 0.95:
                    sc = min(fr, 1 - fr) + 0.001 * len(S) / N
                    if best_s is None or sc > best_s[0]: best_s = (sc, S, v)
            if best_s and (len(M.fl) < 63 or any(f['S'] == best_s[1] and not f['ty'] for f in M.fl)):
                _, S, v = best_s
                fid = next((i for i, f in enumerate(M.fl) if f['S'] == S and not f['ty']), None)
                if fid is None: fid = M.add_frow(S, None, 0.0, INF, big); M.fl[fid]['name'] = cand.get(S, '?')
                f = M.fl[fid]; l0, u0 = node.get('fb', {}).get(fid, (f['glo'], f['gup']))
                c1 = dict(node); c1['fb'] = dict(node.get('fb', {})); c1['fb'][fid] = (l0, math.floor(v))
                c2 = dict(node); c2['fb'] = dict(node.get('fb', {})); c2['fb'][fid] = (math.ceil(v), u0)
                children = [c1, c2]; desc = f'set {cand.get(S,"?")} |S|={len(S)} {v:.3f}'
        if not children:
            fa = [(-abs(v - 0.5), e) for e, v in arc.items() if 1e-4 < v < 1 - 1e-4]
            if not fa:
                log(f'  node without branching candidate (art {artpos}); bound {z:.4f} kept'); globalLB = min(globalLB, z); continue
            _, (i, j) = max(fa)
            c1 = dict(node); c1['forb'] = tuple(node['forb']) + ((i, j),); c1['had_forb'] = True
            force = [(i, k) for k in range(N) if k != j] + [(k, j) for k in range(N) if k != i] + [(-1, j), (i, -1)]
            c2 = dict(node); c2['forb'] = tuple(node['forb']) + tuple(force); c2['had_forb'] = True
            children = [c1, c2]; desc = f'arc {i}->{j} {arc[(i, j)]:.3f}'; log(f'   arc branch at node {nodes}: {desc}')
        cb = [z, z]
        if a.sb > 0 and children and not desc.startswith('arc'):
            # strong branching (lite): alternative candidates = fractional types, best vehicle-level sets, best sets
            alts = [(children, desc)]
            ftl = sorted(ft, reverse=True)[:2] if ft else []
            for _, t in ftl:
                v = tval[t]
                if desc == f'type {t} {v:.3f}': continue
                d1 = dict(node); d1['up'] = list(node['up']); d1['up'][t] = math.floor(v)
                d2 = dict(node); d2['lo'] = list(node['lo']); d2['lo'][t] = math.ceil(v)
                alts.append(([d1, d2], f'type {t} {v:.3f}'))
            vc = []
            for ty in tgroups:
                acts = [(r, v) for (t, km, r), v in act if t in ty]
                if not acts: continue
                for S in bsets:
                    v = sum(vv for r, vv in acts if any(k in S for k in r)); fr = v - math.floor(v)
                    if 0.1 < fr < 0.9: vc.append((min(fr, 1 - fr), S, ty, v))
            vc.sort(key=lambda q: -q[0])
            seen_c = set()
            for sc, S, ty, v in vc:
                if len(alts) >= a.sb + 1: break
                if (S, ty, round(v, 3)) in seen_c: continue
                seen_c.add((S, ty, round(v, 3)))
                fid = next((i for i, f in enumerate(M.fl) if f['S'] == S and f['ty'] == ty), None)
                if fid is None:
                    if len(M.fl) >= 60: continue
                    fid = M.add_frow(S, ty, 0.0, INF, big); M.fl[fid]['name'] = cand.get(S, '?') + f'@{sorted(ty)}'
                dd = f'vset {M.fl[fid]["name"]} |S|={len(S)} {v:.3f}'
                if dd == desc: continue
                f = M.fl[fid]; l0, u0 = node.get('fb', {}).get(fid, (f['glo'], f['gup']))
                d1 = dict(node); d1['fb'] = dict(node.get('fb', {})); d1['fb'][fid] = (l0, math.floor(v))
                d2 = dict(node); d2['fb'] = dict(node.get('fb', {})); d2['fb'][fid] = (math.ceil(v), u0)
                alts.append(([d1, d2], dd))
            if len(alts) > 1:
                bestalt = None
                for ch, dd in alts:
                    zz = []
                    for c in ch:
                        zc, _ = solve_node(c, a.sbtl)
                        zz.append(max(z, zc) if zc is not None else z)
                    sc = (zz[0] - z + 1e-3) * (zz[1] - z + 1e-3)
                    if bestalt is None or sc > bestalt[0]: bestalt = (sc, ch, dd, zz)
                _, children, desc, cb = bestalt
                desc = 'SB ' + desc + f' -> {cb[0]:.2f}/{cb[1]:.2f}'
        for ci, c in enumerate(children):
            c['bound'] = cb[ci] if ci < 2 else z; c['depth'] = node['depth'] + 1
            heapq.heappush(heap, (c['bound'], cnt, c)); cnt += 1
        openb = min([h[0] for h in heap] + [best[0], globalLB])
        if nodes % 5 == 1 or len(heap) < 4:
            log(f'  node {nodes} d{node["depth"]} z={z:.4f} br {desc}; open {len(heap)} LB={openb:.4f} UB={best[0]:.4f} src={len(M.cuts)} fl={len(M.fl)} cols={len(M.cols)}')
    if a.nobranch: openb = rootLB; exhausted = False
    else:
        openb = min([h[0] for h in heap if h[0] < best[0]] + [best[0], globalLB])
        exhausted = not any(h[0] < best[0] - 1e-6 for h in heap) and globalLB >= best[0] - 1e-6
    if a.check: log(f'max |rc pricer - rc master| = {chk[0]:.2e}')
    if proven_pool: openb = best[0]; exhausted = True
    elif poolLB > openb: openb = min(poolLB, best[0])
    log(f'nodes closed by node enumeration: {nclosed[0]}')
    log(f'DONE nodes {nodes} root {rootLB:.4f} LB {openb:.4f} UB {best[0]:.4f} exhausted {exhausted}')
    pr.close()
    if best[1] is not None:
        od = os.path.join(HERE, 'runs', 'results', d, 'cgc'); os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, name + '.out'), 'w') as f:
            f.write('SOLVER cgc\n')
            for v, r in enumerate(best[1]): f.write(f"ROUTE {v} {' '.join(map(str, r))}\n")
    L = json.load(open(lbf)) if os.path.exists(lbf) else {}
    newlb = min(openb, best[0])
    print('RESULT', json.dumps(dict(inst=name, dir=d, root=rootLB, lb=newlb, ub=best[0], nodes=nodes, exhausted=exhausted,
                                    old=L.get('lb_km'), sec=round(time.time() - t0, 1), fleet=[(f.get('name'), f.get('k')) for f in M.fl if f['glo'] > 0])), flush=True)
    if a.write and L and L.get('lb_used') == K and L.get('lb_pen', 0) >= Pbest and newlb - 1e-4 > (L.get('lb_km') or -1):
        L['lb_km'] = round(newlb - 1e-4, 4); L['km_K'] = K
        L['method'] = 'CG + rounded fleet cuts k(S) + 3SRC + B&P (cgc_master.py)'
        L['cgc'] = dict(root=round(rootLB, 4), nodes=nodes, lb=round(newlb, 4), ub=round(best[0], 4), exhausted=exhausted,
                        seconds=round(time.time() - t0, 1), fleet_cuts=[(f.get('name'), f.get('k')) for f in M.fl if f['glo'] > 0])
        if exhausted: L['proven_optimal'] = True
        L['seconds'] = round(L.get('seconds', 0) + time.time() - t0, 1)
        json.dump(L, open(lbf, 'w'), indent=1); log('lb json updated', L['lb_km'])


if __name__ == '__main__':
    main()
