// 05. Clarke-Wright savings with time windows for OPEN routes + Hungarian assignment of chains to brigades.
//
// 1) Chains are built for a "virtual" vehicle: travel matrix = element-wise minimum over the transport
//    modes present in the fleet (optimistic), or the matrix of one specific mode ("per-mode savings").
//    A chain starts at time 0 at the nearest start node (of the relevant brigades).
//    Initially every order is its own chain; open-route saving of appending chain B (first order j)
//    after chain A (last order i):  s(i,j) = dStart(j) - g * d(i,j)   (the trip start->j disappears).
//    Pairs are processed by decreasing saving (optionally noisy); a merge is accepted if the
//    concatenated chain is time-feasible for the virtual vehicle and its skill set is covered by some
//    brigade. Since every merge also saves a brigade, negative savings are accepted too (unless the
//    variant posOnly is used).
// 2) Chains are assigned to real brigades by a min-cost assignment (Hungarian): cost = km of the chain
//    driven by that brigade from its own start if routeFeasible, else BIG*|chain| (= unassigned).
// 3) Orders of unassigned chains are reinserted with insertUnserved (bestInsert, may open brigades).
// 4) Second savings pass on REAL brigades: whole routes are concatenated while feasible.
// A grid of (virtual vehicle, g, posOnly) + noisy restarts is scanned; best raw constructions are
// polished with the shared localSearch. Every second noisy run adds a "squeaky wheel" savings bonus
// to orders that earlier runs left unserved.
#include "common.hpp"
#include "constr_util.hpp"

// Virtual vehicle: its matrices, start nodes and skill masks of the brigades it represents.
struct Virt { vector<double> T, D; vector<int> starts, masks; };

struct SParams { int virt; double g; bool posOnly; double noise; };

