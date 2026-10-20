// Разрушение-восстановление на фиксированных массивах: км-поиск (km_search.hpp), сокращение парка
// (fleet_reduction.hpp), ремонт выталкиванием (ejection.hpp).
//
// Разрушение и восстановление делаются на месте; откат восстанавливает только затронутые маршруты. Вставка
// проверяется за O(1) по состоянию расписания перед позицией (dep, depB — конец работы, пока обед впереди и после
// обеда) и самому позднему началу хвоста (lat, latA); позиции позже конца окна заявки отсекаются. Оборудование —
// по сумме маршрута. Цена ожидания аварий меняется, только если вставляется авария или в маршруте есть авария
// (без обеда — дальше по маршруту): тогда хвост пересчитывается проходом.
#pragma once
#include "core/problem.hpp"

namespace dispatch::lns {

constexpr int MAX_VEHICLES = 32, MAX_LEN = 176, MAX_ORDERS = 176;   // маски бригад — uint32_t
constexpr double INF = 1e18;

// Данные задачи в плоских массивах; «общее» расстояние между заявками gd (среднее по видам транспорта бригад,
// симметризованное) и соседи по нему.
struct Problem {
    const Instance& I;
    int N, V, S, M;
    const double* T[MAX_VEHICLES];
    const double* D[MAX_VEHICLES];
    int start[MAX_VEHICLES], mask[MAX_VEHICLES], mode[MAX_VEHICLES];
    double end[MAX_VEHICLES], maxEnd = 0;   // конец смены бригады и самый поздний среди них
    double a[MAX_ORDERS], b[MAX_ORDERS], pen[MAX_ORDERS];   // b — самое позднее начало среди всех бригад
    double ob[MAX_ORDERS];                   // конец окна из задачи
    double svc[MAX_ORDERS];                  // самая короткая длительность среди бригад — для эвристик
    double svcV[MAX_VEHICLES][MAX_ORDERS];   // длительность у бригады (Instance::svc)
    int nd[MAX_ORDERS], skill[MAX_ORDERS], nCan[MAX_ORDERS];
    uint32_t canMask[MAX_ORDERS];
    double farv[MAX_ORDERS];        // км от ближайшего старта подходящей бригады
    Lunch lun[MAX_VEHICLES];
    double begin[MAX_VEHICLES];     // с какого момента бригада свободна в стартовой точке
    int eq[MAX_ORDERS];             // нужное оборудование (упаковано)
    bool acc[MAX_ORDERS];           // авария: ожидание начала стоит wWait за минуту
    double wVeh, wWait;
    int cap;                        // предел числа бригад: min(V, maxv)
    bool equip, wait;               // есть ограничения оборудования; есть аварии и цена их ожидания
    vector<float> gd;
    vector<vector<int>> nbr;        // все остальные заявки по возрастанию gd
    double maxD = 1;
    // Самое позднее начало заявки k у бригады v: окно и конец её смены.
    double bv(int k, int v) const { return min(ob[k], end[v] - svcV[v][k]); }
    double sv(int k, int v) const { return svcV[v][k]; }
    bool fits(int v, int q) const { return !equip || I.veh[v].fits(q); }
    // Предел бригад км-поиска: пока что-то не обслужено или бригада дешева (wveh меньше стандартной) — cap, иначе
    // текущий парк.
    int kmCap(int used, bool allServed) const { return allServed && wVeh >= W_VEHICLE ? used : cap; }

