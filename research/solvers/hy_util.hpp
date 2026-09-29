// Общие структуры для s12_alns / s13_sisr / s14_hgs_lite.
// Sol: маршруты + прямые времена окончания работ (dep) и обратные «самые поздние начала» (lat),
// что даёт проверку вставки/удаления/обмена за O(1). Плюс операторы восстановления и быстрый локальный поиск.
#pragma once
#include "common.hpp"

namespace hy {
constexpr double INF = 1e18;
constexpr double TOL = 1e-7;   // строже EPS валидатора (1e-6)

// ---------- предвычисления по задаче ----------
struct Ctx {
    const Instance& I; int N, V;
    vector<double> gd;            // N*N симметричное «общее» расстояние между заявками
    vector<vector<int>> nbr;      // соседи по gd (по возрастанию, без себя)
    vector<double> farv;          // расстояние от ближайшей подходящей стартовой точки
    vector<int> nCan;             // число бригад с нужным навыком
    double maxD = 1;
    explicit Ctx(const Instance& in) : I(in), N(in.N), V(in.V) {
        vector<int> modes; for (auto& v : I.veh) modes.push_back(v.mode);
        sort(modes.begin(), modes.end()); modes.erase(unique(modes.begin(), modes.end()), modes.end());
        gd.assign(N * N, 0);
        for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) {
            double s = 0; int a = I.node(i), b = I.node(j);
            for (int m : modes) s += 0.5 * (I.D[m][a * I.M + b] + I.D[m][b * I.M + a]);
            gd[i * N + j] = i == j ? 0 : s / modes.size(); maxD = max(maxD, gd[i * N + j]);
        }
        nbr.assign(N, {});
        for (int i = 0; i < N; i++) {
            for (int j = 0; j < N; j++) if (j != i) nbr[i].push_back(j);
            sort(nbr[i].begin(), nbr[i].end(), [&](int x, int y) { return gd[i * N + x] < gd[i * N + y]; });
        }
        farv.assign(N, 1e9); nCan.assign(N, 0);
        for (int k = 0; k < N; k++) for (int v = 0; v < V; v++) if (I.can(v, k)) {
            nCan[k]++; farv[k] = min(farv[k], I.d(v, I.veh[v].start, I.node(k)));
        }
    }
    double g(int i, int j) const { return gd[i * N + j]; }
};

