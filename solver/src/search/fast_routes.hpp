// Маршруты на фиксированных массивах с кэшем для отжига (search/annealer.hpp).
//
// Для каждой позиции маршрута хранятся время окончания работы (dep), накопленный пробег (ck), самое позднее допустимое
// начало с учётом хвоста (lat) и маска навыков, нужных хвосту (need). Поэтому кандидат «префикс маршрута v + середина +
// хвост маршрута u» проверяется за O(длина середины), если у u и v один вид транспорта. Невыполненные заявки лежат в
// пуле; в пул попадают только заявки, которые в принципе может выполнить хоть одна бригада.
#pragma once
#include "core/problem.hpp"

namespace dispatch::fast {

constexpr int MAX_ORDERS = 160;
constexpr int MAX_VEHICLES = 16;
constexpr int MAX_LEN = 168;

// Данные задачи в плоских массивах и соседи каждой заявки по «времени-расстоянию».
struct Data {
    int N, V, S, M;
    const double* T[MAX_VEHICLES];
    const double* D[MAX_VEHICLES];
    int mode[MAX_VEHICLES], start[MAX_VEHICLES], mask[MAX_VEHICLES];
    double a[MAX_ORDERS], b[MAX_ORDERS], svc[MAX_ORDERS], pen[MAX_ORDERS];
    int skillBit[MAX_ORDERS];
    bool servable[MAX_ORDERS];
    vector<vector<int>> neighbors;

    explicit Data(const Instance& I, int nNeighbors = 16) : N(I.N), V(I.V), S(I.S), M(I.M) {
        if (N > MAX_ORDERS - 8 || V > MAX_VEHICLES) {
            fprintf(stderr, "задача больше предела fast::Data (%d заявок, %d бригад)\n", MAX_ORDERS - 8, MAX_VEHICLES);
            exit(1);
        }
        for (int v = 0; v < V; v++) {
            mode[v] = I.veh[v].mode;
            start[v] = I.veh[v].start;
            mask[v] = I.veh[v].mask;
            T[v] = I.T[mode[v]].data();
            D[v] = I.D[mode[v]].data();
        }
        for (int k = 0; k < N; k++) {
            a[k] = I.ord[k].a;
            b[k] = I.ord[k].b;
            svc[k] = I.ord[k].svc;
            pen[k] = I.penalty(k);
            skillBit[k] = 1 << I.ord[k].skill;
            servable[k] = false;
            for (int v = 0; v < V && !servable[k]; v++) servable[k] = I.can(v, k) && routeFeasible(I, v, {k});
        }
        buildNeighbors(I, nNeighbors);
    }

private:
    // Близость: время переезда (минимум по видам транспорта бригад) плюс половина ожидания; порядок, при котором
    // вторая заявка не успевает, — очень дорого; заявки без общей бригады — в самый конец.
    void buildNeighbors(const Instance& I, int nNeighbors) {
        vector<int> modes;
        for (int v = 0; v < V; v++)
            if (std::find(modes.begin(), modes.end(), mode[v]) == modes.end()) modes.push_back(mode[v]);
        auto travel = [&](int i, int j) {
            double r = 1e18;
            for (int m : modes) r = min(r, I.T[m][i * M + j]);
            return r;
        };
        neighbors.assign(N, {});
        for (int k = 0; k < N; k++) {
            vector<std::pair<double, int>> c;
            for (int n = 0; n < N; n++) {
                if (n == k) continue;
                double best = 1e18;
                for (int dir = 0; dir < 2; dir++) {
                    int x = dir ? n : k, y = dir ? k : n;
                    double t = travel(S + x, S + y), earliest = a[x] + svc[x] + t;
                    double cost = earliest > b[y] + TOL ? 1e6 + t : t + 0.5 * max(0.0, a[y] - (b[x] + svc[x] + t));
                    best = min(best, cost);
                }
                bool common = false;
                for (int v = 0; v < V; v++) common |= I.can(v, k) && I.can(v, n);
                if (!common) best += 1e7;
                c.push_back({best, n});
            }
            std::sort(c.begin(), c.end());
            for (int q = 0; q < min<int>(nNeighbors, (int)c.size()); q++) neighbors[k].push_back(c[q].second);
        }
    }
};

struct Route {
    int len = 0;
    double km = 0;
    int seq[MAX_LEN];
    double dep[MAX_LEN], ck[MAX_LEN], lat[MAX_LEN];
    int need[MAX_LEN + 1];
};

struct Solution {
    const Data* F = nullptr;
    Route r[MAX_VEHICLES];
    int where[MAX_ORDERS], pos[MAX_ORDERS];          // маршрут и позиция заявки; -1 — в пуле
    int pool[MAX_ORDERS], np = 0, poolIdx[MAX_ORDERS];
    double km = 0, poolPen = 0, fixedPen = 0;        // fixedPen — штраф заявок, которые не может взять никто
    int used = 0;

