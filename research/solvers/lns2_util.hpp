// lns2_util: быстрые структуры для s30_alns2 / s31_sisr2 / s32_hgs2 / s33_sasisr.
// FleetRR — фаза сокращения парка (R&R + Σabsence), KmSearch — фаза км (SISR-отжиг + ЛП), см. logs/lns.md.
// Решение на фиксированных массивах (без аллокаций), разрушение-восстановление «на месте» с откатом только
// затронутых маршрутов, O(1)-проверка вставки через dep/lat, отсечение позиций по окну.
#pragma once
#include "lns_util.hpp"

namespace l2 {
constexpr int MAXV = 16, MAXL = 176, MAXN = 176;
constexpr double INF = 1e18, TOL = 1e-7;
static double P(const char* n, double d) { const char* e = getenv(n); return e ? atof(e) : d; }

struct Prob {
    const Instance& I; int N, V, S, M;
    const double* T[MAXV]; const double* D[MAXV]; int st[MAXV], mask[MAXV], mode[MAXV];
    double a[MAXN], b[MAXN], svc[MAXN], pen[MAXN]; int nd[MAXN], skill[MAXN], canMask[MAXN], nCan[MAXN];
    double farv[MAXN];
    vector<float> gd; vector<vector<int>> nbr; double maxD = 1, avgNb = 1;
    explicit Prob(const Instance& in) : I(in), N(in.N), V(in.V), S(in.S), M(in.M) {
        if (N > MAXN - 2 || V > MAXV) { fprintf(stderr, "too large\n"); exit(1); }
        for (int v = 0; v < V; v++) { mode[v] = I.veh[v].mode; st[v] = I.veh[v].start; mask[v] = I.veh[v].mask; T[v] = I.T[mode[v]].data(); D[v] = I.D[mode[v]].data(); }
        for (int k = 0; k < N; k++) {
            a[k] = I.ord[k].a; svc[k] = I.ord[k].svc; b[k] = min(I.ord[k].b, SHIFT - svc[k]); pen[k] = I.penalty(k);
            nd[k] = S + k; skill[k] = I.ord[k].skill; canMask[k] = 0; nCan[k] = 0; farv[k] = 1e9;
            for (int v = 0; v < V; v++) if (I.can(v, k)) { canMask[k] |= 1 << v; nCan[k]++; farv[k] = min(farv[k], I.d(v, st[v], nd[k])); }
        }
        vector<int> modes; for (int v = 0; v < V; v++) if (find(modes.begin(), modes.end(), mode[v]) == modes.end()) modes.push_back(mode[v]);
        gd.assign(N * N, 0);
        for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) {
            double s = 0; int x = nd[i], y = nd[j];
            for (int m : modes) s += 0.5 * (I.D[m][x * M + y] + I.D[m][y * M + x]);
            gd[i * N + j] = i == j ? 0 : s / modes.size(); maxD = max(maxD, (double)gd[i * N + j]);
        }
        nbr.assign(N, {}); double sn = 0;
        for (int i = 0; i < N; i++) {
            for (int j = 0; j < N; j++) if (j != i) nbr[i].push_back(j);
            sort(nbr[i].begin(), nbr[i].end(), [&](int x, int y) { return gd[i * N + x] < gd[i * N + y]; });
            if (N > 1) sn += gd[i * N + nbr[i][0]];
        }
        avgNb = max(1e-3, sn / max(1, N));
    }
    inline double t(int v, int i, int j) const { return T[v][i * M + j]; }
    inline double d(int v, int i, int j) const { return D[v][i * M + j]; }
    inline bool can(int v, int k) const { return canMask[k] >> v & 1; }
    inline double g(int i, int j) const { return gd[i * N + j]; }
};

