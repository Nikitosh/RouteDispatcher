// Private header of agent "sa": improved SA (s20+) built on sa_util.hpp.
#pragma once
#include "common.hpp"
namespace sa2 {
constexpr int MAXN = 160, MAXV = 16, MAXL = 168;
constexpr double TEPS = 1e-7;

struct Fast {
    int N, V, S, M;
    const double* T[MAXV]; const double* D[MAXV];
    int mode[MAXV], st[MAXV], mask[MAXV];
    double a[MAXN], b[MAXN], bb[MAXN], svc[MAXN], pen[MAXN]; int skb[MAXN], skill[MAXN];
    bool servable[MAXN];
    vector<vector<int>> nb;   // ближайшие по «времени-расстоянию» заявки
    explicit Fast(const Instance& I, int K = 16) {
        N = I.N; V = I.V; S = I.S; M = I.M;
        if (N > MAXN - 8 || V > MAXV) { fprintf(stderr, "instance too large for sa_util\n"); exit(1); }
        for (int v = 0; v < V; v++) {
            mode[v] = I.veh[v].mode; st[v] = I.veh[v].start; mask[v] = I.veh[v].mask;
            T[v] = I.T[mode[v]].data(); D[v] = I.D[mode[v]].data();
        }
        for (int k = 0; k < N; k++) {
            a[k] = I.ord[k].a; b[k] = I.ord[k].b; svc[k] = I.ord[k].svc; bb[k] = min(b[k], SHIFT - svc[k]); pen[k] = I.penalty(k);
            skill[k] = I.ord[k].skill; skb[k] = 1 << skill[k];
            servable[k] = false;
            for (int v = 0; v < V && !servable[k]; v++) if (I.can(v, k) && routeFeasible(I, v, {k})) servable[k] = true;
        }
        // родство: время переезда (минимум по используемым режимам) + ожидание; недопустимый порядок — дорого
        vector<int> modes; for (int v = 0; v < V; v++) if (find(modes.begin(), modes.end(), mode[v]) == modes.end()) modes.push_back(mode[v]);
        auto tmin = [&](int i, int j) { double r = 1e18; for (int m : modes) r = min(r, I.T[m][i * M + j]); return r; };
        nb.assign(N, {});
        for (int k = 0; k < N; k++) {
            vector<pair<double, int>> c;
            for (int n = 0; n < N; n++) if (n != k) {
                double best = 1e18;
                for (int dir = 0; dir < 2; dir++) {
                    int x = dir ? n : k, y = dir ? k : n;
                    double t = tmin(S + x, S + y), e = a[x] + svc[x] + t;
                    double cost = e > b[y] + TEPS ? 1e6 + t : t + 0.5 * max(0.0, a[y] - (b[x] + svc[x] + t));
                    best = min(best, cost);
                }
                // заявки без общей бригады — в конец
                bool common = false; for (int v = 0; v < V; v++) if (I.can(v, k) && I.can(v, n)) common = true;
                if (!common) best += 1e7;
                c.push_back({best, n});
            }
            sort(c.begin(), c.end());
            for (int q = 0; q < min<int>(K, c.size()); q++) nb[k].push_back(c[q].second);
        }
    }
};

struct Route {
    int len = 0; double km = 0, tw = 0;
    int seq[MAXL]; double dep[MAXL], ck[MAXL], lat[MAXL]; int need[MAXL + 1];
    double twp[MAXL], Ls[MAXL], TWs[MAXL];   // time warp: накопленный на префиксе; суффикс (L, TW) по Видалю
};

struct Sol {
    const Fast* F = nullptr;
    Route r[MAXV];
    int where[MAXN], pos[MAXN];
    int pool[MAXN], np = 0, pidx[MAXN];   // пул только из обслуживаемых (servable) заявок
    double km = 0, tw = 0; int used = 0; double poolPen = 0, constPen = 0;