    explicit Problem(const Instance& in)
        : I(in), N(in.N), V(in.V), S(in.S), M(in.M), wVeh(in.wVeh), wWait(in.wWait), cap(min(in.V, in.maxUsed)), equip(in.equip),
          wait(in.waits()) {
        if (N > MAX_ORDERS - 2 || V > MAX_VEHICLES) {
            fprintf(stderr, "задача больше предела lns::Problem (%d заявок, %d бригад)\n", MAX_ORDERS - 2, MAX_VEHICLES);
            exit(1);
        }
        for (int v = 0; v < V; v++) {
            mode[v] = I.veh[v].mode;
            start[v] = I.veh[v].start;
            mask[v] = I.veh[v].mask;
            end[v] = I.veh[v].end;
            maxEnd = max(maxEnd, end[v]);
            T[v] = I.T[mode[v]].data();
            D[v] = I.D[mode[v]].data();
            lun[v] = I.veh[v].lunch;
            begin[v] = I.veh[v].begin;
        }
        for (int k = 0; k < N; k++) {
            eq[k] = I.ord[k].eq;
            acc[k] = I.acc(k);
            a[k] = I.ord[k].a;
            ob[k] = I.ord[k].b;
            svc[k] = 1e18;
            b[k] = -1e18;
            for (int v = 0; v < V; v++) {
                svcV[v][k] = I.svc(k, v);
                svc[k] = min(svc[k], svcV[v][k]);
                b[k] = max(b[k], min(ob[k], end[v] - svcV[v][k]));
            }
            pen[k] = I.penalty(k);
            nd[k] = S + k;
            skill[k] = I.ord[k].skill;
            canMask[k] = 0;
            nCan[k] = 0;
            farv[k] = 1e9;
            for (int v = 0; v < V; v++)
                if (I.can(v, k)) {
                    canMask[k] |= 1u << v;
                    nCan[k]++;
                    farv[k] = min(farv[k], I.d(v, start[v], nd[k]));
                }
        }
        vector<int> modes;
        for (int v = 0; v < V; v++)
            if (std::find(modes.begin(), modes.end(), mode[v]) == modes.end()) modes.push_back(mode[v]);
        gd.assign(N * N, 0);
        for (int i = 0; i < N; i++)
            for (int j = 0; j < N; j++) {
                double s = 0;
                int x = nd[i], y = nd[j];
                for (int m : modes) s += 0.5 * (I.D[m][x * M + y] + I.D[m][y * M + x]);
                gd[i * N + j] = i == j ? 0 : s / modes.size();
                maxD = max(maxD, (double)gd[i * N + j]);
            }
        nbr.assign(N, {});
        for (int i = 0; i < N; i++) {
            for (int j = 0; j < N; j++)
                if (j != i) nbr[i].push_back(j);
            std::sort(nbr[i].begin(), nbr[i].end(), [&](int x, int y) { return gd[i * N + x] < gd[i * N + y]; });
        }
    }
    double t(int v, int i, int j) const { return T[v][i * M + j]; }
    double d(int v, int i, int j) const { return D[v][i * M + j]; }
    bool can(int v, int k) const { return canMask[k] >> v & 1; }
    double g(int i, int j) const { return gd[i * N + j]; }
};

struct Solution {
    int V = 0, N = 0;
    int len[MAX_VEHICLES];
    int r[MAX_VEHICLES][MAX_LEN];
    // По позициям: dep, depB — конец работы, пока обед впереди (путь A) и после обеда (путь B, INF_TIME — нельзя);
    // lat, latF, latA — самое позднее начало с учётом хвоста, если обед был / будет после последней работы / где-то в
    // хвосте; wA, wB — минуты ожидания аварий путей A и B до позиции включительно; qs — оборудование до позиции
    // включительно; nAcc[i] — аварий на позициях ≥ i.
    double dep[MAX_VEHICLES][MAX_LEN], depB[MAX_VEHICLES][MAX_LEN];
    double lat[MAX_VEHICLES][MAX_LEN], latF[MAX_VEHICLES][MAX_LEN], latA[MAX_VEHICLES][MAX_LEN];
    double wA[MAX_VEHICLES][MAX_LEN], wB[MAX_VEHICLES][MAX_LEN];
    int qs[MAX_VEHICLES][MAX_LEN], nAcc[MAX_VEHICLES][MAX_LEN + 1];
    double km[MAX_VEHICLES], wc[MAX_VEHICLES];   // пробег и цена ожидания аварий (wWait · минуты)
    int qTot[MAX_VEHICLES];
    int rt[MAX_ORDERS];     // маршрут заявки; < 0 — не обслуживается

    Solution() = default;
    Solution(const Problem& P, const Routes& R) { load(P, R); }
    // Копируется только занятая часть массивов.
    Solution(const Solution& o) { copyFrom(o); }
    Solution& operator=(const Solution& o) {
        if (this != &o) copyFrom(o);
        return *this;
    }

    void init(const Problem& P) {
        V = P.V;
        N = P.N;
        for (int v = 0; v < V; v++) {
            len[v] = 0;
            km[v] = wc[v] = 0;
            qTot[v] = 0;
            nAcc[v][0] = 0;
        }
        for (int k = 0; k < N; k++) rt[k] = -1;
    }
    void load(const Problem& P, const Routes& R) {
        init(P);
        for (int v = 0; v < V && v < (int)R.size(); v++) {
            len[v] = (int)R[v].size();
            for (int i = 0; i < len[v]; i++) r[v][i] = R[v][i];
        }
        for (int v = 0; v < V; v++) rebuild(P, v);
    }
    Routes routes() const {
        Routes R(V);
        for (int v = 0; v < V; v++) R[v].assign(r[v], r[v] + len[v]);
        return R;
    }

