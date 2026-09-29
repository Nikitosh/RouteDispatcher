// Общая инфраструктура для s10_sa и s11_tabu: быстрые маршруты с кэшем (время отъезда, накопленный км,
// latest-start для суффикса, маска нужных навыков суффикса), инкрементальная проверка кандидата
// «префикс v + середина + суффикс u», пул невыполненных, жадное построение.
#pragma once
#include "common.hpp"

namespace sau {
constexpr int MAXN = 160, MAXV = 16, MAXL = 168;
constexpr double TEPS = 1e-7;

struct Fast {
    int N, V, S, M;
    const double* T[MAXV]; const double* D[MAXV];
    int mode[MAXV], st[MAXV], mask[MAXV];
    double a[MAXN], b[MAXN], svc[MAXN], pen[MAXN]; int skb[MAXN], skill[MAXN];
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
            a[k] = I.ord[k].a; b[k] = I.ord[k].b; svc[k] = I.ord[k].svc; pen[k] = I.penalty(k);
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
    int len = 0; double km = 0;
    int seq[MAXL]; double dep[MAXL], ck[MAXL], lat[MAXL]; int need[MAXL + 1];
};

struct Sol {
    const Fast* F = nullptr;
    Route r[MAXV];
    int where[MAXN], pos[MAXN];
    int pool[MAXN], np = 0, pidx[MAXN];   // пул только из обслуживаемых (servable) заявок
    double km = 0; int used = 0; double poolPen = 0, constPen = 0;

    void init(const Fast& f) {
        F = &f; np = 0; km = 0; used = 0; poolPen = 0; constPen = 0;
        for (int v = 0; v < F->V; v++) { r[v].len = 0; r[v].km = 0; r[v].need[0] = 0; }
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
        km -= R.km; if (R.len) used--;
        if (len && s != R.seq) memcpy(R.seq, s, sizeof(int) * len);
        R.len = len;
        const double* T = F->T[v]; const double* D = F->D[v]; int M = F->M, S = F->S;
        double t = 0, dist = 0; int prev = F->st[v];
        for (int p = 0; p < len; p++) {
            int k = R.seq[p], n = S + k;
            t += T[prev * M + n]; if (t < F->a[k]) t = F->a[k];
            dist += D[prev * M + n]; R.ck[p] = dist;
            t += F->svc[k]; R.dep[p] = t; prev = n;
            where[k] = v; pos[k] = p;
        }
        R.need[len] = 0;
        for (int p = len - 1; p >= 0; p--) {
            int k = R.seq[p];
            double l = min(F->b[k], SHIFT - F->svc[k]);
            if (p + 1 < len) l = min(l, R.lat[p + 1] - F->svc[k] - T[(S + k) * M + S + R.seq[p + 1]]);
            R.lat[p] = l; R.need[p] = R.need[p + 1] | F->skb[k];
        }
        R.km = dist; km += dist; if (len) used++;
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
}  // namespace sau
