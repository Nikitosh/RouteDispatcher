// 09. Beam search construction. Orders are taken in a fixed sequence (by window start, ties: deadline,
// priority, distance from depot; variants put all-day orders last or sort by deadline). A state is the
// set of current routes; it is expanded by inserting the next order at any feasible position of any
// brigade (O(1) check via cached ASAP starts and latest-start slack). Unused brigades of identical type
// are symmetric, so only one of them may be opened. Score = km + W_VEHICLE*used + 1e6*penalty(unserved)
// + lambda*time consumed. Duplicate states (same multiset of (type, route)) are merged. Beam width is
// doubled while the predicted time fits; final states are polished with localSearch.
#include "gm_util.hpp"
#include <unordered_set>
using namespace gm;

struct RC { vector<int> seq; vector<double> st, lat; double km = 0; uint64_t h = 0; };
struct State { vector<RC> r; double score = 0; uint64_t hash = 0; };

struct Beam {
    const Instance& I; vector<int> ty;
    Beam(const Instance& I) : I(I), ty(brigadeTypes(I)) {}
    static uint64_t mix(uint64_t x) { x += 0x9E3779B97F4A7C15ULL; x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL; x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL; return x ^ (x >> 31); }
    void recompute(int v, RC& c) {
        int L = c.seq.size(); c.st.resize(L); c.lat.resize(L);
        double t = 0, km = 0; int prev = I.veh[v].start;
        for (int i = 0; i < L; i++) { int k = c.seq[i], n = I.node(k); double b = max(I.ord[k].a, t + I.t(v, prev, n)); c.st[i] = b; t = b + I.ord[k].svc; km += I.d(v, prev, n); prev = n; }
        for (int i = L - 1; i >= 0; i--) {
            int k = c.seq[i]; double l = min(I.ord[k].b, SHIFT - I.ord[k].svc);
            if (i + 1 < L) l = min(l, c.lat[i + 1] - I.ord[k].svc - I.t(v, I.node(k), I.node(c.seq[i + 1])));
            c.lat[i] = l;
        }
        c.km = km;
        uint64_t h = mix(ty[v] + 1); for (int k : c.seq) h = mix(h ^ (uint64_t)(k + 7));
        c.h = L ? h : 0;
    }
    struct Cand { double sc; int par, v, pos; };

