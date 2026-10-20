// Маршруты на фиксированных массивах с кэшем для отжига (search/annealer.hpp).
//
// Для каждой позиции маршрута хранятся состояние расписания (конец работы до и после обеда: dep, depB; минуты ожидания
// аварий), накопленный пробег (ck) и оборудование (qs), самое позднее допустимое начало с учётом хвоста (lat, latA,
// latF) и маска навыков, нужных хвосту (need). Поэтому кандидат «префикс маршрута v + середина + хвост маршрута u»
// проверяется за O(длина середины), если у u и v один вид транспорта и обед, а в хвосте нет аварий (иначе хвост
// пересчитывается проходом). Невыполненные заявки лежат в пуле; в пул попадают только заявки, которые в принципе может
// выполнить хоть одна бригада.
#pragma once
#include "core/problem.hpp"

namespace dispatch::fast {

constexpr int MAX_ORDERS = 160;
constexpr int MAX_VEHICLES = 32;
constexpr int MAX_LEN = 168;

// Данные задачи в плоских массивах и соседи каждой заявки по «времени-расстоянию».
struct Data {
    int N, V, S, M;
    const double* T[MAX_VEHICLES];
    const double* D[MAX_VEHICLES];
    int mode[MAX_VEHICLES], start[MAX_VEHICLES], mask[MAX_VEHICLES];
    double end[MAX_VEHICLES];       // конец смены бригады
    double begin[MAX_VEHICLES];     // с какого момента бригада свободна в стартовой точке
    Lunch lun[MAX_VEHICLES];
    int capOf[MAX_VEHICLES][NITEMS], capTot[MAX_VEHICLES];
    double a[MAX_ORDERS], b[MAX_ORDERS], pen[MAX_ORDERS];
    double svc[MAX_ORDERS];         // самая короткая длительность среди бригад — для эвристик (соседи, порядки)
    double svcV[MAX_VEHICLES][MAX_ORDERS];   // длительность у бригады (Instance::svc)
    double speed[MAX_VEHICLES];     // темп бригады
    int skillBit[MAX_ORDERS], eq[MAX_ORDERS];
    bool servable[MAX_ORDERS], acc[MAX_ORDERS];
    double wVeh, wWait;
    int maxUsed;
    bool equip, wait;               // есть ограничения оборудования; есть аварии и цена их ожидания
    int classOf[MAX_VEHICLES];      // первая бригада с теми же стартом, транспортом, навыками, сменой, обедом и запасом
    vector<vector<int>> neighbors;

    double sv(int k, int v) const { return svcV[v][k]; }
    double last(int k, int v) const { return min(b[k], end[v] - svcV[v][k]); }
    bool fits(int v, int q) const {
        if (!equip) return true;
        int x0 = eqItem(q, 0), x1 = eqItem(q, 1);
        return x0 <= capOf[v][0] && x1 <= capOf[v][1] && x0 + x1 <= capTot[v];
    }
    // Пороги хвоста маршрута u годятся для бригады v: та же матрица и темп, смена не короче, тот же обед (или у v его нет).
    bool tailShared(int u, int v) const {
        return mode[u] == mode[v] && speed[u] == speed[v] && end[u] <= end[v] && (lun[v].ld <= 0 || lun[u] == lun[v]);
    }

