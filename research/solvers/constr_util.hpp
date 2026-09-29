// Private helpers for the construction solvers s03..s06 (regret, Solomon I1, savings, sweep).
// NOTE: the Makefile only tracks common.hpp as a dependency; after editing this file remove bin/s03..s06.
//
//  * Sched / insCost  - O(1) time-window feasibility test of an insertion using forward departure
//                       times and backward "latest start" slacks (exact for waiting-allowed windows).
//  * reassign         - Hungarian re-assignment of whole routes to brigades (applied in Pool::add).
//  * Pool / finish    - keep the best raw constructions, polish them with the shared localSearch
//                       while time remains and return the best polished one.
//  * env NOPOLISH=1   - return the best RAW construction (after the construction's own
//                       insertUnserved repair) without localSearch; used to measure raw quality.
//  * env ELIM=1       - experimental: route elimination before pooling (off by default).
#pragma once
#include "common.hpp"
#include <cstdlib>
#include <map>

namespace cu {
constexpr double INF = numeric_limits<double>::infinity();
constexpr double TOL = 1e-7;   // stricter than the validator's 1e-6

// Schedule of route r of brigade v.
// dep[p]: departure time from position p (p = 0 is the start node, p = 1..n is customer r[p-1]).
// lat[p]: latest service start at position p (1..n) such that the suffix stays feasible.
struct Sched { vector<double> dep, lat; };

inline void build(const Instance& I, int v, const vector<int>& r, Sched& s) {
    int n = r.size();
    s.dep.assign(n + 1, 0.0); s.lat.assign(n + 2, INF);
    int prev = I.veh[v].start; double t = 0;
    for (int p = 1; p <= n; p++) {
        int k = r[p - 1], nd = I.node(k);
        double beg = max(t + I.t(v, prev, nd), I.ord[k].a);
        t = beg + I.ord[k].svc; s.dep[p] = t; prev = nd;
    }
    for (int p = n; p >= 1; p--) {
        int k = r[p - 1];
        double L = min(I.ord[k].b, SHIFT - I.ord[k].svc);
        if (p < n) L = min(L, s.lat[p + 1] - I.t(v, I.node(k), I.node(r[p])) - I.ord[k].svc);
        s.lat[p] = L;
    }
}

// Km delta of inserting order k before position pos (0..n) of route r; INF if infeasible.
inline double insCost(const Instance& I, int v, const vector<int>& r, const Sched& s, int pos, int k) {
    if (!I.can(v, k)) return INF;
    int prev = pos == 0 ? I.veh[v].start : I.node(r[pos - 1]), nk = I.node(k);
    double beg = max(s.dep[pos] + I.t(v, prev, nk), I.ord[k].a);
    if (beg > I.ord[k].b + TOL || beg + I.ord[k].svc > SHIFT + TOL) return INF;
    double dk = I.d(v, prev, nk);
    if (pos < (int)r.size()) {
        int nn = I.node(r[pos]);
        if (beg + I.ord[k].svc + I.t(v, nk, nn) > s.lat[pos + 1] + TOL) return INF;
        dk += I.d(v, nk, nn) - I.d(v, prev, nn);
    }
    return dk;
}
// Service start of k if inserted at pos (assumes feasibility was checked).
inline double insStart(const Instance& I, int v, const vector<int>& r, const Sched& s, int pos, int k) {
    int prev = pos == 0 ? I.veh[v].start : I.node(r[pos - 1]);
    return max(s.dep[pos] + I.t(v, prev, I.node(k)), I.ord[k].a);
}
// Cheapest feasible position of k in route r: returns km delta (INF if none), writes pos.
inline double bestPos(const Instance& I, int v, const vector<int>& r, const Sched& s, int k, int& pos) {
    double best = INF; pos = -1;
    if (!I.can(v, k)) return INF;
    for (int p = 0; p <= (int)r.size(); p++) {
        double c = insCost(I, v, r, s, p, k);
        if (c < best) best = c, pos = p;
    }
    return best;
}

// Make every route feasible (drop orders from the first violation on) and reinsert leftovers.
inline void repair(const Instance& I, Routes& R) {
    R.resize(I.V);
    vector<int> seen(I.N, 0);
    for (int v = 0; v < I.V; v++) {
        vector<int> keep;
        for (int k : R[v]) {
            if (seen[k]) continue;
            keep.push_back(k);
            if (!routeFeasible(I, v, keep)) { keep.pop_back(); continue; }
            seen[k] = 1;
        }
        R[v] = keep;
    }
    insertUnserved(I, R);
}

// Hungarian algorithm (e-maxx), square matrix a[1..n][1..n]; returns assignment row -> col (1-based).
inline vector<int> hungarian(const vector<vector<double>>& a) {
    int n = (int)a.size() - 1;
    vector<double> u(n + 1), v(n + 1); vector<int> p(n + 1), way(n + 1);
    for (int i = 1; i <= n; i++) {
        p[0] = i; int j0 = 0; vector<double> minv(n + 1, INF); vector<char> used(n + 1, 0);
        do {
            used[j0] = 1; int i0 = p[j0], j1 = 0; double delta = INF;
            for (int j = 1; j <= n; j++) if (!used[j]) {
                double cur = a[i0][j] - u[i0] - v[j];
                if (cur < minv[j]) minv[j] = cur, way[j] = j0;
                if (minv[j] < delta) delta = minv[j], j1 = j;
            }
            for (int j = 0; j <= n; j++) if (used[j]) u[p[j]] += delta, v[j] -= delta; else minv[j] -= delta;
            j0 = j1;
        } while (p[j0] != 0);
        do { int j1 = way[j0]; p[j0] = p[j1]; j0 = j1; } while (j0);
    }
    vector<int> rowCol(n + 1);
    for (int j = 1; j <= n; j++) rowCol[p[j]] = j;
    return rowCol;
}

// Re-assign whole routes to brigades by min-cost assignment (cost = km if the brigade can run the
// route from its own start, else BIG). Fixes "remote brigade drives to Moscow while a Moscow brigade
// drives to Kashira" type crossings that relocate/swap/2-opt* cannot repair.
inline void reassign(const Instance& I, Routes& R) {
    int V = I.V; const double BIG = 1e7;
    vector<vector<double>> a(V + 1, vector<double>(V + 1, 0));
    for (int r = 0; r < V; r++) for (int v = 0; v < V; v++) {
        if (R[r].empty()) { a[r + 1][v + 1] = 0; continue; }
        double km; a[r + 1][v + 1] = routeFeasible(I, v, R[r], &km) ? km : BIG * (1 + R[r].size());
    }
    auto rc = hungarian(a);
    Routes out(V);
    for (int r = 0; r < V; r++) {
        int v = rc[r + 1] - 1;
        if (R[r].empty()) continue;
        if (a[r + 1][v + 1] >= BIG) return;       // cannot happen (identity is feasible); keep R
        out[v] = R[r];
    }
    R = out;
}

// Route elimination (experimental, only with env ELIM=1): try to dissolve routes (smallest first) by
// inserting all their orders into the other non-empty routes; accept if every order fits.
inline void eliminateRoutes(const Instance& I, Routes& R) {
    bool again = true;
    while (again) {
        again = false;
        vector<int> idx; for (int v = 0; v < I.V; v++) if (!R[v].empty()) idx.push_back(v);
        sort(idx.begin(), idx.end(), [&](int x, int y) { return R[x].size() < R[y].size(); });
        for (int v : idx) {
            Routes T = R; vector<int> ks = T[v]; T[v].clear(); bool ok = true;
            for (int k : ks) {
                auto p = bestInsert(I, T, k, 1e12);
                if (p.v < 0 || p.delta > 1e11) { ok = false; break; }
                T[p.v].insert(T[p.v].begin() + p.pos, k);
            }
            if (ok) { R = T; again = true; break; }
        }
    }
}
// "Squeaky wheel": add `amount` to bonus[k] of every order not served by R.
inline void bumpUnserved(const Instance& I, const Routes& R, vector<double>& bonus, double amount) {
    vector<char> in(I.N, 0); for (auto& r : R) for (int k : r) in[k] = 1;
    for (int k = 0; k < I.N; k++) if (!in[k]) bonus[k] += amount;
}
inline bool envFlag(const char* n) { const char* e = getenv(n); return e && *e == '1'; }

inline bool noPolish() { const char* e = getenv("NOPOLISH"); return e && *e == '1'; }

// Pool of the best distinct raw constructions (distinct by rounded scalar).
struct Pool {
    size_t cap; std::map<long long, Routes> m;   // key = scalar * 1000
    int builds = 0;                              // number of constructions offered
    explicit Pool(size_t c = 6) : cap(c) {}
    double bestScalar() const { return m.empty() ? INF : m.begin()->first / 1000.0; }
    void add(const Instance& I, Routes R) {
        builds++;
        reassign(I, R);
        if (envFlag("ELIM")) eliminateRoutes(I, R);
        Score s = evaluate(I, R); if (!s.feasible) return;
        long long key = llround(s.scalar() * 1000);
        if (m.count(key)) return;
        if (m.size() >= cap && key >= prev(m.end())->first) return;
        m[key] = R;
        if (m.size() > cap) m.erase(prev(m.end()));
    }
};

// Polish pool candidates (best first) with localSearch until `deadline` (seconds on timer T).
inline Routes finish(const Instance& I, Pool& P, const Timer& T, double deadline) {
    if (P.m.empty()) { Routes R(I.V); insertUnserved(I, R); P.add(I, R); }
    if (noPolish()) return P.m.begin()->second;
    Routes best; double bs = INF, longest = 0; bool first = true; int polished = 0;
    for (auto& [key, cand] : P.m) {
        double rem = deadline - T.sec();
        if (!first && rem < 1.5 * longest + 0.002) break;   // do not start a polish we cannot finish
        Routes R = cand; double t0 = T.sec();
        localSearch(I, R, max(rem, 0.001));
        longest = max(longest, T.sec() - t0);
        Score s = evaluate(I, R);
        if (s.feasible && s.scalar() < bs) bs = s.scalar(), best = R;
        first = false; polished++;
    }
    if (envFlag("CU_DEBUG")) fprintf(stderr, "builds=%d polished=%d t=%.3f\n", P.builds, polished, T.sec());
    if (best.empty()) best = P.m.begin()->second;
    return best;
}

// Mean travel time between order nodes for a mode (speed proxy of a transport mode).
inline double meanTime(const Instance& I, int mode) {
    double s = 0; long long c = 0;
    for (int i = 0; i < I.N; i++) for (int j = 0; j < I.N; j++) if (i != j) { s += I.T[mode][I.node(i) * I.M + I.node(j)]; c++; }
    return c ? s / c : 0;
}
}  // namespace cu
