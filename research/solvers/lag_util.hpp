// lag_util: Lagrangian column generation toolkit for s8x solvers (agent lagcg, see logs/lagcg.md).
//  * Types  - vehicles grouped by (start, mode, mask) with counts.
//  * Pool   - route columns (type, order set) deduplicated by set keeping min km.
//  * HPricer- heuristic labeling (limited labels per node, memory-free dominance, elementary paths) for negative
//             reduced-cost routes  rc = fix + km - sum u_k.
//  * XPricer- exact ng-route labeling with completion bound (copied/adapted from cg_price.cpp) -> valid min rc
//             per type (falls back to the completion bound when the label cap is hit).
//  * Lagr   - subgradient on the pool Lagrangian (covering rows dualised by u, total-vehicle row by lam,
//             type counts kept in the subproblem), Lagrangian heuristic, rc-fixing, DFS set partitioning.
//  * CapKm  - copy of l2::KmSearch that feeds every accepted route into the pool.
#pragma once
#include "lns2_util.hpp"
#include <unordered_map>
#include <climits>
#include <memory>
#include <array>

namespace lg {
using l2::Prob;
constexpr int BW = 3;
struct Bits {
    uint64_t w[BW];
    void clear() { w[0] = w[1] = w[2] = 0; }
    bool has(int k) const { return (w[k >> 6] >> (k & 63)) & 1; }
    void set(int k) { w[k >> 6] |= 1ULL << (k & 63); }
    bool inter(const Bits& o) const { return (w[0] & o.w[0]) | (w[1] & o.w[1]) | (w[2] & o.w[2]); }
    void orWith(const Bits& o) { w[0] |= o.w[0]; w[1] |= o.w[1]; w[2] |= o.w[2]; }
    void andNot(const Bits& o) { w[0] &= ~o.w[0]; w[1] &= ~o.w[1]; w[2] &= ~o.w[2]; }
    bool subsetOf(const Bits& o) const { return !(w[0] & ~o.w[0]) && !(w[1] & ~o.w[1]) && !(w[2] & ~o.w[2]); }
    bool operator==(const Bits& o) const { return w[0] == o.w[0] && w[1] == o.w[1] && w[2] == o.w[2]; }
    int cnt() const { return __builtin_popcountll(w[0]) + __builtin_popcountll(w[1]) + __builtin_popcountll(w[2]); }
};

struct Types {
    int T = 0; vector<int> st, mode, mask, cnt, rep; vector<vector<int>> vehs; vector<int> tOf;
    explicit Types(const Instance& I) : tOf(I.V) {
        for (int v = 0; v < I.V; v++) {
            int f = -1;
            for (int t = 0; t < T; t++) if (st[t] == I.veh[v].start && mode[t] == I.veh[v].mode && mask[t] == I.veh[v].mask) { f = t; break; }
            if (f < 0) { f = T++; st.push_back(I.veh[v].start); mode.push_back(I.veh[v].mode); mask.push_back(I.veh[v].mask); cnt.push_back(0); rep.push_back(v); vehs.push_back({}); }
            cnt[f]++; vehs[f].push_back(v); tOf[v] = f;
        }
    }
};

struct Col { int t, off, len; double km; Bits b; };
struct KeyHash { size_t operator()(const pair<Bits, int>& k) const { uint64_t h = 1469598103934665603ULL ^ (uint64_t)k.second; for (int i = 0; i < BW; i++) { h ^= k.first.w[i]; h *= 1099511628211ULL; h ^= h >> 29; } return h; } };
struct KeyEq { bool operator()(const pair<Bits, int>& a, const pair<Bits, int>& b) const { return a.second == b.second && a.first == b.first; } };

struct Pool {
    const Prob& P; const Types& TY;
    vector<Col> cols; vector<int> data;
    unordered_map<pair<Bits, int>, int, KeyHash, KeyEq> idx;
    long long adds = 0;
    Pool(const Prob& p, const Types& ty) : P(p), TY(ty) { cols.reserve(1 << 16); data.reserve(1 << 20); idx.reserve(1 << 16); }
    const int* seq(const Col& c) const { return data.data() + c.off; }
    // km of seq for type t or -1 if infeasible
    double evalRoute(int t, const int* s, int len) const {
        int v = TY.rep[t]; double tm = 0, dist = 0; int prev = P.st[v];
        for (int i = 0; i < len; i++) {
            int k = s[i]; if (!P.can(v, k)) return -1; int n = P.nd[k];
            double beg = max(tm + P.t(v, prev, n), P.a[k]); if (beg > P.b[k] + l2::TOL) return -1;
            tm = beg + P.svc[k]; dist += P.d(v, prev, n); prev = n;
        }
        return dist;
    }
    // add a (known feasible) route of type t; returns column index (existing if dominated)
    int add(int t, const int* s, int len, double km) {
        if (len <= 0) return -1;
        adds++;
        Bits b; b.clear(); for (int i = 0; i < len; i++) b.set(s[i]);
        auto key = make_pair(b, t);
        auto it = idx.find(key);
        if (it != idx.end()) {
            Col& c = cols[it->second];
            if (km < c.km - 1e-9) { c.off = data.size(); data.insert(data.end(), s, s + len); c.km = km; }
            return it->second;
        }
        int id = cols.size(); cols.push_back({t, (int)data.size(), len, km, b}); data.insert(data.end(), s, s + len);
        idx.emplace(key, id);
        return id;
    }
    // add the route of vehicle v (any vehicle of the type) from an l2::Sol
    void addSolRoute(const l2::Sol& s, int v) { if (s.len[v]) add(TY.tOf[v], s.r[v], s.len[v], s.km[v]); }
    void addSol(const l2::Sol& s) { for (int v = 0; v < s.V; v++) addSolRoute(s, v); }
};

// ---------------- heuristic pricing ----------------
struct HPricer {
    const Prob& P; const Types& TY; int N;
    struct L { int node, par; double t, cost, km; Bits vis; bool alive; };
    vector<L> lab; vector<vector<int>> at; vector<vector<vector<int>>> succ;  // succ[t][k] (k==N: depot)
    int nbK; double beta = 1;
    HPricer(const Prob& p, const Types& ty, int nb = 25) : P(p), TY(ty), N(p.N), at(p.N), nbK(nb) {
        succ.assign(TY.T, vector<vector<int>>(N + 1));
        for (int t = 0; t < TY.T; t++) {
            int v = TY.rep[t];
            for (int k = 0; k <= N; k++) {
                int nk = k < N ? P.nd[k] : P.st[v]; double fk = k < N ? P.a[k] + P.svc[k] : 0;
                if (k < N && !P.can(v, k)) continue;
                vector<pair<double, int>> c;
                for (int j = 0; j < N; j++) if (j != k && P.can(v, j)) {
                    double arr = max(fk + P.t(v, nk, P.nd[j]), P.a[j]); if (arr > P.b[j] + l2::TOL) continue;
                    // "time-distance": km + waiting/10
                    double wait = max(0.0, P.a[j] - (k < N ? P.b[k] + P.svc[k] : 0) - P.t(v, nk, P.nd[j]));
                    c.push_back({P.d(v, nk, P.nd[j]) + 0.02 * wait, j});
                }
                sort(c.begin(), c.end());
                int lim = k < N ? nbK : 2 * nbK;
                for (int i = 0; i < (int)c.size() && i < lim; i++) succ[t][k].push_back(c[i].second);
            }
        }
    }
    struct Out { int t; double rc, km; vector<int> r; };
    // label cap per node Lc; appends negative rc routes (at most maxCols best) to out
    void price(int t, const double* u, double fix, int Lc, int maxCols, vector<Out>& out) {
        lab.clear(); for (auto& a : at) a.clear();
        int v = TY.rep[t];
        typedef pair<double, int> QE; priority_queue<QE, vector<QE>, greater<QE>> pq;
        vector<pair<double, int>> neg;
        auto tryAdd = [&](L X) {
            auto& lst = at[X.node];
            for (int id : lst) { const L& Y = lab[id]; if (Y.alive && Y.t <= X.t + 1e-9 && Y.cost <= X.cost + 1e-9) return; }
            size_t w = 0; int worst = -1; double wc = -1e18;
            for (size_t i = 0; i < lst.size(); i++) {
                L& Y = lab[lst[i]]; if (!Y.alive) continue;
                if (X.t <= Y.t + 1e-9 && X.cost <= Y.cost + 1e-9) { Y.alive = false; continue; }
                lst[w++] = lst[i];
            }
            lst.resize(w);
            if ((int)lst.size() >= Lc) {
                for (size_t i = 0; i < lst.size(); i++) if (lab[lst[i]].cost > wc) { wc = lab[lst[i]].cost; worst = i; }
                if (X.cost >= wc) return;
                lab[lst[worst]].alive = false; lst.erase(lst.begin() + worst);
            }
            int nid = lab.size(); X.alive = true; lab.push_back(X); lst.push_back(nid); pq.push({X.t, nid});
        };
        for (int j : succ[t][N]) {
            int nj = P.nd[j]; double beg = max(P.t(v, P.st[v], nj), P.a[j]); if (beg > P.b[j] + l2::TOL) continue;
            L X; X.node = j; X.par = -1; X.t = beg + P.svc[j]; X.km = P.d(v, P.st[v], nj); X.cost = beta * X.km - u[j]; X.vis.clear(); X.vis.set(j);
            tryAdd(X);
        }
        while (!pq.empty()) {
            auto [tm, id] = pq.top(); pq.pop();
            if (!lab[id].alive) continue;
            L X = lab[id];
            double rc = fix + X.cost;
            if (rc < -1e-6) neg.push_back({rc, id});
            int nk = P.nd[X.node];
            for (int j : succ[t][X.node]) {
                if (X.vis.has(j)) continue;
                int nj = P.nd[j]; double beg = max(X.t + P.t(v, nk, nj), P.a[j]); if (beg > P.b[j] + l2::TOL) continue;
                L Y; Y.node = j; Y.par = id; Y.t = beg + P.svc[j]; double dd = P.d(v, nk, nj); Y.km = X.km + dd; Y.cost = X.cost + beta * dd - u[j]; Y.vis = X.vis; Y.vis.set(j);
                tryAdd(Y);
            }
        }
        sort(neg.begin(), neg.end());
        vector<Bits> seen; int kept = 0;
        for (auto& [rc, id] : neg) {
            if (kept >= maxCols) break;
            const Bits& b = lab[id].vis; bool dup = false;
            for (auto& s : seen) if (s == b) { dup = true; break; }
            if (dup) continue;
            seen.push_back(b); kept++;
            Out o; o.t = t; o.rc = rc; o.km = lab[id].km;
            for (int x = id; x >= 0; x = lab[x].par) o.r.push_back(lab[x].node);
            reverse(o.r.begin(), o.r.end()); out.push_back(move(o));
        }
    }
};

// ---------------- exact ng pricing (valid min rc per type) ----------------
struct XPricer {
    const Prob& P; const Types& TY; int N; int B = 2;  // time bucket (minutes) of the completion bound
    vector<Bits> ng; vector<vector<int>> succ;
    vector<vector<double>> F; vector<int> F0;  // F[k][(u - F0[k]) / B]
    const double* u = nullptr; double fix = 0, beta = 1; const Timer* tmr = nullptr; double tDead = 1e18; bool fAborted = false;
    long long labels = 0;
    XPricer(const Prob& p, const Types& ty, int ngsize = 8) : P(p), TY(ty), N(p.N) {
        succ.resize(TY.T);
        for (int t = 0; t < TY.T; t++) for (int k = 0; k < N; k++) if (P.can(TY.rep[t], k)) succ[t].push_back(k);
        ng.resize(N);
        for (int k = 0; k < N; k++) {
            vector<pair<double, int>> c;
            for (int j = 0; j < N; j++) if (j != k) {
                bool kj = P.a[k] + P.svc[k] <= P.b[j] + 1e-9, jk = P.a[j] + P.svc[j] <= P.b[k] + 1e-9;
                if (!(kj && jk)) continue;
                c.push_back({P.g(k, j), j});
            }
            sort(c.begin(), c.end()); ng[k].clear(); ng[k].set(k);
            for (int i = 0; i < (int)c.size() && i < ngsize - 1; i++) ng[k].set(c[i].second);
        }
        F.resize(N); F0.resize(N);
    }
    // finish time at k lies in [a_k+svc, b_k+svc]
    inline double Fb(int k, double fin) const {
        int i = ((int)floor(fin + 1e-9) - F0[k]) / B; if (i < 0) i = 0; if (i >= (int)F[k].size()) i = F[k].size() - 1; return F[k][i];
    }
    void computeF(int t) {
        int v = TY.rep[t]; auto& al = succ[t];
        // process nodes by decreasing latest finish so that successors' F are ready? successor finish later in time,
        // so do a time sweep: bucket index over absolute minutes, from late to early.
        for (int k : al) { F0[k] = (int)floor(P.a[k] + P.svc[k]); int hi = (int)floor(P.b[k] + P.svc[k] + 1e-9); F[k].assign((hi - F0[k]) / B + 1, 0.0); }
        // absolute bucket start times descending
        int maxT = 721;
        vector<vector<pair<int, int>>> byT(maxT + 1);   // (k, idx) whose bucket start minute == m
        for (int k : al) for (int i = 0; i < (int)F[k].size(); i++) { int m = F0[k] + i * B; if (m > 720) m = 720; byT[m].push_back({k, i}); }
        fAborted = false;
        for (int m = maxT; m >= 0; m--) { if (tmr && (m & 15) == 0 && tmr->sec() > tDead) { fAborted = true; return; } for (auto [k, i] : byT[m]) {
            double best = 0; int nk = P.nd[k];
            for (int j : al) {
                if (j == k) continue;
                double st = max(m + P.t(v, nk, P.nd[j]), P.a[j]); if (st > P.b[j] + l2::TOL) continue;
                double fin = st + P.svc[j];
                double c = beta * P.d(v, nk, P.nd[j]) - u[j] + Fb(j, fin);  // fin > m strictly (svc>0)
                if (c < best) best = c;
            }
            F[k][i] = best;
        } }
    }
    double depotBound(int t) {
        int v = TY.rep[t]; double best = 0;
        for (int j : succ[t]) {
            double st = max(P.t(v, P.st[v], P.nd[j]), P.a[j]); if (st > P.b[j] + l2::TOL) continue;
            best = min(best, beta * P.d(v, P.st[v], P.nd[j]) - u[j] + Fb(j, st + P.svc[j]));
        }
        return fix + best;
    }
    struct L { int node, par; double t, cost, km; Bits mem, cs; bool alive; };
    const vector<double>* csig = nullptr; const vector<vector<int>>* cutsOf = nullptr;   // subset-row cuts (sigma <= 0)
    inline void applyCuts(L& X, int j) const { if (!csig) return; for (int c : (*cutsOf)[j]) { if (X.cs.has(c)) { X.cs.w[c >> 6] &= ~(1ULL << (c & 63)); X.cost -= (*csig)[c]; } else X.cs.set(c); } }
    inline double cutGap(const Bits& a, const Bits& b) const { if (!csig) return 0; double g = 0; for (int w = 0; w < BW; w++) { uint64_t x = a.w[w] & ~b.w[w]; while (x) { int c = w * 64 + __builtin_ctzll(x); x &= x - 1; g -= (*csig)[c]; } } return g; }
    vector<L> pool; vector<pair<double, vector<int>>>* outCols = nullptr; double outKm = 0; int maxOut = 50;
    // exact min rc (ng relaxation) of type t, or the completion bound if the cap is hit. complete flag out.
    vector<pair<double, int>> negIds;
    double minrc(int t, const double* uu, double fx, size_t cap, bool& complete) {
        u = uu; fix = fx; computeF(t);
        if (fAborted) { complete = false; return -1e18; }
        int v = TY.rep[t]; pool.clear(); vector<vector<int>> atN(N);
        typedef pair<double, int> QE; priority_queue<QE, vector<QE>, greater<QE>> pq;
        double mr = 1e18;
        auto tryAdd = [&](L X) {
            double lb = fix + X.cost + Fb(X.node, X.t);
            if (lb >= -1e-9) { if (lb < mr) mr = lb; return; }
            auto& lst = atN[X.node];
            for (int id : lst) { const L& Y = pool[id]; if (Y.alive && Y.t <= X.t + 1e-9 && Y.cost + cutGap(Y.cs, X.cs) <= X.cost + 1e-9 && Y.mem.subsetOf(X.mem)) return; }
            size_t w = 0;
            for (size_t i = 0; i < lst.size(); i++) { L& Y = pool[lst[i]]; if (!Y.alive) continue; if (X.t <= Y.t + 1e-9 && X.cost + cutGap(X.cs, Y.cs) <= Y.cost + 1e-9 && X.mem.subsetOf(Y.mem)) { Y.alive = false; continue; } lst[w++] = lst[i]; }
            lst.resize(w); int nid = pool.size(); X.alive = true; pool.push_back(X); lst.push_back(nid); pq.push({X.t, nid});
        };
        for (int j : succ[t]) {
            double st = max(P.t(v, P.st[v], P.nd[j]), P.a[j]); if (st > P.b[j] + l2::TOL) continue;
            L X; X.node = j; X.par = -1; X.t = st + P.svc[j]; X.km = P.d(v, P.st[v], P.nd[j]); X.cost = beta * X.km - u[j]; X.mem.clear(); X.mem.set(j); X.cs.clear(); applyCuts(X, j); tryAdd(X);
        }
        complete = true;
        while (!pq.empty()) {
            auto [tm, id] = pq.top(); pq.pop();
            if (!pool[id].alive) continue;
            if (pool.size() > cap || (tmr && (pool.size() & 255) == 0 && tmr->sec() > tDead)) { complete = false; break; }
            L X = pool[id]; double rc = fix + X.cost; if (rc < mr) mr = rc;
            if (outCols && rc < -1e-6) negIds.push_back({rc, id});
            int nk = P.nd[X.node];
            for (int j : succ[t]) {
                if (X.mem.has(j)) continue;
                double st = max(X.t + P.t(v, nk, P.nd[j]), P.a[j]); if (st > P.b[j] + l2::TOL) continue;
                L Y; Y.node = j; Y.par = id; Y.t = st + P.svc[j]; double dd = P.d(v, nk, P.nd[j]); Y.km = X.km + dd; Y.cost = X.cost + beta * dd - u[j];
                for (int w = 0; w < BW; w++) Y.mem.w[w] = X.mem.w[w] & ng[j].w[w];
                Y.mem.set(j); Y.cs = X.cs; applyCuts(Y, j); tryAdd(Y);
            }
        }
        labels += pool.size();
        if (outCols) {   // elementary negative routes, best first, distinct sets
            sort(negIds.begin(), negIds.end()); vector<Bits> seen; int kept = 0;
            for (auto& [rc, id] : negIds) {
                if (kept >= maxOut) break;
                vector<int> r; Bits b; b.clear(); bool elem = true;
                for (int x = id; x >= 0; x = pool[x].par) { int k = pool[x].node; if (b.has(k)) { elem = false; break; } b.set(k); r.push_back(k); }
                if (!elem) continue;
                bool dup = false; for (auto& q : seen) if (q == b) { dup = true; break; } if (dup) continue;
                seen.push_back(b); kept++; reverse(r.begin(), r.end()); outCols->push_back({pool[id].km, r});
            }
            negIds.clear();
        }
        if (!complete) return depotBound(t);
        if (mr > 1e17) mr = 0;
        return mr;
    }
};

// ---------------- Lagrangian over the pool + primal heuristics ----------------
struct Lagr {
    const Prob& P; const Types& TY; Pool& PL; int N, K;
    vector<char> row;              // orders that must be covered (1) — others are forbidden in columns
    vector<double> u, bestU; double lam = 0, bestLam = 0, Lbest = -1e18;
    vector<double> rc;             // rc with lam
    Lagr(const Prob& p, const Types& ty, Pool& pl) : P(p), TY(ty), PL(pl), N(p.N), K(p.V), row(p.N, 1), u(p.N, 0) {}
    bool colOK(const Col& c) const { const int* s = PL.seq(c); for (int i = 0; i < c.len; i++) if (!row[s[i]]) return false; return true; }
    // pool Lagrangian value; fills g (subgradient) and x selection
    vector<int> sel; vector<vector<pair<double, int>>> byT;
    double eval(vector<double>& g, double& gl) {
        int C = PL.cols.size(); rc.resize(C);
        byT.assign(TY.T, {});
        for (int c = 0; c < C; c++) {
            const Col& col = PL.cols[c]; const int* s = PL.seq(col);
            double r = col.km + lam; bool ok = true;
            for (int i = 0; i < col.len; i++) { r -= u[s[i]]; if (!row[s[i]]) ok = false; }
            rc[c] = ok ? r : 1e18;
            if (ok && r < 0) byT[col.t].push_back({r, c});
        }
        double L = -lam * K; for (int k = 0; k < N; k++) if (row[k]) L += u[k];
        g.assign(N, 0); for (int k = 0; k < N; k++) g[k] = row[k] ? 1 : 0;
        sel.clear(); int nx = 0;
        for (int t = 0; t < TY.T; t++) {
            auto& v = byT[t]; int m = min((int)v.size(), TY.cnt[t]);
            if (m < (int)v.size()) nth_element(v.begin(), v.begin() + m, v.end());
            for (int i = 0; i < m; i++) { L += v[i].first; sel.push_back(v[i].second); nx++; const Col& col = PL.cols[v[i].second]; const int* s = PL.seq(col); for (int q = 0; q < col.len; q++) g[s[q]] -= 1; }
        }
        gl = nx - K;
        return L;
    }
    // subgradient iterations with Polyak step toward target UB
    double theta = 1.0; int noImp = 0; vector<double> dir; double dirL = 0; double defl = l2::P("DEFL", 0.3); int rule = (int)l2::P("SGRULE", 1);
    double wmax = -1e18, wmin = 1e18; int wcnt = 0;
    void subgrad(int iters, double UB) {
        vector<double> g; double gl;
        if ((int)dir.size() != N) dir.assign(N, 0);
        for (int it = 0; it < iters; it++) {
            double L = eval(g, gl);
            if (L > Lbest + 1e-9) { Lbest = L; bestU = u; bestLam = lam; noImp = 0; } else noImp++;
            if (rule == 0) { if (noImp >= 15) { theta *= 0.6; noImp = 0; if (theta < 0.005) theta = 0.005; } }
            else {   // Caprara-Fischetti-Toth window rule
                wmax = max(wmax, L); wmin = min(wmin, L);
                if (++wcnt >= 20) { double rel = (wmax - wmin) / max(1.0, fabs(wmax)); if (rel > 0.01) theta *= 0.5; else if (rel < 0.001) theta = min(2.0, theta * 1.5); wcnt = 0; wmax = -1e18; wmin = 1e18; }
            }
            double nrm = 0;
            for (int k = 0; k < N; k++) { dir[k] = g[k] + defl * dir[k]; nrm += dir[k] * dir[k]; }
            dirL = gl + defl * dirL; if (lam > 0 || dirL > 0) nrm += dirL * dirL;
            if (nrm < 1e-12) break;
            double tgt = UB;
            double step = theta * max(1e-3 * fabs(UB), tgt - L) / nrm;
            for (int k = 0; k < N; k++) if (row[k]) u[k] += step * dir[k];
            lam = max(0.0, lam + step * dirL);
        }
    }
    void resetBest() { Lbest = -1e18; }
    void useBest() { if (!bestU.empty()) { u = bestU; lam = bestLam; } }
    // rc for current u (lam included)
    void computeRc() {
        int C = PL.cols.size(); rc.resize(C);
        for (int c = 0; c < C; c++) { const Col& col = PL.cols[c]; const int* s = PL.seq(col); double r = col.km + lam; bool ok = true; for (int i = 0; i < col.len; i++) { r -= u[s[i]]; if (!row[s[i]]) ok = false; } rc[c] = ok ? r : 1e18; }
    }

