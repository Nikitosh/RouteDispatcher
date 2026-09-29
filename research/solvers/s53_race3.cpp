// 53. Гонка v3: кандидат 0 — полный этап парка (K бригад); остальные строятся с нуля regret-вставкой
// с жёстким пределом K бригад (случайный порядок, уровень сожаления, шум) — разные наборы бригад почти даром.
// Ниже описание s51: 51. Гонка кандидатов: несколько решений с минимальным парком (разные сиды и наборы бригад), затем раунды
// оптимизации км с отсевом худшей половины (successive halving). Блоки взяты из s33.
// Исходный комментарий s33: 33. Гибрид (лучший из s30–s33): 1) отжиг s10 (починка + сокращение бригад) до 0.45·T; 2) R&R-сокращение парка
// FleetRR (ALNS-операторы × regret-1/2/3 с шумом, приёмка по штрафу / актуальной Σabsence) до 0.6·T; 3) фаза км
// KmSearch: SISR-отжиг (T 5→0.05 средних рёбер) + удаление маршрута / пары маршрутов (смена набора бригад),
// рекорды полируются ЛП lns_util. Опции (env): FLR — число независимых фаз парка, KMR/CYC/RST — рестарты,
// VEX — перебор исключаемой бригады, SAF/RREND — доли времени. Код отжига ниже скопирован из s10_sa.cpp.
// Исходный s10: Мульти-жадное построение + имитация отжига (в стиле AHC), три фазы по времени:
//  1) починка (если жадность обслужила не всё): SA с пулом невыполненных, открытие бригад дёшево (Wv=5),
//     штрафы пула большие и растут для «застрявших» заявок; рестарты со случайных жадных решений;
//  2) сокращение бригад (Nagata–Bräysy-подобно): от лучшего решения удалить малый маршрут (вероятность ~1/len²),
//     его заявки жадно вставить в другие, остаток — в пул; SA без открытия бригад при высокой температуре
//     (почти без учёта км) до опустошения пула, иначе откат; суррогат с −β·Σ|маршрут|²;
//  3) минимизация км при фиксированном числе бригад: SA, T = T0^(1-x)·T1^x.
// Ходы (гранулярные, через K ближайших по «времени-расстоянию»): relocate, relocate в лучшую позицию, or-opt(2–3,
// с разворотом), swap, cross-exchange, 2-opt*, 2-opt внутри, перенос хвоста/маршрута на другую бригаду, обмен
// бригад, вставка из пула, вытеснение в пул (соседа / случайной / лучшая вставка с вытеснением), снятие в пул.
// Проверка хода — O(длина изменённого куска) через кэш latest-start суффикса. Лучшее — по Score::scalar().
#include "sa_util.hpp"
#include "lns2_util.hpp"
using namespace sau;

struct SA {
    const Fast& F; Sol S; Rng rng;
    double Wv = 100, beta = 0.5, beta0 = 0.5, T = 1, T0 = 5, T1 = 0.05, Wp0 = 500, Winc = 1;
    int incPeriod = 20000;
    double wp[MAXN];
    int bufA[MAXL], bufB[MAXL], midA[MAXL], midB[MAXL], tmp[MAXL];
    double bestScore = 1e300; int bestLen[MAXV], bestSeq[MAXV][MAXL];
    bool dbg = getenv("SA_DBG") != nullptr;
    long long iters = 0, acc = 0, elimTry = 0, elimAcc = 0;
    SA(const Fast& f, uint64_t seed) : F(f), rng(seed) {}

