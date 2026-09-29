// 04. Solomon (1987) I1 sequential insertion, adapted to open routes and a heterogeneous fleet.
//
// Routes are built one brigade at a time. To choose WHICH brigade to open next, a full I1 route is
// built for every still-unused brigade and the one with the best "absorption" is kept (most orders, most
// service minutes, or rarity-weighted orders; ties -> fewer km). Route construction for brigade v:
//   seed   : unrouted order with the farthest distance from its nearest start node ("home" distance;
//            with several start nodes plain "farthest from v's start" would send the Kashira/Stupino
//            brigades to Moscow), restricted to orders whose nearest start is v's start if any exist;
//            or the earliest deadline b (same restriction);
//   c1(i,u,j) = a1 * c11 + (1 - a1) * c12,
//       c11 = d(i,u) + d(u,j) - mu * d(i,j)     (open route end: c11 = d(i,u))
//       c12 = start_j(new) - start_j(old)        (open route end: end-of-route time increase)
//   c2(u)  = lambda * dHome(u) - c1(i*,u,j*)  -> insert the order with max c2 at its best place.
// A small grid over (a1, mu, lambda, seed rule, brigade rule) is scanned within the time limit, the
// best raw constructions are polished with the shared localSearch. Later grid passes use a "squeaky
// wheel" bonus for orders that earlier runs could not serve.
#include "common.hpp"
#include "constr_util.hpp"

struct IParams { double a1, mu, lam; int seedRule, vehRule; };

// Build one I1 route for brigade v over the orders with free[k] = 1.
static vector<int> i1Route(const Instance& I, int v, const vector<char>& freeK, const IParams& P,
                           const vector<double>& bonus) {
    vector<int> r; cu::Sched s; cu::build(I, v, r, s);
    int st = I.veh[v].start;
    // home distance: distance (in v's mode) from the nearest start node of the fleet
    vector<double> home(I.N, cu::INF);
    for (int k = 0; k < I.N; k++) for (auto& w : I.veh) home[k] = min(home[k], I.d(v, w.start, I.node(k)));
    // seed (first pass: only orders for which v's start is the nearest start)
    int seed = -1;
    for (int pass = 0; pass < 2 && seed < 0; pass++) {
        double bestKey = -cu::INF;
        for (int k = 0; k < I.N; k++) {
            if (!freeK[k] || cu::insCost(I, v, r, s, 0, k) == cu::INF) continue;
            if (pass == 0 && I.d(v, st, I.node(k)) > home[k] + 1e-9) continue;
            double key = (P.seedRule == 0 ? home[k] : -I.ord[k].b + 1e-3 * home[k]) + 1e3 * bonus[k];
            if (key > bestKey) bestKey = key, seed = k;
        }
    }
    if (seed < 0) return r;
    vector<char> in(I.N, 0);
    r.push_back(seed); in[seed] = 1; cu::build(I, v, r, s);
    while (true) {
        int bu = -1, bp = -1; double bc2 = -cu::INF;
        for (int u = 0; u < I.N; u++) {
            if (!freeK[u] || in[u] || !I.can(v, u)) continue;
            int nu = I.node(u); double bc1 = cu::INF; int bpos = -1;
            for (int p = 0; p <= (int)r.size(); p++) {
                if (cu::insCost(I, v, r, s, p, u) == cu::INF) continue;
                int prev = p == 0 ? st : I.node(r[p - 1]);
                double beg = cu::insStart(I, v, r, s, p, u), c11, c12;
                if (p < (int)r.size()) {
                    int j = r[p], nj = I.node(j);
                    c11 = I.d(v, prev, nu) + I.d(v, nu, nj) - P.mu * I.d(v, prev, nj);
                    double oldS = s.dep[p + 1] - I.ord[j].svc;
                    double newS = max(beg + I.ord[u].svc + I.t(v, nu, nj), I.ord[j].a);
                    c12 = newS - oldS;
                } else {
                    c11 = I.d(v, prev, nu);
                    c12 = beg + I.ord[u].svc - s.dep[p];
                }
                double c1 = P.a1 * c11 + (1 - P.a1) * c12;
                if (c1 < bc1) bc1 = c1, bpos = p;
            }
            if (bpos < 0) continue;
            double c2 = P.lam * home[u] - bc1 + bonus[u];
            if (c2 > bc2) bc2 = c2, bu = u, bp = bpos;
        }
        if (bu < 0) break;
        r.insert(r.begin() + bp, bu); in[bu] = 1; cu::build(I, v, r, s);
    }
    return r;
}