struct Sol {
    int V = 0, N = 0;
    int len[MAXV]; int r[MAXV][MAXL]; double dep[MAXV][MAXL], lat[MAXV][MAXL], km[MAXV];
    int rt[MAXN];
    void init(const Prob& P) { V = P.V; N = P.N; for (int v = 0; v < V; v++) { len[v] = 0; km[v] = 0; } for (int k = 0; k < N; k++) rt[k] = -1; }
    int used() const { int u = 0; for (int v = 0; v < V; v++) u += len[v] > 0; return u; }
    double kmTot() const { double s = 0; for (int v = 0; v < V; v++) s += km[v]; return s; }
    double penSum(const Prob& P) const { double p = 0; for (int k = 0; k < N; k++) if (rt[k] < 0) p += P.pen[k]; return p; }
    int nUn() const { int c = 0; for (int k = 0; k < N; k++) c += rt[k] < 0; return c; }
    double cost(const Prob& P) const { return penSum(P) * W_UNSERVED + used() * W_VEHICLE + kmTot(); }
    bool better(const Sol& o, const Prob& P) const {
        double p = penSum(P), q = o.penSum(P); if (fabs(p - q) > 1e-9) return p < q;
        int x = used(), y = o.used(); if (x != y) return x < y;
        return kmTot() < o.kmTot() - 1e-9;
    }
    // копирование только используемой части
    void copyFrom(const Sol& o) {
        V = o.V; N = o.N;
        for (int v = 0; v < V; v++) {
            int L = len[v] = o.len[v]; km[v] = o.km[v];
            memcpy(r[v], o.r[v], L * sizeof(int)); memcpy(dep[v], o.dep[v], L * sizeof(double)); memcpy(lat[v], o.lat[v], L * sizeof(double));
        }
        memcpy(rt, o.rt, N * sizeof(int));
    }
    Sol() {}
    Sol(const Sol& o) { copyFrom(o); }
    Sol& operator=(const Sol& o) { if (this != &o) copyFrom(o); return *this; }
    // пересчёт расписания; недопустимые выкидываются (в out)
    void rebuild(const Prob& P, int v, vector<int>* out = nullptr) {
        int L = len[v]; int* R = r[v]; double* Dp = dep[v];
        double tm = 0, dist = 0; int prev = P.st[v], w = 0; const double* T = P.T[v]; const double* D = P.D[v]; int M = P.M;
        for (int i = 0; i < L; i++) {
            int k = R[i], n = P.nd[k];
            double beg = max(tm + T[prev * M + n], P.a[k]);
            if (!P.can(v, k) || beg > P.b[k] + TOL) { rt[k] = -1; if (out) out->push_back(k); continue; }
            tm = beg + P.svc[k]; dist += D[prev * M + n]; prev = n; R[w] = k; Dp[w] = tm; rt[k] = v; w++;
        }
        len[v] = w; km[v] = dist; double* Lt = lat[v];
        for (int i = w - 1; i >= 0; i--) {
            int k = R[i]; double l = P.b[k];
            if (i + 1 < w) l = min(l, Lt[i + 1] - P.svc[k] - T[P.nd[k] * M + P.nd[R[i + 1]]]);
            Lt[i] = l;
        }
    }
    void rebuildAll(const Prob& P) { for (int v = 0; v < V; v++) rebuild(P, v); }
    // прирост км при вставке k в v на позицию p
    inline double insDelta(const Prob& P, int v, int p, int k) const {
        int prev; double tm;
        if (p == 0) { tm = 0; prev = P.st[v]; } else { tm = dep[v][p - 1]; prev = P.nd[r[v][p - 1]]; }
        int nk = P.nd[k]; const double* T = P.T[v]; const double* D = P.D[v]; int M = P.M;
        double beg = max(tm + T[prev * M + nk], P.a[k]); if (beg > P.b[k] + TOL) return INF;
        double e = beg + P.svc[k]; double dd = D[prev * M + nk];
        if (p < len[v]) {
            int nx = P.nd[r[v][p]];
            if (e + T[nk * M + nx] > lat[v][p] + TOL) return INF;
            dd += D[nk * M + nx] - D[prev * M + nx];
        }
        return dd;
    }
    void insertAt(const Prob& P, int v, int p, int k) {
        int L = len[v]; memmove(r[v] + p + 1, r[v] + p, (L - p) * sizeof(int)); r[v][p] = k; len[v] = L + 1; rt[k] = v; rebuild(P, v);
    }
    int posOf(int k) const { int v = rt[k]; for (int i = 0; i < len[v]; i++) if (r[v][i] == k) return i; return -1; }
    inline double remGain(const Prob& P, int v, int i) const {
        int prev = i ? P.nd[r[v][i - 1]] : P.st[v]; int n = P.nd[r[v][i]];
        double g = P.d(v, prev, n);
        if (i + 1 < len[v]) { int nx = P.nd[r[v][i + 1]]; g += P.d(v, n, nx) - P.d(v, prev, nx); }
        return g;
    }
    Routes routes() const { Routes R(V); for (int v = 0; v < V; v++) R[v].assign(r[v], r[v] + len[v]); return R; }
    void fromRoutes(const Prob& P, const Routes& R) {
        init(P);
        for (int v = 0; v < V && v < (int)R.size(); v++) { len[v] = R[v].size(); for (int i = 0; i < len[v]; i++) r[v][i] = R[v][i]; }
        rebuildAll(P);
    }
};