// ---------- решение ----------
struct Sol {
    const Instance* I = nullptr;
    vector<vector<int>> r;
    vector<vector<double>> dep, lat;
    vector<double> km;
    vector<int> rt;    // маршрут заявки или -1
    Sol() {}
    explicit Sol(const Instance& in) : I(&in), r(in.V), dep(in.V), lat(in.V), km(in.V, 0), rt(in.N, -1) {
        for (int v = 0; v < in.V; v++) { r[v].reserve(32); dep[v].reserve(32); lat[v].reserve(32); }
    }
    int used() const { int u = 0; for (auto& x : r) u += !x.empty(); return u; }
    double kmTot() const { double s = 0; for (double x : km) s += x; return s; }
    double pen() const { double p = 0; for (int k = 0; k < I->N; k++) if (rt[k] < 0) p += I->penalty(k); return p; }
    int nUn() const { int c = 0; for (int k = 0; k < I->N; k++) c += rt[k] < 0; return c; }
    double cost(double wU = W_UNSERVED, double wV = W_VEHICLE) const { return pen() * wU + used() * wV + kmTot(); }
    // лексикографическое сравнение (штраф, бригады, км)
    bool better(const Sol& o) const {
        double p = pen(), q = o.pen();
        if (p != q) return p < q;
        int a = used(), b = o.used(); if (a != b) return a < b;
        return kmTot() < o.kmTot() - 1e-9;
    }
    // пересчёт расписания; недопустимые заявки выкидываются (rt=-1, в out)
    void rebuild(int v, vector<int>* out = nullptr) {
        const Instance& In = *I; auto& R = r[v]; auto& D = dep[v]; auto& Lt = lat[v];
        int L = R.size(); D.resize(L); Lt.resize(L);
        double t = 0, dist = 0; int prev = In.veh[v].start, w = 0;
        for (int i = 0; i < L; i++) {
            int k = R[i], n = In.node(k); const Order& o = In.ord[k];
            double beg = max(t + In.t(v, prev, n), o.a);
            if (!In.can(v, k) || beg > o.b + TOL || beg + o.svc > SHIFT + TOL) { rt[k] = -1; if (out) out->push_back(k); continue; }
            t = beg + o.svc; dist += In.d(v, prev, n); prev = n; R[w] = k; D[w] = t; rt[k] = v; w++;
        }
        R.resize(w); D.resize(w); Lt.resize(w); km[v] = dist;
        for (int i = w - 1; i >= 0; i--) {
            const Order& o = In.ord[R[i]];
            double l = min(o.b, SHIFT - o.svc);
            if (i + 1 < w) l = min(l, Lt[i + 1] - o.svc - In.t(v, In.node(R[i]), In.node(R[i + 1])));
            Lt[i] = l;
        }
    }
    void rebuildAll() { for (int v = 0; v < I->V; v++) rebuild(v); }
    // состояние «перед позицией p»: время окончания предыдущей и её узел
    inline void before(int v, int p, double& t, int& prev) const {
        if (p == 0) { t = 0; prev = I->veh[v].start; } else { t = dep[v][p - 1]; prev = I->node(r[v][p - 1]); }
    }
    // обслужить k после (t, prev) бригадой v; возвращает время окончания или -1
    inline double step(int v, double t, int prev, int k) const {
        const Order& o = I->ord[k];
        double beg = max(t + I->t(v, prev, I->node(k)), o.a);
        if (beg > o.b + TOL) return -1;
        double e = beg + o.svc; if (e > SHIFT + TOL) return -1;
        return e;
    }
    // можно ли от (t, prev) продолжить хвостом своего маршрута с позиции j
    inline bool tailOK(int v, double t, int prev, int j) const {
        if (j >= (int)r[v].size()) return true;
        return t + I->t(v, prev, I->node(r[v][j])) <= lat[v][j] + TOL;
    }
    // прирост км при вставке k в v на позицию p (INF если нельзя)
    inline double insDelta(int v, int p, int k) const {
        if (!I->can(v, k)) return INF;
        double t; int prev; before(v, p, t, prev);
        double e = step(v, t, prev, k); if (e < 0) return INF;
        int nk = I->node(k); double dd = I->d(v, prev, nk);
        if (p < (int)r[v].size()) {
            int nx = I->node(r[v][p]);
            if (e + I->t(v, nk, nx) > lat[v][p] + TOL) return INF;
            dd += I->d(v, nk, nx) - I->d(v, prev, nx);
        }
        return dd;
    }
    // выигрыш км при удалении позиции i (без проверки допустимости)
    inline double remGain(int v, int i) const {
        double t; int prev; before(v, i, t, prev); int n = I->node(r[v][i]);
        double g = I->d(v, prev, n);
        if (i + 1 < (int)r[v].size()) { int nx = I->node(r[v][i + 1]); g += I->d(v, n, nx) - I->d(v, prev, nx); }
        return g;
    }
    inline bool canRemove(int v, int i) const { double t; int prev; before(v, i, t, prev); return tailOK(v, t, prev, i + 1); }
    void insertAt(int v, int p, int k) { r[v].insert(r[v].begin() + p, k); rt[k] = v; rebuild(v); }
    int posOf(int k) const { int v = rt[k]; for (int i = 0; i < (int)r[v].size(); i++) if (r[v][i] == k) return i; return -1; }
    // удалить без пересчёта (вызывающий делает rebuild затронутых маршрутов)
    void eraseNoRebuild(int k) { int v = rt[k]; auto& R = r[v]; R.erase(find(R.begin(), R.end(), k)); rt[k] = -1; }
    Routes routes() const { return r; }
};

