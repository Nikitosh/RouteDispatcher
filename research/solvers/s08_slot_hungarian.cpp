// 08. Time-layered assignment. Slots (2-hour windows) are processed chronologically. In each round a
// cost matrix brigades x pending orders of the current slot (+ all-day emergencies, eligible everywhere)
// is solved by the Hungarian algorithm: cost = km from the brigade's current position + wait/idle time
// weight (+ W_VEHICLE for an unused brigade), infeasible pairs excluded. Rounds first use only already
// opened brigades; when nothing fits, the cheapest opening(s) of new brigades are committed and the loop
// continues. Randomised weights / tie-breaks over many runs; best candidates polished by localSearch.
#include "gm_util.hpp"
using namespace gm;

struct Params { double wKm, wWait, wEm, wUrg, noise; int openPer; int emFrom; };

static Routes construct(const Instance& I, const Params& P, Rng& rng) {
    int N = I.N, V = I.V;
    const double SLOT = 120; int NS = 6;
    vector<int> slot(N); vector<char> em(N);
    for (int k = 0; k < N; k++) {
        em[k] = I.ord[k].b - I.ord[k].a > 2 * SLOT;
        slot[k] = min(NS - 1, (int)floor(I.ord[k].a / SLOT + 1e-9));
    }
    vector<int> pos(V), used(V, 0); vector<double> tf(V, 0);
    for (int v = 0; v < V; v++) pos[v] = I.veh[v].start;
    vector<char> done(N, 0); Routes R(V);
    vector<double> noiseK(N); for (int k = 0; k < N; k++) noiseK[k] = P.noise * rng.uni();
    const double BAD = 1e12;
    auto pairCost = [&](int v, int k, double& beg) {
        if (!I.can(v, k)) return BAD;
        int n = I.node(k); beg = max(I.ord[k].a, tf[v] + I.t(v, pos[v], n));
        if (beg > I.ord[k].b + EPS || beg + I.ord[k].svc > SHIFT + EPS) return BAD;
        double c = P.wKm * I.d(v, pos[v], n) + P.wWait * (beg - tf[v]) + noiseK[k];
        if (em[k]) c += P.wEm;
        c -= P.wUrg * max(0.0, 60 - (I.ord[k].b - beg));   // about to expire -> prefer now
        if (!used[v]) c += W_VEHICLE;
        return c;
    };
    for (int s = 0; s < NS; s++) {
        while (true) {
            vector<int> pend;
            for (int k = 0; k < N; k++) if (!done[k] && (slot[k] == s || (em[k] && (s >= P.emFrom || I.ord[k].b < (s + 1) * SLOT + 60)))) pend.push_back(k);
            if (pend.empty()) break;
            // phase A: opened brigades only
            vector<int> ub; for (int v = 0; v < V; v++) if (used[v]) ub.push_back(v);
            bool any = false;
            if (!ub.empty()) {
                vector<vector<double>> C(ub.size(), vector<double>(pend.size()));
                for (size_t i = 0; i < ub.size(); i++) for (size_t j = 0; j < pend.size(); j++) { double b; C[i][j] = pairCost(ub[i], pend[j], b); }
                auto as = assignRect(C);
                for (size_t i = 0; i < ub.size(); i++) if (as[i] >= 0 && C[i][as[i]] < BAD / 2) {
                    int v = ub[i], k = pend[as[i]]; double b; pairCost(v, k, b);
                    R[v].push_back(k); done[k] = 1; tf[v] = b + I.ord[k].svc; pos[v] = I.node(k); any = true;
                }
            }
            if (any) continue;
            // phase B: open new brigades for slot orders (emergencies only if about to expire)
            vector<int> need;
            for (int k : pend) if (!em[k] || I.ord[k].b < (s + 1) * SLOT + 60) need.push_back(k);
            vector<int> fb; for (int v = 0; v < V; v++) if (!used[v]) fb.push_back(v);
            if (need.empty() || fb.empty()) break;
            vector<vector<double>> C(fb.size(), vector<double>(need.size()));
            for (size_t i = 0; i < fb.size(); i++) for (size_t j = 0; j < need.size(); j++) { double b; C[i][j] = pairCost(fb[i], need[j], b); }
            auto as = assignRect(C);
            vector<pair<double, int>> cand;
            for (size_t i = 0; i < fb.size(); i++) if (as[i] >= 0 && C[i][as[i]] < BAD / 2) cand.push_back({C[i][as[i]], (int)i});
            if (cand.empty()) break;
            sort(cand.begin(), cand.end());
            for (int q = 0; q < (int)cand.size() && q < P.openPer; q++) {
                int i = cand[q].second, v = fb[i], k = need[as[i]]; double b; pairCost(v, k, b);
                R[v].push_back(k); done[k] = 1; tf[v] = b + I.ord[k].svc; pos[v] = I.node(k); used[v] = 1;
            }
        }
    }
    insertUnserved(I, R);
    return R;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; Rng rng(seed); bool polish = !noPolish();
    vector<pair<double, Routes>> pool;       // raw candidates
    double bestRawS = INF; Routes bestRaw(I.V);
    int runs = 0;
    double buildUntil = polish ? tl * 0.35 : tl * 0.9;
    while (tm.sec() < buildUntil) {
        Params P;
        if (runs == 0) P = {1, 0.2, 0, 0, 0, 1, 0};
        else P = {1, rng.uni() * 1.0, (rng.uni() * 2 - 1) * 30, rng.uni() * 2, rng.uni() * 10, rng.uni() < 0.7 ? 1 : 1 + rng.randint(3), rng.randint(4)};
        Routes R = construct(I, P, rng); runs++;
        double sc = evaluate(I, R).scalar();
        if (sc < bestRawS) { bestRawS = sc; bestRaw = R; }
        bool dup = false; for (auto& p : pool) if (fabs(p.first - sc) < 1e-6) { dup = true; break; }
        if (!dup) { pool.push_back({sc, R}); sort(pool.begin(), pool.end(), [](auto& x, auto& y) { return x.first < y.first; }); if (pool.size() > 40) pool.pop_back(); }
    }
    if (!polish) { fprintf(stderr, "s08 runs=%d raw=%.1f\n", runs, bestRawS); return bestRaw; }
    Routes best = bestRaw; double bestS = bestRawS; int np = 0;
    for (auto& [s0, R] : pool) {
        double rem = tl * 0.95 - tm.sec(); if (rem <= 0) break;
        polishRoutes(I, R, tm, tl * 0.95); np++;
        double sc = evaluate(I, R).scalar();
        if (sc < bestS) { bestS = sc; best = R; }
    }
    fprintf(stderr, "s08 runs=%d polished=%d raw=%.1f final=%.1f\n", runs, np, bestRawS, bestS);
    return best;
}
int main(int c, char** v) { return runMain(c, v, solve, "08_slot_hungarian+ls"); }