// Откат: сохраняются rt и затронутые маршруты.
struct Undo {
    uint32_t touched = 0; int rtb[MAXN]; int lenb[MAXV]; int rb[MAXV][MAXL]; double kmb[MAXV];
    void begin(const Sol& s) { touched = 0; memcpy(rtb, s.rt, s.N * sizeof(int)); }
    inline void save(const Sol& s, int v) {
        if (touched >> v & 1) return; touched |= 1u << v; lenb[v] = s.len[v]; kmb[v] = s.km[v]; memcpy(rb[v], s.r[v], s.len[v] * sizeof(int));
    }
    void restore(const Prob& P, Sol& s) {
        for (int v = 0; v < s.V; v++) if (touched >> v & 1) { s.len[v] = lenb[v]; memcpy(s.r[v], rb[v], lenb[v] * sizeof(int)); s.rebuild(P, v); }
        memcpy(s.rt, rtb, s.N * sizeof(int)); touched = 0;
    }
};

// удалить набор (вызов save для маршрутов), пересчитать; выпавшие — в pool
inline void removeSet(const Prob& P, Sol& s, const int* rem, int n, vector<int>& pool, Undo* U) {
    uint32_t tch = 0;
    for (int q = 0; q < n; q++) { int k = rem[q]; int v = s.rt[k]; if (v < 0) continue; if (U) U->save(s, v); tch |= 1u << v; s.rt[k] = -2 - v; pool.push_back(k); }
    for (int v = 0; v < s.V; v++) if (tch >> v & 1) {
        int w = 0; for (int i = 0; i < s.len[v]; i++) { int k = s.r[v][i]; if (s.rt[k] >= 0) s.r[v][w++] = k; else s.rt[k] = -1; }
        s.len[v] = w; s.rebuild(P, v, &pool);
    }
}
inline void collectAbsent(const Sol& s, vector<int>& pool) { for (int k = 0; k < s.N; k++) if (s.rt[k] < 0) pool.push_back(k); }

// ---------- восстановление ----------
// жадная вставка с «морганиями»; cap — максимум бригад, openCost — надбавка за новую бригаду
inline void greedyInsert(const Prob& P, Sol& s, vector<int>& pool, Rng& rng, double blink, int cap, double openCost, Undo* U, double noise = 0) {
    int used = s.used(); int w = 0;
    uint32_t blinkTh = (uint32_t)(blink * 4294967296.0);
    for (int q = 0; q < (int)pool.size(); q++) {
        int k = pool[q]; double best = INF; int bv = -1, bp = -1; double bk = P.b[k];
        for (int v = 0; v < P.V; v++) {
            if (!P.can(v, k)) continue;
            int L = s.len[v]; bool empty = L == 0; if (empty && used >= cap) continue;
            double add = empty ? openCost : 0;
            if (add >= best) continue;
            for (int p = 0; p <= L; p++) {
                if (p > 0 && s.dep[v][p - 1] > bk) break;  // дальше только позже
                if (blinkTh && (uint32_t)rng.next() < blinkTh) continue;
                double dd = s.insDelta(P, v, p, k); if (dd >= INF) continue;
                dd += add; if (noise > 0) dd = max(0.0, dd + noise * (2 * rng.uni() - 1));
                if (dd < best) { best = dd; bv = v; bp = p; }
            }
        }
        if (bv < 0) { pool[w++] = k; continue; }
        if (s.len[bv] == 0) used++;
        if (U) U->save(s, bv);
        s.insertAt(P, bv, bp, k);
    }
    pool.resize(w);
}