    // ---- DFS set partitioning over columns with rc <= gapFix (exact within the reduced pool, time/node limited)
    vector<int> cand; vector<vector<int>> colsOf; vector<int> tcnt; Bits target;
    vector<int> bestSel, curSel; double bestCost; long long nodes, nodeLim; Timer* tmr; double tEnd; double mneg;
    int KK;
    void dfs(Bits cov, int used, double cost, double uRem) {
        if (++nodes > nodeLim || ((nodes & 1023) == 0 && tmr->sec() > tEnd)) { nodes = nodeLim + 1; return; }
        if (cov == target) { if (cost < bestCost - 1e-9) { bestCost = cost; bestSel = curSel; } return; }
        if (used >= KK) return;
        // choose uncovered order with fewest compatible candidates (static count)
        int bk = -1; size_t bc = SIZE_MAX;
        for (int k = 0; k < N; k++) if (row[k] && !cov.has(k)) { size_t c = colsOf[k].size(); if (c < bc) { bc = c; bk = k; if (c <= 1) break; } }
        if (bk < 0 || bc == 0) return;
        for (int c : colsOf[bk]) {
            const Col& col = PL.cols[c];
            if (col.b.inter(cov) || tcnt[col.t] >= TY.cnt[col.t]) continue;
            double ur = uRem; const int* s = PL.seq(col); for (int i = 0; i < col.len; i++) ur -= u[s[i]];
            double nc = cost + col.km;
            double lb = nc + (ur > 1e-9 || ur < -1e-9 ? ur + (KK - used - 1) * mneg : 0);
            if (lb >= bestCost - 1e-6) continue;
            Bits c2 = cov; c2.orWith(col.b); tcnt[col.t]++; curSel.push_back(c);
            dfs(c2, used + 1, nc, ur);
            curSel.pop_back(); tcnt[col.t]--;
            if (nodes > nodeLim) return;
        }
    }
    // returns true if found a solution strictly better than UB; sel columns in bestSel
    bool solveSP(double UB, double gapFix, long long nlim, Timer& tm, double tEndIn, int maxCand = 4000) {
        computeRc();
        double L = Lbest > -1e17 ? Lbest : -1e18;
        cand.clear();
        vector<pair<double, int>> v;
        for (int c = 0; c < (int)PL.cols.size(); c++) if (rc[c] <= gapFix) v.push_back({rc[c], c});
        sort(v.begin(), v.end()); if ((int)v.size() > maxCand) v.resize(maxCand);
        colsOf.assign(N, {}); mneg = 0;
        for (auto& [r, c] : v) { cand.push_back(c); const Col& col = PL.cols[c]; const int* s = PL.seq(col); for (int i = 0; i < col.len; i++) colsOf[s[i]].push_back(c); mneg = min(mneg, r - lam); }
        (void)L;
        target.clear(); double uAll = 0; for (int k = 0; k < N; k++) if (row[k]) { target.set(k); uAll += u[k]; }
        tcnt.assign(TY.T, 0); bestSel.clear(); curSel.clear(); bestCost = UB; nodes = 0; nodeLim = nlim; tmr = &tm; tEnd = tEndIn; KK = K;
        Bits z; z.clear(); dfs(z, 0, 0, uAll);
        return !bestSel.empty();
    }
    // Lagrangian heuristic: greedy by rc/len with random noise; returns selected columns (partial cover possible)
    vector<int> greedy(Rng& rng, double noise) {
        computeRc();
        vector<pair<double, int>> v;
        for (int c = 0; c < (int)PL.cols.size(); c++) if (rc[c] < 1e17) v.push_back({(rc[c] - lam) / PL.cols[c].len * (1 + noise * (rng.uni() - 0.5)) , c});
        sort(v.begin(), v.end());
        Bits cov; cov.clear(); vector<int> tc(TY.T, 0), out; int used = 0;
        for (auto& [s, c] : v) {
            if (used >= K) break;
            const Col& col = PL.cols[c]; if (col.b.inter(cov) || tc[col.t] >= TY.cnt[col.t]) continue;
            cov.orWith(col.b); tc[col.t]++; used++; out.push_back(c);
        }
        return out;
    }
    // columns -> l2::Sol (vehicles of each type assigned in order)
    void toSol(const vector<int>& cs, l2::Sol& s) const {
        Routes R(P.V); vector<int> tc(TY.T, 0);
        for (int c : cs) { const Col& col = PL.cols[c]; int v = TY.vehs[col.t][tc[col.t]++]; const int* q = PL.seq(col); R[v].assign(q, q + col.len); }
        s.fromRoutes(P, R);
    }
    // global Lagrangian bound with valid per-type min rc (lam included in fix)
    double globalBound(XPricer& X, size_t cap, bool& allExact, double* uu = nullptr, double lm = -1) {
        const double* uuse = uu ? uu : u.data(); double l = lm >= 0 ? lm : lam;
        double L = -l * K; for (int k = 0; k < N; k++) if (row[k]) L += uuse[k];
        allExact = true;
        for (int t = 0; t < TY.T; t++) { bool comp; double m = X.minrc(t, uuse, l, cap, comp); if (!comp) allExact = false; L += TY.cnt[t] * min(0.0, m); }
        return L;
    }
};

// ---------------- small revised simplex for the restricted master (dense B^-1, m <= ~160) ----------------
// min c x, A x (=|<=) b, x >= 0; A has 0/1 entries. The first m columns are unit columns of the rows
// (artificial with cost bigM for '=' rows, slack with cost 0 for '<=' rows). RHS is perturbed against cycling.
struct LP {
    int m = 0; vector<double> b, cost; vector<vector<int>> A; vector<int> basis, pos; vector<double> Binv, xB, pi;
    long long pivots = 0; int ppos = 0, SEG = (int)l2::P("SEG", 400); Rng rng{12345}; double pert = l2::P("PERT", 1e-6);
    void init(int rows, const vector<double>& rhs, const vector<char>& isEq, double bigM) {
        m = rows; b = rhs; cost.clear(); A.clear(); pos.clear();
        for (int r = 0; r < m; r++) { b[r] += (1 + rng.uni()) * pert * (isEq[r] ? 1.0 : 100.0 * max(1.0, fabs(b[r]))); addCol(isEq[r] ? bigM : 0, {r}); }
        basis.resize(m); for (int r = 0; r < m; r++) { basis[r] = r; pos[r] = r; }
        Binv.assign(m * m, 0); for (int r = 0; r < m; r++) Binv[r * m + r] = 1; xB = b; pi.assign(m, 0);
    }
    int addCol(double c, vector<int> rows) { cost.push_back(c); A.push_back(move(rows)); pos.push_back(-1); return cost.size() - 1; }
    int ncols() const { return cost.size(); }
    // Gauss-Jordan inverse of the basis; singular -> replace by unit columns
    void refactor() {
        vector<double> Bm(m * m, 0);
        for (int c = 0; c < m; c++) for (int r : A[basis[c]]) Bm[r * m + c] = 1;
        vector<double> Iv(m * m, 0); for (int i = 0; i < m; i++) Iv[i * m + i] = 1;
        vector<int> colUsed(m, 0), rowOfCol(m, -1);
        // column-wise elimination with partial pivoting on rows
        vector<int> rowDone(m, 0);
        for (int c = 0; c < m; c++) {
            int pr = -1; double bv = 1e-9;
            for (int r = 0; r < m; r++) if (!rowDone[r] && fabs(Bm[r * m + c]) > bv) { bv = fabs(Bm[r * m + c]); pr = r; }
            if (pr < 0) continue;
            rowDone[pr] = 1; rowOfCol[c] = pr;
            double inv = 1.0 / Bm[pr * m + c];
            for (int j = 0; j < m; j++) { Bm[pr * m + j] *= inv; Iv[pr * m + j] *= inv; }
            for (int r = 0; r < m; r++) if (r != pr) { double f = Bm[r * m + c]; if (f != 0) { for (int j = 0; j < m; j++) { Bm[r * m + j] -= f * Bm[pr * m + j]; Iv[r * m + j] -= f * Iv[pr * m + j]; } } }
        }
        bool sing = false;
        for (int c = 0; c < m; c++) if (rowOfCol[c] < 0) sing = true;
        if (sing) {   // replace dependent columns by unit columns of uncovered rows, then redo
            vector<int> freeRows; for (int r = 0; r < m; r++) if (!rowDone[r]) freeRows.push_back(r);
            int fi = 0;
            for (int c = 0; c < m; c++) if (rowOfCol[c] < 0) { pos[basis[c]] = -1; basis[c] = freeRows[fi++]; pos[basis[c]] = c; }
            refactor(); return;
        }
        // B^-1 row for basis position c is row rowOfCol[c] of Iv
        for (int c = 0; c < m; c++) memcpy(&Binv[c * m], &Iv[rowOfCol[c] * m], m * sizeof(double));
        for (int i = 0; i < m; i++) { double x = 0; for (int r = 0; r < m; r++) x += Binv[i * m + r] * b[r]; xB[i] = x; }
    }
    void computePi() { fill(pi.begin(), pi.end(), 0.0); for (int i = 0; i < m; i++) { double c = cost[basis[i]]; if (c == 0) continue; const double* row = &Binv[i * m]; for (int r = 0; r < m; r++) pi[r] += c * row[r]; } }
    double rcOf(int j) const { double r = cost[j]; for (int q : A[j]) r -= pi[q]; return r; }
    double obj() const { double o = 0; for (int i = 0; i < m; i++) o += cost[basis[i]] * xB[i]; return o; }
    // set a starting basis (list of column ids, one per row position) and refactor
    void setBasis(const vector<int>& bs) { for (int j : basis) pos[j] = -1; basis = bs; for (int i = 0; i < m; i++) pos[basis[i]] = i; refactor(); }
    // primal simplex; returns 0 optimal, 1 iteration limit
    int solve(int maxIt, Timer* tm = nullptr, double tEnd = 1e18) {
        vector<double> d(m);
        for (int it = 0; it < maxIt; it++) {
            if (it % 60 == 59) refactor();
            if (tm && (it & 15) == 15 && tm->sec() > tEnd) return 1;
            computePi();
            int q = -1; double best = -1e-9;
            int nc = ncols();
            // partial pricing: scan cyclic segments of SEG columns, stop at the first segment containing a negative rc
            int scanned = 0;
            while (scanned < nc) {
                int lim = min(nc - scanned, SEG);
                for (int s2 = 0; s2 < lim; s2++) { int j = ppos; if (++ppos >= nc) ppos = 0; if (pos[j] < 0) { double r = rcOf(j); if (r < best) { best = r; q = j; } } }
                scanned += lim;
                if (q >= 0) break;
            }
            if (q < 0) return 0;
            for (int i = 0; i < m; i++) { double x = 0; const double* row = &Binv[i * m]; for (int r : A[q]) x += row[r]; d[i] = x; }
            // Harris ratio test
            double tmax = 1e30; const double tol = 1e-9, tp = 1e-9;
            for (int i = 0; i < m; i++) if (d[i] > tol) tmax = min(tmax, (xB[i] + tp) / d[i]);
            if (tmax >= 1e29) return 2;
            int p = -1; double bd = 0;
            for (int i = 0; i < m; i++) if (d[i] > tol && xB[i] / d[i] <= tmax && d[i] > bd) { bd = d[i]; p = i; }
            double th = max(0.0, xB[p] / d[p]);
            for (int i = 0; i < m; i++) xB[i] -= th * d[i];
            xB[p] = th;
            double inv = 1.0 / d[p]; double* rp = &Binv[p * m]; for (int r = 0; r < m; r++) rp[r] *= inv;
            for (int i = 0; i < m; i++) if (i != p && d[i] != 0) { double f = d[i]; double* ri = &Binv[i * m]; for (int r = 0; r < m; r++) ri[r] -= f * rp[r]; }
            pos[basis[p]] = -1; basis[p] = q; pos[q] = p; pivots++;
        }
        return 1;
    }
    double xOf(int j) const { return pos[j] >= 0 ? xB[pos[j]] : 0; }
};

// ---- LP-based column generation at fixed fleet K (restricted master solved by lg::LP) ----
struct CGRun {
    const l2::Prob& Pb; const lg::Types& TY; lg::Pool& PL; lg::HPricer& HP;
    int N, R = 0, T, m, K; vector<int> rowIdx; lg::LP lp; vector<int> lpOf; vector<int> poolOf;
    double UB, LPval = 0; vector<double> rcP; double beta = 1, cfix = 0;
    CGRun(const l2::Prob& p, const lg::Types& ty, lg::Pool& pl, lg::HPricer& hp) : Pb(p), TY(ty), PL(pl), HP(hp), N(p.N), T(ty.T), rowIdx(p.N, -1) {}
    // subset-row cuts (3-SRC): rows R+T+1+c, coefficient 1 if the route visits >= 2 of the 3 orders, rhs 1
    vector<array<int, 3>> cuts; vector<vector<int>> cutsOfO; vector<double> csig; vector<int> ccnt;
    int C() const { return cuts.size(); }
    void cutHits(const int* s, int len, vector<int>& out) {
        out.clear(); if (cuts.empty()) return; if ((int)ccnt.size() < C()) ccnt.assign(C(), 0);
        for (int i = 0; i < len; i++) for (int c : cutsOfO[s[i]]) if (++ccnt[c] == 2) out.push_back(c);
        for (int i = 0; i < len; i++) for (int c : cutsOfO[s[i]]) ccnt[c] = 0;
    }
    vector<int> hitTmp;
    double cutRc(const int* s, int len) { if (cuts.empty()) return 0; cutHits(s, len, hitTmp); double r = 0; for (int c : hitTmp) r -= lp.pi[R + T + 1 + c]; return r; }
    bool compat(const lg::Col& c) const { const int* s = PL.seq(c); for (int i = 0; i < c.len; i++) if (rowIdx[s[i]] < 0) return false; return true; }
    int toLP(int c) {
        if (c < (int)lpOf.size() && lpOf[c] >= 0) return lpOf[c];
        if ((int)lpOf.size() <= c) lpOf.resize(PL.cols.size(), -1);
        const lg::Col& col = PL.cols[c]; vector<int> rows; const int* s = PL.seq(col);
        for (int i = 0; i < col.len; i++) rows.push_back(rowIdx[s[i]]);
        rows.push_back(R + col.t); rows.push_back(R + T);
        if (!cuts.empty()) { cutHits(s, col.len, hitTmp); for (int c : hitTmp) rows.push_back(R + T + 1 + c); }
        int j = lp.addCol(beta * col.km + cfix, rows); lpOf[c] = j; poolOf.resize(j + 1, -1); poolOf[j] = c; return j;
    }
    double rcPool(int c) const {
        const lg::Col& col = PL.cols[c]; const int* s = PL.seq(col); double r = beta * col.km + cfix - lp.pi[R + col.t] - lp.pi[R + T];
        for (int i = 0; i < col.len; i++) { int q = rowIdx[s[i]]; if (q < 0) return 1e18; r -= lp.pi[q]; }
        if (!cuts.empty()) { r += const_cast<CGRun*>(this)->cutRc(s, col.len); }
        return r;
    }
    int addNegPool(int maxAdd) {
        vector<pair<double, int>> v;
        for (int c = 0; c < (int)PL.cols.size(); c++) { if (c < (int)lpOf.size() && lpOf[c] >= 0) continue; double r = rcPool(c); if (r < -1e-7) v.push_back({r, c}); }
        if ((int)v.size() > maxAdd) { nth_element(v.begin(), v.begin() + maxAdd, v.end()); v.resize(maxAdd); }
        for (auto& x : v) toLP(x.second);
        return v.size();
    }
    int rounds = 0, priced = 0, sifted = 0, xrounds = 0, xpriced = 0; bool converged = false; double bestFarley = -1e18, bestL = -1e18, tLPs = 0; int XEVERY = (int)l2::P("XEVERY", 0), lastX = -1; int WENT = (int)l2::P("WENT", 0); double stopL = 1e18; lg::XPricer* X = nullptr; int XTH = (int)l2::P("XTH", 1); size_t XCAP = (size_t)l2::P("XCAP", 2e5);
    // returns LP value over (pool + priced columns)
    vector<int> tcap; int maxRounds = 1 << 30;
    int crashRow(int c) const { const Col& col = PL.cols[c]; const int* q = PL.seq(col); int br = rowIdx[q[0]]; for (int i = 1; i < col.len; i++) if (lp.b[rowIdx[q[i]]] < lp.b[br]) br = rowIdx[q[i]]; return br; }
    // rows = orders with mask 1; type caps; vehicle cap
    void setup(const vector<char>& rowMask, const vector<int>& tc, int Kc, double ub) {
        R = 0; for (int k = 0; k < N; k++) rowIdx[k] = rowMask[k] ? R++ : -1;
        K = Kc; UB = ub; m = R + T + 1 + C(); tcap = tc;
        vector<double> rhs(m, 1); vector<char> eq(m, 1);
        for (int t = 0; t < T; t++) { rhs[R + t] = tc[t]; eq[R + t] = 0; }
        rhs[R + T] = K; eq[R + T] = 0;
        for (int c = 0; c < C(); c++) eq[R + T + 1 + c] = 0;
        lp.init(m, rhs, eq, beta == 0 ? l2::P("FBIGM", 1.5) * cfix : l2::P("BIGM", 100));
        lpOf.assign(PL.cols.size(), -1); poolOf.assign(lp.ncols(), -1);
    }
    void run(const l2::Sol& best, double tEnd, Timer& tm) {
        vector<char> rm(N); for (int k = 0; k < N; k++) rm[k] = best.rt[k] >= 0;
        setup(rm, TY.cnt, best.used(), best.kmTot());
        vector<int> bs(m); for (int i = 0; i < m; i++) bs[i] = i;
        incCols.clear();
        for (int v = 0; v < Pb.V; v++) if (best.len[v]) { int c = PL.add(TY.tOf[v], best.r[v], best.len[v], best.km[v]); int j = toLP(c); bs[crashRow(c)] = j; incCols.push_back(c); }
        if (l2::P("CRASH", 1) > 0) lp.setBasis(bs);
        cgLoop(tEnd, tm);
    }
    void cgLoop(double tEnd, Timer& tm) {
        int LC = (int)l2::P("LC", 6), MC = (int)l2::P("MC", 20), SIFT = (int)l2::P("SIFT", 300);
        HP.beta = beta; if (X) { X->beta = beta; X->tmr = &tm; X->tDead = tEnd; }
        vector<double> pc; double alpha = l2::P("ALPHA", 0.5); bool needSolve = true;
        auto duals = [&](const vector<double>& pi, vector<double>& u, vector<double>& fx) {
            u.assign(N, -1e9); fx.assign(T, 0);
            for (int k = 0; k < N; k++) if (rowIdx[k] >= 0) u[k] = pi[rowIdx[k]];
            for (int t = 0; t < T; t++) fx[t] = cfix - pi[R + t] - pi[R + T];
        };
        while (tm.sec() < tEnd) {
            if (rounds >= maxRounds) break;
            if (needSolve) { double q0 = tm.sec(); lp.solve(100000, &tm, tEnd); rounds++; tLPs += tm.sec() - q0; }
            needSolve = true;
            int added = addNegPool(SIFT); sifted += added;
            const vector<double>& pl = lp.pi;
            vector<double> ps(m);
            if (pc.empty()) pc = pl;
            for (int i = 0; i < m; i++) ps[i] = alpha * pc[i] + (1 - alpha) * pl[i];
            vector<double> u, fx, uL, fxL; duals(ps, u, fx); duals(pl, uL, fxL);
            vector<lg::HPricer::Out> out;
            for (int t = 0; t < T; t++) HP.price(t, u.data(), fx[t], LC, MC, out);
            int negLP = 0;
            for (auto& o : out) { int c = PL.add(o.t, o.r.data(), o.r.size(), o.km); toLP(c); double r = beta * o.km + fxL[o.t] + cutRc(o.r.data(), o.r.size()); for (int k : o.r) r -= uL[k]; if (r < -1e-7) negLP++; }
            priced += out.size();
            if (negLP == 0 && alpha > 0 && !added) {   // mispricing: move the center to the LP duals and price again
                bool same = true; for (int i = 0; i < m; i++) if (fabs(pc[i] - pl[i]) > 1e-9) { same = false; break; }
                pc = pl;
                if (!same) { needSolve = out.empty() ? false : true; if (!out.empty()) continue; else continue; }
            } else if (!WENT) pc = ps;
            if (X && ((!added && negLP < XTH) || (XEVERY > 0 && rounds % XEVERY == 0 && rounds != lastX))) {
                lastX = rounds;   // exact ng pricing at the LP duals: columns + valid bound
                vector<pair<double, vector<int>>> xc; X->outCols = &xc; X->maxOut = MC;
                vector<double> uB = uL; if (beta == 0) for (int k = 0; k < N; k++) if (rowIdx[k] >= 0) uB[k] = max(0.0, uB[k]);
                double L = lp.pi[R + T] * K, su = 0; for (int k = 0; k < N; k++) if (rowIdx[k] >= 0) { L += uB[k]; su += uB[k]; }
                if (!cuts.empty()) { csig.assign(C(), 0); for (int c = 0; c < C(); c++) { csig[c] = min(0.0, lp.pi[R + T + 1 + c]); L += csig[c]; } X->csig = &csig; X->cutsOf = &cutsOfO; } else X->csig = nullptr;
                bool allEx = true; int nx = 0; double theta = 0, fixB = cfix - pl[R + T]; vector<double> mrT(T, 0);
                for (int t = 0; t < T; t++) {
                    xc.clear(); bool comp; double mr = X->minrc(t, uB.data(), fixB, XCAP, comp);
                    if (!comp) allEx = false;
                    mrT[t] = mr; theta = max(theta, fixB - mr);
                    for (auto& [km, r] : xc) { double rr = beta * km + fxL[t] + cutRc(r.data(), r.size()); for (int k : r) rr -= uL[k]; if (rr < -1e-7) { int c = PL.add(t, r.data(), r.size(), km); toLP(c); nx++; } }
                }
                {   // at most K columns in total, at most tcap_t of type t: greedy allocation to the most negative min rc
                    vector<int> ord(T); iota(ord.begin(), ord.end(), 0); sort(ord.begin(), ord.end(), [&](int a, int b) { return mrT[a] < mrT[b]; });
                    int left = K; for (int t : ord) { if (left <= 0 || mrT[t] >= 0) break; int q = min(left, tcap[t]); L += q * mrT[t]; left -= q; }
                }
                if (beta == 0 && cfix > 0 && theta > 1e-9) { double LF = cfix * su / theta; bestFarley = max(bestFarley, LF); L = max(L, LF); }
                X->outCols = nullptr; xrounds++; xpriced += nx; if (L > bestL) { bestL = L; if (WENT) pc = pl; }
                if (bestL > stopL) break;
                if (nx == 0 && allEx) { converged = true; break; }
                if (nx == 0 && !added && negLP == 0) break;
                continue;
            }
            if (!added && out.empty()) break;
        }
        LPval = lp.obj();
    }
    // ---- 3-SRC separation on the current LP solution; returns number of cuts added (LP is rebuilt)
    vector<int> incCols;   // pool ids of the incumbent (crash basis after a rebuild)
    int separate(int maxNew, int maxPerOrder) {
        vector<pair<int, double>> xs;   // pool id, x
        for (int j = m; j < lp.ncols(); j++) { double x = lp.xOf(j); if (x > 1e-6 && poolOf[j] >= 0) xs.push_back({poolOf[j], x}); }
        int P = xs.size(); if (!P) return 0; int Wd = (P + 63) / 64;
        vector<double> w(N * N, 0); vector<uint64_t> ob(N * Wd, 0);
        for (int q = 0; q < P; q++) {
            const Col& col = PL.cols[xs[q].first]; const int* sq = PL.seq(col);
            for (int a = 0; a < col.len; a++) { ob[sq[a] * Wd + q / 64] |= 1ULL << (q & 63); for (int b = a + 1; b < col.len; b++) { int i = min(sq[a], sq[b]), j = max(sq[a], sq[b]); w[i * N + j] += xs[q].second; } }
        }
        vector<pair<double, array<int, 3>>> viol;
        set<array<int, 3>> have(cuts.begin(), cuts.end());
        for (int i = 0; i < N; i++) for (int j = i + 1; j < N; j++) {
            double wij = w[i * N + j]; if (wij < 1e-6) continue;
            for (int k = j + 1; k < N; k++) {
                double sum = wij + w[i * N + k] + w[j * N + k]; if (sum <= 1 + 1e-3) continue;
                double t = 0;
                for (int q = 0; q < Wd; q++) { uint64_t z = ob[i * Wd + q] & ob[j * Wd + q] & ob[k * Wd + q]; while (z) { int b = q * 64 + __builtin_ctzll(z); z &= z - 1; t += xs[b].second; } }
                double lhs = sum - 2 * t;
                if (lhs > 1 + 1e-3) { array<int, 3> a{i, j, k}; if (!have.count(a)) viol.push_back({-lhs, a}); }
            }
        }
        sort(viol.begin(), viol.end());
        vector<int> per(N, 0); for (auto& c : cuts) for (int q : c) per[q]++;
        int added = 0;
        for (auto& [v, a] : viol) {
            if (added >= maxNew || C() >= 64 * BW) break;
            if (per[a[0]] >= maxPerOrder || per[a[1]] >= maxPerOrder || per[a[2]] >= maxPerOrder) continue;
            cuts.push_back(a); for (int q : a) per[q]++; added++;
        }
        if (!added) return 0;
        cutsOfO.assign(N, {}); for (int c = 0; c < C(); c++) for (int q : cuts[c]) cutsOfO[q].push_back(c);
        ccnt.assign(C(), 0);
        // rebuild the master with the same columns + cut rows, crash from the incumbent
        vector<int> keep; for (int j = m; j < lp.ncols(); j++) if (poolOf[j] >= 0) keep.push_back(poolOf[j]);
        vector<char> rm(N); for (int k = 0; k < N; k++) rm[k] = rowIdx[k] >= 0;
        setup(rm, tcap, K, UB);
        for (int c : keep) toLP(c);
        vector<int> bs(m); for (int i = 0; i < m; i++) bs[i] = i;
        for (int c : incCols) { int j = toLP(c); bs[crashRow(c)] = j; }
        lp.setBasis(bs); converged = false;
        return added;
    }
    // DFS over columns with rc <= UB - LP, bound LP + sum rc
    vector<int> cand, bestSel, curSel, tcnt; vector<vector<int>> colsOf; double bestCost; long long nodes, nodeLim; Timer* tmr; double tE; lg::Bits target;
    void dfs(const lg::Bits& cov, int used, double cost, double rcs) {
        if (++nodes > nodeLim || ((nodes & 1023) == 0 && tmr->sec() > tE)) { nodes = nodeLim + 1; return; }
        if (cov == target) { if (cost < bestCost - 1e-9) { bestCost = cost; bestSel = curSel; } return; }
        if (used >= K) return;
        int bk = -1; int bc = INT_MAX;
        for (int k = 0; k < N; k++) if (rowIdx[k] >= 0 && !cov.has(k)) {
            int c = 0; for (int j : colsOf[k]) { const lg::Col& col = PL.cols[j]; if (!col.b.inter(cov) && tcnt[col.t] < TY.cnt[col.t]) { if (++c >= bc) break; } }
            if (c < bc) { bc = c; bk = k; if (c <= 1) break; }
        }
        if (bk < 0 || bc == 0) return;
        for (int j : colsOf[bk]) {
            const lg::Col& col = PL.cols[j];
            if (col.b.inter(cov) || tcnt[col.t] >= TY.cnt[col.t]) continue;
            double r2 = rcs + max(0.0, rcP[j]);
            if (LPval + r2 >= bestCost - 1e-6 || cost + col.km >= bestCost - 1e-6) continue;
            lg::Bits c2 = cov; c2.orWith(col.b); tcnt[col.t]++; curSel.push_back(j);
            dfs(c2, used + 1, cost + col.km, r2);
            curSel.pop_back(); tcnt[col.t]--;
            if (nodes > nodeLim) return;
        }
    }
    int nCand = 0; double FIXINT = l2::P("FIXINT", 0.99); long long fixNodes = 0; bool fixFound = false;
    bool sp(Timer& tm, double tEnd, long long nlim, int maxCand) {
        rcP.assign(PL.cols.size(), 1e18);
        vector<pair<double, int>> v; double gap = UB - LPval + 1e-6;
        for (int c = 0; c < (int)PL.cols.size(); c++) { double r = rcPool(c); rcP[c] = r; if (r <= gap) v.push_back({r, c}); }
        sort(v.begin(), v.end()); if ((int)v.size() > maxCand) v.resize(maxCand);
        nCand = v.size();
        colsOf.assign(N, {}); target.clear();
        for (int k = 0; k < N; k++) if (rowIdx[k] >= 0) target.set(k);
        for (auto& [r, c] : v) { const lg::Col& col = PL.cols[c]; const int* s = PL.seq(col); for (int i = 0; i < col.len; i++) colsOf[s[i]].push_back(c); }
        tcnt.assign(T, 0); bestSel.clear(); curSel.clear(); bestCost = UB - 1e-6; nodes = 0; nodeLim = nlim; tmr = &tm; tE = tEnd;
        if (FIXINT > 0) {   // first: fix the (near) integral LP columns and search the residual only
            lg::Bits cov; cov.clear(); double cost = 0; int used = 0;
            for (int j = m; j < lp.ncols(); j++) if (lp.xOf(j) >= FIXINT) { int c = poolOf[j]; const lg::Col& col = PL.cols[c]; if (col.b.inter(cov) || tcnt[col.t] >= TY.cnt[col.t]) continue; cov.orWith(col.b); cost += col.km; used++; tcnt[col.t]++; curSel.push_back(c); }
            nodeLim = nlim / 4; dfs(cov, used, cost, 0); fixNodes = nodes; fixFound = !bestSel.empty();
            tcnt.assign(T, 0); curSel.clear(); nodes = 0; nodeLim = nlim;
        }
        lg::Bits z; z.clear(); dfs(z, 0, 0, 0);
        return !bestSel.empty();
    }
    void toSol(const vector<int>& cs, l2::Sol& s) const {
        Routes Rt(Pb.V); vector<int> tc(T, 0);
        for (int c : cs) { const lg::Col& col = PL.cols[c]; int v = TY.vehs[col.t][tc[col.t]++]; const int* q = PL.seq(col); Rt[v].assign(q, q + col.len); }
        s.fromRoutes(Pb, Rt);
    }
    // valid global Lagrangian bound from current duals (needs all orders as rows)
    double bound(lg::XPricer& X, size_t cap, bool& exact) {
        vector<double> u(N, 0); double L = lp.pi[R + T] * K;
        for (int k = 0; k < N; k++) { u[k] = lp.pi[rowIdx[k]]; L += u[k]; }
        exact = true;
        for (int t = 0; t < T; t++) { bool comp; double mr = X.minrc(t, u.data(), cfix - lp.pi[R + T], cap, comp); if (!comp) exact = false; L += TY.cnt[t] * min(0.0, mr); }
        return L;
    }
};


// ---------------- KmSearch copy with column capture ----------------
struct CapKm {
    const Instance& I; const Prob& Pb; Rng& rng; Timer& tm; lu::LocalSearch LS; l2::Ruins RU; l2::Regret RG; l2::Undo U; vector<int> pool;
    Pool* PL = nullptr; const vector<double>* duals = nullptr; double pDual = l2::P("PDUAL", 0.1), dnoise = l2::P("DNOISE", 1.0), edgeD = 1; vector<pair<double, int>> dsc;
    double cbar = l2::P("CBAR2", 10), T0f = l2::P("T0", 5.0), Tff = l2::P("TF", 0.05), blink = l2::P("BLINK", 0.01);
    double pRoute = l2::P("PROUTE", 0.05), pPair = l2::P("PPAIR", 0.05); int lsBest = (int)l2::P("LSB", 1);
    long iters = 0;
    CapKm(const Instance& in, const Prob& p, Rng& r, Timer& t) : I(in), Pb(p), rng(r), tm(t), LS(in), RU(p) { pool.reserve(in.N); }
    void polish(l2::Sol& s, int cap, double tEnd) {
        lu::Sol x = l2::toLu(I, s);
        LS.run(x, rng, cap, &tm, tEnd);
        l2::Sol y; y.fromRoutes(Pb, x.r);
        if (y.better(s, Pb) || (y.penSum(Pb) == s.penSum(Pb) && y.used() == s.used() && y.kmTot() <= s.kmTot())) s = y;
        if (PL) PL->addSol(s);
    }
    void run(l2::Sol& best, double tEnd, long maxIt = -1, double T0x = -1, double Tfx = -1, int capX = -1, const l2::Sol* start = nullptr) {
        const int N = Pb.N; int cap = capX > 0 ? capX : best.used(); l2::Sol cur = start ? *start : best; RU.cbar = cbar;
        if (best.nUn() > 0 && capX <= 0) cap = Pb.V;
        const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30));
        double km0 = max(1.0, best.kmTot()); int served = N - best.nUn();
        double edge = km0 / max(1, served); edgeD = edge;
        const double T0 = (T0x > 0 ? T0x : T0f) * edge, Tf = (Tfx > 0 ? Tfx : Tff) * edge;
        double t1 = tm.sec(), span = max(1e-9, tEnd - t1), T = T0;
        double curCost = cur.cost(Pb), bestCost = best.cost(Pb);
        for (long it = 0;; it++) {
            if ((it & 15) == 0) {
                double el = tm.sec(); if (el > tEnd || (maxIt > 0 && it >= maxIt)) break;
                double x = (el - t1) / span; if (maxIt > 0) x = max(x, (double)it / maxIt);
                T = T0 * pow(Tf / T0, min(1.0, x));
            }
            iters++;
            U.begin(cur); pool.clear(); l2::collectAbsent(cur, pool);
            RU.clearMarks(); bool big = false;
            double uu = rng.uni();
            if (duals && rng.uni() < pDual) {   // dual-informed worst removal: orders whose detour exceeds their LP price
                dsc.clear();
                for (int v = 0; v < Pb.V; v++) for (int i = 0; i < cur.len[v]; i++) { int k = cur.r[v][i]; dsc.push_back({-(cur.remGain(Pb, v, i) - (*duals)[k]) - dnoise * edgeD * rng.uni(), k}); }
                int q = min((int)dsc.size(), qmin + rng.randint(qmax - qmin + 1));
                if (q > 0) { partial_sort(dsc.begin(), dsc.begin() + q, dsc.end()); for (int i = 0; i < q; i++) RU.add(dsc[i].second); }
            } else
            if (uu < pRoute) RU.route(cur, rng, qmin + rng.randint(qmax - qmin + 1));
            else if (uu < pRoute + pPair) { RU.routePair(cur, rng); big = true; }
            else RU.sisr(cur, rng);
            l2::removeSet(Pb, cur, RU.rem.data(), RU.rem.size(), pool, &U);
            if (big && rng.uni() < 0.5) { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, cur, pool, rng, 2, cap, W_VEHICLE, &U); }
            else { l2::sisrSort(Pb, pool, rng); l2::greedyInsert(Pb, cur, pool, rng, blink, cap, W_VEHICLE, &U); }
            double c = cur.cost(Pb);
            if (c < curCost - T * log(rng.uni() + 1e-300)) {
                curCost = c;
                if (PL && pool.empty()) for (int v = 0; v < Pb.V; v++) if (U.touched >> v & 1) PL->addSolRoute(cur, v);
                if (c < bestCost - 1e-9) {
                    best = cur; bestCost = c;
                    if (lsBest) { polish(best, cap, tEnd); double c2 = best.cost(Pb); if (c2 < bestCost - 1e-9) { bestCost = c2; cur = best; curCost = c2; } }
                }
            } else U.restore(Pb, cur);
        }
    }
};
} // namespace lg
