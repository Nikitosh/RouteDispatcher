// 06. Sweep heuristic (Gillett & Miller style) for the heterogeneous open VRPTW.
//
// The instance has no coordinates, so 2-D coordinates are recovered from the (symmetrized) car distance
// matrix by classical multidimensional scaling (top-2 eigenvectors by power iteration).
// Every order is attached to its nearest start node ("group"): on Yugo-vostok the Kashira/Stupino
// orders (~90 km from the office) form their own groups around start nodes 1 and 2, which are swept
// first with the brigades living there. Within a group, orders are sorted by polar angle around the
// group's start node, rotated to start at angle theta0, in either direction.
// Sector filling: a route is opened at the first unrouted order in angular order; the brigade scans
// forward and inserts every order it can (cheapest time-feasible position) until more than maxFail
// consecutive orders do not fit. To pick WHICH brigade opens the sector, every unused brigade of the
// group (or any unused brigade if the group has none left) is simulated and the one absorbing most
// orders (ties -> fewer km) is kept. Leftovers go through insertUnserved, then shared localSearch.
// Grid: theta0 (24 angles) x direction x maxFail {0, 2, 5, all}, then random angles (half of them with
// a "squeaky wheel" weight on orders earlier runs left unserved).
#include "common.hpp"
#include "constr_util.hpp"

struct Geo { vector<double> x, y; };

// Classical MDS on the symmetrized car distance matrix.
static Geo mds(const Instance& I) {
    int M = I.M; const auto& D = I.D[0];
    vector<double> B(M * M);
    vector<double> rowm(M, 0); double all = 0;
    for (int i = 0; i < M; i++) for (int j = 0; j < M; j++) {
        double d = 0.5 * (D[i * M + j] + D[j * M + i]); B[i * M + j] = d * d;
    }
    for (int i = 0; i < M; i++) { for (int j = 0; j < M; j++) rowm[i] += B[i * M + j]; rowm[i] /= M; all += rowm[i]; }
    all /= M;
    for (int i = 0; i < M; i++) for (int j = 0; j < M; j++) B[i * M + j] = -0.5 * (B[i * M + j] - rowm[i] - rowm[j] + all);
    vector<vector<double>> ev; vector<double> lam;
    for (int e = 0; e < 2; e++) {
        vector<double> v(M), w(M);
        for (int i = 0; i < M; i++) v[i] = 1.0 + 0.01 * ((i * 7919 + e * 104729) % 97);
        double l = 0;
        for (int it = 0; it < 300; it++) {
            for (int i = 0; i < M; i++) { double s = 0; for (int j = 0; j < M; j++) s += B[i * M + j] * v[j]; w[i] = s; }
            for (size_t q = 0; q < ev.size(); q++) {   // deflation
                double dot = 0; for (int i = 0; i < M; i++) dot += ev[q][i] * v[i];
                for (int i = 0; i < M; i++) w[i] -= lam[q] * dot * ev[q][i];
            }
            double nr = 0; for (double z : w) nr += z * z; nr = sqrt(nr);
            if (nr < 1e-12) break;
            l = nr; for (int i = 0; i < M; i++) v[i] = w[i] / nr;
        }
        ev.push_back(v); lam.push_back(l);
    }
    Geo g; g.x.resize(M); g.y.resize(M);
    for (int i = 0; i < M; i++) g.x[i] = ev[0][i] * sqrt(max(lam[0], 0.0)), g.y[i] = ev[1][i] * sqrt(max(lam[1], 0.0));
    return g;
}

struct WParams { double theta0; int dir, maxFail; };

struct SweepData {
    vector<int> starts;                 // distinct start nodes
    vector<vector<int>> members;        // group -> orders
    vector<vector<double>> angle;       // group -> angle of each member (same order)
    vector<int> groupOfStart;           // start node -> group index
};

static SweepData prepare(const Instance& I, const Geo& G) {
    SweepData S;
    for (auto& w : I.veh) if (find(S.starts.begin(), S.starts.end(), w.start) == S.starts.end()) S.starts.push_back(w.start);
    // remote groups (start != 0) first, then the office
    stable_sort(S.starts.begin(), S.starts.end(), [](int a, int b) { return (a != 0) > (b != 0); });
    S.members.resize(S.starts.size()); S.angle.resize(S.starts.size()); S.groupOfStart.assign(I.S, -1);
    for (size_t g = 0; g < S.starts.size(); g++) S.groupOfStart[S.starts[g]] = g;
    for (int k = 0; k < I.N; k++) {
        int bg = 0; double bd = cu::INF;
        for (size_t g = 0; g < S.starts.size(); g++) {
            double d = I.D[0][S.starts[g] * I.M + I.node(k)];
            if (d < bd) bd = d, bg = g;
        }
        int c = S.starts[bg], n = I.node(k);
        S.members[bg].push_back(k);
        S.angle[bg].push_back(atan2(G.y[n] - G.y[c], G.x[n] - G.x[c]));
    }
    return S;
}

