// Отжиг на быстрых маршрутах (fast_routes.hpp).
//
// Суррогатная цель: км + Wv·бригады + Σ wp[k] по заявкам в пуле − β·Σ длина². Слагаемое с β тянет к неравным
// маршрутам: короткий потом проще удалить. Вес wp[k] растёт, пока заявка лежит в пуле.
// Фазы run(): починка (обслужить всё, открывать бригады можно) → сокращение бригад (удалить маршрут и отжигом без
// открытия бригад вернуть заявки из пула) → км при фиксированном парке. Опции включают оценку снизу по нагрузке,
// переназначение маршрутов бригадам (венгерский алгоритм) и циклы «разбиение + удаление» в фазе км.
#pragma once
#include "search/assignment.hpp"
#include "search/fast_routes.hpp"

namespace dispatch::fast {

struct AnnealerOptions {
    double elimShare = 0.6;      // фаза сокращения заканчивается на этой доле отведённого времени
    bool stopAfterElim = false;  // вернуть результат сразу после сокращения (км ищут другие)
    bool workBoundStop = false;  // не пытаться сократить парк ниже оценки снизу по нагрузке
    bool reassign = false;       // переназначение маршрутов бригадам в фазе км
    double splitShare = 0;       // доля фазы км на циклы «разбиение + удаление»
};

// Оценка снизу числа бригад по нагрузке: Σ(работа + самый короткий заезд) / длина смены.
inline int workLowerBound(const Data& F) {
    double total = 0;
    for (int k = 0; k < F.N; k++) {
        double shortest = 1e18;
        for (int v = 0; v < F.V; v++) {
            if (!(F.skillBit[k] & F.mask[v])) continue;
            const double* T = F.T[v];
            shortest = min(shortest, T[F.start[v] * F.M + F.S + k]);
            for (int j = 0; j < F.N; j++)
                if (j != k) shortest = min(shortest, T[(F.S + j) * F.M + F.S + k]);
        }
        if (shortest < 1e17) total += F.svc[k] + shortest;
    }
    return (int)ceil(total / SHIFT - 1e-9);
}

class Annealer {
public:
    // poll() вызывается каждые 256 итераций (true — оборвать поиск), onAttempt() — перед каждой попыткой удаления.
    std::function<bool()> poll;
    std::function<void()> onAttempt;
    bool aborted = false;
    double bestScore = 1e300;

    Annealer(const Data& f, uint64_t seed, AnnealerOptions opt = {}) : F(f), rng(seed), opt_(opt), workBound_(workLowerBound(f)) {}

    Routes bestRoutes() const {
        Routes R(F.V);
        for (int v = 0; v < F.V; v++) R[v].assign(bestSeq_[v], bestSeq_[v] + bestLen_[v]);
        return R;
    }
    // Сделать R текущим и лучшим решением (подхват чужого решения).
    void loadBest(const Routes& R) {
        S.load(F, R);
        bestScore = S.score();
        saveBest();
    }
    bool inKmPhase() const { return elimDone_; }

    // Три фазы до момента deadline по часам clock.
    Routes run(const Routes& init, double deadline, const Timer& clock) {
        S.load(F, init);
        resetPoolWeights();
        checkBest();
        const double tStart = clock.sec(), span = max(1e-3, deadline - tStart);
        if (S.np > 0) repairPhase(tStart + span * REPAIR_SHARE, clock);
        elimPhase(tStart + span * opt_.elimShare, clock);
        elimDone_ = true;
        restoreBest();
        if (opt_.stopAfterElim) return bestRoutes();
        kmPhaseSetup();
        if (opt_.reassign) {
            reassignVehicles();
            checkBest();
        }
        const double tKm = clock.sec();
        segment(NO_LIMIT, opt_.splitShare > 0 ? tKm + (deadline - tKm) * (1 - opt_.splitShare) : deadline, T0, T1, -1, clock);
        if (opt_.splitShare > 0 && S.np == 0)
            while (clock.sec() < deadline && !aborted) splitEliminateCycle(deadline, clock);
        if (opt_.reassign) {
            restoreBest();
            if (reassignVehicles()) checkBest();
        }
        return bestRoutes();
    }

