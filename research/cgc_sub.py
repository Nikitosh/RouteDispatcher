"""cgc: k(S) = lower bound on the number of vehicles needed to serve the order subset S alone
(valid for any full solution because dropping orders keeps routes feasible: cgc_tri.py checks t_ij <= t_ik+s_k+t_kj).
Computes ceil of the vehicle-minimisation LP (exact ng pricing, fleet type counts) on a sub-instance."""
import os, sys, math, subprocess, tempfile
import numpy as np, highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__)); INF = highspy.kHighsInf
SCR = os.environ.get('CGC_TMP', os.path.join(HERE, 'results', 'cgc_tmp'))

def write_sub(inst_path, S, fn):
    lines = open(inst_path).read().split('\n'); name = lines[0]
    tok = ' '.join(lines[1:]).split(); it = 0
    N, V, D = int(tok[0]), int(tok[1]), int(tok[2]); it = 3; M = D + N
    ords = [tok[it + 6 * k: it + 6 * k + 6] for k in range(N)]; it += 6 * N
    veh = [tok[it + 3 * v: it + 3 * v + 3] for v in range(V)]; it += 3 * V
    keep = list(range(D)) + [D + k for k in S]
    out = [name + '_sub', f'{len(S)} {V} {D}'] + [' '.join(ords[k]) for k in S] + [' '.join(v) for v in veh]
    for m in range(8):
        A = np.array(tok[it: it + M * M], dtype=object).reshape(M, M); it += M * M
        sub = A[np.ix_(keep, keep)]
        out += [' '.join(row) for row in sub]
    open(fn, 'w').write('\n'.join(out) + '\n')