    int used() const {
        int u = 0;
        for (int v = 0; v < V; v++) u += len[v] > 0;
        return u;
    }
    double kmTot() const {
        double s = 0;
        for (int v = 0; v < V; v++) s += km[v];
        return s;
    }
    // Км и цена ожидания аварий — всё, что в цели ниже бригад.
    double kmCost() const {
        double s = 0;
        for (int v = 0; v < V; v++) s += km[v] + wc[v];
        return s;
    }
    double penSum(const Problem& P) const {
        double p = 0;
        for (int k = 0; k < N; k++)
            if (rt[k] < 0) p += P.pen[k];
        return p;
    }
    int nUn() const {
        int c = 0;
        for (int k = 0; k < N; k++) c += rt[k] < 0;
        return c;
    }
    double cost(const Problem& P) const { return penSum(P) * W_UNSERVED + used() * P.wVeh + kmCost(); }
    // Сначала штраф, затем бригады·wVeh + км + ожидание (при стандартной цене бригады — лексикографически).
    bool better(const Solution& o, const Problem& P) const {
        double p = penSum(P), q = o.penSum(P);
        if (fabs(p - q) > 1e-9) return p < q;
        return used() * P.wVeh + kmCost() < o.used() * P.wVeh + o.kmCost() - 1e-9;
    }

    // Состояние расписания перед позицией p маршрута v; prev — точка, где бригада. Без обеда depB и wB не ведутся,
    // без штрафа ожидания — wA, без оборудования — qs, nAcc.
    Walk before(const Problem& P, int v, int p, int& prev) const {
        if (p == 0) {
            prev = P.start[v];
            return startWalk(P.begin[v]);
        }
        Walk w;
        w.t = w.free = dep[v][p - 1];
        if (P.lun[v].ld > 0) {
            w.tB = depB[v][p - 1];
            w.wB = wB[v][p - 1];
        }
        if (P.wait) w.wA = wA[v][p - 1];
        prev = P.nd[r[v][p - 1]];
        return w;
    }