    void init(const Fast& f) {
        F = &f; np = 0; km = 0; tw = 0; used = 0; poolPen = 0; constPen = 0;
        for (int v = 0; v < F->V; v++) { r[v].len = 0; r[v].km = 0; r[v].tw = 0; r[v].need[0] = 0; }
        for (int k = 0; k < F->N; k++) {
            where[k] = -1; pos[k] = -1; pidx[k] = -1;
            if (F->servable[k]) poolAdd(k); else constPen += F->pen[k];
        }
    }
    void poolAdd(int k) { where[k] = -1; pidx[k] = np; pool[np++] = k; poolPen += F->pen[k]; }
    void poolRemove(int k) { int i = pidx[k]; int l = pool[--np]; pool[i] = l; pidx[l] = i; pidx[k] = -1; poolPen -= F->pen[k]; }
    // Установить маршрут v (seq может указывать на внешний буфер). Обновляет кэши, km, used, where/pos.
    void setRoute(int v, const int* s, int len) {
        Route& R = r[v];
        km -= R.km; tw -= R.tw; if (R.len) used--;
        if (len && s != R.seq) memcpy(R.seq, s, sizeof(int) * len);
        R.len = len;
        const double* T = F->T[v]; const double* D = F->D[v]; int M = F->M, S = F->S;
        double t = 0, dist = 0, w = 0; int prev = F->st[v];
        for (int p = 0; p < len; p++) {
            int k = R.seq[p], n = S + k;
            t += T[prev * M + n]; if (t < F->a[k]) t = F->a[k];
            double bb = F->bb[k]; if (t > bb) { w += t - bb; t = bb; }
            dist += D[prev * M + n]; R.ck[p] = dist; R.twp[p] = w;
            t += F->svc[k]; R.dep[p] = t; prev = n;
            where[k] = v; pos[k] = p;
        }
        R.need[len] = 0;
        for (int p = len - 1; p >= 0; p--) {
            int k = R.seq[p];
            double l = F->bb[k];
            if (p + 1 < len) {
                double dl = F->svc[k] + T[(S + k) * M + S + R.seq[p + 1]];
                l = min(l, R.lat[p + 1] - dl);
                double dtw = max(F->a[k] + dl - R.Ls[p + 1], 0.0);
                R.TWs[p] = R.TWs[p + 1] + dtw; R.Ls[p] = min(R.Ls[p + 1] - dl, F->bb[k]) + dtw;
            } else { R.TWs[p] = 0; R.Ls[p] = F->bb[k]; }
            R.lat[p] = l; R.need[p] = R.need[p + 1] | F->skb[k];
        }
        R.km = dist; R.tw = w; km += dist; tw += w; if (len) used++;
    }
    double trueScore() const { return (poolPen + constPen) * W_UNSERVED + used * W_VEHICLE + km; }
    Routes toRoutes() const {
        Routes R(F->V);
        for (int v = 0; v < F->V; v++) R[v].assign(r[v].seq, r[v].seq + r[v].len);
        return R;
    }
    void fromRoutes(const Fast& f, const Routes& R) {
        init(f);
        for (int v = 0; v < F->V && v < (int)R.size(); v++) {
            for (int k : R[v]) if (pidx[k] >= 0) poolRemove(k);
            setRoute(v, R[v].data(), (int)R[v].size());
        }
    }
};

// Км нового маршрута бригады v = префикс r[v][0..i) + mid[0..m) + суффикс r[u][j..) (u<0 — без суффикса).
// Возвращает -1, если недопустимо. O(m) при совпадении режима транспорта u и v, иначе O(m + |суффикс|).
inline double evalCand(const Fast& F, const Sol& S, int v, int i, const int* mid, int m, int u, int j) {
    const Route& R = S.r[v];
    const double* T = F.T[v]; const double* D = F.D[v]; const int M = F.M, off = F.S, msk = F.mask[v];
    double t, dist; int prev;
    if (i > 0) { t = R.dep[i - 1]; dist = R.ck[i - 1]; prev = off + R.seq[i - 1]; }
    else { t = 0; dist = 0; prev = F.st[v]; }
    for (int q = 0; q < m; q++) {
        int k = mid[q];
        if (!(F.skb[k] & msk)) return -1;
        int n = off + k;
        t += T[prev * M + n]; if (t < F.a[k]) t = F.a[k];
        if (t > F.b[k] + TEPS) return -1;
        t += F.svc[k]; if (t > SHIFT + TEPS) return -1;
        dist += D[prev * M + n]; prev = n;
    }
    if (u >= 0) {
        const Route& U = S.r[u];
        if (j < U.len) {
            if (U.need[j] & ~msk) return -1;
            int n = off + U.seq[j];
            if (F.mode[u] == F.mode[v]) {
                double A = t + T[prev * M + n];
                if (A > U.lat[j] + TEPS) return -1;
                dist += D[prev * M + n] + U.ck[U.len - 1] - U.ck[j];
            } else {
                for (int q = j; q < U.len; q++) {
                    int k = U.seq[q]; n = off + k;
                    t += T[prev * M + n]; if (t < F.a[k]) t = F.a[k];
                    if (t > F.b[k] + TEPS) return -1;
                    t += F.svc[k]; if (t > SHIFT + TEPS) return -1;
                    dist += D[prev * M + n]; prev = n;
                }
            }
        }
    }
    return dist;
}

// То же с временным сдвигом (time warp, Видаль): опоздание разрешено, «откат» времени к b платится в tw.
// Возвращает км или -1 при несовместимости навыков.
inline double evalCandTW(const Fast& F, const Sol& S, int v, int i, const int* mid, int m, int u, int j, double& tw) {
    const Route& R = S.r[v];
    const double* T = F.T[v]; const double* D = F.D[v]; const int M = F.M, off = F.S, msk = F.mask[v];
    double t, dist, w; int prev;
    if (i > 0) { t = R.dep[i - 1]; dist = R.ck[i - 1]; w = R.twp[i - 1]; prev = off + R.seq[i - 1]; }
    else { t = 0; dist = 0; w = 0; prev = F.st[v]; }
    for (int q = 0; q < m; q++) {
        int k = mid[q];
        if (!(F.skb[k] & msk)) return -1;
        int n = off + k;
        t += T[prev * M + n]; if (t < F.a[k]) t = F.a[k];
        if (t > F.bb[k]) { w += t - F.bb[k]; t = F.bb[k]; }
        t += F.svc[k];
        dist += D[prev * M + n]; prev = n;
    }
    if (u >= 0) {
        const Route& U = S.r[u];
        if (j < U.len) {
            if (U.need[j] & ~msk) return -1;
            int n = off + U.seq[j];
            if (F.mode[u] == F.mode[v]) {
                double A = t + T[prev * M + n]; if (A < F.a[U.seq[j]]) A = F.a[U.seq[j]];
                w += U.TWs[j] + max(0.0, A - U.Ls[j]);
                dist += D[prev * M + n] + U.ck[U.len - 1] - U.ck[j];
            } else {
                for (int q = j; q < U.len; q++) {
                    int k = U.seq[q]; n = off + k;
                    t += T[prev * M + n]; if (t < F.a[k]) t = F.a[k];
                    if (t > F.bb[k]) { w += t - F.bb[k]; t = F.bb[k]; }
                    t += F.svc[k];
                    dist += D[prev * M + n]; prev = n;
                }
            }
        }
    }
    tw = w;
    return dist;
}
// Собрать последовательность кандидата в buf, вернуть длину.
inline int compose(const Sol& S, int* buf, int v, int i, const int* mid, int m, int u, int j) {
    int L = 0;
    for (int q = 0; q < i; q++) buf[L++] = S.r[v].seq[q];
    for (int q = 0; q < m; q++) buf[L++] = mid[q];
    if (u >= 0) for (int q = j; q < S.r[u].len; q++) buf[L++] = S.r[u].seq[q];
    return L;
}
inline int candLen(const Sol& S, int i, int m, int u, int j) { return i + m + (u >= 0 ? max(0, S.r[u].len - j) : 0); }

// Лучшая вставка заявки k: cost = Δкм + (пустой маршрут ? openCost : 0) + pressure(newLen) - pressure(oldLen).
// skipRoute — маршрут, в который не вставлять; allowEmpty — можно ли открывать бригаду.
struct Ins { int v = -1, p = -1; double cost = 1e18, km = 0; };
template <class Press>
inline Ins bestIns(const Fast& F, const Sol& S, int k, double openCost, bool allowEmpty, int skipRoute, Press press) {
    Ins best; long long tried[MAXV]; int nt = 0;   // одинаковые пустые бригады (старт, режим, маска) пробуем один раз
    for (int v = 0; v < F.V; v++) {
        if (v == skipRoute || !(F.skb[k] & F.mask[v])) continue;
        const Route& R = S.r[v];
        if (R.len == 0) {
            if (!allowEmpty) continue;
            long long key = ((long long)F.st[v] * 8 + F.mode[v]) * 1000003LL + F.mask[v];
            bool dup = false; for (int q = 0; q < nt; q++) if (tried[q] == key) dup = true;
            if (dup) continue; tried[nt++] = key;
        }
        double base = (R.len == 0 ? openCost : 0) + press(R.len + 1) - press(R.len) - R.km;
        for (int p = 0; p <= R.len; p++) {
            double km = evalCand(F, S, v, p, &k, 1, v, p);
            if (km < 0) continue;
            double c = km + base;
            if (c < best.cost) best = {v, p, c, km};
        }
    }
    return best;
}

// Жадное построение: заявки в заданном порядке, каждая в лучшую позицию (Δкм + openCost за новую бригаду).
inline void greedyBuild(const Fast& F, Sol& S, const vector<int>& order, double openCost) {
    S.init(F);
    int buf[MAXL];
    for (int k : order) {
        if (!F.servable[k] || S.pidx[k] < 0) continue;
        Ins in = bestIns(F, S, k, openCost, true, -1, [](int) { return 0.0; });
        if (in.v < 0) continue;
        int L = compose(S, buf, in.v, in.p, &k, 1, in.v, in.p);
        S.poolRemove(k); S.setRoute(in.v, buf, L);
    }
}
// Несколько жадных построений разными порядками и штрафами, лучший по trueScore.
inline Routes multiGreedy(const Fast& F, Rng& rng, double timeBudget, Sol& out) {
    Timer tm;
    vector<int> base(F.N); iota(base.begin(), base.end(), 0);
    vector<vector<int>> orders;
    auto by = [&](auto key) { vector<int> o = base; stable_sort(o.begin(), o.end(), [&](int x, int y) { return key(x) < key(y); }); orders.push_back(o); };
    by([&](int k) { return F.b[k] * 1000 + F.a[k]; });
    by([&](int k) { return F.a[k] * 1000 + F.b[k]; });
    by([&](int k) { return (F.b[k] - F.a[k]) * 1000 + F.a[k]; });
    by([&](int k) { return -F.pen[k] * 1e7 + (F.b[k] - F.a[k]) * 1000 + F.a[k]; });
    by([&](int k) { return -F.svc[k] * 1e6 + F.b[k]; });
    double opens[] = {1e4, 300, 60, 20};
    Sol cur; double bestS = 1e300; Routes best;
    int it = 0;
    while (true) {
        vector<int> o;
        if (it < (int)orders.size() * 4) o = orders[it / 4];
        else {   // случайные возмущения порядка по b
            o = orders[rng.randint(3)];
            for (int q = 0; q < F.N / 4; q++) { int x = rng.randint(F.N), y = min(F.N - 1, x + 1 + rng.randint(4)); swap(o[x], o[y]); }
        }
        double oc = opens[it % 4];
        greedyBuild(F, cur, o, oc);
        double s = cur.trueScore();
        if (s < bestS) { bestS = s; out = cur; }
        it++;
        if (it >= (int)orders.size() * 4 && tm.sec() > timeBudget) break;
        if (it > 2000) break;
    }
    return out.toRoutes();
}
}  // namespace sa2
using namespace sa2;
#include <time.h>
// Таймер: стеной (по умолчанию) или процессорным временем процесса (SA_CPU=1) — для воспроизводимых сравнений
// на загруженной машине.
struct STimer {
    bool cpu; double c0; chrono::steady_clock::time_point t0;
    static double cpuNow() { timespec ts; clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }
    STimer() : cpu(getenv("SA_CPU") != nullptr), c0(cpuNow()), t0(chrono::steady_clock::now()) {}
    double sec() const { return cpu ? cpuNow() - c0 : chrono::duration<double>(chrono::steady_clock::now() - t0).count(); }
};