    // returns final layer sorted by score
    vector<State> run(const vector<int>& seq, int W, double lambda) {
        vector<State> cur(1); cur[0].r.resize(I.V);
        for (int k : seq) {
            vector<Cand> cand;
            for (int si = 0; si < (int)cur.size(); si++) {
                const State& S = cur[si]; bool any = false;
                for (int v = 0; v < I.V; v++) {
                    if (!I.can(v, k)) continue;
                    const RC& c = S.r[v]; int L = c.seq.size();
                    if (L == 0) { // symmetry: only the lowest-index empty brigade of its type
                        bool first = true; for (int u = 0; u < v; u++) if (ty[u] == ty[v] && S.r[u].seq.empty()) { first = false; break; }
                        if (!first) continue;
                    }
                    for (int p = 0; p <= L; p++) {
                        int prev = p ? I.node(c.seq[p - 1]) : I.veh[v].start;
                        double pe = p ? c.st[p - 1] + I.ord[c.seq[p - 1]].svc : 0;
                        int n = I.node(k);
                        double beg = max(I.ord[k].a, pe + I.t(v, prev, n));
                        if (beg > I.ord[k].b + EPS || beg + I.ord[k].svc > SHIFT + EPS) continue;
                        double dkm = I.d(v, prev, n), tc;
                        if (p < L) {
                            int nx = c.seq[p], nn = I.node(nx);
                            double ns = max(I.ord[nx].a, beg + I.ord[k].svc + I.t(v, n, nn));
                            if (ns > c.lat[p] + EPS) continue;
                            dkm += I.d(v, n, nn) - I.d(v, prev, nn); tc = ns - c.st[p];
                        } else tc = beg + I.ord[k].svc - pe;
                        double sc = S.score + dkm + (L == 0 ? W_VEHICLE : 0) + lambda * tc;
                        cand.push_back({sc, si, v, p}); any = true;
                    }
                }
                if (!any) cand.push_back({S.score + W_UNSERVED * I.penalty(k), si, -1, -1});
            }
            sort(cand.begin(), cand.end(), [](const Cand& a, const Cand& b) { return a.sc < b.sc; });
            vector<State> nxt; nxt.reserve(W); unordered_set<uint64_t> seen;
            for (auto& cd : cand) {
                if ((int)nxt.size() >= W) break;
                const State& P = cur[cd.par];
                uint64_t h = P.hash;
                if (cd.v >= 0) {
                    // compute new route hash cheaply
                    const RC& c = P.r[cd.v]; uint64_t rh = mix(ty[cd.v] + 1);
                    for (int i = 0; i <= (int)c.seq.size(); i++) {
                        if (i == cd.pos) rh = mix(rh ^ (uint64_t)(k + 7));
                        if (i < (int)c.seq.size()) rh = mix(rh ^ (uint64_t)(c.seq[i] + 7));
                    }
                    h = h - mix(c.h) + mix(rh);
                } else h = mix(h ^ (0xABCDEFULL + k));
                if (!seen.insert(h).second) continue;
                State S = P; S.hash = h; S.score = cd.sc;
                if (cd.v >= 0) { auto& c = S.r[cd.v]; c.seq.insert(c.seq.begin() + cd.pos, k); recompute(cd.v, c); }
                nxt.push_back(std::move(S));
            }
            cur.swap(nxt);
        }
        return cur;
    }
};

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; Rng rng(seed); bool polish = !noPolish(); Beam B(I); int N = I.N;
    auto dep = [&](int k) { return I.D[0][I.veh[0].start * I.M + I.node(k)]; };
    vector<vector<int>> seqs(3);
    for (int q = 0; q < 3; q++) {
        auto& s = seqs[q]; s.resize(N); iota(s.begin(), s.end(), 0);
        stable_sort(s.begin(), s.end(), [&](int x, int y) {
            auto& X = I.ord[x]; auto& Y = I.ord[y];
            if (q == 1) { bool wx = X.b - X.a > 240, wy = Y.b - Y.a > 240; if (wx != wy) return wy; }
            if (q == 2) { if (X.b != Y.b) return X.b < Y.b; }
            if (X.a != Y.a) return X.a < Y.a;
            if (X.b != Y.b) return X.b < Y.b;
            if (X.pri != Y.pri) return X.pri < Y.pri;
            return dep(x) < dep(y); });
    }
    double buildUntil = polish ? tl * 0.6 : tl * 0.9;
    vector<pair<double, Routes>> pool; double bestRawS = INF; Routes bestRaw(I.V);
    int W = 1, runs = 0, maxW = 1; double lastT = 0;
    const double lambdas[] = {0, 0.3, 1.0, 0.1};
    while (true) {
        double el = tm.sec();
        if (runs > 0 && el + lastT * 1.1 > buildUntil) { // try smaller width that still fits
            if (W > 1) { W /= 2; lastT /= 2; continue; }
            break;
        }
        int q = runs % 3; double lam = lambdas[(runs / 3) % 4];
        Timer t1; auto fin = B.run(seqs[q], W, lam); double dt = t1.sec(); runs++;
        maxW = max(maxW, W);
        for (int i = 0; i < (int)fin.size() && i < 3; i++) {
            Routes R(I.V); for (int v = 0; v < I.V; v++) R[v] = fin[i].r[v].seq;
            insertUnserved(I, R);
            double sc = evaluate(I, R).scalar();
            if (sc < bestRawS) { bestRawS = sc; bestRaw = R; }
            bool dup = false; for (auto& p : pool) if (fabs(p.first - sc) < 1e-6) dup = true;
            if (!dup) pool.push_back({sc, R});
        }
        // width schedule: double after each full sweep of variants (3 sequences)
        lastT = dt;
        if (runs % 3 == 0 && tm.sec() + dt * 2.2 * 3 < buildUntil) { W *= 2; lastT = dt * 2.2; }
    }
    sort(pool.begin(), pool.end(), [](auto& x, auto& y) { return x.first < y.first; });
    if (!polish) { fprintf(stderr, "s09 runs=%d maxW=%d raw=%.1f\n", runs, maxW, bestRawS); return bestRaw; }
    Routes best = bestRaw; double bestS = bestRawS; int np = 0;
    for (auto& [s0, R] : pool) {
        double rem = tl * 0.95 - tm.sec(); if (rem <= 0) break;
        polishRoutes(I, R, tm, tl * 0.95); np++;
        double sc = evaluate(I, R).scalar();
        if (sc < bestS) { bestS = sc; best = R; }
    }
    fprintf(stderr, "s09 runs=%d maxW=%d polished=%d/%zu raw=%.1f final=%.1f\n", runs, maxW, np, pool.size(), bestRawS, bestS);
    return best;
}
int main(int c, char** v) { return runMain(c, v, solve, "09_beam_search+ls"); }
