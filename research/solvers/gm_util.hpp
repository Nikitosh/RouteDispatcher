// Private helpers for s07/s08/s09 (graph / matching / search solvers).
// Hopcroft–Karp, min-cost max-flow, Hungarian, path-cover lower bound, polish wrapper.
#pragma once
#include "common.hpp"
#include <cstdlib>
#include <unordered_set>

namespace gm {

constexpr double INF = 1e18;

// ---------- Hopcroft–Karp (left n, right m) ----------
struct HopcroftKarp {
    int n, m; vector<vector<int>> g; vector<int> ml, mr, dist;
    HopcroftKarp(int n, int m) : n(n), m(m), g(n), ml(n, -1), mr(m, -1), dist(n) {}
    void add(int u, int v) { g[u].push_back(v); }
    bool bfs() {
        queue<int> q; bool found = false;
        for (int u = 0; u < n; u++) { if (ml[u] < 0) { dist[u] = 0; q.push(u); } else dist[u] = -1; }
        while (!q.empty()) {
            int u = q.front(); q.pop();
            for (int v : g[u]) { int w = mr[v]; if (w < 0) found = true; else if (dist[w] < 0) { dist[w] = dist[u] + 1; q.push(w); } }
        }
        return found;
    }
    bool dfs(int u) {
        for (int v : g[u]) { int w = mr[v]; if (w < 0 || (dist[w] == dist[u] + 1 && dfs(w))) { ml[u] = v; mr[v] = u; return true; } }
        dist[u] = -1; return false;
    }
    int run() { int r = 0; while (bfs()) for (int u = 0; u < n; u++) if (ml[u] < 0 && dfs(u)) r++; return r; }
};

// ---------- min-cost max-flow (SPFA, double costs) ----------
struct MCMF {
    struct E { int to; int cap; double c; };
    int n; vector<E> e; vector<vector<int>> g;
    explicit MCMF(int n) : n(n), g(n) {}
    int add(int u, int v, int cap, double c) {
        g[u].push_back((int)e.size()); e.push_back({v, cap, c});
        g[v].push_back((int)e.size()); e.push_back({u, 0, -c});
        return (int)e.size() - 2;
    }
    // stopWhenNonNegative: stop augmenting once the shortest path cost is >= 0 (min-cost flow of any size)
    pair<int, double> run(int s, int t, bool stopWhenNonNegative = false) {
        int flow = 0; double cost = 0;
        vector<double> d(n); vector<int> pe(n), inq(n);
        while (true) {
            fill(d.begin(), d.end(), INF); fill(pe.begin(), pe.end(), -1);
            deque<int> q; d[s] = 0; q.push_back(s); inq[s] = 1;
            while (!q.empty()) {
                int u = q.front(); q.pop_front(); inq[u] = 0;
                for (int id : g[u]) { auto& x = e[id]; if (x.cap > 0 && d[u] + x.c < d[x.to] - 1e-9) { d[x.to] = d[u] + x.c; pe[x.to] = id; if (!inq[x.to]) { inq[x.to] = 1; q.push_back(x.to); } } }
            }
            if (d[t] >= INF / 2) break;
            if (stopWhenNonNegative && d[t] >= 0) break;
            int f = INT32_MAX;
            for (int v = t; v != s; v = e[pe[v] ^ 1].to) f = min(f, e[pe[v]].cap);
            for (int v = t; v != s; v = e[pe[v] ^ 1].to) { e[pe[v]].cap -= f; e[pe[v] ^ 1].cap += f; }
            flow += f; cost += f * d[t];
        }
        return {flow, cost};
    }
};

// ---------- Hungarian: square or rectangular (n rows <= m cols), minimize. returns row->col ----------
inline vector<int> hungarian(const vector<vector<double>>& a) {
    int n = (int)a.size(); if (!n) return {};
    int m = (int)a[0].size();
    // e-maxx implementation, 1-indexed, requires n <= m
    vector<double> u(n + 1), v(m + 1); vector<int> p(m + 1), way(m + 1);
    for (int i = 1; i <= n; i++) {
        p[0] = i; int j0 = 0; vector<double> minv(m + 1, INF); vector<char> used(m + 1, 0);
        do {
            used[j0] = 1; int i0 = p[j0], j1 = 0; double delta = INF;
            for (int j = 1; j <= m; j++) if (!used[j]) {
                double cur = a[i0 - 1][j - 1] - u[i0] - v[j];
                if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
                if (minv[j] < delta) { delta = minv[j]; j1 = j; }
            }
            for (int j = 0; j <= m; j++) { if (used[j]) { u[p[j]] += delta; v[j] -= delta; } else minv[j] -= delta; }
            j0 = j1;
        } while (p[j0] != 0);
        do { int j1 = way[j0]; p[j0] = p[j1]; j0 = j1; } while (j0);
    }
    vector<int> ans(n, -1);
    for (int j = 1; j <= m; j++) if (p[j]) ans[p[j] - 1] = j - 1;
    return ans;
}
// General rectangular wrapper: returns row->col (or -1), any shape.
inline vector<int> assignRect(const vector<vector<double>>& a) {
    int n = (int)a.size(); if (!n) return {};
    int m = (int)a[0].size();
    if (n <= m) return hungarian(a);
    vector<vector<double>> t(m, vector<double>(n));
    for (int i = 0; i < n; i++) for (int j = 0; j < m; j++) t[j][i] = a[i][j];
    auto c = hungarian(t); vector<int> ans(n, -1);
    for (int j = 0; j < m; j++) if (c[j] >= 0) ans[c[j]] = j;
    return ans;
}

// ---------- path-cover lower bound on number of brigades (relaxation) ----------
// Edge i->j if some brigade able to do both can start j after i: e_i + svc_i + min_v t_v(i,j) <= b_j,
// e_i = earliest possible start of i over able brigades. Any feasible full solution's successor relation
// is a matching here, so N - maxMatching <= #routes of any solution serving all orders.
inline int pathCoverLB(const Instance& I) {
    int N = I.N; vector<double> e(N, INF);
    for (int k = 0; k < N; k++) for (int v = 0; v < I.V; v++) if (I.can(v, k)) e[k] = min(e[k], max(I.ord[k].a, I.t(v, I.veh[v].start, I.node(k))));
    HopcroftKarp hk(N, N);
    for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) if (i != j) {
        double tm = INF;
        for (int v = 0; v < I.V; v++) if (I.can(v, i) && I.can(v, j)) tm = min(tm, I.t(v, I.node(i), I.node(j)));
        if (e[i] + I.ord[i].svc + tm <= I.ord[j].b + EPS) hk.add(i, j);
    }
    return N - hk.run();
}