// regret-K (K=1 — лучшая глобально). Кэш по (заявка, маршрут).
struct Regret {
    vector<double> bc; vector<int> bp; vector<char> done;
    void run(const Prob& P, Sol& s, vector<int>& pool, Rng& rng, int K, int cap, double openCost, Undo* U, double noise = 0) {
        int m = pool.size(), V = P.V; if (!m) return;
        bc.assign(m * V, INF); bp.assign(m * V, -1); done.assign(m, 0);
        auto calc = [&](int i, int v) {
            int k = pool[i]; double best = INF; int bpp = -1;
            if (P.can(v, k)) {
                int L = s.len[v]; bool empty = L == 0; double bk = P.b[k];
                for (int p = 0; p <= L; p++) {
                    if (p > 0 && s.dep[v][p - 1] > bk) break;
                    double dd = s.insDelta(P, v, p, k); if (dd >= INF) continue;
                    if (empty) dd += openCost;
                    if (noise > 0) dd = max(0.0, dd + noise * (2 * rng.uni() - 1));
                    if (dd < best) { best = dd; bpp = p; }
                }
            }
            bc[i * V + v] = best; bp[i * V + v] = bpp;
        };
        for (int i = 0; i < m; i++) for (int v = 0; v < V; v++) calc(i, v);
        int used = s.used(), remaining = m; int KK = max(1, min(K, 4));
        while (remaining) {
            int bi = -1, bV = -1; double bReg = -INF, bC = INF;
            for (int i = 0; i < m; i++) if (!done[i]) {
                double c[4] = {INF, INF, INF, INF}; int cv = -1;
                for (int v = 0; v < V; v++) {
                    double x = bc[i * V + v]; if (x >= INF) continue;
                    if (s.len[v] == 0 && used >= cap) continue;
                    if (x < c[0]) cv = v;
                    for (int j = 0; j < KK; j++) if (x < c[j]) { for (int q = KK - 1; q > j; q--) c[q] = c[q - 1]; c[j] = x; break; }
                }
                if (cv < 0) continue;
                double reg = 0;
                if (KK >= 2) for (int j = 1; j < KK; j++) reg += (c[j] >= INF ? 1e5 : c[j]) - c[0]; else reg = -c[0];
                if (reg > bReg + 1e-9 || (fabs(reg - bReg) <= 1e-9 && c[0] < bC)) { bReg = reg; bC = c[0]; bi = i; bV = cv; }
            }
            if (bi < 0) break;
            int k = pool[bi]; bool wasEmpty = s.len[bV] == 0;
            if (U) U->save(s, bV);
            s.insertAt(P, bV, bp[bi * V + bV], k); done[bi] = 1; remaining--;
            if (wasEmpty) used++;
            for (int i = 0; i < m; i++) if (!done[i]) calc(i, bV);
        }
        int w = 0; for (int i = 0; i < m; i++) if (!done[i]) pool[w++] = pool[i];
        pool.resize(w);
    }
};