    // Пересчитать расписание маршрута v; недопустимые заявки выпадают (в out). Если обеду нет места, выпадает первая
    // работа, которая кончается позже начала окна обеда.
    void rebuild(const Problem& P, int v, vector<int>* out = nullptr) {
        int* R = r[v];
        const double* T = P.T[v];
        const double* D = P.D[v];
        const int M = P.M;
        const Lunch& L = P.lun[v];
        const bool lunch = L.ld > 0;
        int w;
        for (;;) {
            const int len0 = len[v];
            Walk s = startWalk(P.begin[v]);
            double dist = 0;
            int prev = P.start[v], q = 0;
            w = 0;
            for (int i = 0; i < len0; i++) {
                int k = R[i], n = P.nd[k];
                Walk x = s;
                if (!P.can(v, k) || !P.fits(v, q + P.eq[k]) ||
                    !advance(x, L, T[prev * M + n], P.a[k], P.bv(k, v), P.sv(k, v), P.acc[k], TOL)) {
                    rt[k] = -1;
                    if (out) out->push_back(k);
                    continue;
                }
                s = x;
                q += P.eq[k];
                dist += D[prev * M + n];
                prev = n;
                R[w] = k;
                dep[v][w] = s.t;
                if (lunch) {
                    depB[v][w] = s.tB;
                    wB[v][w] = s.wB;
                }
                if (P.wait) wA[v][w] = s.wA;
                if (P.equip) qs[v][w] = q;
                rt[k] = v;
                w++;
            }
            len[v] = w;
            double wt;
            if (settle(s, L, P.end[v], w == 0, TOL, wt)) {
                km[v] = dist;
                wc[v] = P.wWait * wt;
                qTot[v] = q;
                break;
            }
            int i = 0;
            while (i + 1 < w && dep[v][i] <= L.la) i++;
            rt[R[i]] = -1;
            if (out) out->push_back(R[i]);
            memmove(R + i, R + i + 1, (w - i - 1) * sizeof(int));
            len[v] = w - 1;
        }
        if (P.wait) {
            nAcc[v][w] = 0;
            for (int i = w - 1; i >= 0; i--) nAcc[v][i] = nAcc[v][i + 1] + P.acc[R[i]];
        }
        if (!lunch) {
            for (int i = w - 1; i >= 0; i--) {
                int k = R[i];
                double latest = P.bv(k, v);
                if (i + 1 < w) latest = min(latest, lat[v][i + 1] - P.sv(k, v) - T[P.nd[k] * M + P.nd[R[i + 1]]]);
                lat[v][i] = latest;
            }
            return;
        }
        for (int i = w - 1; i >= 0; i--) {
            int k = R[i];
            if (i + 1 < w) {
                int x = R[i + 1];
                tailLatest(L, P.bv(k, v), P.sv(k, v), P.end[v], false, T[P.nd[k] * M + P.nd[x]], P.a[x], lat[v][i + 1], latF[v][i + 1],
                           latA[v][i + 1], lat[v][i], latF[v][i], latA[v][i]);
            } else {
                tailLatest(L, P.bv(k, v), P.sv(k, v), P.end[v], true, 0, 0, 0, 0, 0, lat[v][i], latF[v][i], latA[v][i]);
            }
        }
    }
    // Минуты ожидания аварий маршрута v, если перед позицией p состояние w (бригада в точке prev), а дальше — хвост
    // маршрута с позиции p.
    // Проход идёт, пока в хвосте есть аварии; без обеда он кончается и там, где расписание сошлось с прежним.
    double waitFrom(const Problem& P, int v, int p, Walk w, int prev) const {
        const Lunch& L = P.lun[v];
        const int n0 = len[v];
        if (L.ld <= 0) {
            double t = w.t, wa = w.wA;
            for (int i = p; i < n0 && nAcc[v][i] > 0; i++) {
                int x = r[v][i], n = P.nd[x];
                double s = max(t + P.t(v, prev, n), P.a[x]);
                if (s + P.sv(x, v) == dep[v][i]) return wa + wA[v][n0 - 1] - (i ? wA[v][i - 1] : 0.0);
                if (P.acc[x]) wa += s - P.a[x];
                t = s + P.sv(x, v);
                prev = n;
            }
            return wa;
        }
        double wt;
        for (int i = p; i < n0; i++) {
            int x = r[v][i], n = P.nd[x];
            const double tr = P.t(v, prev, n);
            if (nAcc[v][i] == 0) {   // дальше аварий нет: важно только, по какому пути пойдёт расписание
                wt = quietTailWait(w, takesPathA(w, L, tr, P.a[x], latF[v][i], TOL));
                if (wt >= 0) return wt;
            }
            advance(w, L, tr, P.a[x], P.bv(x, v), P.sv(x, v), P.acc[x], TOL);
            prev = n;
        }
        settle(w, L, P.end[v], false, TOL, wt);
        return wt;
    }
    // Прирост цели ниже бригад (км, ожидание аварий) при вставке k в маршрут v на позицию p; INF, если нельзя.
    __attribute__((always_inline)) double insDelta(const Problem& P, int v, int p, int k) const {
        const int nk = P.nd[k], M = P.M;
        const double* T = P.T[v];
        const double* D = P.D[v];
        const int prev = p ? P.nd[r[v][p - 1]] : P.start[v];
        const double tr = T[prev * M + nk], begin = max((p ? dep[v][p - 1] : 0) + tr, P.a[k]);
        if (begin > P.bv(k, v) + TOL) return INF;              // без обеда не успевает — с обедом тем более
        if (P.equip && !P.fits(v, qTot[v] + P.eq[k])) return INF;
        double dd = D[prev * M + nk];
        const int nx = p < len[v] ? P.nd[r[v][p]] : -1;
        const bool lunch = P.lun[v].ld > 0;
        if (!lunch) {
            if (nx >= 0 && begin + P.sv(k, v) + T[nk * M + nx] > lat[v][p] + TOL) return INF;
        } else if (!(nx >= 0 ? max(begin + P.sv(k, v) + T[nk * M + nx], P.a[r[v][p]]) <= latA[v][p] + TOL   // обед — дальше
                         : max(begin + P.sv(k, v), P.lun[v].la) <= P.lun[v].lb + LUNCH_TOL &&
                               max(begin + P.sv(k, v), P.lun[v].la) + P.lun[v].ld <= P.end[v] + TOL) &&
                   !lunchFits(P, v, p, k, tr)) {
            return INF;
        }
        if (nx >= 0) dd += D[nk * M + nx] - D[prev * M + nx];
        if (P.wait && (P.acc[k] || nAcc[v][lunch ? 0 : p] > 0)) dd += waitDelta(P, v, p, k, tr);
        return dd;
    }
    // Вставка k на позицию p маршрута v бригадой с обедом допустима (без обеда путь A уже успевает).
    __attribute__((noinline)) bool lunchFits(const Problem& P, int v, int p, int k, double tr) const {
        const Lunch& L = P.lun[v];
        int at;
        Walk w = before(P, v, p, at);
        advance(w, L, tr, P.a[k], P.bv(k, v), P.sv(k, v), P.acc[k], TOL);
        double wt;
        if (p == len[v]) return settle(w, L, P.end[v], false, TOL, wt);
        int x = r[v][p];
        return tailOK(w, L, P.t(v, P.nd[k], P.nd[x]), P.a[x], latA[v][p], lat[v][p], TOL);
    }
    // Прирост цены ожидания аварий при вставке k на позицию p маршрута v.
    __attribute__((noinline)) double waitDelta(const Problem& P, int v, int p, int k, double tr) const {
        int at;
        Walk w = before(P, v, p, at);
        advance(w, P.lun[v], tr, P.a[k], P.bv(k, v), P.sv(k, v), P.acc[k], TOL);
        return P.wWait * waitFrom(P, v, p, w, P.nd[k]) - wc[v];
    }
    void insertAt(const Problem& P, int v, int p, int k) {
        memmove(r[v] + p + 1, r[v] + p, (len[v] - p) * sizeof(int));
        r[v][p] = k;
        len[v]++;
        rt[k] = v;
        rebuild(P, v);
    }
    // Выигрыш км при удалении позиции i (без проверки допустимости).
    double remGain(const Problem& P, int v, int i) const {
        int prev = i ? P.nd[r[v][i - 1]] : P.start[v], n = P.nd[r[v][i]];
        double g = P.d(v, prev, n);
        if (i + 1 < len[v]) {
            int nx = P.nd[r[v][i + 1]];
            g += P.d(v, n, nx) - P.d(v, prev, nx);
        }
        return g;
    }

private:
    void copyFrom(const Solution& o) {
        V = o.V;
        N = o.N;
        for (int v = 0; v < V; v++) {
            int L = len[v] = o.len[v];
            km[v] = o.km[v];
            wc[v] = o.wc[v];
            qTot[v] = o.qTot[v];
            memcpy(r[v], o.r[v], L * sizeof(int));
            memcpy(dep[v], o.dep[v], L * sizeof(double));
            memcpy(depB[v], o.depB[v], L * sizeof(double));
            memcpy(lat[v], o.lat[v], L * sizeof(double));
            memcpy(latF[v], o.latF[v], L * sizeof(double));
            memcpy(latA[v], o.latA[v], L * sizeof(double));
            memcpy(wA[v], o.wA[v], L * sizeof(double));
            memcpy(wB[v], o.wB[v], L * sizeof(double));
            memcpy(qs[v], o.qs[v], L * sizeof(int));
            memcpy(nAcc[v], o.nAcc[v], (L + 1) * sizeof(int));
        }
        memcpy(rt, o.rt, N * sizeof(int));
    }
};

// Откат: сохраняются rt и маршруты, отмеченные save().
struct Undo {
    uint32_t touched = 0;
    int rtb[MAX_ORDERS], lenb[MAX_VEHICLES], rb[MAX_VEHICLES][MAX_LEN];