// Interval (bin-packing style) lower bound. For an interval [A,B], orders with a>=A, b<=B: in a route,
// consecutive starts inside [A,B] are spaced by >= svc_i + minOut_i, so with m brigades at most m "last"
// orders are free and the other n-m sizes must fit in m*(B-A). Smallest such m is a valid bound.
inline int intervalLB(const Instance& I) {
    int N = I.N; vector<double> as, bs; int best = 0;
    for (auto& o : I.ord) { as.push_back(o.a); bs.push_back(o.b); }
    sort(as.begin(), as.end()); as.erase(unique(as.begin(), as.end()), as.end());
    sort(bs.begin(), bs.end()); bs.erase(unique(bs.begin(), bs.end()), bs.end());
    for (double A : as) for (double B : bs) if (B >= A) {
        vector<int> S; for (int k = 0; k < N; k++) if (I.ord[k].a >= A && I.ord[k].b <= B) S.push_back(k);
        int n = (int)S.size(); if (n <= best) continue;
        vector<double> sz;
        for (int i : S) {
            double mo = INF;
            for (int j : S) if (j != i) for (int v = 0; v < I.V; v++) if (I.can(v, i) && I.can(v, j)) mo = min(mo, I.t(v, I.node(i), I.node(j)));
            sz.push_back(I.ord[i].svc + (mo < INF ? mo : 0));
        }
        sort(sz.begin(), sz.end());
        for (int m = 1; m <= n; m++) {
            double s = 0; for (int q = 0; q < n - m; q++) s += sz[q];
            if (s <= m * (B - A) + EPS) { best = max(best, m); break; }
        }
    }
    return best;
}

inline bool noPolish() { const char* s = getenv("NOPOLISH"); return s && s[0] && s[0] != '0'; }

