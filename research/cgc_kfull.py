"""cgc: full-instance "fleet count" bound: min sum_{r: type(r) in T', r visits A} x_r over the LP relaxation
{every order covered once (ng-routes), type counts, sum x_r = K, given valid fleet cuts sum_{r visits S} x_r >= k(S)}.
ceil(value) is a valid right-hand side for the cut sum_{r in T', r visits A} x_r >= ceil(value)
(exact pricing with bin/cgc_price: the objective indicator is passed as a fleet cut with dual -1)."""
import os, sys, math, time
import numpy as np, highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check
import cg_master as CM
from cgc_master import Pricer
INF = highspy.kHighsInf


class KFull:
    def __init__(self, inst, K, ng=10):
        self.inst = inst; self.I = load_instance(inst); self.K = K
        self.pr = Pricer(inst, ng); self.types = self.pr.types; self.N = self.I['N']; self.T = len(self.types)
        self.seed = []
        for t in range(self.T):
            for k in range(self.N):
                c = check(self.I, [[]] * self.types[t]['vehs'][0] + [[k]])
                if c['ok']: self.seed.append((t, (k,)))

    def run(self, A, Tp, fleet=(), maxit=500, labcap=3e6, extra_cols=()):
        """fleet: list of (S, types or None, lo). Returns (lower bound, converged)."""
        N, T, K = self.N, self.T, self.K; A = frozenset(A); Tp = frozenset(Tp or range(T))
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1)
        for i in range(N): h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([]))
        for t in range(T): h.addRow(-INF, float(self.types[t]['count']), 0, np.array([], dtype=np.int32), np.array([]))
        h.addRow(float(K), float(K), 0, np.array([], dtype=np.int32), np.array([]))
        fl = [(frozenset(S), frozenset(ty or ()), lo) for S, ty, lo in fleet]
        for S, ty, lo in fl: h.addRow(float(lo), INF, 0, np.array([], dtype=np.int32), np.array([]))
        big = 1000.0
        for i in range(N + T + 1 + len(fl)):   # artificials keep feasibility
            sg = -1.0 if N <= i < N + T else 1.0
            h.addCol(big, 0, INF, 1, np.array([i], dtype=np.int32), np.array([sg]))
        h.addCol(big, 0, INF, 1, np.array([N + T], dtype=np.int32), np.array([-1.0]))
        seen = set()

        def cost(t, r): return 1.0 if t in Tp and any(k in A for k in r) else 0.0

        def add(t, r):
            if (t, r) in seen: return False
            seen.add((t, r)); cnt = {}
            for x in r: cnt[x] = cnt.get(x, 0) + 1
            idx = sorted(cnt) + [N + t, N + T]; vals = [float(cnt[i]) for i in sorted(cnt)] + [1.0, 1.0]
            for c, (S, ty, lo) in enumerate(fl):
                if ((not ty) or t in ty) and any(k in S for k in r): idx.append(N + T + 1 + c); vals.append(1.0)
            h.addCol(cost(t, r), 0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(vals)); return True
        for t, r in list(self.seed) + list(extra_cols): add(t, r)
        level = 1; z = None; conv = False
        for it in range(maxit):
            h.run(); s = h.getSolution(); du = list(s.row_dual); z = h.getInfo().objective_function_value
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            self.pr.fl = [(du[N + T + 1 + c], S, ty) for c, (S, ty, lo) in enumerate(fl)] + [(-1.0, A, Tp)]
            alpha = [-mu[t] - lam for t in range(T)]
            cols, mins, comp = self.pr.price(level, 60 if level == 1 else 200, 0.0, alpha, pi, labcap=labcap)
            added = sum(add(t, r) for (t, km, rc, r) in cols)
            if not added:
                if level == 1: level = 2; continue
                conv = bool(comp); break
            if level == 2 and added < 5: level = 1
        return (z if conv else -1e18), conv

    def close(self): self.pr.close()


if __name__ == '__main__':
    import json
    from cgc_master import build_cands
    inst = sys.argv[1]; K = int(sys.argv[2]); I = load_instance(inst)
    cand = build_cands(I); byname = {v: k for k, v in cand.items()}
    fleet = [(byname[n], None, k) for n, k in json.loads(sys.argv[3])]
    kf = KFull(inst, K)
    for spec in sys.argv[4:]:
        an, tp = spec.split(':'); Tp = set(map(int, tp.split(',')))
        t0 = time.time(); print(an, Tp, kf.run(byname[an], Tp, fleet), round(time.time() - t0, 1), flush=True)
    kf.close()