    void begin(const Solution& s) {
        touched = 0;
        memcpy(rtb, s.rt, s.N * sizeof(int));
    }
    void save(const Solution& s, int v) {
        if (touched >> v & 1) return;
        touched |= 1u << v;
        lenb[v] = s.len[v];
        memcpy(rb[v], s.r[v], s.len[v] * sizeof(int));
    }
    void restore(const Problem& P, Solution& s) {
        for (int v = 0; v < s.V; v++)
            if (touched >> v & 1) {
                s.len[v] = lenb[v];
                memcpy(s.r[v], rb[v], lenb[v] * sizeof(int));
                s.rebuild(P, v);
            }
        memcpy(s.rt, rtb, s.N * sizeof(int));
        touched = 0;
    }
};

// Снять заявки rem[0..n) с маршрутов и пересчитать их; снятые и выпавшие — в pool.
inline void removeSet(const Problem& P, Solution& s, const int* rem, int n, vector<int>& pool, Undo* U) {
    uint32_t touched = 0;
    for (int q = 0; q < n; q++) {
        int k = rem[q], v = s.rt[k];
        if (v < 0) continue;
        if (U) U->save(s, v);
        touched |= 1u << v;
        s.rt[k] = -2 - v;
        pool.push_back(k);
    }
    for (int v = 0; v < s.V; v++)
        if (touched >> v & 1) {
            int w = 0;
            for (int i = 0; i < s.len[v]; i++) {
                int k = s.r[v][i];
                if (s.rt[k] >= 0) s.r[v][w++] = k;
                else s.rt[k] = -1;
            }
            s.len[v] = w;
            s.rebuild(P, v, &pool);
        }
}

inline void collectAbsent(const Solution& s, vector<int>& pool) {
    for (int k = 0; k < s.N; k++)
        if (s.rt[k] < 0) pool.push_back(k);
}

// Жадная вставка в порядке pool: каждая заявка на место с минимальным Δкм (+ openCost за новую бригаду, пока бригад
// меньше cap). Каждая позиция пропускается с вероятностью blink. Невставленные остаются в pool.
inline void greedyInsert(const Problem& P, Solution& s, vector<int>& pool, Rng& rng, double blink, int cap, double openCost, Undo* U) {
    int used = s.used(), w = 0;
    const uint32_t blinkThreshold = (uint32_t)(blink * 4294967296.0);
    for (int q = 0; q < (int)pool.size(); q++) {
        int k = pool[q], bv = -1, bp = -1;
        double best = INF;
        for (int v = 0; v < P.V; v++) {
            if (!P.can(v, k)) continue;
            const int L = s.len[v];
            if (L == 0 && used >= cap) continue;
            double add = L == 0 ? openCost : 0;
            if (add >= best) continue;
            for (int p = 0; p <= L; p++) {
                if (p > 0 && s.dep[v][p - 1] > P.b[k]) break;   // дальше позиции только позже
                if (blinkThreshold && (uint32_t)rng.next() < blinkThreshold) continue;
                double dd = s.insDelta(P, v, p, k);
                if (dd >= INF) continue;
                dd += add;
                if (dd < best) {
                    best = dd;
                    bv = v;
                    bp = p;
                }
            }
        }
        if (bv < 0) {
            pool[w++] = k;
            continue;
        }
        if (s.len[bv] == 0) used++;
        if (U) U->save(s, bv);
        s.insertAt(P, bv, bp, k);
    }
    pool.resize(w);
}

// Вставка с сожалением: K = 1 — лучшая вставка из всех, K ≥ 2 — regret-K (до 4). Лучшие позиции кэшируются по
// парам (заявка, маршрут) и пересчитываются для изменившегося маршрута. noise > 0 — шум ±noise к стоимости.
class Regret {
public:
    void run(const Problem& P, Solution& s, vector<int>& pool, Rng& rng, int K, int cap, double openCost, Undo* U, double noise = 0) {
        const int m = (int)pool.size(), V = P.V;
        if (!m) return;
        bc_.assign(m * V, INF);
        bp_.assign(m * V, -1);
        done_.assign(m, 0);
        auto calc = [&](int i, int v) {
            int k = pool[i], bpp = -1;
            double best = INF;
            if (P.can(v, k)) {
                const int L = s.len[v];
                for (int p = 0; p <= L; p++) {
                    if (p > 0 && s.dep[v][p - 1] > P.b[k]) break;
                    double dd = s.insDelta(P, v, p, k);
                    if (dd >= INF) continue;
                    if (L == 0) dd += openCost;
                    if (noise > 0) dd = max(0.0, dd + noise * (2 * rng.uni() - 1));
                    if (dd < best) {
                        best = dd;
                        bpp = p;
                    }
                }
            }
            bc_[i * V + v] = best;
            bp_[i * V + v] = bpp;
        };
        for (int i = 0; i < m; i++)
            for (int v = 0; v < V; v++) calc(i, v);
        int used = s.used(), remaining = m;
        const int KK = max(1, min(K, 4));
        while (remaining) {
            int bi = -1, bV = -1;
            double bReg = -INF, bC = INF;
            for (int i = 0; i < m; i++) {
                if (done_[i]) continue;
                double c[4] = {INF, INF, INF, INF};
                int cv = -1;
                for (int v = 0; v < V; v++) {
                    double x = bc_[i * V + v];
                    if (x >= INF) continue;
                    if (s.len[v] == 0 && used >= cap) continue;
                    if (x < c[0]) cv = v;
                    for (int j = 0; j < KK; j++)
                        if (x < c[j]) {
                            for (int q = KK - 1; q > j; q--) c[q] = c[q - 1];
                            c[j] = x;
                            break;
                        }
                }
                if (cv < 0) continue;
                double reg = 0;
                if (KK >= 2)
                    for (int j = 1; j < KK; j++) reg += (c[j] >= INF ? 1e5 : c[j]) - c[0];
                else reg = -c[0];
                if (reg > bReg + 1e-9 || (fabs(reg - bReg) <= 1e-9 && c[0] < bC)) {
                    bReg = reg;
                    bC = c[0];
                    bi = i;
                    bV = cv;
                }
            }
            if (bi < 0) break;
            int k = pool[bi];
            bool wasEmpty = s.len[bV] == 0;
            if (U) U->save(s, bV);
            s.insertAt(P, bV, bp_[bi * V + bV], k);
            done_[bi] = 1;
            remaining--;
            if (wasEmpty) used++;
            for (int i = 0; i < m; i++)
                if (!done_[i]) calc(i, bV);
        }
        int w = 0;
        for (int i = 0; i < m; i++)
            if (!done_[i]) pool[w++] = pool[i];
        pool.resize(w);
    }

private:
    vector<double> bc_;
    vector<int> bp_;
    vector<char> done_;
};

// Операторы разрушения: набирают в rem заявки для снятия (без повторов).
class Ruins {
public:
    vector<int> rem;

