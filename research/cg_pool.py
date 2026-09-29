"""Exact solve over a COMPLETE enumerated route pool: LP + 3-subset-row cuts separated on the pool,
reduced-cost fixing, then MIP (HiGHS) with the cuts.  Every route of any solution with value <= UB is in the
pool, hence min(UB, LP_with_cuts) is a valid lower bound and a MIP optimum is the proven optimum."""
import time, itertools
import numpy as np
import scipy.sparse as sp
import highspy
from collections import defaultdict

INF = highspy.kHighsInf
PEN = {1: 100, 2: 50, 3: 20}


class Pool:
    def __init__(self, I, types, routes, stage, Pbest, K, log=print):
        # routes: list of (t, km, tuple)
        self.I, self.types, self.stage, self.Pbest, self.K, self.log = I, types, stage, Pbest, K, log
        self.routes = [r for r in routes if len(set(r[2])) == len(r[2])]
        self.N = I['N']; self.T = len(types)
        self.pk = [PEN[o['pri']] for o in I['ords']]
        self.cuts = []  # list of frozenset triples
        self.cost = np.array([1.0 if stage == 2 else r[1] for r in self.routes])
        self.colsets = [set(r[2]) for r in self.routes]
        # order -> column index array
        oc = defaultdict(list)
        for j, r in enumerate(self.routes):
            for k in r[2]: oc[k].append(j)
        self.oc = {k: np.array(v, dtype=np.int64) for k, v in oc.items()}

    def build(self, active, integer=False, tl=60):
        """active: boolean mask over routes"""
        N, T = self.N, self.T
        h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1)
        if integer: h.setOptionValue('time_limit', float(tl)); h.setOptionValue('mip_rel_gap', 0.0)
        nrow = N + T + 2 + len(self.cuts)
        lo = [1.0] * N + [-INF] * (T + 2) + [-INF] * len(self.cuts)
        up = [1.0] * N + [float(t['count']) for t in self.types] + [float(self.K) if self.stage == 3 else INF, float(self.Pbest)] + [1.0] * len(self.cuts)
        h.addRows(nrow, np.array(lo), np.array(up), 0, np.array([0], dtype=np.int32), np.array([0], dtype=np.int32), np.array([0.0]))
        # y columns
        yub = 1.0 if self.Pbest > 0 else 0.0
        starts, idx, val = [], [], []
        for k in range(N):
            starts.append(len(idx)); idx += [k, N + T + 1]; val += [1.0, float(self.pk[k])]
        h.addCols(N, np.zeros(N), np.zeros(N), np.full(N, yub), len(idx), np.array(starts, dtype=np.int32), np.array(idx, dtype=np.int32), np.array(val))
        cols = np.nonzero(active)[0]; self.cur = cols
        A = self.matrix()[:, cols].tocsc()
        A.sort_indices()
        h.addCols(len(cols), self.cost[cols], np.zeros(len(cols)), np.full(len(cols), INF if not integer else 1.0), A.nnz,
                  A.indptr[:-1].astype(np.int32), A.indices.astype(np.int32), A.data.astype(np.float64))
        if integer:
            nc = N + len(cols)
            h.changeColsIntegrality(nc, np.arange(nc, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * nc))
        return h

    def matrix(self):
        """full constraint matrix over all routes (rows: orders, types, K, pen, cuts)"""
        N, T, R = self.N, self.T, len(self.routes)
        if not hasattr(self, '_base'):
            rows, cols = [], []
            for j, (t, km, r) in enumerate(self.routes):
                for k in r: rows.append(k); cols.append(j)
                rows += [N + t, N + T]; cols += [j, j]
            self._base = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(N + T + 2, R))
            self._cutrows = []
        while len(self._cutrows) < len(self.cuts):
            S = list(self.cuts[len(self._cutrows)])
            cnt = np.bincount(np.concatenate([self.oc.get(k, np.array([], dtype=np.int64)) for k in S]), minlength=R)
            jj = np.nonzero(cnt >= 2)[0]
            self._cutrows.append(sp.csr_matrix((np.ones(len(jj)), (np.zeros(len(jj), dtype=np.int64), jj)), shape=(1, R)))
        return sp.vstack([self._base] + self._cutrows, format='csc') if self._cutrows else self._base.tocsc()

    def separate(self, xs, maxcuts=60):
        """3-SRC separation over support columns xs: list of (colindex, value)"""
        acc = defaultdict(float)
        for j, v in xs:
            r = sorted(self.colsets[j]); seen = set()
            for a, b in itertools.combinations(r, 2):
                for c in range(self.N):
                    if c == a or c == b: continue
                    key = tuple(sorted((a, b, c)))
                    if key in seen: continue
                    seen.add(key); acc[key] += v
        have = set(self.cuts)
        viol = sorted(((v, k) for k, v in acc.items() if v > 1 + 1e-4 and frozenset(k) not in have), reverse=True)
        out = []; used = defaultdict(int)
        for v, k in viol:
            if len(out) >= maxcuts: break
            if any(used[x] >= 6 for x in k): continue  # diversify
            out.append(frozenset(k))
            for x in k: used[x] += 1
        return out

    def run(self, UB, time_budget=600, mip_tl=300, cut_rounds=40):
        t0 = time.time(); active = np.ones(len(self.routes), dtype=bool)
        lb = -1e18; N, T = self.N, self.T; hist = []
        for rnd in range(cut_rounds + 1):
            h = self.build(active); h.run()
            st = h.getModelStatus()
            if st == highspy.HighsModelStatus.kInfeasible:
                self.log('  pool LP infeasible -> no solution better than/equal UB in pool'); return dict(lb=UB, status='infeasible')
            sol = h.getSolution(); z = h.getInfo().objective_function_value
            lb = max(lb, z)
            x = np.array(sol.col_value[N:]); xs = [(self.cur[i], x[i]) for i in np.nonzero(x > 1e-6)[0]]
            # reduced cost fixing
            rc = np.array(sol.col_dual[N:])
            keep = rc <= UB - z + 1e-6
            nact = int(active.sum()); active[self.cur[~keep]] = False
            frac = sum(1 for _, v in xs if 1e-6 < v < 1 - 1e-6)
            self.log(f'  pool rnd{rnd} LP={z:.4f} cuts={len(self.cuts)} active {nact}->{int(active.sum())} frac={frac}')
            if z >= UB - 1e-6: break
            if frac == 0: break
            if rnd == cut_rounds or time.time() - t0 > time_budget * 0.5: break
            hist.append(z)
            if len(hist) >= 4 and hist[-1] - hist[-4] < 1e-3: break
            new = self.separate(xs)
            if not new: break
            self.cuts += new
        res = dict(lb=min(UB, lb), lp=lb, cuts=len(self.cuts), active=int(active.sum()))
        if lb >= UB - 1e-6:
            res['status'] = 'lp_closed'; return res
        tl = max(30, min(mip_tl, time_budget - (time.time() - t0)))
        import os, pickle
        if os.environ.get('CG_DUMP'):
            pickle.dump(dict(routes=[self.routes[j] for j in np.nonzero(active)[0]], cuts=self.cuts, UB=UB, K=self.K, Pbest=self.Pbest,
                             types=self.types, N=N, pk=self.pk, stage=self.stage, lp=lb), open(os.environ['CG_DUMP'], 'wb'))
        h = self.build(active, integer=True, tl=tl); h.setOptionValue('objective_bound', float(UB) + 1e-6); h.run()
        st = str(h.getModelStatus()); info = h.getInfo()
        res['mip_status'] = st
        try: db = info.mip_dual_bound
        except Exception: db = None
        if db is not None and db > -1e20: res['lb'] = max(res['lb'], min(UB, db))
        if 'Infeasible' in st:
            res['lb'] = UB; res['status'] = 'infeasible'; return res
        if info.primal_solution_status == 2:
            x = np.array(h.getSolution().col_value[N:]); obj = info.objective_function_value
            res['obj'] = obj; res['chosen'] = [self.routes[self.cur[i]] for i in np.nonzero(x > 0.5)[0]]
            if 'Optimal' in st: res['status'] = 'optimal'; res['lb'] = obj
            else: res['status'] = 'feasible'
        else:
            res['status'] = 'timeout'
        return res