    inline double press(int len) const { return -beta * len * len; }
    inline bool accept(double d) { return d <= 0 || rng.uni() < exp(-d / T); }
    void checkBest() {
        double s = S.trueScore();
        if (s < bestScore - 1e-9) {
            bestScore = s;
            for (int v = 0; v < F.V; v++) { bestLen[v] = S.r[v].len; memcpy(bestSeq[v], S.r[v].seq, sizeof(int) * S.r[v].len); }
        }
    }
    Routes bestRoutes() const { Routes R(F.V); for (int v = 0; v < F.V; v++) R[v].assign(bestSeq[v], bestSeq[v] + bestLen[v]); return R; }
    double surrogate() const {
        double c = S.km + Wv * S.used;
        for (int q = 0; q < S.np; q++) c += wp[S.pool[q]];
        for (int v = 0; v < F.V; v++) c += press(S.r[v].len);
        return c;
    }
    struct C { int v, i; const int* mid; int m; int u, j; };
    inline double routeDelta(const C& c, double km, int& newLen) {
        const Route& R = S.r[c.v];
        newLen = candLen(S, c.i, c.m, c.u, c.j);
        return km - R.km + Wv * ((newLen > 0) - (R.len > 0)) + press(newLen) - press(R.len);
    }
    // Две разные бригады. Сначала проверяется A (обычно сторона вставки — чаще недопустима).
    bool try2(const C& A, const C& B, double extra) {
        double kmA = evalCand(F, S, A.v, A.i, A.mid, A.m, A.u, A.j); if (kmA < 0) return false;
        double kmB = evalCand(F, S, B.v, B.i, B.mid, B.m, B.u, B.j); if (kmB < 0) return false;
        int la, lb; double d = routeDelta(A, kmA, la) + routeDelta(B, kmB, lb) + extra;
        if (!accept(d)) return false;
        int LA = compose(S, bufA, A.v, A.i, A.mid, A.m, A.u, A.j), LB = compose(S, bufB, B.v, B.i, B.mid, B.m, B.u, B.j);
        S.setRoute(A.v, bufA, LA); S.setRoute(B.v, bufB, LB);
        return true;
    }
    bool try1(const C& A, double extra) {
        double km = evalCand(F, S, A.v, A.i, A.mid, A.m, A.u, A.j); if (km < 0) return false;
        int la; double d = routeDelta(A, km, la) + extra;
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
        double kmA = evalCand(F, S, a, i, nullptr, 0, a, i + 1); if (kmA < 0) return;
        int la = RA.len - 1;
        double dA = kmA - RA.km + Wv * ((la > 0) - 1) + press(la) - press(RA.len);
        double best = 1e18; int bv = -1, bp = -1;
        for (int v = 0; v < F.V; v++) {
            if (v == a || !(F.skb[k] & F.mask[v])) continue;
            const Route& R = S.r[v];
            if (R.len == 0 && !allowOpen) continue;
            double base = (R.len == 0 ? Wv : 0) + press(R.len + 1) - press(R.len) - R.km;
            for (int p = 0; p <= R.len; p++) {
                double km = evalCand(F, S, v, p, &k, 1, v, p);
                if (km < 0) continue;
                double c = km + base + 1e-3 * rng.uni();
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
    void mvPoolIns() {
        int k = S.pool[rng.randint(S.np)];
        Ins in = bestIns(F, S, k, Wv, allowOpen, -1, [&](int l) { return press(l); });
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
    double evalFull(int v, const int* ns, int nl) {
        const Route& R = S.r[v];
        int pre = 0; while (pre < nl && pre < R.len && ns[pre] == R.seq[pre]) pre++;
        int suf = 0; while (suf < nl - pre && suf < R.len - pre && ns[nl - 1 - suf] == R.seq[R.len - 1 - suf]) suf++;
        return evalCand(F, S, v, pre, ns + pre, nl - pre - suf, v, R.len - suf);
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
                    double km = evalFull(v, tmp, nl);
                    if (km < 0) continue;
                    double d = km - R.km + ew - wp[k] + press(nl) - press(R.len) + 1e-3 * rng.uni();
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
    int eliminate(int r, int* left) {
        int L = S.r[r].len; memcpy(elimOrd, S.r[r].seq, sizeof(int) * L);
        if (rng.next() & 1) sort(elimOrd, elimOrd + L, [&](int p, int q) { return F.b[p] - F.a[p] < F.b[q] - F.a[q]; });
        else for (int q = L - 1; q > 0; q--) swap(elimOrd[q], elimOrd[rng.randint(q + 1)]);
        S.setRoute(r, nullptr, 0);
        int nleft = 0;
        for (int q = 0; q < L; q++) {
            int k = elimOrd[q];
            Ins in = bestIns(F, S, k, Wv, false, r, [&](int l) { return press(l); });
            if (in.v < 0) { S.poolAdd(k); left[nleft++] = k; continue; }
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
    void step() {
        double r = rng.uni();
        if (S.np > 0 && r < 0.12) {
            if (r < 0.07 || !allowOut) mvPoolIns();
            else if (r < 0.12 - pEB) mvEject(); else mvEjectBest();
            return;
        }
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
    double wpScale = 1;
    void resetWp() { for (int k = 0; k < F.N; k++) wp[k] = wpScale * Wp0 * F.pen[k] / 50; }
    void growWp() { for (int q = 0; q < S.np; q++) wp[S.pool[q]] += wpScale * Winc * F.pen[S.pool[q]] / 50; }
    // Отрезок SA: до maxIt итераций или до tEnd; T геометрически Ta→Tb. stopPen>=0: выйти, как только штраф пула ≤ stopPen.
    bool segment(long long maxIt, double tEnd, double Ta, double Tb, double stopPen, Timer& tm) {
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
            if (stopPen >= 0 && S.poolPen <= stopPen + 1e-9) return true;
        }
    }
    void restoreBest() { S.fromRoutes(F, bestRoutes()); }
    // только фаза км отжига s10 (фаза 3) от заданного решения до tEnd
    Routes kmOnly(const Routes& init, double tEnd, Timer& tm) {
        S.fromRoutes(F, init); resetWp(); bestScore = 1e300; checkBest();
        allowOpen = S.np > 0; allowOut = false; allowElim = false; beta = 0;
        for (int k = 0; k < F.N; k++) wp[k] = 1e5 * F.pen[k] / 50;
        segment(1LL << 60, tEnd, T0, T1, -1, tm);
        return bestRoutes();
    }

    double pRB = 0.02, pEB = 0.01, wpScaleRepair = 10, WvRepair = 5, betaRepair = 0, fRepair = 0.3, fElim = 0.6, Te = 50, Tr = 20; long long elimIters = 1000000, repIters = 1000000; int maxFails = 40;
    bool stopAfterElim = false;
    Routes run(const Routes& init, double tl, Timer& tm) {
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
        int fails = 0;
        while (tm.sec() < tStart + span * fElim && fails < maxFails) {
            restoreBest();
            int r = pickSmallRoute(); if (r < 0) break;
            double target = S.poolPen;
            elimTry++;
            int left[MAXL]; eliminate(r, left);
            resetWp();
            allowOpen = false; allowOut = true; allowElim = false; beta = beta0;
            bool ok = S.poolPen <= target + 1e-9 || segment(elimIters, tStart + span * fElim, Te, Te, target, tm);
            checkBest();
            if (ok) { elimAcc++; fails = 0; } else fails++;
        }
        // 3) км при фиксированном числе бригад
        restoreBest();
        if (stopAfterElim) return bestRoutes();
        allowOpen = S.np > 0; allowOut = false; allowElim = false; beta = 0;
        for (int k = 0; k < F.N; k++) wp[k] = 1e5 * F.pen[k] / 50;
        segment(1LL << 60, tl, T0, T1, -1, tm);
        if (dbg) { for (int q = 0; q < S.np; q++) { int k = S.pool[q]; fprintf(stderr, "pool k=%d wp=%.1f pen=%.0f a=%.0f b=%.0f svc=%.0f sk=%d\n", k, wp[k], F.pen[k], F.a[k], F.b[k], F.svc[k], F.skill[k]); } }
        return bestRoutes();
    }
};

// Фаза парка: отжиг s10 (починка + удаление маршрутов) до доли SAF отрезка, затем R&R с Σabsence до tEnd.
static l2::Sol fleetOnce(const Instance& I, const l2::Prob& Pb, double tEnd, uint64_t seed, Timer& tm, l2::KmSearch& KS, Rng& rng2) {
    double tBeg = tm.sec();
    Fast F(I);
    Rng rng(seed);
    Sol g; Routes init = multiGreedy(F, rng, (tEnd - tBeg) * 0.08, g);
    SA sa(F, seed * 7 + 3); sa.fElim = l2::P("SAF", 0.75);
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
    if (getenv("SA_INC")) sa.incPeriod = atoi(getenv("SA_INC"));
    sa.stopAfterElim = true;
    Routes R = sa.run(init, tEnd, tm);
    Score s = evaluate(I, R), s0 = evaluate(I, init);
    if (!s.feasible || (s0.feasible && s0.scalar() < s.scalar())) R = init;
    l2::Sol best; best.fromRoutes(Pb, R);
    if (tm.sec() < tEnd) { l2::FleetRR FR(I, Pb, rng2, tm, KS); double tS = best.nUn() ? tm.sec() + 0.5 * (tEnd - tm.sec()) : 0; FR.run(best, tS, tEnd, tEnd); }
    return best;
}

static l2::Sol solveOnce(const Instance& I, const l2::Prob& Pb, double tl, uint64_t seed, Timer& tm) {
    double tBeg = tm.sec(), tSA = 0;
    Rng rng2(seed * 31 + 7);
    l2::KmSearch KS(I, Pb, rng2, tm);
    double rrEnd = tBeg + (tl - tBeg) * l2::P("RREND", 0.6);
    int FLR = (int)l2::P("FLR", 1); l2::Sol best; bool have = false;
    for (int f = 0; f < FLR; f++) {   // несколько независимых фаз парка, лучшая идёт в фазу км
        double segEnd = tm.sec() + (rrEnd - tm.sec()) / (FLR - f);
        l2::Sol c = fleetOnce(I, Pb, segEnd, seed * 7919 + f, tm, KS, rng2);
        if (getenv("DEBUG")) fprintf(stderr, "  fleet %d: used=%d km=%.1f t=%.2f\n", f, c.used(), c.kmTot(), tm.sec());
        if (!have || c.better(best, Pb)) { best = c; have = true; }
    }
    double tRR = tm.sec();
    double SAKM = l2::P("SAKM", 0);
    if (SAKM > 0 && best.nUn() == 0) {   // часть фазы км — ходы отжига s10 (дешёвые соседства), затем SISR
        Fast F(I); SA sk(F, seed * 13 + 5);
        Routes R2 = sk.kmOnly(best.routes(), tm.sec() + (tl - tm.sec()) * SAKM, tm);
        l2::Sol c; c.fromRoutes(Pb, R2); if (c.better(best, Pb)) best = c;
    }
    int KMR = (int)l2::P("KMR", 1), CYC = (int)l2::P("CYC", 1); double ELF = l2::P("ELF", 0.3);
    l2::Sol fleetBest = best;
    if (CYC > 1) {   // циклы: попытка сократить парк R&R от лучшего решения, затем км
        for (int r = 0; r < CYC; r++) {
            double segEnd = tm.sec() + (tl - tm.sec()) / (CYC - r);
            if (r > 0 && best.nUn() == 0 && best.used() > 1) {
                l2::FleetRR FR(I, Pb, rng2, tm, KS); l2::Sol c = best;
                FR.run(c, 0, tm.sec() + (segEnd - tm.sec()) * ELF, segEnd);
                if (c.better(best, Pb)) best = c;
            }
            l2::Sol c = best; KS.run(c, segEnd);
            if (c.better(best, Pb)) best = c;
        }
    } else if (l2::P("VEX", 0) > 0) {
        // исключение бригады: для каждой используемой бригады u — запрет u, её маршрут перевставляется, км-поиск
        double tMain = tm.sec() + (tl - tm.sec()) * l2::P("VEXF", 0.4);
        { l2::Sol c = best; KS.run(c, tMain); if (c.better(best, Pb)) best = c; }
        vector<pair<double, int>> us;
        for (int u = 0; u < Pb.V; u++) if (best.len[u]) us.push_back({-best.km[u] / best.len[u] * (0.9 + 0.2 * rng2.uni()), u});
        sort(us.begin(), us.end());
        int nOpt = min((int)us.size(), (int)l2::P("VEXN", 12));
        double tOptEnd = tm.sec() + (tl - tm.sec()) * 0.8;
        for (int oi = 0; oi < nOpt; oi++) {
            int u = us[oi].second; double segEnd = tm.sec() + (tOptEnd - tm.sec()) / (nOpt - oi);
            if (!best.len[u]) continue;
            l2::Prob P2 = Pb; for (int k = 0; k < Pb.N; k++) P2.canMask[k] &= ~(1 << u);
            l2::Sol c; c.fromRoutes(P2, best.routes());   // маршрут u выпадает
            vector<int> pool; l2::collectAbsent(c, pool); l2::Regret RG;
            for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng2.randint(i + 1)]);
            RG.run(P2, c, pool, rng2, 2, best.used(), W_VEHICLE, nullptr);
            if (getenv("DEBUG") && !pool.empty()) fprintf(stderr, "  vex u=%d left=%d\n", u, (int)pool.size());
            l2::KmSearch K2(I, P2, rng2, tm); K2.run(c, segEnd, -1, -1, -1, best.used());
            l2::Sol c2; c2.fromRoutes(Pb, c.routes());
            if (getenv("DEBUG")) fprintf(stderr, "  vex u=%d -> used=%d km=%.1f (best %.1f)\n", u, c2.used(), c2.kmTot(), best.kmTot());
            if (c2.better(best, Pb)) best = c2;
        }
        { l2::Sol c = best; KS.run(c, tl); if (c.better(best, Pb)) best = c; }
    } else
    for (int r = 0; r < KMR; r++) {
        double segEnd = tm.sec() + (tl - tm.sec()) / (KMR - r);
        l2::Sol c = l2::P("KMRB", 0) ? best : fleetBest;
        KS.run(c, segEnd);
        if (c.better(best, Pb)) best = c;
    }
    if (getenv("DEBUG")) fprintf(stderr, "tRR=%.3f tSA=%.3f it=%ld used=%d km=%.1f\n", tRR, tSA, KS.iters, best.used(), best.kmTot());
    return best;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; tl *= 0.97;
    l2::Prob Pb(I); Rng rng2(seed * 31 + 7);
    l2::KmSearch KS(I, Pb, rng2, tm);
    // число кандидатов зависит от размера: 56 заявок -> 6, 83 -> 4, 100 -> 3, 130 -> 2 (NC задаёт явно)
    int NCa = max(2, (int)lround(6.0 - (I.N - 56) / 18.0));
    int NC = (int)l2::P("NC", l2::P("ADAPT", 0) > 0 ? NCa : 6);
    double F0 = l2::P("F0", 0.25), FA = l2::P("FA", 0.6);
    double FC = l2::P("FC", NC > 1 ? max(0.02, (FA - F0) / (NC - 1)) : 0.05);   // бюджет парка: первый F0*T, остальные FC*T
    double T0 = l2::P("T0R", 5.0);
    vector<l2::Sol> cand;
    l2::Sol base = fleetOnce(I, Pb, tl * F0, seed * 7919, tm, KS, rng2);
    cand.push_back(base); int K = base.used();
    double tGen = tl * l2::P("FG", 0.15) + tm.sec(); int tries = 0, okc = 0;
    while ((int)cand.size() < NC && tm.sec() < tGen && base.nUn() == 0) {
        tries++;
        l2::Sol c; c.fromRoutes(Pb, Routes(Pb.V));
        vector<int> pool(Pb.N); iota(pool.begin(), pool.end(), 0);
        int mode = rng2.randint(3);
        if (mode == 0) for (int i = Pb.N - 1; i > 0; i--) swap(pool[i], pool[rng2.randint(i + 1)]);
        else { vector<double> key(Pb.N); for (int k = 0; k < Pb.N; k++) key[k] = I.ord[k].b + rng2.uni() * (mode == 1 ? 60 : 240);
               sort(pool.begin(), pool.end(), [&](int a, int b) { return key[a] < key[b]; }); }
        l2::Regret RG; RG.run(Pb, c, pool, rng2, 1 + rng2.randint(3), K, W_VEHICLE, nullptr, rng2.uni() * 0.3);
        if (c.nUn() == 0 && c.used() <= K) { cand.push_back(c); okc++; }
    }
    if (getenv("DEBUG")) fprintf(stderr, "база K=%d км %.1f; попыток %d, удачных %d, t=%.2f\n", K, base.kmTot(), tries, okc, tm.sec());
    l2::Sol ref = base; vector<l2::Sol> alive = cand;
    // фаза B: раунды с отсевом
    int round = 0;
    while (alive.size() > 1 && tm.sec() < tl) {
        int rounds_left = 0; for (size_t n = alive.size(); n > 1; n = (n + 1) / 2) rounds_left++;
        double seg = (tl - tm.sec()) / (rounds_left + 1);   // последний раунд — только лучший
        double per = seg / alive.size();
        for (auto& x : alive) KS.run(x, tm.sec() + per, -1, T0 * pow(0.5, round));
        sort(alive.begin(), alive.end(), [&](const l2::Sol& a, const l2::Sol& b) { return a.better(b, Pb); });
        if (getenv("DEBUG")) { fprintf(stderr, "раунд %d:", round); for (auto& x : alive) fprintf(stderr, " %.1f", x.kmTot()); fprintf(stderr, "\n"); }
        alive.resize((alive.size() + 1) / 2); round++;
    }
    l2::Sol best = alive[0];
    KS.run(best, tl, -1, T0 * pow(0.5, round));
    lu::LocalSearch LS(I); lu::Sol x = l2::toLu(I, best); Rng rr(seed); LS.run(x, rr, best.used(), &tm, tl / 0.97 * 0.99);
    l2::Sol y; y.fromRoutes(Pb, x.r); if (y.better(best, Pb)) best = y;
    return l2::finalizeR(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "53_race3"); }