static Routes i1Build(const Instance& I, const IParams& P, const vector<double>& bonus) {
    // rarity weight of an order: 1 / (number of brigades that can serve it as a singleton route)
    vector<double> rare(I.N, 0);
    for (int k = 0; k < I.N; k++) {
        int c = 0; for (int v = 0; v < I.V; v++) c += routeFeasible(I, v, {k});
        rare[k] = c ? 1.0 / c : 0;
    }
    Routes R(I.V); vector<char> freeK(I.N, 1), used(I.V, 0); int left = I.N;
    while (left > 0) {
        int bv = -1; vector<int> br; double bkey = -cu::INF;
        for (int v = 0; v < I.V; v++) {
            if (used[v]) continue;
            auto r = i1Route(I, v, freeK, P, bonus);
            if (r.empty()) continue;
            double work = 0;
            for (int k : r) work += (P.vehRule == 0 ? 1.0 : P.vehRule == 1 ? I.ord[k].svc : 1.0 + 10 * rare[k]) + bonus[k] / 20;
            double key = work * 1e4 - routeKm(I, v, r);
            if (key > bkey) bkey = key, bv = v, br = r;
        }
        if (bv < 0) break;
        R[bv] = br; used[bv] = 1;
        for (int k : br) freeK[k] = 0, left--;
    }
    cu::repair(I, R);
    return R;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer T; Rng rng(seed); cu::Pool pool(6);
    vector<IParams> grid;
    for (int vr : {0, 1, 2}) for (int sr : {0, 1}) for (double lam : {1.0, 2.0, 0.0})
        for (double a1 : {1.0, 0.5, 0.0}) for (double mu : {1.0, 0.5})
            grid.push_back({a1, mu, lam, sr, vr});
    // interleave: shuffle so that the (time-limited) scan covers diverse settings; classic ones first
    for (int i = (int)grid.size() - 1; i > 4; i--) swap(grid[i], grid[4 + rng.randint(i - 3)]);
    double buildEnd = 0.6 * tl, deadline = 0.85 * tl;
    // Determinism: the number of constructions is fixed by tl (calibrated so that the largest instance
    // needs ~40% of tl); the clock is only a safety net on a loaded machine.
    const int maxBuilds = max(20, (int)(600 * tl));
    // first pass over the grid is plain I1; further passes (while time remains) add a "squeaky wheel"
    // bonus (c2 += bonus, seed preference, brigade-choice weight) to orders earlier runs left unserved
    vector<double> bonus(I.N, 0.0), zero(I.N, 0.0);
    for (size_t it = 0; T.sec() < buildEnd && pool.builds < maxBuilds; it++) {
        if (it >= grid.size() && *max_element(bonus.begin(), bonus.end()) == 0) break;   // nothing to fix
        Routes R = i1Build(I, grid[it % grid.size()], it < grid.size() ? zero : bonus);
        vector<char> in(I.N, 0); for (auto& r : R) for (int k : r) in[k] = 1;
        for (int k = 0; k < I.N; k++) if (!in[k]) bonus[k] += 20;
        pool.add(I, R);
    }
    return cu::finish(I, pool, T, deadline);
}
int main(int c, char** v) { return runMain(c, v, solve, "04_solomon_i1+ls"); }