    explicit Ruins(const Problem& p) : P(p), mark_(p.N, 0) { rem.reserve(p.N); }
    void clear() {
        for (int k : rem) mark_[k] = 0;
        rem.clear();
    }
    // SISR (Christiaens, Vanden Berghe): отрезки из нескольких маршрутов рядом со случайной заявкой.
    void sisr(const Solution& s, Rng& rng) {
        double avgLen = 0;
        int nRoutes = 0;
        for (int v = 0; v < P.V; v++)
            if (s.len[v]) {
                avgLen += s.len[v];
                nRoutes++;
            }
        if (!nRoutes) return;
        avgLen /= nRoutes;
        const double lsMax = min(SISR_LMAX, avgLen), ksMax = 4 * SISR_CBAR / (1 + lsMax) - 1;
        int ks = (int)(rng.uni() * ksMax) + 1;
        int seed = rng.randint(P.N);
        uint32_t ruined = 0;
        auto process = [&](int c) {
            int v = s.rt[c];
            if (v < 0 || (ruined >> v & 1) || mark_[c]) return;
            const int* R = s.r[v];
            const int L = s.len[v];
            int ltMax = (int)min((double)L, lsMax), lt = (int)(rng.uni() * ltMax) + 1;
            int pc = 0;
            while (R[pc] != c) pc++;
            if (rng.uni() < SISR_ALPHA || lt >= L) {    // отрезок длины lt, содержащий c
                int lo = max(0, pc - lt + 1), hi = min(pc, L - lt), st = lo + rng.randint(hi - lo + 1);
                for (int x = st; x < st + lt; x++) add(R[x]);
            } else {                                     // отрезок lt + m, внутри остаются m подряд
                int m = 1;
                while (lt + m < L && rng.uni() < SISR_KEEP_GROW) m++;
                int total = lt + m, lo = max(0, pc - total + 1), hi = min(pc, L - total), st = lo + rng.randint(hi - lo + 1);
                int keepStart = st + rng.randint(lt + 1);
                for (int x = st; x < st + total; x++)
                    if (x < keepStart || x >= keepStart + m) add(R[x]);
            }
            ruined |= 1u << v;
            ks--;
        };
        process(seed);
        for (int c : P.nbr[seed]) {
            if (ks <= 0) break;
            process(c);
        }
    }
    // q случайных обслуженных.
    void random(const Solution& s, Rng& rng, int q) {
        servedList(s);
        q = min(q, (int)served_.size());
        for (int i = 0; i < q; i++) {
            int j = i + rng.randint((int)served_.size() - i);
            std::swap(served_[i], served_[j]);
            add(served_[i]);
        }
    }
    // q заявок с наибольшим выигрышем км от удаления (с шумом ±20 %).
    void worst(const Solution& s, Rng& rng, int q) {
        tmp_.clear();
        for (int v = 0; v < P.V; v++)
            for (int i = 0; i < s.len[v]; i++) tmp_.push_back({-(s.remGain(P, v, i) * (0.8 + 0.4 * rng.uni())), s.r[v][i]});
        q = min(q, (int)tmp_.size());
        if (!q) return;
        std::partial_sort(tmp_.begin(), tmp_.begin() + q, tmp_.end());
        for (int i = 0; i < q; i++) add(tmp_[i].second);
    }
    // Shaw: похожие по месту, времени, навыку и маршруту.
    void shaw(const Solution& s, Rng& rng, int q) {
        servedList(s);
        if (served_.empty()) return;
        q = min(q, (int)served_.size());
        add(served_[rng.randint((int)served_.size())]);
        while ((int)rem.size() < q) {
            int r0 = rem[rng.randint((int)rem.size())];
            tmp_.clear();
            for (int k : served_) {
                if (mark_[k]) continue;
                double rel = 9 * P.g(r0, k) / P.maxD + 3 * fabs(P.a[r0] - P.a[k]) / SHIFT + 2 * (P.skill[r0] != P.skill[k]) +
                             0.5 * (s.rt[r0] != s.rt[k]);
                tmp_.push_back({rel, k});
            }
            if (tmp_.empty()) break;
            int j = (int)(pow(rng.uni(), 6.0) * tmp_.size());
            std::nth_element(tmp_.begin(), tmp_.begin() + j, tmp_.end());
            add(tmp_[j].second);
        }
    }
    // Маршрут целиком (чаще короткий), с вероятностью 1/2 плюс соседи его заявки.
    void route(const Solution& s, Rng& rng, int q) {
        int rs[MAX_VEHICLES], n = 0;
        for (int v = 0; v < P.V; v++)
            if (s.len[v]) rs[n++] = v;
        if (!n) return;
        std::sort(rs, rs + n, [&](int x, int y) { return s.len[x] < s.len[y]; });
        int v = rs[(int)(pow(rng.uni(), 2.0) * n)];
        for (int i = 0; i < s.len[v]; i++) add(s.r[v][i]);
        if (rng.uni() < 0.5) {
            int extra = rng.randint(max(1, q / 2)) + 1, seed = s.r[v][rng.randint(s.len[v])];
            for (int k : P.nbr[seed]) {
                if (extra <= 0) break;
                if (s.rt[k] >= 0 && !mark_[k]) {
                    add(k);
                    extra--;
                }
            }
        }
    }
    // Два маршрута: случайный с весом по км и маршрут бригады, чей старт ближе всего к случайной заявке первого.
    void routePair(const Solution& s, Rng& rng) {
        double wsum = 0;
        for (int v = 0; v < P.V; v++)
            if (s.len[v]) wsum += s.km[v] + 1e-3;
        if (wsum <= 0) return;
        double x = rng.uni() * wsum;
        int v1 = -1;
        for (int v = 0; v < P.V; v++)
            if (s.len[v]) {
                x -= s.km[v] + 1e-3;
                v1 = v;
                if (x <= 0) break;
            }
        int c = s.r[v1][rng.randint(s.len[v1])], v2 = -1;
        double bestD = INF;
        for (int v = 0; v < P.V; v++)
            if (v != v1 && s.len[v] && P.can(v, c)) {
                double d = P.d(v, P.start[v], P.nd[c]) * (0.8 + 0.4 * rng.uni());
                if (d < bestD) {
                    bestD = d;
                    v2 = v;
                }
            }
        for (int i = 0; i < s.len[v1]; i++) add(s.r[v1][i]);
        if (v2 >= 0)
            for (int i = 0; i < s.len[v2]; i++) add(s.r[v2][i]);
    }
    // До q ближайших к случайной заявке среди тех, чьё окно пересекается с её окном.
    void slot(const Solution& s, Rng& rng, int q) {
        servedList(s);
        if (served_.empty()) return;
        int seed = served_[rng.randint((int)served_.size())];
        double a = P.a[seed], b = P.b[seed];
        tmp_.clear();
        for (int k : served_)
            if (P.a[k] <= b && P.b[k] >= a) tmp_.push_back({P.g(seed, k), k});
        std::sort(tmp_.begin(), tmp_.end());
        for (int i = 0; i < (int)tmp_.size() && i < q; i++) add(tmp_[i].second);
    }

private:
    static constexpr double SISR_CBAR = 10, SISR_LMAX = 10;   // среднее число снимаемых и предел длины отрезка
    static constexpr double SISR_ALPHA = 0.5, SISR_KEEP_GROW = 0.5;