// ---------- разрушение ----------
struct Ruins {
    const Prob& P; vector<int> rem; vector<char> mark; vector<pair<double, int>> tmp; vector<int> served;
    double cbar = 10, Lmax = 10, alphaSplit = 0.5, splitCont = 0.5;
    explicit Ruins(const Prob& p) : P(p), mark(p.N, 0) { rem.reserve(p.N); }
    void clearMarks() { for (int k : rem) mark[k] = 0; rem.clear(); }
    void add(int k) { if (!mark[k]) { mark[k] = 1; rem.push_back(k); } }
    // SISR (Christiaens & Vanden Berghe)
    void sisr(const Sol& s, Rng& rng, int seedCust = -1) {
        int V = P.V; double avgL = 0; int nr = 0; for (int v = 0; v < V; v++) if (s.len[v]) { avgL += s.len[v]; nr++; }
        if (!nr) return; avgL /= nr;
        double lsmax = min(Lmax, avgL), ksmax = 4 * cbar / (1 + lsmax) - 1;
        int ks = (int)(rng.uni() * ksmax) + 1;
        int seed = seedCust >= 0 ? seedCust : rng.randint(P.N);
        uint32_t ruined = 0;
        auto proc = [&](int c) {
            int v = s.rt[c]; if (v < 0 || (ruined >> v & 1) || mark[c]) return;
            const int* R = s.r[v]; int L = s.len[v];
            int ltmax = (int)min((double)L, lsmax); int lt = (int)(rng.uni() * ltmax) + 1;
            int pc = 0; while (R[pc] != c) pc++;
            if (rng.uni() < alphaSplit || lt >= L) {
                int lo = max(0, pc - lt + 1), hi = min(pc, L - lt); int st = lo + rng.randint(hi - lo + 1);
                for (int x = st; x < st + lt; x++) add(R[x]);
            } else {
                int m = 1; while (lt + m < L && rng.uni() < splitCont) m++;
                int tot = lt + m; int lo = max(0, pc - tot + 1), hi = min(pc, L - tot); int st = lo + rng.randint(hi - lo + 1);
                int keepSt = st + rng.randint(lt + 1);
                for (int x = st; x < st + tot; x++) if (x < keepSt || x >= keepSt + m) add(R[x]);
            }
            ruined |= 1u << v; ks--;
        };
        proc(seed);
        for (int c : P.nbr[seed]) { if (ks <= 0) break; proc(c); }
    }
    void servedList(const Sol& s) { served.clear(); for (int k = 0; k < P.N; k++) if (s.rt[k] >= 0) served.push_back(k); }
    void random(const Sol& s, Rng& rng, int q) {
        servedList(s); q = min(q, (int)served.size());
        for (int i = 0; i < q; i++) { int j = i + rng.randint(served.size() - i); swap(served[i], served[j]); add(served[i]); }
    }
    void worst(const Sol& s, Rng& rng, int q) {
        tmp.clear();
        for (int v = 0; v < P.V; v++) for (int i = 0; i < s.len[v]; i++) tmp.push_back({-(s.remGain(P, v, i) * (0.8 + 0.4 * rng.uni())), s.r[v][i]});
        q = min(q, (int)tmp.size()); if (!q) return;
        partial_sort(tmp.begin(), tmp.begin() + q, tmp.end());
        for (int i = 0; i < q; i++) add(tmp[i].second);
    }
    void shaw(const Sol& s, Rng& rng, int q) {
        servedList(s); if (served.empty()) return; q = min(q, (int)served.size());
        add(served[rng.randint(served.size())]);
        while ((int)rem.size() < q) {
            int r0 = rem[rng.randint(rem.size())]; tmp.clear();
            for (int k : served) if (!mark[k]) {
                double rel = 9 * P.g(r0, k) / P.maxD + 3 * fabs(P.a[r0] - P.a[k]) / SHIFT + 2 * (P.skill[r0] != P.skill[k]) + 0.5 * (s.rt[r0] != s.rt[k]);
                tmp.push_back({rel, k});
            }
            if (tmp.empty()) break;
            int j = (int)(pow(rng.uni(), 6.0) * tmp.size());
            nth_element(tmp.begin(), tmp.begin() + j, tmp.end());
            add(tmp[j].second);
        }
    }
    void route(const Sol& s, Rng& rng, int q) {
        int rs[MAXV], n = 0; for (int v = 0; v < P.V; v++) if (s.len[v]) rs[n++] = v;
        if (!n) return;
        sort(rs, rs + n, [&](int x, int y) { return s.len[x] < s.len[y]; });
        int v = rs[(int)(pow(rng.uni(), 2.0) * n)];
        for (int i = 0; i < s.len[v]; i++) add(s.r[v][i]);
        if (rng.uni() < 0.5) {
            int extra = rng.randint(max(1, q / 2)) + 1; int sd = s.r[v][rng.randint(s.len[v])];
            for (int k : P.nbr[sd]) { if (extra <= 0) break; if (s.rt[k] >= 0 && !mark[k]) { add(k); extra--; } }
        }
    }
    // два маршрута: длинный (вес ~ км) и маршрут бригады, чей старт ближе всего к случайной заявке первого
    void routePair(const Sol& s, Rng& rng) {
        double sw = 0; for (int v = 0; v < P.V; v++) if (s.len[v]) sw += s.km[v] + 1e-3;
        if (sw <= 0) return;
        double x = rng.uni() * sw; int v1 = -1;
        for (int v = 0; v < P.V; v++) if (s.len[v]) { x -= s.km[v] + 1e-3; v1 = v; if (x <= 0) break; }
        int c = s.r[v1][rng.randint(s.len[v1])]; int v2 = -1; double bd = INF;
        for (int v = 0; v < P.V; v++) if (v != v1 && s.len[v] && P.can(v, c)) { double d = P.d(v, P.st[v], P.nd[c]) * (0.8 + 0.4 * rng.uni()); if (d < bd) { bd = d; v2 = v; } }
        for (int i = 0; i < s.len[v1]; i++) add(s.r[v1][i]);
        if (v2 >= 0) for (int i = 0; i < s.len[v2]; i++) add(s.r[v2][i]);
    }
    void slot(const Sol& s, Rng& rng, int q) {
        servedList(s); if (served.empty()) return;
        int sd = served[rng.randint(served.size())]; double a = P.a[sd], b = P.b[sd]; tmp.clear();
        for (int k : served) if (P.a[k] <= b && P.b[k] >= a) tmp.push_back({P.g(sd, k), k});
        sort(tmp.begin(), tmp.end());
        for (int i = 0; i < (int)tmp.size() && i < q; i++) add(tmp[i].second);
    }
};