static Routes savingsBuild(const Instance& I, const Virt& W, const SParams& P, Rng& rng, const vector<double>& bonus) {
    int N = I.N, M = I.M;
    auto tt = [&](int a, int b) { return W.T[a * M + b]; };
    auto dd = [&](int a, int b) { return W.D[a * M + b]; };
    vector<double> dStart(N, cu::INF), tStart(N, cu::INF);
    for (int k = 0; k < N; k++) for (int s : W.starts) {
        dStart[k] = min(dStart[k], dd(s, I.node(k))); tStart[k] = min(tStart[k], tt(s, I.node(k)));
    }
    auto maskOk = [&](int m) { for (int x : W.masks) if ((m & x) == m) return true; return false; };
    // chains
    vector<vector<int>> ch(N); vector<int> cid(N), cmask(N);
    for (int k = 0; k < N; k++) { ch[k] = {k}; cid[k] = k; cmask[k] = 1 << I.ord[k].skill; }
    // chain feasibility for the virtual vehicle
    auto feasible = [&](const vector<int>& a, const vector<int>& b) {
        double t = 0; int prev = -1;
        for (int pass = 0; pass < 2; pass++) for (int k : (pass ? b : a)) {
            double arr = prev < 0 ? tStart[k] : t + tt(I.node(prev), I.node(k));
            double beg = max(arr, I.ord[k].a);
            if (beg > I.ord[k].b + cu::TOL) return false;
            t = beg + I.ord[k].svc; if (t > SHIFT + cu::TOL) return false;
            prev = k;
        }
        return true;
    };
    for (int k = 0; k < N; k++) if (!feasible(ch[k], {}) || !maskOk(cmask[k])) { ch[k].clear(); cid[k] = -1; }
    // savings list
    struct Sv { double s; int i, j; };
    vector<Sv> L; L.reserve(N * N);
    for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) if (i != j && cid[i] >= 0 && cid[j] >= 0) {
        double s = dStart[j] - P.g * dd(I.node(i), I.node(j)) + bonus[i] + bonus[j];
        if (P.posOnly && s <= 0) continue;
        if (P.noise > 0) s += P.noise * (rng.uni() * 2 - 1) * (fabs(s) + 1);
        L.push_back({s, i, j});
    }
    sort(L.begin(), L.end(), [](const Sv& x, const Sv& y) { return x.s > y.s; });
    for (auto& e : L) {
        int A = cid[e.i], B = cid[e.j];
        if (A < 0 || B < 0 || A == B) continue;
        if (ch[A].back() != e.i || ch[B].front() != e.j) continue;
        int m = cmask[A] | cmask[B];
        if (!maskOk(m) || !feasible(ch[A], ch[B])) continue;
        for (int k : ch[B]) { ch[A].push_back(k); cid[k] = A; }
        ch[B].clear(); cmask[A] = m;
    }
    vector<vector<int>> chains;
    for (auto& c : ch) if (!c.empty()) chains.push_back(c);
    // assignment chains -> brigades
    int C = chains.size(), n = max(C, I.V);
    const double BIG = 1e7;
    vector<vector<double>> a(n + 1, vector<double>(n + 1, 0));
    for (int c = 0; c < n; c++) for (int v = 0; v < n; v++) {
        if (c >= C) { a[c + 1][v + 1] = 0; continue; }
        double km;
        if (v < I.V && routeFeasible(I, v, chains[c], &km)) a[c + 1][v + 1] = km;
        else {
            double w = 0; for (int k : chains[c]) w += 1 + bonus[k] / 10;
            a[c + 1][v + 1] = BIG * w;
        }
    }
    auto rc = cu::hungarian(a);
    Routes R(I.V);
    for (int c = 0; c < C; c++) {
        int v = rc[c + 1] - 1;
        if (v < I.V && a[c + 1][v + 1] < BIG) R[v] = chains[c];
    }
    cu::repair(I, R);   // reinserts orders of unassigned chains
    // 4) real-vehicle savings: concatenate two whole routes (either order) on either brigade
    //    or on a free one, choosing the merge with the smallest km increase; repeat.
    while (true) {
        double bestInc = cu::INF; int ba = -1, bb = -1, bx = -1;
        for (int a = 0; a < I.V; a++) for (int b = 0; b < I.V; b++) {
            if (a == b || R[a].empty() || R[b].empty()) continue;
            vector<int> cat = R[a]; cat.insert(cat.end(), R[b].begin(), R[b].end());
            double old = routeKm(I, a, R[a]) + routeKm(I, b, R[b]);
            for (int x = 0; x < I.V; x++) {
                if (x != a && x != b && !R[x].empty()) continue;
                double km;
                if (routeFeasible(I, x, cat, &km) && km - old < bestInc) bestInc = km - old, ba = a, bb = b, bx = x;
            }
        }
        if (ba < 0) break;
        vector<int> cat = R[ba]; cat.insert(cat.end(), R[bb].begin(), R[bb].end());
        R[ba].clear(); R[bb].clear(); R[bx] = cat;
    }
    return R;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer T; Rng rng(seed); cu::Pool pool(6);
    // virtual vehicles: 0 = optimistic (min over present modes), then one per present mode
    vector<Virt> virts;
    vector<int> modes; for (auto& w : I.veh) if (find(modes.begin(), modes.end(), w.mode) == modes.end()) modes.push_back(w.mode);
    sort(modes.begin(), modes.end());
    {
        Virt W; W.T.assign(I.M * I.M, cu::INF); W.D.assign(I.M * I.M, cu::INF);
        for (int m : modes) for (int x = 0; x < I.M * I.M; x++) { W.T[x] = min(W.T[x], I.T[m][x]); W.D[x] = min(W.D[x], I.D[m][x]); }
        for (auto& w : I.veh) W.starts.push_back(w.start), W.masks.push_back(w.mask);
        virts.push_back(W);
    }
    for (int m : modes) {
        Virt W; W.T = I.T[m]; W.D = I.D[m];
        for (auto& w : I.veh) if (w.mode == m) W.starts.push_back(w.start), W.masks.push_back(w.mask);
        virts.push_back(W);
    }
    vector<SParams> grid;
    for (bool po : {false, true}) for (double g : {1.0, 0.7, 1.4}) for (int vi = 0; vi < (int)virts.size(); vi++)
        grid.push_back({vi, g, po, 0.0});
    double buildEnd = 0.5 * tl, deadline = 0.85 * tl;
    // Determinism: the number of constructions is fixed by tl (calibrated so that the largest instance
    // needs ~40% of tl); the clock is only a safety net on a loaded machine.
    const int maxBuilds = max(20, (int)(850 * tl));
    // "squeaky wheel": orders left unserved get a savings bonus (merged early) in later noisy runs
    vector<double> bonus(I.N, 0.0), zero(I.N, 0.0);
    auto run = [&](const SParams& P, const vector<double>& b) {
        Routes R = savingsBuild(I, virts[P.virt], P, rng, b);
        cu::bumpUnserved(I, R, bonus, 10.0);
        pool.add(I, R);
    };
    for (auto& P : grid) {
        if (T.sec() > buildEnd || pool.builds >= maxBuilds) break;
        run(P, zero);
    }
    for (int it = 0; T.sec() < buildEnd && pool.builds < maxBuilds; it++) {
        SParams P{rng.randint(virts.size()), 0.6 + 0.9 * rng.uni(), rng.randint(3) == 0, 0.05 + 0.3 * rng.uni()};
        run(P, it % 2 ? bonus : zero);
    }
    return cu::finish(I, pool, T, deadline);
}
int main(int c, char** v) { return runMain(c, v, solve, "05_savings+ls"); }