    void init(const Data& f) {
        F = &f;
        np = 0;
        km = poolPen = fixedPen = 0;
        used = 0;
        for (int v = 0; v < F->V; v++) {
            r[v].len = 0;
            r[v].km = 0;
            r[v].need[0] = 0;
        }
        for (int k = 0; k < F->N; k++) {
            where[k] = pos[k] = poolIdx[k] = -1;
            if (F->servable[k]) poolAdd(k);
            else fixedPen += F->pen[k];
        }
    }
    void poolAdd(int k) {
        where[k] = -1;
        poolIdx[k] = np;
        pool[np++] = k;
        poolPen += F->pen[k];
    }
    void poolRemove(int k) {
        int i = poolIdx[k], last = pool[--np];
        pool[i] = last;
        poolIdx[last] = i;
        poolIdx[k] = -1;
        poolPen -= F->pen[k];
    }
    // Записать маршрут v (seq может указывать на внешний буфер) и пересчитать кэши.
    void setRoute(int v, const int* seq, int len) {
        Route& R = r[v];
        km -= R.km;
        if (R.len) used--;
        if (len && seq != R.seq) memcpy(R.seq, seq, sizeof(int) * len);
        R.len = len;
        const double* T = F->T[v];
        const double* D = F->D[v];
        const int M = F->M, S = F->S;
        double t = 0, dist = 0;
        int prev = F->start[v];
        for (int p = 0; p < len; p++) {
            int k = R.seq[p], n = S + k;
            t = max(t + T[prev * M + n], F->a[k]);
            dist += D[prev * M + n];
            R.ck[p] = dist;
            t += F->svc[k];
            R.dep[p] = t;
            prev = n;
            where[k] = v;
            pos[k] = p;
        }
        R.need[len] = 0;
        for (int p = len - 1; p >= 0; p--) {
            int k = R.seq[p];
            double latest = min(F->b[k], SHIFT - F->svc[k]);
            if (p + 1 < len) latest = min(latest, R.lat[p + 1] - F->svc[k] - T[(S + k) * M + S + R.seq[p + 1]]);
            R.lat[p] = latest;
            R.need[p] = R.need[p + 1] | F->skillBit[k];
        }
        R.km = dist;
        km += dist;
        if (len) used++;
    }
    double score() const { return (poolPen + fixedPen) * W_UNSERVED + used * W_VEHICLE + km; }
    Routes routes() const {
        Routes R(F->V);
        for (int v = 0; v < F->V; v++) R[v].assign(r[v].seq, r[v].seq + r[v].len);
        return R;
    }
    void load(const Data& f, const Routes& R) {
        init(f);
        for (int v = 0; v < F->V && v < (int)R.size(); v++) {
            for (int k : R[v])
                if (poolIdx[k] >= 0) poolRemove(k);
            setRoute(v, R[v].data(), (int)R[v].size());
        }
    }
};

// Пробег нового маршрута бригады v = префикс r[v][0..i) + mid[0..m) + хвост r[u][j..) (u < 0 — без хвоста);
// -1, если маршрут недопустим.
inline double evalCandidate(const Data& F, const Solution& S, int v, int i, const int* mid, int m, int u, int j) {
    const Route& R = S.r[v];
    const double* T = F.T[v];
    const double* D = F.D[v];
    const int M = F.M, off = F.S, msk = F.mask[v];
    double t = i > 0 ? R.dep[i - 1] : 0, dist = i > 0 ? R.ck[i - 1] : 0;
    int prev = i > 0 ? off + R.seq[i - 1] : F.start[v];
    for (int q = 0; q < m; q++) {
        int k = mid[q], n = off + k;
        if (!(F.skillBit[k] & msk)) return -1;
        t = max(t + T[prev * M + n], F.a[k]);
        if (t > F.b[k] + TOL) return -1;
        t += F.svc[k];
        if (t > SHIFT + TOL) return -1;
        dist += D[prev * M + n];
        prev = n;
    }
    if (u < 0 || j >= S.r[u].len) return dist;
    const Route& U = S.r[u];
    if (U.need[j] & ~msk) return -1;
    int n = off + U.seq[j];
    if (F.mode[u] == F.mode[v]) {                   // хвост в той же матрице: проверка по lat, пробег из ck
        if (t + T[prev * M + n] > U.lat[j] + TOL) return -1;
        return dist + D[prev * M + n] + U.ck[U.len - 1] - U.ck[j];
    }
    for (int q = j; q < U.len; q++) {               // другая матрица: хвост пересчитывается целиком
        int k = U.seq[q];
        n = off + k;
        t = max(t + T[prev * M + n], F.a[k]);
        if (t > F.b[k] + TOL) return -1;
        t += F.svc[k];
        if (t > SHIFT + TOL) return -1;
        dist += D[prev * M + n];
        prev = n;
    }
    return dist;
}

// Последовательность кандидата в buf; возвращает её длину.
inline int compose(const Solution& S, int* buf, int v, int i, const int* mid, int m, int u, int j) {
    int L = 0;
    for (int q = 0; q < i; q++) buf[L++] = S.r[v].seq[q];
    for (int q = 0; q < m; q++) buf[L++] = mid[q];
    if (u >= 0)
        for (int q = j; q < S.r[u].len; q++) buf[L++] = S.r[u].seq[q];
    return L;
}

inline int candidateLen(const Solution& S, int i, int m, int u, int j) { return i + m + (u >= 0 ? max(0, S.r[u].len - j) : 0); }

struct Insertion {
    int v = -1, p = -1;
    double cost = 1e18, km = 0;
};

// Одинаковые пустые бригады (старт, вид транспорта, навыки) при поиске вставки достаточно попробовать один раз.
inline long long vehicleClass(const Data& F, int v) { return ((long long)F.start[v] * 8 + F.mode[v]) * 1000003LL + F.mask[v]; }

// Лучшая вставка заявки k: Δкм + openCost за открытие пустой бригады + press(новая длина) − press(старая длина).
template <class Pressure>
inline Insertion bestInsertion(const Data& F, const Solution& S, int k, double openCost, bool allowEmpty, int skipRoute, Pressure press) {
    Insertion best;
    long long tried[MAX_VEHICLES];
    int nTried = 0;
    for (int v = 0; v < F.V; v++) {
        if (v == skipRoute || !(F.skillBit[k] & F.mask[v])) continue;
        const Route& R = S.r[v];
        if (R.len == 0) {
            if (!allowEmpty) continue;
            long long key = vehicleClass(F, v);
            if (std::find(tried, tried + nTried, key) != tried + nTried) continue;
            tried[nTried++] = key;
        }
        double base = (R.len == 0 ? openCost : 0) + press(R.len + 1) - press(R.len) - R.km;
        for (int p = 0; p <= R.len; p++) {
            double km = evalCandidate(F, S, v, p, &k, 1, v, p);
            if (km < 0) continue;
            if (km + base < best.cost) best = {v, p, km + base, km};
        }
    }
    return best;
}

// Жадное построение: заявки в заданном порядке, каждая на лучшее место (Δкм + openCost за новую бригаду).
inline void greedyBuild(const Data& F, Solution& S, const vector<int>& order, double openCost) {
    S.init(F);
    int buf[MAX_LEN];
    for (int k : order) {
        if (!F.servable[k] || S.poolIdx[k] < 0) continue;
        Insertion in = bestInsertion(F, S, k, openCost, true, -1, [](int) { return 0.0; });
        if (in.v < 0) continue;
        int L = compose(S, buf, in.v, in.p, &k, 1, in.v, in.p);
        S.poolRemove(k);
        S.setRoute(in.v, buf, L);
    }
}

// Несколько жадных построений разными порядками и ценами открытия бригады; лучшее по цели — в out.
inline Routes multiGreedy(const Data& F, Rng& rng, double budget, Solution& out) {
    Timer timer;
    vector<int> identity(F.N);
    std::iota(identity.begin(), identity.end(), 0);
    vector<vector<int>> orders;
    auto sortedBy = [&](auto key) {
        vector<int> o = identity;
        std::stable_sort(o.begin(), o.end(), [&](int x, int y) { return key(x) < key(y); });
        orders.push_back(o);
    };
    sortedBy([&](int k) { return F.b[k] * 1000 + F.a[k]; });
    sortedBy([&](int k) { return F.a[k] * 1000 + F.b[k]; });
    sortedBy([&](int k) { return (F.b[k] - F.a[k]) * 1000 + F.a[k]; });
    sortedBy([&](int k) { return -F.pen[k] * 1e7 + (F.b[k] - F.a[k]) * 1000 + F.a[k]; });
    sortedBy([&](int k) { return -F.svc[k] * 1e6 + F.b[k]; });
    const double openCosts[] = {1e4, 300, 60, 20};
    const int nFixed = (int)orders.size() * 4;
    Solution cur;
    double bestScore = 1e300;
    for (int it = 0; it <= 2000; it++) {
        vector<int> o;
        if (it < nFixed) o = orders[it / 4];
        else {                                     // случайные локальные перестановки одного из первых порядков
            o = orders[rng.randint(3)];
            for (int q = 0; q < F.N / 4; q++) {
                int x = rng.randint(F.N), y = min(F.N - 1, x + 1 + rng.randint(4));
                std::swap(o[x], o[y]);
            }
        }
        greedyBuild(F, cur, o, openCosts[it % 4]);
        if (cur.score() < bestScore) {
            bestScore = cur.score();
            out = cur;
        }
        if (it + 1 >= nFixed && timer.sec() > budget) break;
    }
    return out.routes();
}

}  // namespace dispatch::fast