// порядок вставки SISR: случайно 4, по ширине окна/дефициту навыка 4, дальние 2, ближние 1
inline void sisrSort(const Prob& P, vector<int>& pool, Rng& rng) {
    for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
    int w = rng.randint(11);
    if (w < 4) return;
    if (w < 8) stable_sort(pool.begin(), pool.end(), [&](int x, int y) {
        if (P.nCan[x] != P.nCan[y] && (P.nCan[x] < 4 || P.nCan[y] < 4)) return P.nCan[x] < P.nCan[y];
        return P.b[x] - P.a[x] < P.b[y] - P.a[y]; });
    else if (w < 10) stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return P.farv[x] > P.farv[y]; });
    else stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return P.farv[x] < P.farv[y]; });
}

// конвертация в lu::Sol для полировки существующим локальным поиском
inline lu::Sol toLu(const Instance& I, const Sol& s) { lu::Sol x(I); for (int v = 0; v < s.V; v++) x.r[v].assign(s.r[v], s.r[v] + s.len[v]); x.rebuildAll(); return x; }
inline void fromLu(const Prob& P, Sol& s, const lu::Sol& x) { s.fromRoutes(P, x.r); }

inline int pickRouteToRemove(const Prob& P, const Sol& s, Rng& rng, const vector<double>& absence) {
    int best = -1; double bv = INF;
    for (int v = 0; v < P.V; v++) if (s.len[v]) {
        double val = 0; for (int i = 0; i < s.len[v]; i++) val += absence[s.r[v][i]];
        val += 1e-3 * s.len[v] + 1e-6 * rng.uni();
        if (val < bv) { bv = val; best = v; }
    }
    return best;
}

inline Routes finalizeR(const Instance& I, const Sol& s) {
    lu::Sol x = toLu(I, s); return lu::finalize(I, x);
}