// Удалить набор заявок, пересчитать затронутые маршруты; вылетевшие из-за нарушений добавляются в pool.
inline void removeSet(Sol& s, const vector<int>& rem, vector<int>& pool) {
    uint32_t touched = 0;
    for (int k : rem) if (s.rt[k] >= 0) { touched |= 1u << s.rt[k]; s.eraseNoRebuild(k); pool.push_back(k); }
    for (int v = 0; v < s.I->V; v++) if (touched >> v & 1) s.rebuild(v, &pool);
}
inline void collectAbsent(const Sol& s, vector<int>& pool) {
    vector<char> in(s.I->N, 0); for (int k : pool) in[k] = 1;
    for (int k = 0; k < s.I->N; k++) if (s.rt[k] < 0 && !in[k]) pool.push_back(k);
}

// ---------- восстановление ----------
// Жадная вставка по порядку pool с «морганиями» (пропуск позиции с вероятностью blink) и шумом.
// cap — максимум бригад; openCost — надбавка за открытие новой.
inline void greedyInsert(Sol& s, vector<int>& pool, Rng& rng, double blink, int cap, double openCost, double noise = 0) {
    const Instance& I = *s.I; int used = s.used(); vector<int> left;
    for (int k : pool) {
        double best = INF; int bv = -1, bp = -1;
        for (int v = 0; v < I.V; v++) {
            if (!I.can(v, k)) continue;
            bool empty = s.r[v].empty(); if (empty && used >= cap) continue;
            int L = s.r[v].size();
            for (int p = 0; p <= L; p++) {
                if (blink > 0 && rng.uni() < blink) continue;
                double d = s.insDelta(v, p, k); if (d >= INF) continue;
                if (empty) d += openCost;
                if (noise > 0) d = max(0.0, d + noise * (2 * rng.uni() - 1));
                if (d < best) { best = d; bv = v; bp = p; }
            }
        }
        if (bv < 0) { left.push_back(k); continue; }
        if (s.r[bv].empty()) used++;
        s.insertAt(bv, bp, k);
    }
    pool.swap(left);
}

// Вставка с сожалением K (K=1 — «лучшая из всех», K>=2 — regret-K). Кэш по (заявка, маршрут).
inline void regretInsert(Sol& s, vector<int>& pool, Rng& rng, int K, int cap, double openCost, double noise = 0) {
    const Instance& I = *s.I; int m = pool.size(), V = I.V; if (!m) return;
    vector<double> bc(m * V); vector<int> bpv(m * V);
    auto calc = [&](int i, int v) {
        int k = pool[i]; double best = INF; int bp = -1;
        if (I.can(v, k)) {
            int L = s.r[v].size(); bool empty = L == 0;
            for (int p = 0; p <= L; p++) {
                double d = s.insDelta(v, p, k); if (d >= INF) continue;
                if (empty) d += openCost;
                if (noise > 0) d = max(0.0, d + noise * (2 * rng.uni() - 1));
                if (d < best) { best = d; bp = p; }
            }
        }
        bc[i * V + v] = best; bpv[i * V + v] = bp;
    };
    for (int i = 0; i < m; i++) for (int v = 0; v < V; v++) calc(i, v);
    vector<char> done(m, 0); int used = s.used(); int remaining = m;
    while (remaining) {
        int bi = -1; double bReg = -INF, bC = INF; int bV = -1;
        for (int i = 0; i < m; i++) if (!done[i]) {
            double c[4] = {INF, INF, INF, INF}; int cv = -1; int KK = max(1, min(K, 4));
            for (int v = 0; v < V; v++) {
                if (s.r[v].empty() && used >= cap) continue;
                double x = bc[i * V + v]; if (x >= INF) continue;
                if (x < c[0]) cv = v;
                for (int j = 0; j < KK; j++) if (x < c[j]) { for (int q = KK - 1; q > j; q--) c[q] = c[q - 1]; c[j] = x; break; }
            }
            if (cv < 0) continue;
            double reg = 0;
            if (K >= 2) for (int j = 1; j < KK; j++) reg += (c[j] >= INF ? 1e5 : c[j]) - c[0];
            else reg = -c[0];
            if (reg > bReg + 1e-9 || (fabs(reg - bReg) <= 1e-9 && c[0] < bC)) { bReg = reg; bC = c[0]; bi = i; bV = cv; }
        }
        if (bi < 0) break;
        int k = pool[bi]; bool wasEmpty = s.r[bV].empty();
        s.insertAt(bV, bpv[bi * V + bV], k); done[bi] = 1; remaining--;
        if (wasEmpty) used++;
        for (int i = 0; i < m; i++) if (!done[i]) calc(i, bV);
    }
    vector<int> left; for (int i = 0; i < m; i++) if (!done[i]) left.push_back(pool[i]);
    pool.swap(left);
}