    // Только фаза км от заданного решения.
    Routes kmOnly(const Routes& init, double deadline, const Timer& clock) {
        S.load(F, init);
        resetPoolWeights();
        bestScore = 1e300;
        checkBest();
        kmPhaseSetup();
        segment(NO_LIMIT, deadline, T0, T1, -1, clock);
        return bestRoutes();
    }

private:
    static constexpr long long NO_LIMIT = 1LL << 60;
    static constexpr double T0 = 5, T1 = 0.05;               // температура фазы км
    static constexpr double T_REPAIR = 20, T_ELIM = 50;      // температура починки и сокращения
    static constexpr double T_SPLIT = 2;                     // начальная температура км после разбиения
    static constexpr double WV = 100, WV_REPAIR = 5;         // вес бригады в суррогате
    static constexpr double BETA = 0.5;                      // давление к неравным маршрутам при сокращении
    static constexpr double WP0 = 500, WP_GROW = 1, WP_REPAIR_SCALE = 10, WP_KM = 1e5;
    static constexpr int WP_GROW_PERIOD = 20000;
    static constexpr double P_RELOCATE_BEST = 0.02, P_EJECT_BEST = 0.01;
    static constexpr double REPAIR_SHARE = 0.3;
    static constexpr long long REPAIR_ITERS = 1000000, ELIM_ITERS = 1000000, SPLIT_ELIM_ITERS = 300000, SPLIT_KM_ITERS = 300000;
    static constexpr int MAX_ELIM_FAILS = 40;
    static constexpr double SPLIT_NOISE = 0.3;

    const Data& F;
    Solution S;
    Rng rng;
    AnnealerOptions opt_;
    int workBound_;
    bool elimDone_ = false;
    double T = 1, Wv = WV, beta = BETA, wpScale = 1;
    bool allowOpen = true, allowOut = true;
    double wp[MAX_ORDERS];
    int bufA[MAX_LEN], bufB[MAX_LEN], midA[MAX_LEN], midB[MAX_LEN], tmp[MAX_LEN], ejectBest_[MAX_LEN], elimOrder_[MAX_LEN];
    int bestLen_[MAX_VEHICLES], bestSeq_[MAX_VEHICLES][MAX_LEN];

    // Кусок кандидата: маршрут v = префикс r[v][0..i) + mid[0..m) + хвост r[u][j..).
    struct Piece {
        int v, i;
        const int* mid;
        int m;
        int u, j;
    };

    double press(int len) const { return -beta * len * len; }
    bool accept(double delta) { return delta <= 0 || rng.uni() < exp(-delta / T); }
    void saveBest() {
        for (int v = 0; v < F.V; v++) {
            bestLen_[v] = S.r[v].len;
            memcpy(bestSeq_[v], S.r[v].seq, sizeof(int) * S.r[v].len);
        }
    }
    void checkBest() {
        double s = S.score();
        if (s < bestScore - 1e-9) {
            bestScore = s;
            saveBest();
        }
    }
    void restoreBest() { S.load(F, bestRoutes()); }
    void resetPoolWeights() {
        for (int k = 0; k < F.N; k++) wp[k] = wpScale * WP0 * F.pen[k] / 50;
    }
    void growPoolWeights() {
        for (int q = 0; q < S.np; q++) wp[S.pool[q]] += wpScale * WP_GROW * F.pen[S.pool[q]] / 50;
    }
    // Км при фиксированном парке: из маршрутов не выбрасывать, пустые бригады открывать, только если есть пул.
    void kmPhaseSetup() {
        allowOpen = S.np > 0;
        allowOut = false;
        beta = 0;
        for (int k = 0; k < F.N; k++) wp[k] = WP_KM * F.pen[k] / 50;
    }

