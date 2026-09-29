// 03. Parallel regret-k insertion (k = 2, 3) + noise-randomized restarts, then shared localSearch.
//
// All brigades are "available" from the start. For every unrouted order and every brigade we keep the
// cheapest feasible insertion cost (km delta; inserting into an EMPTY route additionally costs the
// opening penalty W_VEHICLE + a small "brigade quality" term, so the fleet stays small and fast/
// universal brigades are opened first). At each step the order with the largest regret
//     regret_k = sum_{j=2..k} (c_j - c_1)      (missing options count as BIG)
// is inserted at its best place. Orders that have exactly one existing route left get regret ~W_VEHICLE
// and are grabbed before that option disappears. Only costs of the modified route are recomputed.
// Variants: k in {2,3}, quality weight, emergencies-first flag, then random km-noise restarts; every
// second restart adds a "squeaky wheel" regret bonus to orders that earlier runs failed to serve.
#include "common.hpp"
#include "constr_util.hpp"

struct RParams { int K; double wq; bool priFirst; double noise; };

static Routes regretBuild(const Instance& I, const RParams& P, Rng& rng, const vector<double>& quality,
                          const vector<double>& bonus) {
    const double BIGR = 1e6;
    int N = I.N, V = I.V;
    Routes R(I.V); vector<cu::Sched> S(V);
    for (int v = 0; v < V; v++) cu::build(I, v, R[v], S[v]);
    vector<vector<double>> C(N, vector<double>(V, cu::INF));
    vector<vector<int>> Pos(N, vector<int>(V, -1));
    vector<char> done(N, 0);
    auto upd = [&](int k, int v) {
        int p; double c = cu::bestPos(I, v, R[v], S[v], k, p);
        if (c < cu::INF) {
            if (P.noise > 0) c *= 1 + P.noise * (rng.uni() * 2 - 1);
            if (R[v].empty()) c += W_VEHICLE + P.wq * quality[v];
        }
        C[k][v] = c; Pos[k][v] = p;
    };
    for (int k = 0; k < N; k++) for (int v = 0; v < V; v++) upd(k, v);
    vector<double> cs(V);
    while (true) {
        int bk = -1, bv = -1; double bestKey = -cu::INF, bestC1 = cu::INF;
        for (int k = 0; k < N; k++) {
            if (done[k]) continue;
            for (int v = 0; v < V; v++) cs[v] = C[k][v];
            int kk = min(P.K, V);
            partial_sort(cs.begin(), cs.begin() + kk, cs.end());
            if (cs[0] == cu::INF) continue;              // cannot be inserted anywhere
            double reg = 0;
            for (int j = 1; j < kk; j++) reg += (cs[j] == cu::INF ? BIGR : cs[j] - cs[0]);
            if (P.priFirst && I.ord[k].pri == 1) reg += 1e9;
            reg += bonus[k];
            if (reg > bestKey + 1e-9 || (fabs(reg - bestKey) <= 1e-9 && cs[0] < bestC1)) {
                bestKey = reg; bestC1 = cs[0]; bk = k;
            }
        }
        if (bk < 0) break;
        for (int v = 0; v < V; v++) if (C[bk][v] == bestC1) { bv = v; break; }
        R[bv].insert(R[bv].begin() + Pos[bk][bv], bk);
        done[bk] = 1;
        cu::build(I, bv, R[bv], S[bv]);
        for (int k = 0; k < N; k++) if (!done[k]) upd(k, bv);
    }
    cu::repair(I, R);   // safety + leftovers (normally nothing to do)
    return R;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer T; Rng rng(seed); cu::Pool pool(6);
    // brigade quality penalty: slow mode and missing skills cost "virtual km" on opening
    vector<double> mt(4); for (int m = 0; m < 4; m++) mt[m] = cu::meanTime(I, m);
    vector<double> quality(I.V);
    for (int v = 0; v < I.V; v++) {
        int miss = 0; for (int s = 0; s < 3; s++) if (!((I.veh[v].mask >> s) & 1)) miss++;
        quality[v] = mt[I.veh[v].mode] + 10.0 * miss;
    }
    double qmin = *min_element(quality.begin(), quality.end());
    for (auto& q : quality) q -= qmin;   // relative, so that a car brigade gets ~0
    const double wqs[] = {0.0, 0.3, 1.0};
    double buildEnd = 0.5 * tl, deadline = 0.85 * tl;
    // Determinism: the number of constructions is fixed by tl (calibrated so that the largest instance
    // needs ~40% of tl); the clock is only a safety net on a loaded machine.
    const int maxBuilds = max(20, (int)(1400 * tl));
    // "squeaky wheel" bonus: orders left unserved by earlier runs get a regret bonus in later runs
    vector<double> zero(I.N, 0.0), bonus(I.N, 0.0);
    auto run = [&](const RParams& P, const vector<double>& b) {
        Routes R = regretBuild(I, P, rng, quality, b);
        vector<char> in(I.N, 0); for (auto& r : R) for (int k : r) in[k] = 1;
        for (int k = 0; k < I.N; k++) if (!in[k]) bonus[k] += 2e4;
        pool.add(I, R);
    };
    // deterministic grid
    for (int K : {2, 3}) for (double wq : wqs) for (bool pf : {false, true}) {
        if (T.sec() > buildEnd || pool.builds >= maxBuilds) break;
        run({K, wq, pf, 0.0}, zero);
    }
    // noisy restarts (every second one uses the squeaky-wheel bonus)
    for (int it = 0; T.sec() < buildEnd && pool.builds < maxBuilds; it++) {
        RParams P{2 + rng.randint(2), wqs[rng.randint(3)], rng.randint(2) == 1, 0.05 + 0.25 * rng.uni()};
        run(P, it % 2 ? bonus : zero);
    }
    return cu::finish(I, pool, T, deadline);
}
int main(int c, char** v) { return runMain(c, v, solve, "03_regret_insertion+ls"); }