// ---------- быстрый локальный поиск ----------
// Окрестности: relocate (меж/внутри), swap, 2-opt*, or-opt (внутри), перестановка целых маршрутов между бригадами,
// вставка невыполненных. Критерий: штраф*1e6 + бригады*1e4 + км. cap — максимум бригад.
struct LocalSearch {
    const Instance& I; int V, N;
    vector<vector<double>> pk;               // pk[v][i] — км первых i посещений
    vector<vector<double>> suf[8];           // suf[m][v][j] — км хвоста r[v][j..] в режиме m (рёбра внутри)
    int buf[128];
    explicit LocalSearch(const Instance& in) : I(in), V(in.V), N(in.N), pk(in.V) { for (int m = 0; m < 8; m++) suf[m].assign(in.V, {}); }
    double dm(int m, int a, int b) const { return I.D[m][a * I.M + b]; }
    void cache(const Sol& s, int v) {
        const auto& R = s.r[v]; int L = R.size(); pk[v].assign(L + 1, 0);
        int prev = I.veh[v].start;
        for (int i = 0; i < L; i++) { pk[v][i + 1] = pk[v][i] + I.d(v, prev, I.node(R[i])); prev = I.node(R[i]); }
        for (int m = 0; m < 8; m++) {
            if (I.D[m].empty()) continue;
            auto& S = suf[m][v]; S.assign(L + 1, 0);
            for (int j = L - 2; j >= 0; j--) S[j] = S[j + 1] + dm(m, I.node(R[j]), I.node(R[j + 1]));
        }
    }
    // допустимость последовательности buf[0..n) для бригады v; km в out
    bool feasBuf(int v, int n, double& kmOut) const {
        double t = 0, dist = 0; int prev = I.veh[v].start;
        for (int i = 0; i < n; i++) {
            int k = buf[i]; if (!I.can(v, k)) return false;
            const Order& o = I.ord[k]; int nd = I.node(k);
            double beg = max(t + I.t(v, prev, nd), o.a);
            if (beg > o.b + TOL) return false;
            t = beg + o.svc; if (t > SHIFT + TOL) return false;
            dist += I.d(v, prev, nd); prev = nd;
        }
        kmOut = dist; return true;
    }
    void touch(Sol& s, int v) { s.rebuild(v); cache(s, v); }