// Fill brigade v scanning `order` (unrouted only) from index `from`; returns the route.
static vector<int> fill(const Instance& I, int v, const vector<int>& order, size_t from,
                        const vector<char>& routed, int maxFail) {
    vector<int> r; cu::Sched s; cu::build(I, v, r, s);
    int fails = 0; size_t n = order.size();
    for (size_t q = 0; q < n; q++) {
        int k = order[(from + q) % n];
        if (routed[k] || !I.can(v, k)) continue;
        int p; double c = cu::bestPos(I, v, r, s, k, p);
        if (c == cu::INF) { if (!r.empty() && ++fails > maxFail) break; continue; }
        r.insert(r.begin() + p, k); cu::build(I, v, r, s); fails = 0;
    }
    return r;
}

static Routes sweepBuild(const Instance& I, const SweepData& S, const WParams& P, const vector<double>& bonus) {
    Routes R(I.V); vector<char> routed(I.N, 0), used(I.V, 0);
    for (size_t g = 0; g < S.starts.size(); g++) {
        // angular order of the group, rotated to theta0, in direction dir
        vector<pair<double, int>> a;
        for (size_t q = 0; q < S.members[g].size(); q++) {
            double t = S.angle[g][q] - P.theta0;
            t = fmod(t + 8 * M_PI, 2 * M_PI); if (P.dir < 0) t = 2 * M_PI - t;
            a.push_back({t, S.members[g][q]});
        }
        sort(a.begin(), a.end());
        vector<int> order; for (auto& e : a) order.push_back(e.second);
        while (true) {
            size_t from = 0; while (from < order.size() && routed[order[from]]) from++;
            if (from == order.size()) break;
            bool local = false;
            for (int v = 0; v < I.V; v++) if (!used[v] && I.veh[v].start == S.starts[g]) local = true;
            int bv = -1; vector<int> br; double bkey = -cu::INF;
            for (int v = 0; v < I.V; v++) {
                if (used[v] || (local && I.veh[v].start != S.starts[g])) continue;
                auto r = fill(I, v, order, from, routed, P.maxFail);
                if (r.empty()) continue;
                double w = 0; for (int k : r) w += 1 + bonus[k];
                double key = w * 1e4 - routeKm(I, v, r);
                if (key > bkey) bkey = key, bv = v, br = r;
            }
            if (bv < 0) break;          // nobody can take the remaining group orders
            R[bv] = br; used[bv] = 1; for (int k : br) routed[k] = 1;
        }
    }
    cu::repair(I, R);
    return R;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer T; Rng rng(seed); cu::Pool pool(6);
    Geo G = mds(I); SweepData S = prepare(I, G);
    vector<WParams> grid;
    const int NA = 24;
    for (int mf : {2, 5, 0, 1000}) for (int dir : {1, -1}) for (int q = 0; q < NA; q++)
        grid.push_back({-M_PI + 2 * M_PI * q / NA, dir, mf});
    // random order of the grid (seeded) so that a short time limit still sees diverse settings
    for (int i = (int)grid.size() - 1; i > 0; i--) swap(grid[i], grid[rng.randint(i + 1)]);
    double buildEnd = 0.5 * tl, deadline = 0.85 * tl;
    // Determinism: the number of constructions is fixed by tl (calibrated so that the largest instance
    // needs ~40% of tl); the clock is only a safety net on a loaded machine.
    const int maxBuilds = max(20, (int)(3500 * tl));
    // "squeaky wheel": orders left unserved weigh more when choosing the brigade of a sector
    vector<double> bonus(I.N, 0.0), zero(I.N, 0.0);
    auto run = [&](const WParams& P, const vector<double>& b) {
        Routes R = sweepBuild(I, S, P, b);
        cu::bumpUnserved(I, R, bonus, 1.0);
        pool.add(I, R);
    };
    for (auto& P : grid) {
        if (T.sec() > buildEnd || pool.builds >= maxBuilds) break;
        run(P, zero);
    }
    // remaining build time: random start angles, every second run with the bonus
    for (int it = 0; T.sec() < buildEnd && pool.builds < maxBuilds; it++) {
        WParams P{-M_PI + 2 * M_PI * rng.uni(), rng.randint(2) ? 1 : -1, rng.randint(8)};
        run(P, it % 2 ? bonus : zero);
    }
    return cu::finish(I, pool, T, deadline);
}
int main(int c, char** v) { return runMain(c, v, solve, "06_sweep+ls"); }
