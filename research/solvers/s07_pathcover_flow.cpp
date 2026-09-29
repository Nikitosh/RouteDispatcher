// 07. Fleet minimisation as minimum path cover in a DAG.
// Tentative start time T_i per order; edge i->j if T_i + svc_i + tref(i,j) <= T_j (tref = optimistic
// travel over a chosen subset of fleet modes, both skills doable by one brigade). Min-cost max-flow on
// the bipartite split graph (cost = distance) gives few chains with low deadhead km; chains are split
// where no real brigade can run them and assigned to brigades by the Hungarian algorithm; leftovers go
// through bestInsert. Iterated with different start-time rules / travel models / perturbations; each
// candidate is polished by the shared localSearch, best scalar() kept.
#include "gm_util.hpp"
using namespace gm;

struct PC {
    const Instance& I; int N; Rng rng;
    vector<int> modes;                                   // modes present in the fleet
    vector<vector<char>> compat;                         // some brigade can do both i and j
    vector<vector<vector<double>>> tm, dm;               // [q][i][j]: q-th fastest present mode (among brigades able to do both)
    vector<double> e;                                    // earliest possible start
    PC(const Instance& I, uint64_t seed) : I(I), N(I.N), rng(seed) {
        for (auto& v : I.veh) if (find(modes.begin(), modes.end(), v.mode) == modes.end()) modes.push_back(v.mode);
        compat.assign(N, vector<char>(N, 0));
        int Q = (int)modes.size(); tm.assign(Q, vector<vector<double>>(N, vector<double>(N, INF))); dm = tm;
        for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) if (i != j) {
            vector<pair<double, double>> ts;
            for (int m : modes) {
                bool ok = false; for (int v = 0; v < I.V; v++) if (I.veh[v].mode == m && I.can(v, i) && I.can(v, j)) ok = true;
                if (ok) ts.push_back({I.T[m][I.node(i) * I.M + I.node(j)], I.D[m][I.node(i) * I.M + I.node(j)]});
            }
            if (ts.empty()) continue;
            compat[i][j] = 1; sort(ts.begin(), ts.end());
            for (int q = 0; q < Q; q++) { auto p = ts[min(q, (int)ts.size() - 1)]; tm[q][i][j] = p.first; dm[q][i][j] = p.second; }
        }
        e.assign(N, INF);
        for (int k = 0; k < N; k++) for (int v = 0; v < I.V; v++) if (I.can(v, k)) e[k] = min(e[k], max(I.ord[k].a, I.t(v, I.veh[v].start, I.node(k))));
    }

    // chains via min-cost max-flow on split graph
    vector<vector<int>> chains(const vector<double>& T, int q, double noise) {
        int s = 2 * N, t = 2 * N + 1; MCMF f(2 * N + 2);
        const double BIG = 1e5;
        for (int i = 0; i < N; i++) { f.add(s, i, 1, 0); f.add(N + i, t, 1, 0); }
        for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) if (compat[i][j]) {
            if (T[i] + I.ord[i].svc + tm[q][i][j] <= T[j] + EPS) {
                double c = dm[q][i][j] + (noise > 0 ? noise * rng.uni() : 0);
                f.add(i, N + j, 1, c - BIG);
            }
        }
        f.run(s, t);
        vector<int> nxt(N, -1), hasPrev(N, 0);
        for (int i = 0; i < N; i++) for (int id : f.g[i]) { auto& x = f.e[id]; if (x.to >= N && x.to < 2 * N && x.cap == 0 && (id % 2 == 0)) { nxt[i] = x.to - N; hasPrev[x.to - N] = 1; } }
        vector<vector<int>> ch;
        for (int i = 0; i < N; i++) if (!hasPrev[i]) { vector<int> c; for (int k = i; k >= 0; k = nxt[k]) c.push_back(k); ch.push_back(c); }
        return ch;
    }

    int feasPrefix(int v, const vector<int>& r) {        // longest feasible prefix for brigade v
        double t = 0; int prev = I.veh[v].start;
        for (int i = 0; i < (int)r.size(); i++) {
            int k = r[i]; if (!I.can(v, k)) return i;
            int n = I.node(k); double beg = max(t + I.t(v, prev, n), I.ord[k].a);
            if (beg > I.ord[k].b + EPS || beg + I.ord[k].svc > SHIFT + EPS) return i;
            t = beg + I.ord[k].svc; prev = n;
        }
        return (int)r.size();
    }

    // split chains no brigade can run, then assign chains to brigades (Hungarian), leftovers -> bestInsert
    Routes assign(vector<vector<int>> ch) {
        vector<vector<int>> ok; vector<int> leftovers;
        while (!ch.empty()) {
            auto c = ch.back(); ch.pop_back();
            int bestP = 0;
            for (int v = 0; v < I.V; v++) { bestP = max(bestP, feasPrefix(v, c)); if (bestP == (int)c.size()) break; }
            if (bestP == (int)c.size()) { ok.push_back(c); continue; }
            if (bestP == 0) { leftovers.push_back(c[0]); if (c.size() > 1) ch.push_back(vector<int>(c.begin() + 1, c.end())); continue; }
            ok.push_back(vector<int>(c.begin(), c.begin() + bestP));
            ch.push_back(vector<int>(c.begin() + bestP, c.end()));
        }
        int C = (int)ok.size();
        vector<vector<double>> cost(C, vector<double>(I.V, 0));
        for (int c = 0; c < C; c++) {
            double pen = 0; for (int k : ok[c]) pen += I.penalty(k);
            for (int v = 0; v < I.V; v++) { double km; if (routeFeasible(I, v, ok[c], &km)) cost[c][v] = km - 1e6 * pen; }
        }
        auto as = assignRect(cost);
        Routes R(I.V);
        for (int c = 0; c < C; c++) {
            if (as[c] >= 0 && cost[c][as[c]] < 0) R[as[c]] = ok[c];
            else for (int k : ok[c]) leftovers.push_back(k);
        }
        insertUnserved(I, R);
        return R;
    }
};

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; PC pc(I, seed); int N = I.N;
    fprintf(stderr, "LB pathcover=%d interval=%d\n", pathCoverLB(I), intervalLB(I));
    bool polish = !noPolish();
    Routes best(I.V), bestRaw(I.V); double bestS = INF, bestRawS = INF;
    int Q = (int)pc.modes.size(), it = 0;
    vector<double> curT(N);
    for (int k = 0; k < N; k++) curT[k] = pc.e[k];
    int lastImprove = 0; long long nch = 0, nit = 0; int minChains = 1 << 30;
    while (tm.sec() < tl * 0.9) {
        vector<double> T(N);
        int rule = it < 4 ? it : (bestS < INF && pc.rng.uni() < 0.6 ? 3 : pc.rng.randint(3));
        int q = it < 4 ? 0 : (pc.rng.uni() < 0.6 ? 0 : pc.rng.randint(min(Q, 2)));
        for (int k = 0; k < N; k++) {
            double lo = pc.e[k], hi = max(lo, I.ord[k].b), u = pc.rng.uni();
            if (rule == 0) T[k] = lo;
            else if (rule == 1) T[k] = lo + (hi - lo) * u;
            else if (rule == 2) T[k] = lo + (hi - lo) * 0.5 * u;         // early-biased
            else {                                                         // perturb times of the best solution
                double sig = 5 + 30 * pc.rng.uni();
                T[k] = min(hi, max(lo, curT[k] + sig * (pc.rng.uni() * 2 - 1)));
            }
        }
        auto ch = pc.chains(T, q, it < 4 ? 0 : 3.0);
        nch += ch.size(); nit++; minChains = min(minChains, (int)ch.size());
        Routes R = pc.assign(ch);
        double rs = evaluate(I, R).scalar();
        if (rs < bestRawS) { bestRawS = rs; bestRaw = R; }
        if (polish) {
            double rem = tl * 0.95 - tm.sec(); if (rem <= 0) break;
            polishRoutes(I, R, tm, tl * 0.95);
        }
        double sc = evaluate(I, R).scalar();
        if (sc < bestS) {
            bestS = sc; best = R; lastImprove = it;
            for (int v = 0; v < I.V; v++) { auto st = routeStarts(I, v, R[v]); for (size_t p = 0; p < R[v].size(); p++) curT[R[v][p]] = st[p]; }
        }
        it++;
    }
    fprintf(stderr, "s07 iters=%d avgChains=%.2f minChains=%d raw=%.1f final=%.1f lastImprove=%d\n", it, nit ? (double)nch / nit : 0., minChains, bestRawS, bestS, lastImprove);
    return polish ? best : bestRaw;
}
int main(int c, char** v) { return runMain(c, v, solve, "07_pathcover_flow+ls"); }