    bool tryRelocate(Sol& s, int cap) {
        int used = s.used();
        for (int a = 0; a < V; a++) for (int i = 0; i < (int)s.r[a].size(); i++) {
            int k = s.r[a][i]; int La = s.r[a].size();
            double g = s.remGain(a, i) + (La == 1 ? W_VEHICLE : 0);
            if (!s.canRemove(a, i)) continue;
            for (int b = 0; b < V; b++) {
                if (b == a || !I.can(b, k)) continue;
                bool empty = s.r[b].empty();
                if (empty && (La != 1 && used >= cap)) continue;
                double pen = empty ? W_VEHICLE : 0;
                if (g - pen < 1e-7) continue;
                for (int p = 0; p <= (int)s.r[b].size(); p++) {
                    double d = s.insDelta(b, p, k);
                    if (d + pen < g - 1e-7) {
                        s.r[a].erase(s.r[a].begin() + i); s.r[b].insert(s.r[b].begin() + p, k);
                        touch(s, a); touch(s, b); return true;
                    }
                }
            }
        }
        return false;
    }
    bool trySwap(Sol& s) {
        for (int a = 0; a < V; a++) for (int b = a + 1; b < V; b++) {
            int La = s.r[a].size(), Lb = s.r[b].size(); if (!La || !Lb) continue;
            for (int i = 0; i < La; i++) {
                int u = s.r[a][i]; if (!I.can(b, u)) continue;
                double ta; int pa; s.before(a, i, ta, pa);
                int na = i + 1 < La ? I.node(s.r[a][i + 1]) : -1;
                double baseA = I.d(a, pa, I.node(u)) + (na >= 0 ? I.d(a, I.node(u), na) : 0);
                for (int j = 0; j < Lb; j++) {
                    int w = s.r[b][j]; if (!I.can(a, w)) continue;
                    double tb; int pb; s.before(b, j, tb, pb);
                    int nb = j + 1 < Lb ? I.node(s.r[b][j + 1]) : -1;
                    double baseB = I.d(b, pb, I.node(w)) + (nb >= 0 ? I.d(b, I.node(w), nb) : 0);
                    double newA = I.d(a, pa, I.node(w)) + (na >= 0 ? I.d(a, I.node(w), na) : 0);
                    double newB = I.d(b, pb, I.node(u)) + (nb >= 0 ? I.d(b, I.node(u), nb) : 0);
                    if (newA + newB >= baseA + baseB - 1e-7) continue;
                    double e = s.step(a, ta, pa, w); if (e < 0 || !s.tailOK(a, e, I.node(w), i + 1)) continue;
                    e = s.step(b, tb, pb, u); if (e < 0 || !s.tailOK(b, e, I.node(u), j + 1)) continue;
                    swap(s.r[a][i], s.r[b][j]); touch(s, a); touch(s, b); return true;
                }
            }
        }
        return false;
    }
    // 2-opt*: A' = A[0..i) + B[j..], B' = B[0..j) + A[i..]
    bool tryTwoOptStar(Sol& s) {
        for (int a = 0; a < V; a++) for (int b = a + 1; b < V; b++) {
            int La = s.r[a].size(), Lb = s.r[b].size(); if (!La && !Lb) continue;
            int ma = I.veh[a].mode, mb = I.veh[b].mode;
            double old = s.km[a] + s.km[b] + (La ? W_VEHICLE : 0) + (Lb ? W_VEHICLE : 0);
            for (int i = 0; i <= La; i++) for (int j = 0; j <= Lb; j++) {
                if ((i == La && j == Lb) || (i == 0 && j == 0)) continue;
                int nA = i + (Lb - j), nB = j + (La - i);
                int lastA = i ? I.node(s.r[a][i - 1]) : I.veh[a].start;
                int lastB = j ? I.node(s.r[b][j - 1]) : I.veh[b].start;
                double ka = pk[a][i] + (j < Lb ? I.d(a, lastA, I.node(s.r[b][j])) + suf[ma][b][j] : 0);
                double kb = pk[b][j] + (i < La ? I.d(b, lastB, I.node(s.r[a][i])) + suf[mb][a][i] : 0);
                double nw = ka + kb + (nA ? W_VEHICLE : 0) + (nB ? W_VEHICLE : 0);
                if (nw >= old - 1e-7) continue;
                int n = 0; for (int x = 0; x < i; x++) buf[n++] = s.r[a][x]; for (int x = j; x < Lb; x++) buf[n++] = s.r[b][x];
                double k1, k2; if (!feasBuf(a, n, k1)) continue;
                vector<int> ra(buf, buf + n);
                n = 0; for (int x = 0; x < j; x++) buf[n++] = s.r[b][x]; for (int x = i; x < La; x++) buf[n++] = s.r[a][x];
                if (!feasBuf(b, n, k2)) continue;
                s.r[b].assign(buf, buf + n); s.r[a] = ra; touch(s, a); touch(s, b); return true;
            }
        }
        return false;
    }
    // or-opt внутри маршрута: перенос сегмента длины 1..3, а также обмен двух заявок
    bool tryIntra(Sol& s) {
        for (int v = 0; v < V; v++) {
            int L = s.r[v].size(); if (L < 2) continue;
            const auto& R = s.r[v]; double old = s.km[v];
            for (int len = 1; len <= 3 && len < L; len++) for (int i = 0; i + len <= L; i++) for (int p = 0; p <= L - len; p++) {
                if (p == i) continue;
                int n = 0; auto rest = [&](int x) { return x < i ? R[x] : R[x + len]; }; // маршрут без сегмента
                for (int x = 0; x < p; x++) buf[n++] = rest(x);
                for (int x = i; x < i + len; x++) buf[n++] = R[x];
                for (int x = p; x < L - len; x++) buf[n++] = rest(x);
                double k; if (!feasBuf(v, n, k) || k >= old - 1e-7) continue;
                s.r[v].assign(buf, buf + n); touch(s, v); return true;
            }
            for (int i = 0; i < L; i++) for (int j = i + 1; j < L; j++) {
                int n = 0; for (int x = 0; x < L; x++) buf[n++] = R[x]; swap(buf[i], buf[j]);
                double k; if (!feasBuf(v, n, k) || k >= old - 1e-7) continue;
                s.r[v].assign(buf, buf + n); touch(s, v); return true;
            }
        }
        return false;
    }
    // перенос целого маршрута на другую бригаду (в т.ч. пустую) или обмен маршрутами
    bool tryRouteSwap(Sol& s) {
        for (int a = 0; a < V; a++) for (int b = a + 1; b < V; b++) {
            if (s.r[a].empty() && s.r[b].empty()) continue;
            int n = 0; for (int k : s.r[b]) buf[n++] = k; double ka, kb;
            if (!feasBuf(a, n, ka)) continue;
            n = 0; for (int k : s.r[a]) buf[n++] = k;
            if (!feasBuf(b, n, kb)) continue;
            if (ka + kb < s.km[a] + s.km[b] - 1e-7) { swap(s.r[a], s.r[b]); touch(s, a); touch(s, b); return true; }
        }
        return false;
    }
    bool tryInsertAbsent(Sol& s, int cap, Rng& rng) {
        vector<int> pool; collectAbsent(s, pool); if (pool.empty()) return false;
        int before = pool.size(); greedyInsert(s, pool, rng, 0, cap, W_VEHICLE);
        if ((int)pool.size() < before) { for (int v = 0; v < V; v++) cache(s, v); return true; }
        return false;
    }
    void run(Sol& s, Rng& rng, int cap, const Timer* tm = nullptr, double tEnd = 1e18) {
        for (int v = 0; v < V; v++) cache(s, v);
        int it = 0;
        while (true) {
            if (tm && (++it & 3) == 0 && tm->sec() > tEnd) break;
            if (tryInsertAbsent(s, cap, rng)) continue;
            if (tryRelocate(s, cap)) continue;
            if (trySwap(s)) continue;
            if (tryTwoOptStar(s)) continue;
            if (tryRouteSwap(s)) continue;
            if (tryIntra(s)) continue;
            break;
        }
    }
};