struct SA2 {
    const Fast& F; Sol S; Rng rng;
    double Wv = 100, beta = 0.5, beta0 = 0.5, T = 1, T0 = 5, T1 = 0.05, Wp0 = 500, Winc = 1;
    int incPeriod = 20000;
    double wp[MAXN];
    int bufA[MAXL], bufB[MAXL], midA[MAXL], midB[MAXL], tmp[MAXL];
    double bestScore = 1e300; int bestLen[MAXV], bestSeq[MAXV][MAXL];
    bool dbg = getenv("SA_DBG") != nullptr;
    double tRepEnd=0,tElimEnd=0,kmAfterElim=0; int usedAfterElim=0;
    long long iters = 0, acc = 0, elimTry = 0, elimAcc = 0;
    // Нижняя граница числа бригад по нагрузке: Σ(обслуживание + минимальный заезд) / длина смены.
    int workLB = 0; bool useLB = true;
    void computeLB() {
        double tot = 0;
        for (int k = 0; k < F.N; k++) {
            double mi = 1e18;
            for (int v = 0; v < F.V; v++) {
                const double* T = F.T[v];
                mi = min(mi, T[F.st[v] * F.M + F.S + k]);
                for (int j = 0; j < F.N; j++) if (j != k) mi = min(mi, T[(F.S + j) * F.M + F.S + k]);
            }
            tot += F.svc[k] + mi;
        }
        workLB = (int)ceil(tot / SHIFT - 1e-9);
    }
    vector<vector<int>> nearSt;   // для каждой бригады: заявки по близости к её старту (с учётом навыков)
    SA2(const Fast& f, uint64_t seed) : F(f), rng(seed) {
        memset(inRz, 0, sizeof(inRz));
        computeLB();
        nearSt.assign(F.V, {});
        for (int v = 0; v < F.V; v++) {
            vector<pair<double, int>> c;
            for (int k = 0; k < F.N; k++) if (F.skb[k] & F.mask[v]) c.push_back({F.D[v][F.st[v] * F.M + F.S + k], k});
            sort(c.begin(), c.end());
            for (int q = 0; q < min<int>(30, c.size()); q++) nearSt[v].push_back(c[q].second);
        }
    }

    inline double press(int len) const { return -beta * len * len; }
    inline bool accept(double d) { return d <= 0 || rng.uni() < exp(-d / T); }
    int twAlt = 0; bool twMode = false; double lamAdd = 0, WvTW = 0, betaTW = 0, lam = 1, lam0 = 1, lamMul = 1.2; long long twElimOk = 0;
    inline double ev(int v, int i, const int* mid, int m, int u, int j, double& tw) {
        if (!twMode) { tw = 0; return evalCand(F, S, v, i, mid, m, u, j); }
        return evalCandTW(F, S, v, i, mid, m, u, j, tw);
    }
    void checkBest() {
        if (S.tw > 1e-9) return;
        double s = S.trueScore();
        if (s < bestScore - 1e-9) {
            bestScore = s;
            for (int v = 0; v < F.V; v++) { bestLen[v] = S.r[v].len; memcpy(bestSeq[v], S.r[v].seq, sizeof(int) * S.r[v].len); }
        }
    }
    Routes bestRoutes() const { Routes R(F.V); for (int v = 0; v < F.V; v++) R[v].assign(bestSeq[v], bestSeq[v] + bestLen[v]); return R; }
    double surrogate() const {
        double c = S.km + Wv * S.used + lam * S.tw;
        for (int q = 0; q < S.np; q++) c += wp[S.pool[q]];
        for (int v = 0; v < F.V; v++) c += press(S.r[v].len);
        return c;
    }
    struct C { int v, i; const int* mid; int m; int u, j; };
    inline double routeDelta(const C& c, double km, double tw, int& newLen) {
        const Route& R = S.r[c.v];
        newLen = candLen(S, c.i, c.m, c.u, c.j);
        return km - R.km + lam * (tw - R.tw) + Wv * ((newLen > 0) - (R.len > 0)) + press(newLen) - press(R.len);
    }
    // Две разные бригады. Сначала проверяется A (обычно сторона вставки — чаще недопустима).
    bool try2(const C& A, const C& B, double extra) {
        double twA, twB;
        double kmA = ev(A.v, A.i, A.mid, A.m, A.u, A.j, twA); if (kmA < 0) return false;
        double kmB = ev(B.v, B.i, B.mid, B.m, B.u, B.j, twB); if (kmB < 0) return false;
        int la, lb; double d = routeDelta(A, kmA, twA, la) + routeDelta(B, kmB, twB, lb) + extra;
        if (!accept(d)) return false;
        int LA = compose(S, bufA, A.v, A.i, A.mid, A.m, A.u, A.j), LB = compose(S, bufB, B.v, B.i, B.mid, B.m, B.u, B.j);
        S.setRoute(A.v, bufA, LA); S.setRoute(B.v, bufB, LB);
        return true;
    }
    bool try1(const C& A, double extra) {
        double tw; double km = ev(A.v, A.i, A.mid, A.m, A.u, A.j, tw); if (km < 0) return false;
        int la; double d = routeDelta(A, km, tw, la) + extra;
        if (!accept(d)) return false;
        int LA = compose(S, bufA, A.v, A.i, A.mid, A.m, A.u, A.j);
        S.setRoute(A.v, bufA, LA);
        return true;
    }
    // Внутримаршрутная замена на полную последовательность ns: сама находит общий префикс/суффикс.
    bool tryIntra(int v, const int* ns, int nl, double extra) {
        const Route& R = S.r[v];
        int pre = 0; while (pre < nl && pre < R.len && ns[pre] == R.seq[pre]) pre++;
        if (pre == nl && nl == R.len) return false;
        int suf = 0; while (suf < nl - pre && suf < R.len - pre && ns[nl - 1 - suf] == R.seq[R.len - 1 - suf]) suf++;
        C c{v, pre, ns + pre, nl - pre - suf, v, R.len - suf};
        return try1(c, extra);
    }
    int randRouted() { for (int t = 0; t < 6; t++) { int k = rng.randint(F.N); if (S.where[k] >= 0) return k; } return -1; }
    int randNb(int k) { const auto& v = F.nb[k]; return v[rng.randint((int)v.size())]; }