    explicit Data(const Instance& I, int nNeighbors = 16)
        : N(I.N), V(I.V), S(I.S), M(I.M), wVeh(I.wVeh), wWait(I.wWait), maxUsed(min(I.V, I.maxUsed)), equip(I.equip), wait(I.waits()) {
        if (N > MAX_ORDERS - 8 || V > MAX_VEHICLES) {
            fprintf(stderr, "задача больше предела fast::Data (%d заявок, %d бригад)\n", MAX_ORDERS - 8, MAX_VEHICLES);
            exit(1);
        }
        for (int v = 0; v < V; v++) {
            mode[v] = I.veh[v].mode;
            start[v] = I.veh[v].start;
            mask[v] = I.veh[v].mask;
            end[v] = I.veh[v].end;
            begin[v] = I.veh[v].begin;
            lun[v] = I.veh[v].lunch;
            for (int i = 0; i < NITEMS; i++) capOf[v][i] = I.veh[v].cap[i];
            capTot[v] = I.veh[v].capTot;
            speed[v] = I.veh[v].speed;
            T[v] = I.T[mode[v]].data();
            D[v] = I.D[mode[v]].data();
            classOf[v] = v;
            for (int u = 0; u < v; u++) {
                const Vehicle &x = I.veh[u], &y = I.veh[v];
                if (x.start == y.start && x.mode == y.mode && x.mask == y.mask && x.end == y.end && x.begin == y.begin &&
                    x.lunch == y.lunch && x.cap[0] == y.cap[0] && x.cap[1] == y.cap[1] && x.capTot == y.capTot && x.speed == y.speed) {
                    classOf[v] = u;
                    break;
                }
            }
        }
        for (int k = 0; k < N; k++) {
            a[k] = I.ord[k].a;
            b[k] = I.ord[k].b;
            svc[k] = 1e18;
            for (int v = 0; v < V; v++) {
                svcV[v][k] = I.svc(k, v);
                svc[k] = min(svc[k], svcV[v][k]);
            }
            pen[k] = I.penalty(k);
            skillBit[k] = 1 << I.ord[k].skill;
            eq[k] = I.ord[k].eq;
            acc[k] = I.acc(k);
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
    double km = 0, wait = 0, cost = 0;   // пробег, минуты ожидания аварий, км + wWait·ожидание
    int qTot = 0;                        // оборудование маршрута (упаковано)
    int seq[MAX_LEN];
    double dep[MAX_LEN], depB[MAX_LEN], ck[MAX_LEN], lat[MAX_LEN], latA[MAX_LEN], latF[MAX_LEN], wA[MAX_LEN], wB[MAX_LEN];
    int qs[MAX_LEN];
    int need[MAX_LEN + 1], nAcc[MAX_LEN + 1];
};

struct Solution {
    const Data* F = nullptr;
    Route r[MAX_VEHICLES];
    int where[MAX_ORDERS], pos[MAX_ORDERS];          // маршрут и позиция заявки; -1 — в пуле
    int pool[MAX_ORDERS], np = 0, poolIdx[MAX_ORDERS];
    double km = 0, cost = 0, poolPen = 0, fixedPen = 0;   // fixedPen — штраф заявок, которые не может взять никто
    int used = 0;

    void init(const Data& f) {
        F = &f;
        np = 0;
        km = cost = poolPen = fixedPen = 0;
        used = 0;
        for (int v = 0; v < F->V; v++) {
            r[v].len = 0;
            r[v].km = r[v].wait = r[v].cost = 0;
            r[v].qTot = 0;
            r[v].need[0] = r[v].nAcc[0] = 0;
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
    // Записать маршрут v (seq может указывать на внешний буфер) и пересчитать кэши. Маршрут должен быть допустим.
    void setRoute(int v, const int* seq, int len) {
        Route& R = r[v];
        km -= R.km;
        cost -= R.cost;
        if (R.len) used--;
        if (len && seq != R.seq) memcpy(R.seq, seq, sizeof(int) * len);
        R.len = len;
        const double* T = F->T[v];
        const double* D = F->D[v];
        const int M = F->M, S = F->S;
        const Lunch& L = F->lun[v];
        const bool lunch = L.ld > 0;
        Walk w = startWalk(F->begin[v]);
        double dist = 0;
        int prev = F->start[v], q = 0;
        for (int p = 0; p < len; p++) {
            int k = R.seq[p], n = S + k;
            advance(w, L, T[prev * M + n], F->a[k], F->last(k, v), F->sv(k, v), F->acc[k], TOL);
            dist += D[prev * M + n];
            q += F->eq[k];
            R.ck[p] = dist;
            R.dep[p] = w.t;
            if (lunch) {
                R.depB[p] = w.tB;
                R.wB[p] = w.wB;
            }
            R.wA[p] = w.wA;
            R.qs[p] = q;
            prev = n;
            where[k] = v;
            pos[k] = p;
        }
        settle(w, L, F->end[v], len == 0, TOL, R.wait);
        R.need[len] = R.nAcc[len] = 0;
        for (int p = len - 1; p >= 0; p--) {
            int k = R.seq[p];
            if (!lunch) {
                double latest = F->last(k, v);
                if (p + 1 < len) latest = min(latest, R.lat[p + 1] - F->sv(k, v) - T[(S + k) * M + S + R.seq[p + 1]]);
                R.lat[p] = latest;
            } else if (p + 1 < len) {
                int x = R.seq[p + 1];
                tailLatest(L, F->last(k, v), F->sv(k, v), F->end[v], false, T[(S + k) * M + S + x], F->a[x], R.lat[p + 1], R.latF[p + 1],
                           R.latA[p + 1], R.lat[p], R.latF[p], R.latA[p]);
            } else {
                tailLatest(L, F->last(k, v), F->sv(k, v), F->end[v], true, 0, 0, 0, 0, 0, R.lat[p], R.latF[p], R.latA[p]);
            }
            R.need[p] = R.need[p + 1] | F->skillBit[k];
            R.nAcc[p] = R.nAcc[p + 1] + F->acc[k];
        }
        R.km = dist;
        R.qTot = q;
        R.cost = dist + F->wWait * R.wait;
        km += dist;
        cost += R.cost;
        if (len) used++;
    }
    double score() const { return (poolPen + fixedPen) * W_UNSERVED + used * F->wVeh + cost; }
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

// Минуты ожидания аварий маршрута бригады v: после состояния w (в точке prev) — хвост маршрута U с позиции j; пороги U
// годятся для v (Data::tailShared). Проход идёт, пока в хвосте есть аварии; без обеда он кончается и там, где
// расписание сошлось с прежним расписанием U.
inline double tailWait(const Data& F, const Route& U, int v, int j, Walk w, int prev) {
    const Lunch& L = F.lun[v];
    const double* T = F.T[v];
    const int M = F.M, off = F.S;
    if (L.ld <= 0) {
        double t = w.t, wa = w.wA;
        for (int i = j; i < U.len && U.nAcc[i] > 0; i++) {
            int x = U.seq[i], n = off + x;
            double s = max(t + T[prev * M + n], F.a[x]);
            if (s + F.sv(x, v) == U.dep[i]) return wa + U.wA[U.len - 1] - (i ? U.wA[i - 1] : 0.0);
            if (F.acc[x]) wa += s - F.a[x];
            t = s + F.sv(x, v);
            prev = n;
        }
        return wa;
    }
    double wt;
    for (int i = j; i < U.len; i++) {
        int x = U.seq[i], n = off + x;
        const double tr = T[prev * M + n];
        if (U.nAcc[i] == 0) {
            wt = quietTailWait(w, takesPathA(w, L, tr, F.a[x], U.latF[i], TOL));
            if (wt >= 0) return wt;
        }
        advance(w, L, tr, F.a[x], F.last(x, v), F.sv(x, v), F.acc[x], TOL);
        prev = n;
    }
    settle(w, L, F.end[v], false, TOL, wt);
    return wt;
}

// Цель ниже бригад (км + wWait·минуты ожидания аварий) нового маршрута бригады v = префикс r[v][0..i) + mid[0..m) +
// хвост r[u][j..) (u < 0 — без хвоста); -1, если маршрут недопустим.
inline double evalCandidate(const Data& F, const Solution& S, int v, int i, const int* mid, int m, int u, int j) {
    const Route& R = S.r[v];
    const double* T = F.T[v];
    const double* D = F.D[v];
    const int M = F.M, off = F.S, msk = F.mask[v];
    const Lunch& L = F.lun[v];
    Walk w;
    double dist;
    int prev, q = 0;
    if (i > 0) {
        w.t = w.free = R.dep[i - 1];
        if (L.ld > 0) {
            w.tB = R.depB[i - 1];
            w.wB = R.wB[i - 1];
        }
        if (F.wait) w.wA = R.wA[i - 1];
        dist = R.ck[i - 1];
        if (F.equip) q = R.qs[i - 1];
        prev = off + R.seq[i - 1];
    } else {
        w = startWalk(F.begin[v]);
        dist = 0;
        q = 0;
        prev = F.start[v];
    }
    for (int x = 0; x < m; x++) {
        int k = mid[x], n = off + k;
        if (!(F.skillBit[k] & msk)) return -1;
        if (!advance(w, L, T[prev * M + n], F.a[k], F.last(k, v), F.sv(k, v), F.acc[k], TOL)) return -1;
        dist += D[prev * M + n];
        q += F.eq[k];
        prev = n;
    }
    double wt;
    if (u < 0 || j >= S.r[u].len) {
        if (!F.fits(v, q) || !settle(w, L, F.end[v], i + m == 0, TOL, wt)) return -1;
        return dist + F.wWait * wt;
    }
    const Route& U = S.r[u];
    if (U.need[j] & ~msk) return -1;
    if (F.equip && !F.fits(v, q + U.qTot - (j ? U.qs[j - 1] : 0))) return -1;
    int x = U.seq[j], n = off + x;
    if (F.tailShared(u, v)) {   // проверка по порогам хвоста
        const double tr = T[prev * M + n];
        if (!tailOK(w, L, tr, F.a[x], U.latA[j], U.lat[j], TOL)) return -1;
        const double base = dist + D[prev * M + n] + U.ck[U.len - 1] - U.ck[j];
        if (!F.wait) return base;
        if (U.nAcc[j] == 0) {
            wt = quietTailWait(w, takesPathA(w, L, tr, F.a[x], U.latF[j], TOL));
            if (wt >= 0) return base + F.wWait * wt;
        }
        return base + F.wWait * tailWait(F, U, v, j, w, prev);
    }
    for (int y = j; y < U.len; y++) {               // хвост пересчитывается целиком
        int k = U.seq[y];
        n = off + k;
        if (!advance(w, L, T[prev * M + n], F.a[k], F.last(k, v), F.sv(k, v), F.acc[k], TOL)) return -1;
        dist += D[prev * M + n];
        prev = n;
    }
    if (!settle(w, L, F.end[v], false, TOL, wt)) return -1;
    return dist + F.wWait * wt;
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

// Одинаковые пустые бригады (старт, вид транспорта, навыки, смена, обед, запас) при поиске вставки достаточно
// попробовать один раз.
inline long long vehicleClass(const Data& F, int v) { return F.classOf[v]; }

// Лучшая вставка заявки k: Δцели (км, ожидание аварий) + openCost за открытие пустой бригады + press(новая длина) −
// press(старая длина). Пустую бригаду не открывает, если бригад уже maxUsed.
template <class Pressure>
inline Insertion bestInsertion(const Data& F, const Solution& S, int k, double openCost, bool allowEmpty, int skipRoute, Pressure press) {
    Insertion best;
    long long tried[MAX_VEHICLES];
    int nTried = 0;
    allowEmpty = allowEmpty && S.used < F.maxUsed;
    for (int v = 0; v < F.V; v++) {
        if (v == skipRoute || !(F.skillBit[k] & F.mask[v])) continue;
        const Route& R = S.r[v];
        if (R.len == 0) {
            if (!allowEmpty) continue;
            long long key = vehicleClass(F, v);
            if (std::find(tried, tried + nTried, key) != tried + nTried) continue;
            tried[nTried++] = key;
        }
        double base = (R.len == 0 ? openCost : 0) + press(R.len + 1) - press(R.len) - R.cost;
        for (int p = 0; p <= R.len; p++) {
            double km = evalCandidate(F, S, v, p, &k, 1, v, p);
            if (km < 0) continue;
            if (km + base < best.cost) best = {v, p, km + base, km};   // km — цель ниже бригад
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