    double pieceDelta(const Piece& c, double km) const {
        const Route& R = S.r[c.v];
        int newLen = candidateLen(S, c.i, c.m, c.u, c.j);
        return km - R.km + Wv * ((newLen > 0) - (R.len > 0)) + press(newLen) - press(R.len);
    }
    // Две разные бригады; сначала проверяется A (обычно сторона вставки, чаще недопустима).
    bool try2(const Piece& A, const Piece& B, double extra) {
        double kmA = evalCandidate(F, S, A.v, A.i, A.mid, A.m, A.u, A.j);
        if (kmA < 0) return false;
        double kmB = evalCandidate(F, S, B.v, B.i, B.mid, B.m, B.u, B.j);
        if (kmB < 0) return false;
        if (!accept(pieceDelta(A, kmA) + pieceDelta(B, kmB) + extra)) return false;
        int LA = compose(S, bufA, A.v, A.i, A.mid, A.m, A.u, A.j);
        int LB = compose(S, bufB, B.v, B.i, B.mid, B.m, B.u, B.j);
        S.setRoute(A.v, bufA, LA);
        S.setRoute(B.v, bufB, LB);
        return true;
    }
    bool try1(const Piece& A, double extra) {
        double km = evalCandidate(F, S, A.v, A.i, A.mid, A.m, A.u, A.j);
        if (km < 0) return false;
        if (!accept(pieceDelta(A, km) + extra)) return false;
        int LA = compose(S, bufA, A.v, A.i, A.mid, A.m, A.u, A.j);
        S.setRoute(A.v, bufA, LA);
        return true;
    }
    // Общий префикс и суффикс нового порядка ns маршрута v со старым: кусок между ними — единственное изменение.
    Piece intraPiece(int v, const int* ns, int nl) const {
        const Route& R = S.r[v];
        int pre = 0;
        while (pre < nl && pre < R.len && ns[pre] == R.seq[pre]) pre++;
        int suf = 0;
        while (suf < nl - pre && suf < R.len - pre && ns[nl - 1 - suf] == R.seq[R.len - 1 - suf]) suf++;
        return {v, pre, ns + pre, nl - pre - suf, v, R.len - suf};
    }
    bool tryIntra(int v, const int* ns, int nl, double extra) {
        const Route& R = S.r[v];
        Piece c = intraPiece(v, ns, nl);
        if (c.i == nl && nl == R.len) return false;
        return try1(c, extra);
    }
    int randomRouted() {
        for (int t = 0; t < 6; t++) {
            int k = rng.randint(F.N);
            if (S.where[k] >= 0) return k;
        }
        return -1;
    }
    int randomNeighbor(int k) {
        const vector<int>& nb = F.neighbors[k];
        return nb[rng.randint((int)nb.size())];
    }

    // ---- ходы ----