    const Problem& P;
    vector<char> mark_;
    vector<std::pair<double, int>> tmp_;
    vector<int> served_;

    void add(int k) {
        if (mark_[k]) return;
        mark_[k] = 1;
        rem.push_back(k);
    }
    void servedList(const Solution& s) {
        served_.clear();
        for (int k = 0; k < P.N; k++)
            if (s.rt[k] >= 0) served_.push_back(k);
    }
};

// Порядок вставки после SISR: случайный (4/11), по дефициту бригад с навыком и ширине окна (4/11), сначала дальние
// от стартов (2/11), сначала ближние (1/11).
inline void sisrSort(const Problem& P, vector<int>& pool, Rng& rng) {
    rng.shuffle(pool.begin(), pool.end());
    int w = rng.randint(11);
    if (w < 4) return;
    if (w < 8)
        std::stable_sort(pool.begin(), pool.end(), [&](int x, int y) {
            if (P.nCan[x] != P.nCan[y] && (P.nCan[x] < 4 || P.nCan[y] < 4)) return P.nCan[x] < P.nCan[y];
            return P.b[x] - P.a[x] < P.b[y] - P.a[y];
        });
    else if (w < 10) std::stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return P.farv[x] > P.farv[y]; });
    else std::stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return P.farv[x] < P.farv[y]; });
}