// Types of interchangeable brigades (same start, mode, mask).
inline vector<int> brigadeTypes(const Instance& I) {
    vector<int> ty(I.V);
    for (int v = 0; v < I.V; v++) { ty[v] = v; for (int u = 0; u < v; u++) if (I.veh[u].start == I.veh[v].start && I.veh[u].mode == I.veh[v].mode && I.veh[u].mask == I.veh[v].mask) { ty[v] = ty[u]; break; } }
    return ty;
}

// Try to empty whole routes (smallest first): remove a route, reinsert its orders without opening
// new brigades; accept if everything fits. Cheap fleet-reduction step.
inline bool eliminateRoutes(const Instance& I, Routes& R, const Timer& tm, double until) {
    bool any = false, again = true;
    while (again && tm.sec() < until) {
        again = false;
        vector<int> idx; for (int v = 0; v < I.V; v++) if (!R[v].empty()) idx.push_back(v);
        sort(idx.begin(), idx.end(), [&](int x, int y) { return R[x].size() < R[y].size(); });
        for (int v : idx) {
            if (tm.sec() >= until) break;
            Routes R2 = R; vector<int> orders = R2[v]; R2[v].clear();
            bool ok = true;
            // most constrained first
            sort(orders.begin(), orders.end(), [&](int x, int y) { return I.ord[x].b - I.ord[x].a < I.ord[y].b - I.ord[y].a; });
            for (int k : orders) {
                InsertPos best;
                for (int u = 0; u < I.V; u++) if (u != v && !R2[u].empty() && I.can(u, k))
                    for (int p = 0; p <= (int)R2[u].size(); p++) { double dl = insertDelta(I, R2, u, p, k); if (dl < best.delta) best = {u, p, dl}; }
                if (best.v < 0) { ok = false; break; }
                R2[best.v].insert(R2[best.v].begin() + best.pos, k);
            }
            if (ok) { R = R2; any = again = true; break; }
        }
    }
    return any;
}

// Depth-1 ejection repair for unserved orders: put unserved k into route v in place of some order j,
// then reinsert j anywhere (or leave j out if its penalty is lower than k's).
inline bool ejectionRepair(const Instance& I, Routes& R, const Timer& tm, double until) {
    bool any = false, again = true;
    while (again && tm.sec() < until) {
        again = false;
        vector<int> in(I.N, 0); for (auto& r : R) for (int k : r) in[k] = 1;
        vector<int> un; for (int k = 0; k < I.N; k++) if (!in[k]) un.push_back(k);
        sort(un.begin(), un.end(), [&](int x, int y) { return I.penalty(x) > I.penalty(y); });
        for (int k : un) {
            if (tm.sec() >= until) break;
            double bestGain = 0; Routes bestR;
            for (int v = 0; v < I.V; v++) if (I.can(v, k)) for (int i = 0; i < (int)R[v].size(); i++) {
                int j = R[v][i]; Routes R2 = R; R2[v].erase(R2[v].begin() + i);
                InsertPos p; for (int q = 0; q <= (int)R2[v].size(); q++) { double dl = insertDelta(I, R2, v, q, k, 0); if (dl < p.delta) p = {v, q, dl}; }
                if (p.v < 0) continue;
                R2[v].insert(R2[v].begin() + p.pos, k);
                auto pj = bestInsert(I, R2, j);
                double gain;
                if (pj.v >= 0) { R2[pj.v].insert(R2[pj.v].begin() + pj.pos, j); gain = I.penalty(k) * W_UNSERVED - pj.delta - p.delta; }
                else gain = (I.penalty(k) - I.penalty(j)) * W_UNSERVED - p.delta;
                if (gain > bestGain + 1e-6) { bestGain = gain; bestR = R2; }
            }
            if (bestGain > 0) { R = bestR; any = again = true; break; }
        }
    }
    return any;
}

// optional extra polish (env GM_EXTRA=1): ejection repair + route elimination before the shared LS
inline bool extraOn() { const char* s = getenv("GM_EXTRA"); return s && s[0] && s[0] != '0'; }
inline void polishRoutes(const Instance& I, Routes& R, const Timer& tm, double until) {
    if (extraOn()) { ejectionRepair(I, R, tm, until); eliminateRoutes(I, R, tm, until); }
    double rem = until - tm.sec(); if (rem > 0) localSearch(I, R, rem);
    if (extraOn()) { if (ejectionRepair(I, R, tm, until) | eliminateRoutes(I, R, tm, until)) { rem = until - tm.sec(); if (rem > 0) localSearch(I, R, rem); } }
}

}  // namespace gm