    // Отрезок длины L маршрута заявки k ставится рядом с её соседом (or-opt), возможно развёрнутым.
    void relocate(int L) {
        int k = randomRouted();
        if (k < 0) return;
        int n = randomNeighbor(k), b = S.where[n];
        if (b < 0) return;
        int a = S.where[k], i = S.pos[k];
        const Route& RA = S.r[a];
        L = min(L, RA.len - i);
        bool rev = L > 1 && (rng.next() & 1);
        for (int q = 0; q < L; q++) midA[q] = rev ? RA.seq[i + L - 1 - q] : RA.seq[i + q];
        int p = S.pos[n] + (int)(rng.next() & 1);
        if (a != b) {
            try2({b, p, midA, L, b, p}, {a, i, nullptr, 0, a, i + L}, 0);
            return;
        }
        if (p >= i && p <= i + L) return;
        int nl = 0;
        if (p < i) {
            for (int q = 0; q < p; q++) tmp[nl++] = RA.seq[q];
            for (int q = 0; q < L; q++) tmp[nl++] = midA[q];
            for (int q = p; q < i; q++) tmp[nl++] = RA.seq[q];
            for (int q = i + L; q < RA.len; q++) tmp[nl++] = RA.seq[q];
        } else {
            for (int q = 0; q < i; q++) tmp[nl++] = RA.seq[q];
            for (int q = i + L; q < p; q++) tmp[nl++] = RA.seq[q];
            for (int q = 0; q < L; q++) tmp[nl++] = midA[q];
            for (int q = p; q < RA.len; q++) tmp[nl++] = RA.seq[q];
        }
        tryIntra(a, tmp, nl, 0);
    }
    // Заявка в лучшую позицию другого маршрута.
    void relocateBest() {
        int k = randomRouted();
        if (k < 0) return;
        int a = S.where[k], i = S.pos[k];
        const Route& RA = S.r[a];
        double kmA = evalCandidate(F, S, a, i, nullptr, 0, a, i + 1);
        if (kmA < 0) return;
        int la = RA.len - 1;
        double dA = kmA - RA.km + Wv * ((la > 0) - 1) + press(la) - press(RA.len);
        double best = 1e18;
        int bv = -1, bp = -1;
        for (int v = 0; v < F.V; v++) {
            if (v == a || !(F.skillBit[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0 && !allowOpen) continue;
            double base = (R.len == 0 ? Wv : 0) + press(R.len + 1) - press(R.len) - R.km;
            for (int p = 0; p <= R.len; p++) {
                double km = evalCandidate(F, S, v, p, &k, 1, v, p);
                if (km < 0) continue;
                double c = km + base + 1e-3 * rng.uni();
                if (c < best) {
                    best = c;
                    bv = v;
                    bp = p;
                }
            }
        }
        if (bv < 0 || !accept(best + dA)) return;
        int LB = compose(S, bufB, bv, bp, &k, 1, bv, bp), LA = compose(S, bufA, a, i, nullptr, 0, a, i + 1);
        S.setRoute(a, bufA, LA);
        S.setRoute(bv, bufB, LB);
    }
    // Обмен заявки с соседом (или соседней с ним по маршруту).
    void swapMove() {
        int k = randomRouted();
        if (k < 0) return;
        int n = randomNeighbor(k), b = S.where[n];
        if (b < 0) return;
        int j = S.pos[n], r = rng.randint(3);
        if (r == 1 && j + 1 < S.r[b].len) j++;
        else if (r == 2 && j > 0) j--;
        int m = S.r[b].seq[j];
        if (m == k) return;
        int a = S.where[k], i = S.pos[k];
        if (a != b) {
            try2({a, i, &m, 1, a, i + 1}, {b, j, &k, 1, b, j + 1}, 0);
            return;
        }
        const Route& R = S.r[a];
        memcpy(tmp, R.seq, sizeof(int) * R.len);
        std::swap(tmp[i], tmp[j]);
        tryIntra(a, tmp, R.len, 0);
    }
    // Обмен отрезков (до 3 и до 3 заявок) между маршрутами заявки и соседа.
    void crossExchange() {
        int k = randomRouted();
        if (k < 0) return;
        int n = randomNeighbor(k), b = S.where[n], a = S.where[k];
        if (b < 0 || a == b) return;
        int i = S.pos[k], j = S.pos[n] + (int)(rng.next() & 1);
        const Route &RA = S.r[a], &RB = S.r[b];
        int L1 = min(1 + rng.randint(3), RA.len - i), L2 = min(rng.randint(4), RB.len - j);
        if (L2 <= 0 && L1 <= 0) return;
        for (int q = 0; q < L1; q++) midA[q] = RA.seq[i + q];
        for (int q = 0; q < L2; q++) midB[q] = RB.seq[j + q];
        try2({b, j, midA, L1, b, j + L2}, {a, i, midB, L2, a, i + L1}, 0);
    }
    // 2-opt*: обмен хвостами так, чтобы k и сосед n стали соседними.
    void twoOptStar() {
        int k = randomRouted();
        if (k < 0) return;
        int n = randomNeighbor(k), b = S.where[n], a = S.where[k];
        if (b < 0 || a == b) return;
        int i = S.pos[k], j = S.pos[n];
        if (rng.next() & 1) try2({a, i + 1, nullptr, 0, b, j}, {b, j, nullptr, 0, a, i + 1}, 0);   // k → n
        else try2({b, j + 1, nullptr, 0, a, i}, {a, i, nullptr, 0, b, j + 1}, 0);                  // n → k
    }
    // 2-opt внутри маршрута: разворот отрезка между k и соседом.
    void twoOpt() {
        int k = randomRouted();
        if (k < 0) return;
        int n = randomNeighbor(k), a = S.where[k];
        if (S.where[n] != a) return;
        int i = S.pos[k], j = S.pos[n];
        if (i > j) std::swap(i, j);
        const Route& R = S.r[a];
        memcpy(tmp, R.seq, sizeof(int) * R.len);
        std::reverse(tmp + i + 1, tmp + j + 1);
        tryIntra(a, tmp, R.len, 0);
    }
    // Хвост (или весь маршрут) на пустую бригаду, либо обмен маршрутами двух бригад.
    void vehicleMove() {
        int a = rng.randint(F.V), e = rng.randint(F.V);
        if (a == e || S.r[a].len == 0) return;
        if (S.r[e].len == 0) {
            int i = rng.randint(S.r[a].len);
            if (!allowOpen || (rng.next() & 1)) i = 0;
            try2({e, 0, nullptr, 0, a, i}, {a, i, nullptr, 0, -1, 0}, 0);
        } else {
            try2({a, 0, nullptr, 0, e, 0}, {e, 0, nullptr, 0, a, 0}, 0);
        }
    }
    // Заявка из пула в лучшую позицию.
    void poolInsert() {
        int k = S.pool[rng.randint(S.np)];
        Insertion in = bestInsertion(F, S, k, Wv, allowOpen, -1, [&](int l) { return press(l); });
        if (in.v < 0 || !accept(in.cost - wp[k])) return;
        int L = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
        S.poolRemove(k);
        S.setRoute(in.v, bufA, L);
    }
    // Заявка из пула вытесняет в пул соседа или случайную заявку его маршрута.
    void poolEject() {
        int k = S.pool[rng.randint(S.np)];
        int n = randomNeighbor(k), b = S.where[n];
        if (b < 0) return;
        int j = S.pos[n];
        const Route& R = S.r[b];
        int mode = rng.randint(3);
        if (mode == 0) {
            if (!try1({b, j, &k, 1, b, j + 1}, wp[n] - wp[k])) return;
            S.poolRemove(k);
            S.poolAdd(n);
            return;
        }
        int p = j + (mode == 2), q = rng.randint(R.len);
        int e = R.seq[q], nl = 0;
        for (int t = 0; t <= R.len; t++) {
            if (t == p) tmp[nl++] = k;
            if (t < R.len && t != q) tmp[nl++] = R.seq[t];
        }
        if (!tryIntra(b, tmp, nl, wp[e] - wp[k])) return;
        S.poolRemove(k);
        S.poolAdd(e);
    }
    // Лучшая вставка заявки из пула с вытеснением не более одной заявки: все маршруты и позиции.
    void poolEjectBest() {
        int k = S.pool[rng.randint(S.np)];
        double best = 1e18;
        int bv = -1, be = -1, bl = 0;
        for (int v = 0; v < F.V; v++) {
            if (!(F.skillBit[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0) continue;
            for (int q = -1; q < R.len; q++) {
                int e = q >= 0 ? R.seq[q] : -1;
                double ew = e >= 0 ? wp[e] : 0;
                if (ew - wp[k] >= best) continue;
                for (int p = 0; p <= R.len; p++) {
                    if (q >= 0 && p == q + 1) continue;   // то же, что p == q
                    int nl = 0;
                    for (int t = 0; t <= R.len; t++) {
                        if (t == p) tmp[nl++] = k;
                        if (t < R.len && t != q) tmp[nl++] = R.seq[t];
                    }
                    Piece c = intraPiece(v, tmp, nl);
                    double km = evalCandidate(F, S, c.v, c.i, c.mid, c.m, c.u, c.j);
                    if (km < 0) continue;
                    double d = km - R.km + ew - wp[k] + press(nl) - press(R.len) + 1e-3 * rng.uni();
                    if (d < best) {
                        best = d;
                        bv = v;
                        be = e;
                        bl = nl;
                        memcpy(ejectBest_, tmp, sizeof(int) * nl);
                    }
                }
            }
        }
        if (bv < 0 || !accept(best)) return;
        S.poolRemove(k);
        S.setRoute(bv, ejectBest_, bl);
        if (be >= 0) S.poolAdd(be);
    }
    // Заявка из маршрута в пул.
    void unassign() {
        int k = randomRouted();
        if (k < 0) return;
        int a = S.where[k], i = S.pos[k];
        if (!try1({a, i, nullptr, 0, a, i + 1}, wp[k])) return;
        S.poolAdd(k);
    }
    void step() {
        double r = rng.uni();
        if (S.np > 0 && r < 0.12) {
            if (r < 0.07 || !allowOut) poolInsert();
            else if (r < 0.12 - P_EJECT_BEST) poolEject();
            else poolEjectBest();
            return;
        }
        r = rng.uni();
        if (r < P_RELOCATE_BEST) relocateBest();
        else if (r < 0.28) relocate(1);
        else if (r < 0.43) relocate(2 + rng.randint(2));
        else if (r < 0.58) swapMove();
        else if (r < 0.70) crossExchange();
        else if (r < 0.85) twoOptStar();
        else if (r < 0.90) twoOpt();
        else if (r < 0.96) vehicleMove();
        else if (allowOut) unassign();
    }

    // Отрезок отжига: до maxIt итераций или до tEnd, температура геометрически Ta → Tb. При stopPen ≥ 0 — выход с
    // true, как только штраф пула не больше stopPen.
    bool segment(long long maxIt, double tEnd, double Ta, double Tb, double stopPen, const Timer& clock) {
        const double t0 = clock.sec();
        for (long long it = 0;; ) {
            if ((it & 255) == 0) {
                double now = clock.sec();
                if (now >= tEnd) return false;
                if (poll && poll()) {
                    aborted = true;
                    return false;
                }
                double x = max((double)it / maxIt, (now - t0) / max(1e-9, tEnd - t0));
                if (x >= 1) return false;
                T = pow(Ta, 1 - x) * pow(Tb, x);
            }
            it++;
            if (it % WP_GROW_PERIOD == 0) growPoolWeights();
            step();
            checkBest();
            if (stopPen >= 0 && S.poolPen <= stopPen + 1e-9) return true;
        }
    }

    // ---- фазы ----

    // Починка: обслужить всё; рестарты со случайных жадных решений.
    void repairPhase(double tEnd, const Timer& clock) {
        allowOpen = true;
        allowOut = true;
        beta = 0;
        Wv = WV_REPAIR;
        wpScale = WP_REPAIR_SCALE;
        for (int attempt = 0; clock.sec() < tEnd && !aborted; attempt++) {
            if (attempt > 0) {
                vector<int> order(F.N);
                std::iota(order.begin(), order.end(), 0);
                vector<double> key(F.N);
                for (int k = 0; k < F.N; k++) key[k] = F.b[k] + F.a[k] * 1e-3 + rng.uni() * 90;
                std::sort(order.begin(), order.end(), [&](int x, int y) { return key[x] < key[y]; });
                static const double openCosts[] = {0, 20, 100, 1e4};
                greedyBuild(F, S, order, openCosts[rng.randint(4)]);
                checkBest();
            }
            resetPoolWeights();
            if (segment(REPAIR_ITERS, tEnd, T_REPAIR, T_REPAIR, 0, clock)) break;
        }
        Wv = WV;
        wpScale = 1;
        restoreBest();
    }

    // Маршрут для удаления: случайный с весом 1/длина²; -1, если непустой маршрут один.
    int pickSmallRoute() {
        double wsum = 0;
        int nonEmpty = 0;
        for (int v = 0; v < F.V; v++)
            if (S.r[v].len) {
                wsum += 1.0 / (S.r[v].len * S.r[v].len);
                nonEmpty++;
            }
        if (nonEmpty <= 1) return -1;
        double x = rng.uni() * wsum;
        for (int v = 0; v < F.V; v++)
            if (S.r[v].len) {
                x -= 1.0 / (S.r[v].len * S.r[v].len);
                if (x <= 0) return v;
            }
        return -1;
    }
    // Удалить маршрут r: его заявки жадно в другие непустые маршруты, остаток — в пул.
    void eliminate(int r) {
        const int L = S.r[r].len;
        memcpy(elimOrder_, S.r[r].seq, sizeof(int) * L);
        if (rng.next() & 1) std::sort(elimOrder_, elimOrder_ + L, [&](int p, int q) { return F.b[p] - F.a[p] < F.b[q] - F.a[q]; });
        else rng.shuffle(elimOrder_, elimOrder_ + L);
        S.setRoute(r, nullptr, 0);
        for (int q = 0; q < L; q++) {
            int k = elimOrder_[q];
            Insertion in = bestInsertion(F, S, k, Wv, false, r, [&](int l) { return press(l); });
            if (in.v < 0) {
                S.poolAdd(k);
                continue;
            }
            int nl = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
            S.setRoute(in.v, bufA, nl);
        }
    }
    // Сокращение: удалить маршрут, отжигом без открытия бригад вернуть штраф пула к прежнему.
    void elimPhase(double tEnd, const Timer& clock) {
        int fails = 0;
        while (clock.sec() < tEnd && fails < MAX_ELIM_FAILS && !aborted) {
            if (onAttempt) onAttempt();
            restoreBest();
            if (opt_.workBoundStop && S.np == 0 && S.used <= workBound_) break;
            int r = pickSmallRoute();
            if (r < 0) break;
            double target = S.poolPen;
            eliminate(r);
            resetPoolWeights();
            allowOpen = false;
            allowOut = true;
            beta = BETA;
            bool ok = S.poolPen <= target + 1e-9 || segment(ELIM_ITERS, tEnd, T_ELIM, T_ELIM, target, clock);
            checkBest();
            fails = ok ? 0 : fails + 1;
        }
    }

    // Хвост маршрута a с позиции i — на пустую бригаду e (лучший по Δкм вариант с шумом).
    bool splitMove() {
        double best = 1e18;
        int ba = -1, bi = -1, be = -1;
        long long tried[MAX_VEHICLES];
        int nTried = 0;
        const double noise = SPLIT_NOISE * S.km / max(1, S.used);
        for (int e = 0; e < F.V; e++) {
            if (S.r[e].len) continue;
            long long key = vehicleClass(F, e);
            if (std::find(tried, tried + nTried, key) != tried + nTried) continue;
            tried[nTried++] = key;
            for (int a = 0; a < F.V; a++) {
                const Route& R = S.r[a];
                for (int i = 0; i < R.len; i++) {
                    if (R.need[i] & ~F.mask[e]) continue;
                    double kmE = evalCandidate(F, S, e, 0, nullptr, 0, a, i);
                    if (kmE < 0) continue;
                    double d = kmE + (i > 0 ? R.ck[i - 1] : 0) - R.km + noise * rng.uni();
                    if (d < best) {
                        best = d;
                        ba = a;
                        bi = i;
                        be = e;
                    }
                }
            }
        }
        if (ba < 0) return false;
        int LE = compose(S, bufA, be, 0, nullptr, 0, ba, bi);
        S.setRoute(be, bufA, LE);
        memcpy(bufB, S.r[ba].seq, sizeof(int) * bi);
        S.setRoute(ba, bufB, bi);
        return true;
    }
    // Смена набора бригад при том же их числе: разбиение, удаление малого маршрута, короткий отжиг км.
    void splitEliminateCycle(double tEnd, const Timer& clock) {
        restoreBest();
        const double pen0 = S.poolPen;
        if (!splitMove()) return;
        int r = pickSmallRoute();
        if (r < 0) return;
        eliminate(r);
        resetPoolWeights();
        allowOpen = false;
        allowOut = true;
        beta = BETA;
        const double tSaved = T;
        bool ok = S.poolPen <= pen0 + 1e-9 || segment(SPLIT_ELIM_ITERS, tEnd, T_ELIM, T_ELIM, pen0, clock);
        if (ok) {
            if (opt_.reassign) reassignVehicles();
            allowOut = false;
            beta = 0;
            for (int k = 0; k < F.N; k++) wp[k] = WP_KM * F.pen[k] / 50;
            segment(SPLIT_KM_ITERS, tEnd, T_SPLIT, T1, -1, clock);
        }
        allowOut = false;
        beta = 0;
        T = tSaved;
        for (int k = 0; k < F.N; k++) wp[k] = WP_KM * F.pen[k] / 50;
    }

    // Оптимальное назначение текущих маршрутов бригадам (маршрут целиком). true — км уменьшились.
    bool reassignVehicles() {
        const double INFEASIBLE = 1e9;
        int rs[MAX_VEHICLES], n = 0;
        for (int v = 0; v < F.V; v++)
            if (S.r[v].len) rs[n++] = v;
        if (n == 0) return false;
        vector<vector<double>> cost(n, vector<double>(F.V));
        for (int i = 0; i < n; i++)
            for (int v = 0; v < F.V; v++) {
                double km = evalCandidate(F, S, v, 0, S.r[rs[i]].seq, S.r[rs[i]].len, -1, 0);
                cost[i][v] = km < 0 ? INFEASIBLE : km;
            }
        vector<int> column = assignMinCost(cost, n, F.V), rowOf(F.V, -1);
        for (int i = 0; i < n; i++) rowOf[column[i]] = i;
        double total = 0;
        for (int v = 0; v < F.V; v++)
            if (rowOf[v] >= 0) total += cost[rowOf[v]][v];
        if (total >= INFEASIBLE / 2 || total > S.km - 1e-6) return false;
        Routes old(n);
        for (int i = 0; i < n; i++) old[i].assign(S.r[rs[i]].seq, S.r[rs[i]].seq + S.r[rs[i]].len);
        for (int v = 0; v < F.V; v++)
            if (S.r[v].len) S.setRoute(v, nullptr, 0);
        for (int i = 0; i < n; i++) S.setRoute(column[i], old[i].data(), (int)old[i].size());
        return true;
    }
};

}  // namespace dispatch::fast