struct Adaptive {
    vector<double> w, sc; vector<int> cnt;
    explicit Adaptive(int n) : w(n, 1), sc(n, 0), cnt(n, 0) {}
    int pick(Rng& rng) const { double s = 0; for (double x : w) s += x; double u = rng.uni() * s; for (int i = 0; i < (int)w.size(); i++) { u -= w[i]; if (u <= 0) return i; } return w.size() - 1; }
    void add(int i, double x) { sc[i] += x; cnt[i]++; }
    void update(double r) { for (int i = 0; i < (int)w.size(); i++) { if (cnt[i]) w[i] = w[i] * (1 - r) + r * sc[i] / cnt[i]; w[i] = max(w[i], 0.05); sc[i] = 0; cnt[i] = 0; } }
};
// ---------- фаза км: SISR-отжиг при фиксированном парке + полировка рекордов ----------
struct KmSearch {
    const Instance& I; const Prob& Pb; Rng& rng; Timer& tm; lu::LocalSearch LS; Ruins RU; Regret RG; Undo U; vector<int> pool;
    double cbar = P("CBAR2", 10), T0f = P("T0", 5.0), Tff = P("TF", 0.05), blink = P("BLINK", 0.01);
    double pRoute = P("PROUTE", 0.05), pPair = P("PPAIR", 0.05); int lsBest = (int)P("LSB", 1);
    long iters = 0; int kmOps = (int)P("KMOPS", 0); Adaptive AD{6}, AC{2};
    KmSearch(const Instance& in, const Prob& p, Rng& r, Timer& t) : I(in), Pb(p), rng(r), tm(t), LS(in), RU(p) { pool.reserve(in.N); }
    void polish(Sol& s, int cap, double tEnd) {
        lu::Sol x = toLu(I, s);
        LS.run(x, rng, cap, &tm, tEnd);
        Sol y; y.fromRoutes(Pb, x.r);
        if (y.better(s, Pb) || (y.penSum(Pb) == s.penSum(Pb) && y.used() == s.used() && y.kmTot() <= s.kmTot())) s = y;
    }
    // best — вход/выход; поиск до tEnd; temperature в единицах среднего ребра
    void run(Sol& best, double tEnd, long maxIt = -1, double T0x = -1, double Tfx = -1, int capX = -1) {
        const int N = Pb.N; int cap = capX > 0 ? capX : best.used(); Sol cur = best; RU.cbar = cbar;
        if (best.nUn() > 0 && capX <= 0) cap = Pb.V;   // не всё обслужено: разрешаем открывать бригады (штраф >> бригада)
        const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30));
        double km0 = max(1.0, best.kmTot()); int served = N - best.nUn();
        double edge = km0 / max(1, served);
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
            U.begin(cur); pool.clear(); collectAbsent(cur, pool);
            RU.clearMarks(); int dop = -1, cop = -1; bool big = false;
            if (kmOps) {
                dop = AD.pick(rng); cop = AC.pick(rng); int q = qmin + rng.randint(qmax - qmin + 1);
                switch (dop) { case 0: RU.sisr(cur, rng); break; case 1: RU.random(cur, rng, q); break; case 2: RU.worst(cur, rng, q); break;
                               case 3: RU.shaw(cur, rng, q); break; case 4: RU.route(cur, rng, q); break; default: RU.routePair(cur, rng); }
                removeSet(Pb, cur, RU.rem.data(), RU.rem.size(), pool, &U);
                if (cop == 1) { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, cur, pool, rng, 2, cap, W_VEHICLE, &U); }
                else { sisrSort(Pb, pool, rng); greedyInsert(Pb, cur, pool, rng, blink, cap, W_VEHICLE, &U); }
            } else {
                double u = rng.uni();
                if (u < pRoute) RU.route(cur, rng, qmin + rng.randint(qmax - qmin + 1));
                else if (u < pRoute + pPair) { RU.routePair(cur, rng); big = true; }
                else RU.sisr(cur, rng);
                removeSet(Pb, cur, RU.rem.data(), RU.rem.size(), pool, &U);
                if (big && rng.uni() < 0.5) { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, cur, pool, rng, 2, cap, W_VEHICLE, &U); }
                else { sisrSort(Pb, pool, rng); greedyInsert(Pb, cur, pool, rng, blink, cap, W_VEHICLE, &U); }
            }
            double c = cur.cost(Pb); double score = 0;
            if (c < curCost - T * log(rng.uni() + 1e-300)) {
                score = c < curCost - 1e-9 ? 9 : 3;
                curCost = c;
                if (c < bestCost - 1e-9) { score = 33;
                    best = cur; bestCost = c;
                    if (lsBest) { polish(best, cap, tEnd); double c2 = best.cost(Pb); if (c2 < bestCost - 1e-9) { bestCost = c2; cur = best; curCost = c2; } }
                }
            } else U.restore(Pb, cur);
            if (kmOps) { AD.add(dop, score); AC.add(cop, score); if (iters % 200 == 0) { AD.update(0.1); AC.update(0.1); } }
        }
    }
};
// ---------- фаза парка: R&R с Σabsence (SISR/ALNS-стиль), адаптивный выбор операторов ----------
struct FleetRR {
    const Instance& I; const Prob& Pb; Rng& rng; Timer& tm; KmSearch& KS; Ruins RU; Regret RG; Undo U; vector<int> pool; vector<double> absence;
    enum { D_SISR, D_RANDOM, D_WORST, D_SHAW, D_ROUTE, D_SLOT, ND };
    enum { C_GREEDY, C_REG2, C_REG3, NC };
    Adaptive AD{ND}, AC{NC};
    double blink = P("BLINK", 0.01), ernd = P("ERND", 0.5); int fleetOps = (int)P("FOPS", 1); long ERST = (long)P("ERST", 0);
    long iters = 0; int nAttempt = 0; bool stopOnSuccess = false; vector<int> prevAbs; int freshAbs = (int)P("FRESH", 1);
    FleetRR(const Instance& in, const Prob& p, Rng& r, Timer& t, KmSearch& ks) : I(in), Pb(p), rng(r), tm(t), KS(ks), RU(p), absence(in.N, 0) {
        pool.reserve(in.N); RU.cbar = P("CBAR", 10); RU.Lmax = P("LMAX", 10);
    }
    double sumAbs(const Sol& s) { double x = 0; for (int k = 0; k < Pb.N; k++) if (s.rt[k] < 0) x += absence[k]; return x; }
    void ruinOp(Sol& s, int op, int q) {
        RU.clearMarks();
        switch (op) {
        case D_SISR: RU.sisr(s, rng); break;
        case D_RANDOM: RU.random(s, rng, q); break;
        case D_WORST: RU.worst(s, rng, q); break;
        case D_SHAW: RU.shaw(s, rng, q); break;
        case D_ROUTE: RU.route(s, rng, q); break;
        case D_SLOT: RU.slot(s, rng, q); break;
        }
        removeSet(Pb, s, RU.rem.data(), RU.rem.size(), pool, &U);
    }
    int fmode = (int)P("FMODE", 1); Adaptive A5{5}, AN{2}, A4{4};
    void recreateOp(Sol& s, int op, int cap, double noise = 0) {
        if (fmode == 2 && op == 0) { sisrSort(Pb, pool, rng); greedyInsert(Pb, s, pool, rng, blink, cap, W_VEHICLE, &U); return; }
        if (fmode == 2) { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, s, pool, rng, op, cap, W_VEHICLE, &U, noise); return; }
        if (fmode == 1) { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, s, pool, rng, op + 1, cap, W_VEHICLE, &U, noise); return; }
        if (op == C_GREEDY) { sisrSort(Pb, pool, rng); greedyInsert(Pb, s, pool, rng, blink, cap, W_VEHICLE, &U); }
        else { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, s, pool, rng, op == C_REG2 ? 2 : 3, cap, W_VEHICLE, &U); }
    }
    // best — вход/выход. До tServe пытаемся обслужить всё, затем удаляем маршруты. Выход в tEnd.
    void run(Sol& best, double tServe, double tEnd, double tPolish, long maxIt = -1) {
        long it0 = iters;
        const int N = Pb.N;
        const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30));
        Sol cur = best; int cap = Pb.V; bool elim = best.nUn() == 0; long attemptStart = 0;
        auto startElim = [&] {
            cur = best; cap = best.used() - 1; attemptStart = iters;
            int v = pickRouteToRemove(Pb, cur, rng, absence);
            if (nAttempt++ > 0 && rng.uni() < ernd) {   // случайный маршрут, вес 1/len^2
                double sw = 0, w[MAXV]; for (int u = 0; u < Pb.V; u++) { w[u] = cur.len[u] ? 1.0 / (cur.len[u] * cur.len[u]) : 0; sw += w[u]; }
                double x = rng.uni() * sw; for (int u = 0; u < Pb.V; u++) { x -= w[u]; if (w[u] > 0 && x <= 0) { v = u; break; } }
            }
            vector<int> tmp; removeSet(Pb, cur, cur.r[v], cur.len[v], tmp, nullptr);
        };
        if (elim && best.used() > 1) startElim(); else if (elim) cap = 0;
        double curPen = cur.penSum(Pb), curAbs = sumAbs(cur);
        while (cap > 0 && tm.sec() < tEnd && (maxIt < 0 || iters - it0 < maxIt)) {
            if (!elim && tm.sec() > tServe) { elim = true; startElim(); curPen = cur.penSum(Pb); curAbs = sumAbs(cur); }
            iters++;
            if (elim && ERST > 0 && iters - attemptStart > ERST) { startElim(); curPen = cur.penSum(Pb); curAbs = sumAbs(cur); }
            int dop = fleetOps ? AD.pick(rng) : D_SISR, cop = fleetOps ? AC.pick(rng) : C_GREEDY, nop = 0;
            if (fmode == 1) { dop = 1 + A5.pick(rng); nop = AN.pick(rng); }
            if (fmode == 2) { dop = AD.pick(rng); cop = A4.pick(rng); nop = AN.pick(rng); }
            U.begin(cur); pool.clear(); collectAbsent(cur, pool); prevAbs.assign(pool.begin(), pool.end());
            ruinOp(cur, dop, qmin + rng.randint(qmax - qmin + 1));
            recreateOp(cur, cop, cap, nop ? 0.025 * Pb.maxD : 0);
            for (int k : pool) absence[k] += 1;
            if (freshAbs) { curAbs = 0; for (int k : prevAbs) curAbs += absence[k]; }
            double p = cur.penSum(Pb), a = sumAbs(cur), score = 0;
            if (cur.better(best, Pb)) {
                score = 33; best = cur; KS.polish(best, cap, tPolish);
                if (getenv("DEBUG")) fprintf(stderr, "  RR t=%.3f it=%ld used=%d un=%d km=%.1f\n", tm.sec(), iters, best.used(), best.nUn(), best.kmTot());
                if (best.nUn() == 0 || elim) { elim = true; if (best.used() <= 1 || stopOnSuccess) break; startElim(); }
                else cur = best;
                curPen = cur.penSum(Pb); curAbs = sumAbs(cur);
            } else if (p < curPen - 1e-9) { score = 9; curPen = p; curAbs = a; }
            else if (a < curAbs) { score = 13; curPen = p; curAbs = a; }
            else U.restore(Pb, cur);
            if (fmode == 2) { AD.add(dop, score); A4.add(cop, score); AN.add(nop, score); if (iters % 100 == 0) { AD.update(0.1); A4.update(0.1); AN.update(0.1); } }
            else if (fmode == 1) { A5.add(dop - 1, score); AC.add(cop, score); AN.add(nop, score); if (iters % 100 == 0) { A5.update(0.1); AC.update(0.1); AN.update(0.1); } }
            else if (fleetOps) { AD.add(dop, score); AC.add(cop, score); if (iters % 100 == 0) { AD.update(0.1); AC.update(0.1); } }
        }
    }
};
} // namespace l2