// ---------- SISR-разрушение (Christiaens & Vanden Berghe) ----------
struct SisrRuin {
    double cbar = 10, Lmax = 10, alphaSplit = 0.5, splitCont = 0.5;  // splitCont: вероятность увеличить число сохраняемых m
    void apply(Sol& s, const Ctx& C, Rng& rng, vector<int>& pool, int seedCust = -1) const {
        const Instance& I = *s.I; int V = I.V;
        double avgL = 0; int nr = 0; for (auto& r : s.r) if (!r.empty()) { avgL += r.size(); nr++; }
        if (!nr) return; avgL /= nr;
        double lsmax = min(Lmax, avgL);
        double ksmax = 4 * cbar / (1 + lsmax) - 1;
        int ks = (int)(rng.uni() * ksmax) + 1;
        int seed = seedCust >= 0 ? seedCust : rng.randint(I.N);
        vector<char> ruined(V, 0); vector<int> rem;
        vector<char> isRem(I.N, 0);
        auto proc = [&](int c) {
            int v = s.rt[c]; if (v < 0 || ruined[v] || isRem[c]) return;
            const auto& R = s.r[v]; int L = R.size();
            int ltmax = (int)min((double)L, lsmax); int lt = (int)(rng.uni() * ltmax) + 1;
            int pc = find(R.begin(), R.end(), c) - R.begin();
            if (rng.uni() < alphaSplit || lt >= L) {
                // строка длины lt, содержащая c
                int lo = max(0, pc - lt + 1), hi = min(pc, L - lt);
                int st = lo + rng.randint(hi - lo + 1);
                for (int x = st; x < st + lt; x++) { rem.push_back(R[x]); isRem[R[x]] = 1; }
            } else {
                int m = 1; while (lt + m < L && rng.uni() < splitCont) m++;
                int tot = lt + m;
                int lo = max(0, pc - tot + 1), hi = min(pc, L - tot);
                int st = lo + rng.randint(hi - lo + 1);
                int keepSt = st + rng.randint(lt + 1); // сохранённый подотрезок длины m внутри
                for (int x = st; x < st + tot; x++) if (x < keepSt || x >= keepSt + m) { rem.push_back(R[x]); isRem[R[x]] = 1; }
            }
            ruined[v] = 1; ks--;
        };
        if (s.rt[seed] < 0) { // семя не в маршруте — берём ближайших
            for (int c : C.nbr[seed]) { if (ks <= 0) break; proc(c); }
        } else {
            proc(seed);
            for (int c : C.nbr[seed]) { if (ks <= 0) break; proc(c); }
        }
        removeSet(s, rem, pool);
    }
};