class SubPricer:
    def __init__(self, fn, ng=10, binary='bin/cg_price'):
        self.p = subprocess.Popen([os.path.join(HERE, binary), fn, str(ng)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        T = int(self.p.stdout.readline().split()[1]); self.types = []
        for _ in range(T):
            a = list(map(int, self.p.stdout.readline().split())); self.types.append(dict(count=a[0], start=a[1], mode=a[2], mask=a[3], vehs=a[4:]))
    def price(self, level, alpha, pi, labcap=3e6, cuts=()):
        cuts = [c for c in cuts if c[3] < -1e-9]
        self.p.stdin.write(f'PRICE {level} 200 0 {labcap}\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n'
                           + f'{len(cuts)}\n' + ''.join(f'{a} {b} {c} {sg:.10f}\n' for a, b, c, sg in cuts)); self.p.stdin.flush()
        h = self.p.stdout.readline().split(); n, comp = int(h[1]), int(h[2]); mins = list(map(float, self.p.stdout.readline().split()[1:]))
        cols = []
        for _ in range(n):
            a = self.p.stdout.readline().split(); cols.append((int(a[0]), tuple(int(x) for x in a[4:])))
        return cols, mins, comp
    def close(self):
        try: self.p.stdin.write('QUIT\n'); self.p.stdin.flush(); self.p.wait(5)
        except Exception: self.p.kill()

def kS(inst_path, S, ng=10, maxit=400, labcap=3e6, integer=False, enum_max=300000, mip_tl=60, want_duals=False, src_rounds=0, cost_types=None, K=None, counts=None, group_max=None, seed_cols=None):
    """counts: per-type upper bounds overriding the fleet counts; group_max: (set of types, max routes) extra row."""
    """cost_types: routes of these types cost 1, others 0 (None = all cost 1); K: at most K routes in total."""
    """returns (lower bound, converged). Lower bound valid always (Lagrangian with MINRC).
    integer=True: if the LP converged with exact pricing, enumerate every elementary route with reduced cost
    <= ceil(z) - z (all routes of any solution with ceil(z) vehicles) and solve the partitioning MIP over them:
    infeasible => bound ceil(z)+1 (returned as ceil(z)+1 - 1e-9 style value = ceil(z)+0.5)."""
    S = sorted(S); n = len(S); z0 = []
    if n == 0: return 0.0, True
    fd, fn = tempfile.mkstemp(suffix='.txt', dir=SCR); os.close(fd)
    write_sub(inst_path, S, fn)
    pr = SubPricer(fn, ng); types = pr.types; T = len(types)
    h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1)
    for i in range(n): h.addRow(1.0, INF, 0, np.array([], dtype=np.int32), np.array([]))
    cnts = [types[t]['count'] if counts is None else counts[t] for t in range(T)]
    for t in range(T): h.addRow(-INF, float(cnts[t]), 0, np.array([], dtype=np.int32), np.array([]))
    h.addRow(-INF, float(K) if K is not None else INF, 0, np.array([], dtype=np.int32), np.array([]))
    gm_t, gm_v = group_max if group_max else (frozenset(), INF)
    h.addRow(-INF, float(gm_v), 0, np.array([], dtype=np.int32), np.array([]))
    ct = [1.0 if (cost_types is None or t in cost_types) else 0.0 for t in range(T)]
    # artificial (big) to be feasible
    for i in range(n): h.addCol(1000.0, 0, INF, 1, np.array([i], dtype=np.int32), np.array([1.0]))
    seen = set(); best = 0.0; conv = False; level = 1; colsl = []; hid = []; cuts = []; cutrow = []
    if seed_cols:
        pos = {k: i for i, k in enumerate(S)}
        for (t, r) in seed_cols:
            rr = tuple(pos[k] for k in r if k in pos)
            if not rr or (t, rr) in seen or (cost_types is not None and False): continue
            seen.add((t, rr)); cnt = {}
            for x in rr: cnt[x] = cnt.get(x, 0) + 1
            idx = sorted(cnt) + [n + t, n + T]; vals = [float(cnt[i]) for i in sorted(cnt)] + [1.0, 1.0]
            if t in gm_t: idx.append(n + T + 1); vals.append(1.0)
            hid.append(h.getNumCol()); colsl.append((t, rr))
            h.addCol(ct[t], 0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
    for it in range(maxit):
        h.run(); s = h.getSolution(); du = list(s.row_dual); z = h.getInfo().objective_function_value
        pi = du[:n]; mu = du[n:n + T]
        cc = [(C[0], C[1], C[2], du[row]) for C, row in zip(cuts, cutrow)]
        lam = du[n + T]
        gd = du[n + T + 1]
        alpha = [ct[t] - mu[t] - lam - (gd if t in gm_t else 0.0) for t in range(T)]
        cols, mins, comp = pr.price(level, alpha, pi, labcap, cuts=cc)
        if not cuts:
            L = sum(pi) + lam * (K if K is not None else 0) + sum(cnts[t] * min(0.0, mins[t] + mu[t]) for t in range(T)) if not group_max else -1e18
            best = max(best, L)
        add = 0
        for (t, r) in cols:
            if (t, r) in seen: continue
            seen.add((t, r)); cnt = {}
            for x in r: cnt[x] = cnt.get(x, 0) + 1
            idx = sorted(cnt) + [n + t, n + T]; vals = [float(cnt[i]) for i in sorted(cnt)] + [1.0, 1.0]
            if t in gm_t: idx.append(n + T + 1); vals.append(1.0)
            for C, row in zip(cuts, cutrow):
                q = sum(cnt.get(o, 0) for o in C) // 2
                if q: idx.append(row); vals.append(float(q))
            hid.append(h.getNumCol()); colsl.append((t, r))
            h.addCol(ct[t], 0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals)); add += 1
        if not add:
            if level == 1: level = 2; continue
            if not comp: break
            best = max(best, z); conv = True
            # 3-SRC rounds until the LP passes the next integer (then ceil rises) or no violated cut / 150 cuts
            if src_rounds <= 0 or len(cuts) >= 150: break
            if not z0: z0.append(z)
            if math.ceil(z - 1e-6) > math.ceil(z0[0] - 1e-6): break
            x = list(s.col_value); acc = {}
            import itertools
            for j, (t, r) in enumerate(colsl):
                v = x[hid[j]]
                if v < 1e-6: continue
                rs = sorted(set(r)); cnt = {o: r.count(o) for o in rs}
                for a_, b_ in itertools.combinations(rs, 2):
                    for c_ in range(n):
                        if c_ == a_ or c_ == b_: continue
                        key = tuple(sorted((a_, b_, c_)))
                        acc.setdefault(key, set()).add(j)
            viol = []
            for key, js in acc.items():
                v = sum(x[hid[j]] * (sum(colsl[j][1].count(o) for o in key) // 2) for j in js)
                if v > 1 + 1e-3 and key not in cuts: viol.append((v, key))
            viol.sort(reverse=True); new = []; used = {}
            for v, key in viol:
                if len(new) >= 30: break
                if any(used.get(o, 0) >= 5 for o in key): continue
                new.append(key)
                for o in key: used[o] = used.get(o, 0) + 1
            if not new: break
            src_rounds -= 1
            for key in new:
                idx = []; vals = []
                for j, (t, r) in enumerate(colsl):
                    q = sum(r.count(o) for o in key) // 2
                    if q: idx.append(hid[j]); vals.append(float(q))
                cutrow.append(h.getNumRow()); cuts.append(key)
                h.addRow(-INF, 1.0, len(idx), np.array(idx, dtype=np.int32), np.array(vals))
            level = 1; continue
        if level == 2 and add < 5: level = 1
    if integer and conv:
        c = math.ceil(best - 1e-6)
        gap = c - z + 1e-6
        efn = fn + '.enum'
        pr.p.stdin.write(f'ENUM {gap:.10f} 0 {enum_max} {efn}\n' + ' '.join(f'{a:.10f}' for a in alpha) + '\n' + ' '.join(f'{x:.10f}' for x in pi) + '\n0\n'); pr.p.stdin.flush()
        hh = pr.p.stdout.readline().split(); ne, comp = int(hh[1]), int(hh[2])
        routes = []
        if os.path.exists(efn):
            for line in open(efn):
                q = line.split(); routes.append((int(q[0]), tuple(int(x) for x in q[4:])))
            os.remove(efn)
        if comp:
            m = highspy.Highs(); m.setOptionValue('output_flag', False); m.setOptionValue('threads', 1); m.setOptionValue('time_limit', float(mip_tl))
            for i in range(n): m.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
            for t in range(T): m.addRow(-INF, float(types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
            m.addRow(-INF, float(c), 0, np.array([], dtype=np.int32), np.array([]))
            for (t, r) in routes:
                idx = sorted(r) + [n + t, n + T]
                m.addCol(1.0, 0, 1, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
            nc = len(routes)
            if nc:
                m.changeColsIntegrality(nc, np.arange(nc, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * nc))
            m.run(); st = str(m.getModelStatus())
            if 'Infeasible' in st or nc == 0: best = c + 0.5
            print(f'      kS-int |S|={n}: LP {z:.4f} pool {nc} MIP {st}', flush=True)
        else:
            print(f'      kS-int |S|={n}: enumeration incomplete ({ne})', flush=True)
    pr.close(); os.remove(fn)
    if want_duals: return best, conv, dict(zip(S, pi)), {t: mu[t] for t in range(T)}, types
    return best, conv

if __name__ == '__main__':
    import json
    inst = sys.argv[1]; S = json.loads(sys.argv[2]); print(kS(inst, S))