// Маршрут для удаления: наименьшая сумма «неудач» absence его заявок, при равенстве — короче.
inline int pickRouteToRemove(const Problem& P, const Solution& s, Rng& rng, const vector<double>& absence) {
    int best = -1;
    double bestVal = INF;
    for (int v = 0; v < P.V; v++) {
        if (!s.len[v]) continue;
        double val = 0;
        for (int i = 0; i < s.len[v]; i++) val += absence[s.r[v][i]];
        val += 1e-3 * s.len[v] + 1e-6 * rng.uni();
        if (val < bestVal) {
            bestVal = val;
            best = v;
        }
    }
    return best;
}

// Адаптивный выбор оператора: вероятность пропорциональна весу, вес сглаживается к среднему баллу.
class Adaptive {
public:
    explicit Adaptive(int n) : w_(n, 1), score_(n, 0), count_(n, 0) {}
    int pick(Rng& rng) const {
        double s = 0;
        for (double x : w_) s += x;
        double u = rng.uni() * s;
        for (int i = 0; i < (int)w_.size(); i++) {
            u -= w_[i];
            if (u <= 0) return i;
        }
        return (int)w_.size() - 1;
    }
    void add(int i, double x) {
        score_[i] += x;
        count_[i]++;
    }
    void update(double rate) {
        for (int i = 0; i < (int)w_.size(); i++) {
            if (count_[i]) w_[i] = w_[i] * (1 - rate) + rate * score_[i] / count_[i];
            w_[i] = max(w_[i], 0.05);
            score_[i] = 0;
            count_[i] = 0;
        }
    }

private:
    vector<double> w_, score_;
    vector<int> count_;
};

}  // namespace dispatch::lns