    // relocate / or-opt: сегмент [i, i+L) маршрута a ставится рядом с соседом n
    void mvRelocate(int L) {
        int k = randRouted(); if (k < 0) return;
        int n = randNb(k); int b = S.where[n]; if (b < 0) return;
        int a = S.where[k], i = S.pos[k]; const Route& RA = S.r[a];
        L = min(L, RA.len - i);
        bool rev = L > 1 && (rng.next() & 1);
        for (int q = 0; q < L; q++) midA[q] = rev ? RA.seq[i + L - 1 - q] : RA.seq[i + q];
        int p = S.pos[n] + (int)(rng.next() & 1);
        if (a != b) {
            try2(C{b, p, midA, L, b, p}, C{a, i, nullptr, 0, a, i + L}, 0);
        } else {
            if (p >= i && p <= i + L) return;
            int nl = 0;
            if (p < i) { for (int q = 0; q < p; q++) tmp[nl++] = RA.seq[q]; for (int q = 0; q < L; q++) tmp[nl++] = midA[q];
                         for (int q = p; q < i; q++) tmp[nl++] = RA.seq[q]; for (int q = i + L; q < RA.len; q++) tmp[nl++] = RA.seq[q]; }
            else { for (int q = 0; q < i; q++) tmp[nl++] = RA.seq[q]; for (int q = i + L; q < p; q++) tmp[nl++] = RA.seq[q];
                   for (int q = 0; q < L; q++) tmp[nl++] = midA[q]; for (int q = p; q < RA.len; q++) tmp[nl++] = RA.seq[q]; }
            tryIntra(a, tmp, nl, 0);
        }
    }
    // relocate в лучшую позицию по всем маршрутам
    void mvRelocateBest() {
        int k = randRouted(); if (k < 0) return;
        int a = S.where[k], i = S.pos[k]; const Route& RA = S.r[a];
        double twA; double kmA = ev(a, i, nullptr, 0, a, i + 1, twA); if (kmA < 0) return;
        int la = RA.len - 1;
        double dA = kmA - RA.km + lam * (twA - RA.tw) + Wv * ((la > 0) - 1) + press(la) - press(RA.len);
        double best = 1e18; int bv = -1, bp = -1;
        for (int v = 0; v < F.V; v++) {
            if (v == a || !(F.skb[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0 && !allowOpen) continue;
            double base = (R.len == 0 ? Wv : 0) + press(R.len + 1) - press(R.len) - R.km - lam * R.tw;
            for (int p = 0; p <= R.len; p++) {
                double tw; double km = ev(v, p, &k, 1, v, p, tw);
                if (km < 0) continue;
                double c = km + lam * tw + base + 1e-3 * rng.uni();
                if (c < best) { best = c; bv = v; bp = p; }
            }
        }
        if (bv < 0 || !accept(best + dA)) return;
        int LB = compose(S, bufB, bv, bp, &k, 1, bv, bp), LA = compose(S, bufA, a, i, nullptr, 0, a, i + 1);
        S.setRoute(a, bufA, LA); S.setRoute(bv, bufB, LB);
    }
    void mvSwap() {
        int k = randRouted(); if (k < 0) return;
        int n = randNb(k); int b = S.where[n]; if (b < 0) return;
        int j = S.pos[n]; int r = rng.randint(3);
        if (r == 1 && j + 1 < S.r[b].len) j++; else if (r == 2 && j > 0) j--;
        int m = S.r[b].seq[j]; if (m == k) return;
        int a = S.where[k], i = S.pos[k];
        if (a != b) { try2(C{a, i, &m, 1, a, i + 1}, C{b, j, &k, 1, b, j + 1}, 0); }
        else { const Route& R = S.r[a]; memcpy(tmp, R.seq, sizeof(int) * R.len); swap(tmp[i], tmp[j]); tryIntra(a, tmp, R.len, 0); }
    }
    void mvCross() {
        int k = randRouted(); if (k < 0) return;
        int n = randNb(k); int b = S.where[n]; int a = S.where[k]; if (b < 0 || a == b) return;
        int i = S.pos[k], j = S.pos[n] + (int)(rng.next() & 1);
        const Route &RA = S.r[a], &RB = S.r[b];
        int L1 = min(1 + rng.randint(3), RA.len - i), L2 = min(rng.randint(4), RB.len - j);
        if (L2 <= 0 && L1 <= 0) return;
        for (int q = 0; q < L1; q++) midA[q] = RA.seq[i + q];
        for (int q = 0; q < L2; q++) midB[q] = RB.seq[j + q];
        try2(C{b, j, midA, L1, b, j + L2}, C{a, i, midB, L2, a, i + L1}, 0);
    }
    void mv2optStar() {
        int k = randRouted(); if (k < 0) return;
        int n = randNb(k); int b = S.where[n]; int a = S.where[k]; if (b < 0 || a == b) return;
        int i = S.pos[k], j = S.pos[n];
        if (rng.next() & 1) try2(C{a, i + 1, nullptr, 0, b, j}, C{b, j, nullptr, 0, a, i + 1}, 0);   // k -> n
        else try2(C{b, j + 1, nullptr, 0, a, i}, C{a, i, nullptr, 0, b, j + 1}, 0);                // n -> k
    }
    void mv2opt() {
        int k = randRouted(); if (k < 0) return;
        int n = randNb(k); int a = S.where[k]; if (S.where[n] != a) return;
        int i = S.pos[k], j = S.pos[n]; if (i > j) swap(i, j);
        const Route& R = S.r[a]; memcpy(tmp, R.seq, sizeof(int) * R.len);
        reverse(tmp + i + 1, tmp + j + 1);   // i -> j станет соседним
        tryIntra(a, tmp, R.len, 0);
    }
    // хвост (или весь маршрут) на пустую бригаду; либо обмен бригад целиком
    bool allowOpen = true, allowOut = true, allowElim = false;
    void mvVehicle() {
        int a = rng.randint(F.V), e = rng.randint(F.V); if (a == e || S.r[a].len == 0) return;
        if (S.r[e].len == 0) {
            int i = rng.randint(S.r[a].len); if (!allowOpen || (rng.next() & 1)) i = 0;
            try2(C{e, 0, nullptr, 0, a, i}, C{a, i, nullptr, 0, -1, 0}, 0);
        } else try2(C{a, 0, nullptr, 0, e, 0}, C{e, 0, nullptr, 0, a, 0}, 0);
    }
    // Лучшая вставка (с учётом time warp в twMode): стоимость = Δкм + λ·Δtw + открытие + давление.
    double elimTwMax = 1e18;
    Ins bestIns2(int k, double openCost, bool allowEmpty, int skipRoute) {
        Ins best; long long tried[MAXV]; int nt = 0;
        for (int v = 0; v < F.V; v++) {
            if (v == skipRoute || !(F.skb[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0) {
                if (!allowEmpty) continue;
                long long key = ((long long)F.st[v] * 8 + F.mode[v]) * 1000003LL + F.mask[v];
                bool dup = false; for (int q = 0; q < nt; q++) if (tried[q] == key) dup = true;
                if (dup) continue; tried[nt++] = key;
            }
            double base = (R.len == 0 ? openCost : 0) + press(R.len + 1) - press(R.len) - R.km - lam * R.tw;
            for (int p = 0; p <= R.len; p++) {
                double tw; double km = ev(v, p, &k, 1, v, p, tw);
                if (km < 0) continue;
                double c = km + lam * tw + base;
                if (c < best.cost) best = {v, p, c, km};
            }
        }
        return best;
    }
    void mvPoolIns() {
        int k = S.pool[rng.randint(S.np)];
        Ins in = bestIns2(k, Wv, allowOpen, -1);
        if (in.v < 0 || !accept(in.cost - wp[k])) return;
        int L = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
        S.poolRemove(k); S.setRoute(in.v, bufA, L);
    }
    // заявка из пула вытесняет соседа (или случайную заявку маршрута соседа) в пул
    void mvEject() {
        int k = S.pool[rng.randint(S.np)];
        int n = randNb(k); int b = S.where[n]; if (b < 0) return;
        int j = S.pos[n]; const Route& R = S.r[b];
        int mode = rng.randint(3);
        if (mode == 0) {
            if (!try1(C{b, j, &k, 1, b, j + 1}, wp[n] - wp[k])) return;
            S.poolRemove(k); S.poolAdd(n);
        } else {
            int p = j + (mode == 2), q = rng.randint(R.len);
            int e = R.seq[q], nl = 0;
            for (int t = 0; t <= R.len; t++) { if (t == p) tmp[nl++] = k; if (t < R.len && t != q) tmp[nl++] = R.seq[t]; }
            if (!tryIntra(b, tmp, nl, wp[e] - wp[k])) return;
            S.poolRemove(k); S.poolAdd(e);
        }
    }
    // Внутримаршрутная оценка полной последовательности (без применения); -1 если недопустимо
    double evalFull(int v, const int* ns, int nl, double& tw) {
        const Route& R = S.r[v];
        int pre = 0; while (pre < nl && pre < R.len && ns[pre] == R.seq[pre]) pre++;
        int suf = 0; while (suf < nl - pre && suf < R.len - pre && ns[nl - 1 - suf] == R.seq[R.len - 1 - suf]) suf++;
        return ev(v, pre, ns + pre, nl - pre - suf, v, R.len - suf, tw);
    }
    // Вставка заявки из пула с вытеснением не более одной заявки: перебор всех маршрутов/позиций
    int ebBest[MAXL];
    void mvEjectBest() {
        int k = S.pool[rng.randint(S.np)];
        double best = 1e18; int bv = -1, be = -1, bl = 0;
        for (int v = 0; v < F.V; v++) {
            if (!(F.skb[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0) continue;
            for (int q = -1; q < R.len; q++) {
                int e = q >= 0 ? R.seq[q] : -1;
                double ew = e >= 0 ? wp[e] : 0;
                if (ew - wp[k] >= best) continue;
                for (int p = 0; p <= R.len; p++) {
                    if (q >= 0 && (p == q || p == q + 1) && p != q) continue;
                    int nl = 0;
                    for (int t = 0; t <= R.len; t++) { if (t == p) tmp[nl++] = k; if (t < R.len && t != q) tmp[nl++] = R.seq[t]; }
                    double tw; double km = evalFull(v, tmp, nl, tw);
                    if (km < 0) continue;
                    double d = km - R.km + lam * (tw - R.tw) + ew - wp[k] + press(nl) - press(R.len) + 1e-3 * rng.uni();
                    if (d < best) { best = d; bv = v; be = e; bl = nl; memcpy(ebBest, tmp, sizeof(int) * nl); }
                }
            }
        }
        if (bv < 0 || !accept(best)) return;
        S.poolRemove(k); S.setRoute(bv, ebBest, bl);
        if (be >= 0) S.poolAdd(be);
    }
    void mvUnassign() {
        int k = randRouted(); if (k < 0) return;
        int a = S.where[k], i = S.pos[k];
        if (!try1(C{a, i, nullptr, 0, a, i + 1}, wp[k])) return;
        S.poolAdd(k);
    }
    // удаление маршрута: заявки жадно в другие непустые маршруты, остаток — в пул
    int elimOrd[MAXL], bkLen[MAXV], bkSeq[MAXV][MAXL];
    int pickSmallRoute() {
        double wsum = 0; int nz = 0;
        for (int v = 0; v < F.V; v++) if (S.r[v].len) { wsum += 1.0 / (S.r[v].len * S.r[v].len); nz++; }
        if (nz <= 1) return -1;
        double x = rng.uni() * wsum;
        for (int v = 0; v < F.V; v++) if (S.r[v].len) { x -= 1.0 / (S.r[v].len * S.r[v].len); if (x <= 0) return v; }
        return -1;
    }
    // Выбор маршрута для удаления по «лёгкости»: пробное жадное удаление каждого маршрута, число/штраф
    // невставленных + уже сделанные попытки с этим маршрутом (сбрасываются при новом лучшем) + шум.
    int elimPick = 0; int triedCnt[MAXV]; double triedBest = -1;
    int pickEasyRoute() {
        if (triedBest != bestScore) { memset(triedCnt, 0, sizeof(triedCnt)); triedBest = bestScore; }
        int nz = 0; for (int v = 0; v < F.V; v++) if (S.r[v].len) nz++;
        if (nz <= 1) return -1;
        backupAll();
        double bestSc = 1e18; int br = -1; int left[MAXL];
        for (int v = 0; v < F.V; v++) {
            if (!S.r[v].len) continue;
            int L = S.r[v].len;
            int nl = eliminate(v, left);
            double lp = 0; for (int q = 0; q < nl; q++) lp += F.pen[left[q]];
            for (int q = 0; q < nl; q++) S.poolRemove(left[q]);
            restoreAll();
            double sc = lp / 20.0 + 0.05 * L + 1.5 * triedCnt[v] + rng.uni();
            if (sc < bestSc) { bestSc = sc; br = v; }
        }
        if (br >= 0) triedCnt[br]++;
        return br;
    }
    int eliminate(int r, int* left) {
        int L = S.r[r].len; memcpy(elimOrd, S.r[r].seq, sizeof(int) * L);
        if (rng.next() & 1) sort(elimOrd, elimOrd + L, [&](int p, int q) { return F.b[p] - F.a[p] < F.b[q] - F.a[q]; });
        else for (int q = L - 1; q > 0; q--) swap(elimOrd[q], elimOrd[rng.randint(q + 1)]);
        S.setRoute(r, nullptr, 0);
        int nleft = 0;
        for (int q = 0; q < L; q++) {
            int k = elimOrd[q];
            Ins in = bestIns2(k, Wv, false, r);
            if (in.v < 0 || (twMode && in.cost > elimTwMax)) { S.poolAdd(k); left[nleft++] = k; continue; }
            int nl = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
            S.setRoute(in.v, bufA, nl);
        }
        return nleft;
    }
    void mvRouteElim() {   // как ход SA (принятие по суррогату)
        int r = pickSmallRoute(); if (r < 0) return;
        elimTry++;
        double before = surrogate();
        for (int v = 0; v < F.V; v++) { bkLen[v] = S.r[v].len; memcpy(bkSeq[v], S.r[v].seq, sizeof(int) * S.r[v].len); }
        int left[MAXL]; int nleft = eliminate(r, left);
        if (accept(surrogate() - before)) { elimAcc++; return; }
        for (int q = 0; q < nleft; q++) S.poolRemove(left[q]);
        for (int v = 0; v < F.V; v++) {
            bool same = bkLen[v] == S.r[v].len && !memcmp(bkSeq[v], S.r[v].seq, sizeof(int) * bkLen[v]);
            if (!same) S.setRoute(v, bkSeq[v], bkLen[v]);
        }
    }

    // Разрушение-восстановление: (0) k и его ближайшие соседи; (1) весь маршрут a с разрешением открыть одну пустую
    // бригаду e (смена бригады в разнородном парке). Вставка жадная по лучшей позиции, принятие по суррогату.
    int rzSet[MAXL], rzN = 0; bool inRz[MAXN];
    double pRuin = 0.0; int ruinMax = 10; bool ruinSwap = false;
    Ins bestInsOnly(int k, int skipRoute, int openV) {
        Ins best;
        for (int v = 0; v < F.V; v++) {
            if (v == skipRoute || !(F.skb[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0 && v != openV && !allowOpen) continue;
            double base = (R.len == 0 ? (v == openV ? 0 : Wv) : 0) + press(R.len + 1) - press(R.len) - R.km;
            for (int p = 0; p <= R.len; p++) {
                double km = evalCand(F, S, v, p, &k, 1, v, p);
                if (km < 0) continue;
                double c = km + base + 1e-3 * rng.uni();
                if (c < best.cost) best = {v, p, c, km};
            }
        }
        return best;
    }
    void backupAll() { for (int v = 0; v < F.V; v++) { bkLen[v] = S.r[v].len; memcpy(bkSeq[v], S.r[v].seq, sizeof(int) * S.r[v].len); } }
    void restoreAll() {
        for (int v = 0; v < F.V; v++) {
            bool same = bkLen[v] == S.r[v].len && !memcmp(bkSeq[v], S.r[v].seq, sizeof(int) * bkLen[v]);
            if (!same) S.setRoute(v, bkSeq[v], bkLen[v]);
        }
    }
    long long ruinTry = 0, ruinAcc = 0;
    void mvRuin() {
        int mode = ruinSwap && rng.uni() < 0.3 ? 1 : 0;
        int openV = -1, skipR = -1;
        rzN = 0;
        if (mode == 0) {
            int k = randRouted(); if (k < 0) return;
            int q = 2 + rng.randint(ruinMax - 1);
            rzSet[rzN++] = k;
            for (int n : F.nb[k]) { if (rzN >= q) break; if (S.where[n] >= 0 && rng.uni() < 0.8) rzSet[rzN++] = n; }
        }
        else {
            int e = rng.randint(F.V); if (S.r[e].len != 0) return;
            int a = pickSmallRoute(); if (a < 0 || a == e) return;
            if (!(S.r[a].need[0] & F.mask[e])) return;
            for (int q = 0; q < S.r[a].len; q++) rzSet[rzN++] = S.r[a].seq[q];
            if (rng.next() & 1) {   // плюс ближайшие к старту e заявки из других маршрутов
                int q = 2 + rng.randint(ruinMax - 1), c = 0;
                for (int k : nearSt[e]) { if (c >= q) break; if (S.where[k] >= 0 && S.where[k] != a) { rzSet[rzN++] = k; c++; } }
            }
            openV = e; skipR = a;
        }
        ruinTry++;
        double before = surrogate();
        backupAll();
        for (int q = 0; q < rzN; q++) inRz[rzSet[q]] = true;
        // снять заявки с маршрутов
        bool touched[MAXV] = {};
        for (int q = 0; q < rzN; q++) touched[S.where[rzSet[q]]] = true;
        for (int v = 0; v < F.V; v++) if (touched[v]) {
            int nl = 0; const Route& R = S.r[v];
            for (int t = 0; t < R.len; t++) if (!inRz[R.seq[t]]) tmp[nl++] = R.seq[t];
            S.setRoute(v, tmp, nl);
        }
        for (int q = 0; q < rzN; q++) { inRz[rzSet[q]] = false; S.where[rzSet[q]] = -2; }
        // заявки из пула тоже пробуем вставить
        int poolTaken[4], npt = 0;
        if (S.np > 0) { int c = 1 + rng.randint(min(3, S.np)); for (int q = 0; q < c && S.np > 0; q++) { int k = S.pool[rng.randint(S.np)]; S.poolRemove(k); poolTaken[npt++] = k; rzSet[rzN++] = k; } }
        // порядок вставки
        int o = rng.randint(3);
        if (o == 0) for (int q = rzN - 1; q > 0; q--) swap(rzSet[q], rzSet[rng.randint(q + 1)]);
        else if (o == 1) sort(rzSet, rzSet + rzN, [&](int x, int y) { return F.b[x] - F.a[x] < F.b[y] - F.a[y]; });
        else sort(rzSet, rzSet + rzN, [&](int x, int y) { return F.a[x] < F.a[y]; });
        int nfail = 0, failed[MAXL];
        for (int q = 0; q < rzN; q++) {
            int k = rzSet[q];
            Ins in = bestInsOnly(k, mode == 1 ? skipR : -1, openV);
            if (in.v < 0) { failed[nfail++] = k; S.poolAdd(k); continue; }
            int nl = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
            S.setRoute(in.v, bufA, nl);
        }
        if (accept(surrogate() - before)) { ruinAcc++; return; }
        for (int q = 0; q < nfail; q++) S.poolRemove(failed[q]);
        restoreAll();
        for (int q = 0; q < npt; q++) S.poolAdd(poolTaken[q]);
    }

    // Разбиение + удаление (смена набора бригад при том же их числе): хвост маршрута a (с позиции i) переносится на
    // пустую бригаду e (вариант с лучшим Δкм + шум), затем удаляется малый маршрут (пул, SA без открытия бригад),
    // затем короткий отжиг км. Лучшее отслеживается как обычно (checkBest).
    bool useTW = false, seTW = false, useReassign = true, elimLog = getenv("SA_ELIMLOG") != nullptr;
    double fSE = 0.0, seNoise = 0.3, seT0 = 2; long long seElimIters = 300000, seKmIters = 300000;
    long long seTry = 0, seOk = 0, seImp = 0;
    bool splitMove() {
        double best = 1e18; int ba = -1, bi = -1, be = -1;
        long long tried[MAXV]; int nt = 0;
        double noise = seNoise * S.km / max(1, S.used);
        for (int e = 0; e < F.V; e++) {
            if (S.r[e].len) continue;
            long long key = ((long long)F.st[e] * 8 + F.mode[e]) * 1000003LL + F.mask[e];
            bool dup = false; for (int q = 0; q < nt; q++) if (tried[q] == key) dup = true;
            if (dup) continue; tried[nt++] = key;
            for (int a = 0; a < F.V; a++) {
                const Route& R = S.r[a]; if (!R.len) continue;
                for (int i = 0; i < R.len; i++) {
                    if (R.need[i] & ~F.mask[e]) continue;
                    double kmE = evalCand(F, S, e, 0, nullptr, 0, a, i); if (kmE < 0) continue;
                    double d = kmE + (i > 0 ? R.ck[i - 1] : 0) - R.km + noise * rng.uni();
                    if (d < best) { best = d; ba = a; bi = i; be = e; }
                }
            }
        }
        if (ba < 0) return false;
        int LE = compose(S, bufA, be, 0, nullptr, 0, ba, bi);
        S.setRoute(be, bufA, LE);
        memcpy(bufB, S.r[ba].seq, sizeof(int) * bi); S.setRoute(ba, bufB, bi);
        return true;
    }
    void seCycle(STimer& tm, double tEnd, double Tk0, double Tk1) {
        restoreBest();
        double pen0 = S.poolPen, b0 = bestScore;
        if (!splitMove()) return;
        seTry++;
        int r = pickSmallRoute(); if (r < 0) return;
        twMode = useTW && seTW; lam = lam0;
        int left[MAXL]; eliminate(r, left);
        resetWp();
        allowOpen = false; allowOut = true; beta = twMode ? betaTW : beta0; ruinSwap = false;
        double Tsave = T;
        bool ok = (S.poolPen <= pen0 + 1e-9 && S.tw <= 1e-9) || segment(seElimIters, tEnd, Te, Te, pen0, tm);
        twMode = false; lam = lam0;
        if (ok) {
            seOk++;
            if (useReassign) reassign();
            allowOut = false; beta = 0; ruinSwap = true;
            for (int k = 0; k < F.N; k++) wp[k] = 1e5 * F.pen[k] / 50;
            segment(seKmIters, tEnd, Tk0, Tk1, -1, tm);
            if (bestScore < b0 - 1e-9) seImp++;
        }
        allowOut = false; beta = 0; ruinSwap = true; T = Tsave;
        for (int k = 0; k < F.N; k++) wp[k] = 1e5 * F.pen[k] / 50;
    }

    // Оптимальное переназначение маршрутов бригадам целиком (венгерский алгоритм, маршруты x бригады).
    double seqKm(int v, const int* sq, int len) const {
        const double* T = F.T[v]; const double* D = F.D[v]; int M = F.M, off = F.S, msk = F.mask[v];
        double t = 0, dist = 0; int prev = F.st[v];
        for (int q = 0; q < len; q++) {
            int k = sq[q]; if (!(F.skb[k] & msk)) return -1;
            int n = off + k; t += T[prev * M + n]; if (t < F.a[k]) t = F.a[k];
            if (t > F.b[k] + TEPS) return -1;
            t += F.svc[k]; if (t > SHIFT + TEPS) return -1;
            dist += D[prev * M + n]; prev = n;
        }
        return dist;
    }
    long long reassignImp = 0;
    bool reassign() {
        int rs[MAXV], n = 0, m = F.V;
        for (int v = 0; v < F.V; v++) if (S.r[v].len) rs[n++] = v;
        if (n == 0) return false;
        const double INF = 1e9;
        static double c[MAXV + 1][MAXV + 1];
        for (int i = 0; i < n; i++) for (int v = 0; v < m; v++) { double x = seqKm(v, S.r[rs[i]].seq, S.r[rs[i]].len); c[i + 1][v + 1] = x < 0 ? INF : x; }
        // e-maxx: n <= m
        vector<double> u(n + 1), w(m + 1); vector<int> p(m + 1), way(m + 1);
        for (int i = 1; i <= n; i++) {
            p[0] = i; int j0 = 0; vector<double> minv(m + 1, 1e18); vector<char> used(m + 1, 0);
            do {
                used[j0] = 1; int i0 = p[j0], j1 = 0; double delta = 1e18;
                for (int j = 1; j <= m; j++) if (!used[j]) {
                    double cur = c[i0][j] - u[i0] - w[j];
                    if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
                    if (minv[j] < delta) { delta = minv[j]; j1 = j; }
                }
                for (int j = 0; j <= m; j++) if (used[j]) { u[p[j]] += delta; w[j] -= delta; } else minv[j] -= delta;
                j0 = j1;
            } while (p[j0] != 0);
            do { int j1 = way[j0]; p[j0] = p[j1]; j0 = j1; } while (j0);
        }
        double tot = 0; int asg[MAXV];
        for (int j = 1; j <= m; j++) if (p[j]) { asg[p[j] - 1] = j - 1; tot += c[p[j]][j]; }
        if (tot >= INF / 2 || tot > S.km - 1e-6) return false;
        static int seqs[MAXV][MAXL]; int lens[MAXV];
        for (int i = 0; i < n; i++) { lens[i] = S.r[rs[i]].len; memcpy(seqs[i], S.r[rs[i]].seq, sizeof(int) * lens[i]); }
        for (int v = 0; v < F.V; v++) if (S.r[v].len) S.setRoute(v, nullptr, 0);
        for (int i = 0; i < n; i++) S.setRoute(asg[i], seqs[i], lens[i]);
        reassignImp++;
        return true;
    }
    void step() {
        double r = rng.uni();
        if (S.np > 0 && r < 0.12) {
            if (r < 0.07 || !allowOut) mvPoolIns();
            else if (r < 0.12 - pEB) mvEject(); else mvEjectBest();
            return;
        }
        if (pRuin > 0 && rng.uni() < pRuin) { mvRuin(); return; }
        r = rng.uni();
        if (r < pRB) mvRelocateBest();
        else if (r < 0.28) mvRelocate(1);
        else if (r < 0.43) mvRelocate(2 + rng.randint(2));
        else if (r < 0.58) mvSwap();
        else if (r < 0.70) mvCross();
        else if (r < 0.85) mv2optStar();
        else if (r < 0.90) mv2opt();
        else if (r < 0.96) mvVehicle();
        else if (allowOut) { if (r < 0.9985 || !allowElim) mvUnassign(); else mvRouteElim(); }
    }
    double wpScale = 1, tScale = 0, kmWv = 0; bool kmOpen = false;
    void resetWp() { for (int k = 0; k < F.N; k++) wp[k] = wpScale * Wp0 * F.pen[k] / 50; }
    void growWp() { for (int q = 0; q < S.np; q++) wp[S.pool[q]] += wpScale * Winc * F.pen[S.pool[q]] / 50; if (twMode && S.tw > 1e-9) lam = lam * lamMul + lamAdd; }
    // Отрезок SA: до maxIt итераций или до tEnd; T геометрически Ta→Tb. stopPen>=0: выйти, как только штраф пула ≤ stopPen.
    bool segment(long long maxIt, double tEnd, double Ta, double Tb, double stopPen, STimer& tm) {
        double t0 = tm.sec(); long long it = 0;
        while (true) {
            if ((it & 255) == 0) {
                double el = tm.sec(); if (el >= tEnd) return false;
                double x = max((double)it / maxIt, (el - t0) / max(1e-9, tEnd - t0)); if (x >= 1) return false;
                T = pow(Ta, 1 - x) * pow(Tb, x);
                if (dbg && (iters & ((1 << 20) - 1)) < 256) fprintf(stderr, "t=%.3f T=%.3f km=%.1f used=%d np=%d best=%.1f\n", el, T, S.km, S.used, S.np, bestScore);
            }
            it++; iters++;
            if (it % incPeriod == 0) growWp();
            step();
            checkBest();
            if (stopPen >= 0 && S.poolPen <= stopPen + 1e-9 && S.tw <= 1e-9) return true;
        }
    }
    void restoreBest() { S.fromRoutes(F, bestRoutes()); }

    double pRB = 0.02, pEB = 0.01, wpScaleRepair = 10, WvRepair = 5, betaRepair = 0, fRepair = 0.3, fElim = 0.6, Te = 50, Tr = 20; long long elimIters = 1000000, repIters = 1000000; int maxFails = 40;
    Routes run(const Routes& init, double tl, STimer& tm) {
        S.fromRoutes(F, init);
        resetWp(); checkBest();
        double tStart = tm.sec(), span = max(1e-3, tl - tStart);
        // 1) починка: обслужить всё, открывать бригады можно; рестарты со случайных жадных решений
        if (S.np > 0) {
            allowOpen = true; allowOut = true; allowElim = false; beta = betaRepair;
            double wv = Wv; Wv = WvRepair; wpScale = wpScaleRepair;
            double tRep = tStart + span * fRepair;
            for (int att = 0; tm.sec() < tRep; att++) {
                if (att > 0) {   // новое случайное жадное решение
                    vector<int> o(F.N); iota(o.begin(), o.end(), 0);
                    vector<double> key(F.N); for (int k = 0; k < F.N; k++) key[k] = F.b[k] + F.a[k] * 1e-3 + rng.uni() * 90;
                    sort(o.begin(), o.end(), [&](int x, int y) { return key[x] < key[y]; });
                    static const double oc[] = {0, 20, 100, 1e4};
                    greedyBuild(F, S, o, oc[rng.randint(4)]);
                    checkBest();
                }
                resetWp();
                if (segment(repIters, tRep, Tr, Tr, 0, tm)) break;
            }
            Wv = wv; wpScale = 1;
            restoreBest();
        }
        // 2) сокращение бригад: удалить маршрут, SA без открытия бригад до опустошения пула
        tRepEnd = tm.sec();
        int fails = 0;
        while (tm.sec() < tStart + span * fElim && fails < maxFails) {
            restoreBest();
            if (useLB && S.np == 0 && S.used <= workLB) break;   // нижняя граница по нагрузке достигнута
            int r = elimPick ? pickEasyRoute() : pickSmallRoute(); if (r < 0) break;
            double target = S.poolPen;
            elimTry++;
            twMode = useTW && (twAlt == 0 || elimTry % twAlt == 0); lam = lam0;
            int left[MAXL]; eliminate(r, left);
            resetWp();
            allowOpen = false; allowOut = true; allowElim = false; beta = twMode ? betaTW : beta0;
            double wvSave = Wv; if (twMode) Wv = WvTW;
            bool ok = (S.poolPen <= target + 1e-9 && S.tw <= 1e-9) || segment(elimIters, tStart + span * fElim, Te, Te, target, tm);
            if (getenv("SA_TWDBG")) { double sw = 0; for (int v = 0; v < F.V; v++) sw += S.r[v].tw; fprintf(stderr, "attempt ok=%d np=%d tw=%.6f sum=%.6f lam=%.3g km=%.1f used=%d\n", ok, S.np, S.tw, sw, lam, S.km, S.used); }
            Wv = wvSave;
            checkBest();
            twMode = false; lam = lam0;
            if (ok) { elimAcc++; if (elimLog) fprintf(stderr, "ELIMOK fails=%d t=%.3f used=%d\n", fails, tm.sec() / tl, S.used); fails = 0; } else fails++;
        }
        tElimEnd = tm.sec();
        // 3) км при фиксированном числе бригад
        restoreBest(); usedAfterElim = S.used; kmAfterElim = S.km;
        allowOpen = S.np > 0 || kmOpen; allowOut = false; allowElim = false; beta = 0; ruinSwap = true; if (kmWv > 0) Wv = kmWv;
        for (int k = 0; k < F.N; k++) wp[k] = 1e5 * F.pen[k] / 50;
        double sc = tScale > 0 ? tScale * S.km / max(1, F.N) : 1;
        if (useReassign) { reassign(); checkBest(); }
        double tKm0 = tm.sec();
        segment(1LL << 60, fSE > 0 ? tKm0 + (tl - tKm0) * (1 - fSE) : tl, T0 * sc, T1 * sc, -1, tm);
        if (fSE > 0 && S.np == 0) while (tm.sec() < tl) seCycle(tm, tl, seT0 * sc, T1 * sc);
        if (useReassign) { restoreBest(); if (reassign()) checkBest(); }
        if (dbg) { for (int q = 0; q < S.np; q++) { int k = S.pool[q]; fprintf(stderr, "pool k=%d wp=%.1f pen=%.0f a=%.0f b=%.0f svc=%.0f sk=%d\n", k, wp[k], F.pen[k], F.a[k], F.b[k], F.svc[k], F.skill[k]); } }
        return bestRoutes();
    }
};


// Параметры продуктового варианта (s23): time warp при удалении маршрутов, нижняя граница, разбиение+удаление, венгерский.
inline void sa2Defaults(SA2& sa) {
    sa.fSE = 0.5; sa.useTW = false; sa.lam0 = 5; sa.lamMul = 1; sa.lamAdd = 1; sa.useLB = true; sa.useReassign = true;
}
inline void sa2Env(SA2& sa) {
    if (getenv("SA_WV")) sa.Wv = atof(getenv("SA_WV"));
    if (getenv("SA_BETA")) sa.beta0 = atof(getenv("SA_BETA"));
    if (getenv("SA_T0")) sa.T0 = atof(getenv("SA_T0"));
    if (getenv("SA_T1")) sa.T1 = atof(getenv("SA_T1"));
    if (getenv("SA_WP")) sa.Wp0 = atof(getenv("SA_WP"));
    if (getenv("SA_WINC")) sa.Winc = atof(getenv("SA_WINC"));
    if (getenv("SA_WVR")) sa.WvRepair = atof(getenv("SA_WVR"));
    if (getenv("SA_BETAR")) sa.betaRepair = atof(getenv("SA_BETAR"));
    if (getenv("SA_WPSR")) sa.wpScaleRepair = atof(getenv("SA_WPSR"));
    if (getenv("SA_PEB")) sa.pEB = atof(getenv("SA_PEB"));
    if (getenv("SA_REPIT")) sa.repIters = atoll(getenv("SA_REPIT"));
    if (getenv("SA_FREP")) sa.fRepair = atof(getenv("SA_FREP"));
    if (getenv("SA_PRB")) sa.pRB = atof(getenv("SA_PRB"));
    if (getenv("SA_TE")) sa.Te = atof(getenv("SA_TE"));
    if (getenv("SA_TR")) sa.Tr = atof(getenv("SA_TR"));
    if (getenv("SA_FELIM")) sa.fElim = atof(getenv("SA_FELIM"));
    if (getenv("SA_EIT")) sa.elimIters = atoll(getenv("SA_EIT"));
    if (getenv("SA_FAILS")) sa.maxFails = atoi(getenv("SA_FAILS"));
    if (getenv("SA_TSC")) sa.tScale = atof(getenv("SA_TSC"));
    if (getenv("SA_PRUIN")) sa.pRuin = atof(getenv("SA_PRUIN"));
    if (getenv("SA_RMAX")) sa.ruinMax = atoi(getenv("SA_RMAX"));
    if (getenv("SA_KMOPEN")) sa.kmOpen = atoi(getenv("SA_KMOPEN"));
    if (getenv("SA_KMWV")) sa.kmWv = atof(getenv("SA_KMWV"));
    if (getenv("SA_FSE")) sa.fSE = atof(getenv("SA_FSE"));
    if (getenv("SA_SENOISE")) sa.seNoise = atof(getenv("SA_SENOISE"));
    if (getenv("SA_SET0")) sa.seT0 = atof(getenv("SA_SET0"));
    if (getenv("SA_SEEIT")) sa.seElimIters = atoll(getenv("SA_SEEIT"));
    if (getenv("SA_SEKIT")) sa.seKmIters = atoll(getenv("SA_SEKIT"));
    if (getenv("SA_REASSIGN")) sa.useReassign = atoi(getenv("SA_REASSIGN"));
    if (getenv("SA_TW")) sa.useTW = atoi(getenv("SA_TW"));
    if (getenv("SA_SETW")) sa.seTW = atoi(getenv("SA_SETW"));
    if (getenv("SA_LAM")) sa.lam0 = atof(getenv("SA_LAM"));
    if (getenv("SA_LAMMUL")) sa.lamMul = atof(getenv("SA_LAMMUL"));
    if (getenv("SA_TWMAX")) sa.elimTwMax = atof(getenv("SA_TWMAX"));
    if (getenv("SA_BETATW")) sa.betaTW = atof(getenv("SA_BETATW"));
    if (getenv("SA_WVTW")) sa.WvTW = atof(getenv("SA_WVTW"));
    if (getenv("SA_LAMADD")) sa.lamAdd = atof(getenv("SA_LAMADD"));
    if (getenv("SA_LB")) sa.useLB = atoi(getenv("SA_LB"));
    if (getenv("SA_TWALT")) sa.twAlt = atoi(getenv("SA_TWALT"));
    if (getenv("SA_EPICK")) sa.elimPick = atoi(getenv("SA_EPICK"));
    if (getenv("SA_INC")) sa.incPeriod = atoi(getenv("SA_INC"));
}