// Порядок заявок для восстановления (SISR): случайно 4, по ширине окна 4, дальние 2, ближние 1.
inline void sisrSort(vector<int>& pool, const Ctx& C, Rng& rng) {
    const Instance& I = C.I;
    for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
    int w = rng.randint(11);
    if (w < 4) return;
    if (w < 8) stable_sort(pool.begin(), pool.end(), [&](int x, int y) {
        if (C.nCan[x] != C.nCan[y] && (C.nCan[x] < 4 || C.nCan[y] < 4)) return C.nCan[x] < C.nCan[y];
        return I.ord[x].b - I.ord[x].a < I.ord[y].b - I.ord[y].a; });
    else if (w < 10) stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return C.farv[x] > C.farv[y]; });
    else stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return C.farv[x] < C.farv[y]; });
}

// Финальная страховка: проверка через routeFeasible общего каркаса.
inline Routes finalize(const Instance& I, const Sol& s) {
    Routes R = s.r; R.resize(I.V);
    for (int v = 0; v < I.V; v++) while (!routeFeasible(I, v, R[v]) && !R[v].empty()) {
        // выкинуть первую нарушающую
        double t = 0; int prev = I.veh[v].start;
        for (int i = 0; i < (int)R[v].size(); i++) {
            int k = R[v][i], n = I.node(k);
            double beg = max(t + I.t(v, prev, n), I.ord[k].a);
            if (!I.can(v, k) || beg > I.ord[k].b + EPS || beg + I.ord[k].svc > SHIFT + EPS) { R[v].erase(R[v].begin() + i); break; }
            t = beg + I.ord[k].svc; prev = n;
        }
    }
    return R;
}

// Удалить маршрут для сокращения парка: выбирается маршрут с наименьшей суммой absence (или наименьший).
inline int pickRouteToRemove(const Sol& s, Rng& rng, const vector<double>* absence) {
    int best = -1; double bv = INF;
    for (int v = 0; v < s.I->V; v++) if (!s.r[v].empty()) {
        double val = 0;
        if (absence) { for (int k : s.r[v]) val += (*absence)[k]; val += 1e-3 * s.r[v].size(); }
        else val = s.r[v].size();
        val += 1e-6 * rng.uni();
        if (val < bv) { bv = val; best = v; }
    }
    return best;
}

// ---------- операторы разрушения ALNS (Ropke & Pisinger) ----------
enum { R_RANDOM, R_WORST, R_SHAW, R_ROUTE, R_SLOT, NR };
struct Destroyer {
    const Ctx& C; const Instance& I; int N, V;
    vector<int> rem; vector<char> mark; vector<pair<double, int>> tmpv;
    explicit Destroyer(const Ctx& c) : C(c), I(c.I), N(c.N), V(c.V), mark(c.N) {}
    void apply(Sol& s, Rng& rng, int op, int q, vector<int>& pool) {
        rem.clear(); fill(mark.begin(), mark.end(), 0);
        vector<int> served; for (int k = 0; k < N; k++) if (s.rt[k] >= 0) served.push_back(k);
        if (served.empty()) return;
        q = min(q, (int)served.size());
        switch (op) {
        case R_RANDOM:
            for (int i = 0; i < q; i++) { int j = i + rng.randint(served.size() - i); swap(served[i], served[j]); rem.push_back(served[i]); }
            break;
        case R_WORST: {
            tmpv.clear();
            for (int v = 0; v < V; v++) for (int i = 0; i < (int)s.r[v].size(); i++)
                tmpv.push_back({-(s.remGain(v, i) + (s.r[v].size() == 1 ? 30.0 : 0)), s.r[v][i]});
            sort(tmpv.begin(), tmpv.end());
            for (int i = 0; i < q && !tmpv.empty(); i++) {
                int j = (int)(pow(rng.uni(), 3.0) * tmpv.size());
                rem.push_back(tmpv[j].second); tmpv.erase(tmpv.begin() + j);
            }
            break; }
        case R_SHAW: {
            int sd = served[rng.randint(served.size())]; rem.push_back(sd); mark[sd] = 1;
            while ((int)rem.size() < q) {
                int r0 = rem[rng.randint(rem.size())]; tmpv.clear();
                for (int k : served) if (!mark[k]) {
                    double rel = 9 * C.g(r0, k) / C.maxD + 3 * fabs(I.ord[r0].a - I.ord[k].a) / SHIFT
                               + 2 * (I.ord[r0].skill != I.ord[k].skill) + 1 * (s.rt[r0] == s.rt[k] ? 0 : 1) * 0.5;
                    tmpv.push_back({rel, k});
                }
                if (tmpv.empty()) break;
                sort(tmpv.begin(), tmpv.end());
                int j = (int)(pow(rng.uni(), 6.0) * tmpv.size());
                rem.push_back(tmpv[j].second); mark[tmpv[j].second] = 1;
            }
            break; }
        case R_ROUTE: {
            // маршрут, смещённо к коротким; при малом маршруте добивка соседями
            vector<int> rs; for (int v = 0; v < V; v++) if (!s.r[v].empty()) rs.push_back(v);
            sort(rs.begin(), rs.end(), [&](int x, int y) { return s.r[x].size() < s.r[y].size(); });
            int v = rs[(int)(pow(rng.uni(), 2.0) * rs.size())];
            for (int k : s.r[v]) { rem.push_back(k); mark[k] = 1; }
            if (rng.uni() < 0.5) { // плюс ближайшие к маршруту заявки
                int extra = rng.randint(max(1, q / 2)) + 1; int sd = s.r[v][rng.randint(s.r[v].size())];
                for (int k : C.nbr[sd]) { if (extra <= 0) break; if (s.rt[k] >= 0 && !mark[k]) { rem.push_back(k); mark[k] = 1; extra--; } }
            }
            break; }
        case R_SLOT: {
            int sd = served[rng.randint(served.size())]; double a = I.ord[sd].a, b = I.ord[sd].b;
            tmpv.clear();
            for (int k : served) if (I.ord[k].a <= b && I.ord[k].b >= a) tmpv.push_back({C.g(sd, k), k});
            sort(tmpv.begin(), tmpv.end());
            for (int i = 0; i < (int)tmpv.size() && i < q; i++) rem.push_back(tmpv[i].second);
            break; }
        }
        removeSet(s, rem, pool);
        }
};
} // namespace hy
