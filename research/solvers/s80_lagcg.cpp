// 80. Lagrangian / LP column-generation hybrid (agent lagcg, logs/lagcg.md, toolkit solvers/lag_util.hpp).
//  1) fleet phase of s33 (SA repair + route elimination + FleetRR; code copied below) until RREND*T;
//  2) km phase: CapKm (= l2::KmSearch whose accepted routes are captured into a column pool) until LAGF of the rest;
//  3) CG phase (LAGD of the rest): restricted master over pool (own simplex), heuristic dual pricing (limited-label
//     elementary labeling) + exact ng pricing (valid Lagrangian bound), then primal: LP rounding + regret repair,
//     price-and-dive, DFS set partitioning over columns with rc <= UB - LP (first with near-integral LP columns fixed);
//  4) CapKm from the LP rounding (RRF share) and from the incumbent, with a dual-informed worst-removal ruin
//     (orders whose detour exceeds their LP dual pi_k), final LS as in s33.
//  0) fleet proof: at FLB2 (0.3) of the SA elimination window, a Lagrangian/Farley fleet lower bound (CG with cost 1 per
//     route, budget FLBT=0.15 of the fleet window, early exit once ceil(LB) >= current fleet); if the current fleet is
//     proven optimal the rest of the fleet phase (SA elimination + FleetRR) is skipped and its time goes to the km phase.
// Options (env): RREND, LAGF, LAGD, CGF, NLAG, FLB2, FLBT, FLB (LB right after the greedy; default 0 = off),
//   GAPSKIP (skip the SP primal when (UB-LP)/UB > 0.08), CGMAXN (skip the CG phase when N > CGMAXN, default 100), PDUAL, RRF, FIXINT, DIVE, DFS, NLIM, MAXCAND, LC, MC, NB, NG, XCAP, SIFT, ALPHA, DEBUG, BOUND.
// ---- below: copy of s33_sasisr.cpp (SA + fleetOnce) ----
// 33. Гибрид (лучший из s30–s33): 1) отжиг s10 (починка + сокращение бригад) до 0.45·T; 2) R&R-сокращение парка
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
#include "lag_util.hpp"
using namespace sau;
static int FLEET_LB = -1, FLEET_TARGET = 1 << 20; static double FLEET_L = -1e18, T_FLEET_DONE = -1; static const Instance* I_P = nullptr; static const l2::Prob* PB_P = nullptr;
static int fleetBound(const Instance& I, const l2::Prob& Pb, const Routes& init, double tEnd, Timer& tm);

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
    bool stopAfterElim = false; double FLB2 = l2::P("FLB2", 0.3), FLBT = l2::P("FLBT", 0.15); bool lbTried = false;
#define I_ (*I_P)
#define Pb_ PB_P
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
            if (FLB2 > 0 && !lbTried && bestScore < W_UNSERVED && tm.sec() > tStart + span * fElim * FLB2) {   // prove the fleet once, mid-way
                lbTried = true; int u = 0; for (int v = 0; v < F.V; v++) u += bestLen[v] > 0;
                FLEET_TARGET = u; FLEET_LB = fleetBound(I_, *Pb_, bestRoutes(), tm.sec() + FLBT * span, tm);
            }
            if (FLEET_LB > 0 && bestScore < W_UNSERVED) { int u = 0; for (int v = 0; v < F.V; v++) u += bestLen[v] > 0; if (u <= FLEET_LB) break; }
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
    if (l2::P("FLB", 0) > 0) FLEET_LB = fleetBound(I, Pb, init, tm.sec() + (tEnd - tBeg) * l2::P("FLB", 0), tm);
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
    if (tm.sec() < tEnd && !(FLEET_LB > 0 && best.nUn() == 0 && best.used() <= FLEET_LB)) { l2::FleetRR FR(I, Pb, rng2, tm, KS); double tS = best.nUn() ? tm.sec() + 0.5 * (tEnd - tm.sec()) : 0; FR.run(best, tS, tEnd, tEnd); }
    return best;
}

// ======================= Lagrangian column generation part (agent lagcg) =======================
#include "lag_util.hpp"
using lg::CGRun;
#include <climits>
#include <memory>
struct LagStats { int rr = 0, dives = 0, rounds = 0, priced = 0, spFound = 0, spBetter = 0, cand = 0; long long nodes = 0; double Lpool = 0, gap = 0; };
static LagStats LST;
static vector<double> DUALS; static double KM_LB = -1e18; static int KM_LB_K = -1; static l2::Sol LAST_RR; static bool HAVE_RR = false; static int RR_LEFT = 0;
static bool DBG = getenv("DEBUG") != nullptr;

// Lagrangian CG phase at fixed fleet K = best.used(): subgradient on the pool, heuristic pricing, rc-fixing, DFS SP.
static bool lagPhase(const Instance& I, const l2::Prob& Pb, const lg::Types& TY, lg::Pool& PL, lg::HPricer& HP, l2::Sol& best,
                     double tEnd, Timer& tm, Rng& rng) {
    lg::Lagr LG(Pb, TY, PL);
    for (int k = 0; k < Pb.N; k++) LG.row[k] = best.rt[k] >= 0;
    LG.K = best.used();
    for (int v = 0; v < Pb.V; v++) {
        int L = best.len[v]; if (!L) continue; double gs = 0; vector<double> g(L);
        for (int i = 0; i < L; i++) { g[i] = max(0.05, best.remGain(Pb, v, i)); gs += g[i]; }
        for (int i = 0; i < L; i++) LG.u[best.r[v][i]] = best.km[v] * g[i] / gs;
    }
    double UB = best.kmTot(), t0 = tm.sec(), span = tEnd - t0;
    double tPrice = t0 + span * l2::P("LAGPR", 0.45), tSG = t0 + span * l2::P("LAGSG", 0.6);
    int SGI = (int)l2::P("SGI", 25), LC = (int)l2::P("LC", 6), MC = (int)l2::P("MC", 20);
    LG.theta = l2::P("THETA", 0.5);
    while (tm.sec() < tPrice) {
        LG.subgrad(SGI, UB);
        vector<lg::HPricer::Out> out;
        for (int t = 0; t < TY.T; t++) HP.price(t, LG.u.data(), LG.lam, LC, MC, out);
        for (auto& o : out) { bool ok = true; for (int k : o.r) if (!LG.row[k]) ok = false; if (ok) PL.add(o.t, o.r.data(), o.r.size(), o.km); }
        LST.rounds++; LST.priced += out.size();
        if (out.empty()) break;
    }
    if (getenv("SGTEST")) { LG.resetBest(); LG.theta = l2::P("THETA", 0.5); Timer q; for (int i = 0; i < 40; i++) { LG.subgrad(50, UB); fprintf(stderr, "  sg %d UB=%.2f L=%.3f theta=%.4f lam=%.2f t=%.3f\n", (i + 1) * 50, UB, LG.Lbest, LG.theta, LG.lam, q.sec()); } }
    LG.resetBest(); LG.theta = max(LG.theta, 0.1);
    while (tm.sec() < tSG) LG.subgrad(10, UB);
    LG.useBest();
    double gap = UB - LG.Lbest; LST.Lpool = LG.Lbest; LST.gap = gap;
    bool f = LG.solveSP(UB, gap + 1e-6, (long long)l2::P("NLIM", 2e6), tm, tEnd, (int)l2::P("MAXCAND", 3000));
    LST.cand += LG.cand.size(); LST.nodes += LG.nodes;
    if (DBG) fprintf(stderr, "  lag: UB=%.2f Lpool=%.2f gap=%.2f cols=%zu cand=%zu nodes=%lld found=%d best=%.2f rounds=%d\n", UB, LG.Lbest, gap, PL.cols.size(), LG.cand.size(), LG.nodes, (int)f, f ? LG.bestCost : 0, LST.rounds);
    if (!f) return false;
    LST.spFound++;
    l2::Sol c; LG.toSol(LG.bestSel, c);
    if (c.better(best, Pb)) { best = c; LST.spBetter++; return true; }
    return false;
}


// price-and-dive: repeatedly fix LP columns (x>=0.999, else the largest x), re-solve the residual master with a few
// rounds of heuristic pricing; prune when fixed km + residual LP >= UB.
static int DIVE_STEPS = 0;
static bool diveFrom(CGRun& root, double tEnd, Timer& tm, vector<int>& sol, double& solKm) {
    const int N = root.N, T = root.T; lg::Pool& PL = root.PL;
    vector<char> rowMask(N); for (int k = 0; k < N; k++) rowMask[k] = root.rowIdx[k] >= 0;
    vector<int> tc = root.tcap; int Kc = root.K; double fixedKm = 0; sol.clear();
    unique_ptr<CGRun> holder; CGRun* cur = &root; int DR = (int)l2::P("DIVER", 6);
    double fixTh = l2::P("FIXTH", 0.999);
    while (true) {
        for (int r = 0; r < cur->m; r++) if (cur->lp.cost[r] > 0 && cur->lp.xOf(r) > 1e-3) { if (DBG) fprintf(stderr, "    dive fail: artificial row %d x=%.3f\n", r, cur->lp.xOf(r)); return false; }   // artificial in use
        vector<pair<double, int>> xs;
        for (int j = cur->m; j < cur->lp.ncols(); j++) { double x = cur->lp.xOf(j); if (x > 1e-6) xs.push_back({x, cur->poolOf[j]}); }
        if (xs.empty()) return false;
        sort(xs.begin(), xs.end(), [](auto& a, auto& b) { return a.first > b.first; });
        lg::Bits cov; cov.clear(); int nf = 0;
        for (auto& [x, c] : xs) {
            if (nf > 0 && x < fixTh) break;
            const lg::Col& col = PL.cols[c]; if (col.b.inter(cov) || tc[col.t] <= 0 || Kc <= 0) continue;
            cov.orWith(col.b); const int* q = PL.seq(col); for (int i = 0; i < col.len; i++) rowMask[q[i]] = 0;
            tc[col.t]--; Kc--; fixedKm += col.km; sol.push_back(c); nf++;
        }
        DIVE_STEPS++;
        bool done = true; for (int k = 0; k < N; k++) if (rowMask[k]) done = false;
        if (done) { solKm = fixedKm; return fixedKm < root.UB - 1e-6; }
        if (Kc <= 0 || tm.sec() > tEnd || fixedKm >= root.UB - 1e-6) { if (DBG) fprintf(stderr, "    dive stop: Kc=%d fixedKm=%.2f\n", Kc, fixedKm); return false; }
        unique_ptr<CGRun> nx(new CGRun(root.Pb, root.TY, root.PL, root.HP));
        nx->setup(rowMask, tc, Kc, root.UB - fixedKm); nx->maxRounds = DR;
        for (int j = cur->m; j < cur->lp.ncols(); j++) { int c = cur->poolOf[j]; if (c >= 0 && nx->compat(PL.cols[c])) nx->toLP(c); }
        nx->cgLoop(tEnd, tm);
        if (DBG) fprintf(stderr, "    dive step: fixed=%zu km=%.2f Kc=%d resLP=%.2f rounds=%d cols=%d\n", sol.size(), fixedKm, Kc, nx->lp.obj(), nx->rounds, nx->lp.ncols());
        if (l2::P("DPRUNE", 0) > 0 && fixedKm + nx->lp.obj() >= root.UB - 1e-6) return false;
        holder = move(nx); cur = holder.get();
    }
}

// LP rounding + repair: columns by x (noisy) desc, greedily disjoint, the rest by regret insertion (fleet cap K), LS polish.
static bool roundRepair(const Instance& I, CGRun& G, Rng& rng, double noise, l2::Sol& out, Timer& tm, double tEnd) {
    const l2::Prob& Pb = G.Pb; lg::Pool& PL = G.PL; const lg::Types& TY = G.TY;
    vector<pair<double, int>> xs;
    for (int j = G.m; j < G.lp.ncols(); j++) { double x = G.lp.xOf(j); if (x > 1e-6) xs.push_back({-(x * (1 + noise * (rng.uni() - 0.5))), G.poolOf[j]}); }
    sort(xs.begin(), xs.end());
    lg::Bits cov; cov.clear(); vector<int> tc(G.T, 0), sel; int used = 0;
    for (auto& [x, c] : xs) {
        const lg::Col& col = PL.cols[c]; if (used >= G.K || col.b.inter(cov) || tc[col.t] >= G.tcap[col.t]) continue;
        cov.orWith(col.b); tc[col.t]++; used++; sel.push_back(c);
    }
    G.toSol(sel, out);
    vector<int> pool; for (int k = 0; k < Pb.N; k++) if (G.rowIdx[k] >= 0 && out.rt[k] < 0) pool.push_back(k);
    for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
    l2::Regret RG; RG.run(Pb, out, pool, rng, 2, G.K, W_VEHICLE, nullptr);
    RR_LEFT = pool.size();
    if (!pool.empty()) return false;
    lu::LocalSearch LS(I); lu::Sol x = l2::toLu(I, out); LS.run(x, rng, G.K, &tm, tEnd); out.fromRoutes(Pb, x.r);
    return true;
}
static bool cgPhase(const Instance& I, const l2::Prob& Pb, const lg::Types& TY, lg::Pool& PL, lg::HPricer& HP, l2::Sol& best, double tEnd, Timer& tm) {
    CGRun G(Pb, TY, PL, HP);
    double t0 = tm.sec();
    lg::XPricer XP(Pb, TY, (int)l2::P("NG", 8)); if (l2::P("XPR", 1) > 0) G.X = &XP;
    G.run(best, t0 + (tEnd - t0) * l2::P("CGF", 0.6), tm);
    double tLP = tm.sec();
    DUALS.assign(Pb.N, 0); for (int k = 0; k < Pb.N; k++) if (G.rowIdx[k] >= 0) DUALS[k] = G.lp.pi[G.rowIdx[k]];
    if (best.nUn() == 0 && G.bestL > KM_LB) { KM_LB = G.bestL; KM_LB_K = G.K; }
    if (getenv("BOUND") && best.nUn() == 0) {
        Timer bt; lg::XPricer X(Pb, TY, (int)l2::P("NG", 8)); bool ex; double L = G.bound(X, (size_t)l2::P("XCAP", 3e5), ex);
        fprintf(stderr, "BOUND K=%d UB=%.3f LP=%.3f lag=%.3f bestL=%.3f exact=%d conv=%d tb=%.3f labels=%lld\n", G.K, G.UB, G.LPval, L, G.bestL, (int)ex, (int)G.converged, bt.sec(), X.labels);
    }
    if ((G.UB - G.LPval) > l2::P("GAPSKIP", 0.08) * G.UB) {   // LP far from the incumbent: SP heuristics hopeless, give the time back
        if (DBG) fprintf(stderr, "  cg: gap %.1f%% too large, skip primal\n", 100 * (G.UB - G.LPval) / G.UB);
        LST.rounds += G.rounds; return false;
    }
    bool f = false; vector<int> selB; double costB = G.UB;
    int NRR = (int)l2::P("NRR", 3); l2::Sol rrBest; bool haveRR = false; Rng rr(12345 + (uint64_t)(G.UB * 1000));
    for (int q = 0; q < NRR && tm.sec() < tEnd; q++) {
        l2::Sol c; bool okr = roundRepair(I, G, rr, q == 0 ? 0 : 0.5, c, tm, tEnd);
        if (DBG) fprintf(stderr, "  roundRepair %d: ok=%d left=%d used=%d km=%.3f\n", q, (int)okr, RR_LEFT, c.used(), c.kmTot());
        if (!okr) { if (!HAVE_RR || c.nUn() < LAST_RR.nUn() || (c.nUn() == LAST_RR.nUn() && c.kmTot() < LAST_RR.kmTot())) { LAST_RR = c; HAVE_RR = true; } continue; }
        if (DBG) fprintf(stderr, "  roundRepair %d: used=%d km=%.3f (UB %.3f)\n", q, c.used(), c.kmTot(), G.UB);
        if (!haveRR || c.better(rrBest, Pb)) { rrBest = c; haveRR = true; }
    }
    if (haveRR) { LAST_RR = rrBest; HAVE_RR = true; if (rrBest.better(best, Pb)) { best = rrBest; LST.spBetter++; LST.rr++; } }
    if (l2::P("DIVE", 1) > 0 && tm.sec() < tEnd) {
        vector<int> ds; double dkm = 0; int st0 = DIVE_STEPS;
        bool ok = diveFrom(G, tEnd, tm, ds, dkm);
        if (DBG) fprintf(stderr, "  dive: ok=%d km=%.3f steps=%d t=%.3f\n", (int)ok, dkm, DIVE_STEPS - st0, tm.sec() - t0);
        if (ok && dkm < costB) { f = true; selB = ds; costB = dkm; LST.dives++; }
    }
    if (l2::P("DFS", 1) > 0 && tm.sec() < tEnd) {
        G.UB = costB;
        bool g = G.sp(tm, tEnd, (long long)l2::P("NLIM", 2e6), (int)l2::P("MAXCAND", 5000));
        if (g && G.bestCost < costB) { f = true; selB = G.bestSel; costB = G.bestCost; }
    }
    LST.rounds += G.rounds; LST.priced += G.priced; LST.cand += G.nCand; LST.nodes += G.nodes;
    if (DBG) fprintf(stderr, "  cg: UB=%.3f LP=%.3f gap=%.2f%% rounds=%d (lp %.3fs) x=%d/%d conv=%d L=%.3f sifted=%d priced=%d lpcols=%d piv=%lld tLP=%.3f cand=%d nodes=%lld found=%d -> %.3f t=%.3f\n",
                     best.kmTot(), G.LPval, 100 * (best.kmTot() - G.LPval) / best.kmTot(), G.rounds, G.tLPs, G.xrounds, G.xpriced, (int)G.converged, G.bestL, G.sifted, G.priced, G.lp.ncols(), G.lp.pivots, tLP - t0, G.nCand, G.nodes, (int)f, costB, tm.sec() - t0);
    G.bestSel = selB;
    if (!f) return false;
    LST.spFound++;
    l2::Sol c; G.toSol(G.bestSel, c);
    if (c.better(best, Pb)) { best = c; LST.spBetter++; return true; }
    return false;
}


// Lagrangian fleet lower bound: CG with cost 1 per route over all orders (valid for serving everything).
static int fleetBound(const Instance& I, const l2::Prob& Pb, const Routes& init, double tEnd, Timer& tm) {
    lg::Types TY(I); lg::Pool PL(Pb, TY); lg::HPricer HP(Pb, TY, (int)l2::P("NB", 25)); lg::XPricer XP(Pb, TY, (int)l2::P("NG", 8));
    l2::Sol s; s.fromRoutes(Pb, init);
    lg::CGRun G(Pb, TY, PL, HP); G.beta = 0; G.cfix = 1; G.X = &XP; G.XEVERY = (int)l2::P("FXEVERY", 8);
    vector<char> rm(Pb.N, 1); G.setup(rm, TY.cnt, Pb.V, Pb.V);
    vector<int> bs(G.m); for (int i = 0; i < G.m; i++) bs[i] = i;
    for (int v = 0; v < Pb.V; v++) if (s.len[v]) { int c = PL.add(TY.tOf[v], s.r[v], s.len[v], s.km[v]); bs[G.rowIdx[s.r[v][0]]] = G.toLP(c); }
    G.stopL = FLEET_TARGET - 1 + 1e-6; G.lp.setBasis(bs); G.cgLoop(tEnd, tm);
    FLEET_L = G.bestL;
    int lb = G.bestL > -1e17 ? (int)ceil(G.bestL - 1e-6) : -1;
    if (getenv("DEBUG")) fprintf(stderr, "  fleetLB: LP=%.3f L=%.3f -> %d conv=%d rounds=%d x=%d t=%.3f\n", G.lp.obj(), G.bestL, lb, (int)G.converged, G.rounds, G.xrounds, tm.sec());
    return lb;
}

static l2::Sol solveOnce(const Instance& I, const l2::Prob& Pb, double tl, uint64_t seed, Timer& tm) {
    double tBeg = tm.sec();
    Rng rng2(seed * 31 + 7);
    l2::KmSearch KS(I, Pb, rng2, tm);
    lg::Types TY(I); lg::Pool PL(Pb, TY); lg::HPricer HP(Pb, TY, (int)l2::P("NB", 25));
    double rrEnd = tBeg + (tl - tBeg) * l2::P("RREND", 0.6);
    l2::Sol best = fleetOnce(I, Pb, rrEnd, seed * 7919, tm, KS, rng2);
    T_FLEET_DONE = tm.sec();
    if (DBG) fprintf(stderr, "  fleet done t=%.3f used=%d un=%d km=%.2f LB=%d\n", tm.sec(), best.used(), best.nUn(), best.kmTot(), FLEET_LB);
    PL.addSol(best);
    lg::CapKm CK(I, Pb, rng2, tm); CK.PL = &PL;
    int NLAG = (int)l2::P("NLAG", 1); double LAGF = l2::P("LAGF", 0.6), LAGD = l2::P("LAGD", 0.3);
    // km phase split into NLAG segments; each segment: CapKm then Lagrangian SP
    for (int r = 0; r < NLAG; r++) {
        double segEnd = tm.sec() + (tl - tm.sec()) / (NLAG - r);
        double lagEnd = r == NLAG - 1 ? tm.sec() + (tl - tm.sec()) * (LAGF + LAGD) : segEnd;
        double kmEnd = r == NLAG - 1 ? tm.sec() + (tl - tm.sec()) * LAGF : segEnd - (segEnd - tm.sec()) * LAGD / (LAGF + LAGD) * 1.0;
        CK.run(best, kmEnd);
        if (Pb.N > (int)l2::P("CGMAXN", 100)) { CK.run(best, lagEnd); continue; }   // large instances: CG phase does not pay off
        if (best.nUn() == 0 || l2::P("LAGUN", 0) > 0) { if (l2::P("SUBG", 0) > 0) lagPhase(I, Pb, TY, PL, HP, best, lagEnd, tm, rng2); else cgPhase(I, Pb, TY, PL, HP, best, lagEnd, tm); }
        if (!DUALS.empty() && l2::P("PDUAL", 0.1) > 0) CK.duals = &DUALS;
        double RRF = l2::P("RRF", 0.5);
        if (RRF > 0 && HAVE_RR && LAST_RR.kmTot() < best.kmTot() * l2::P("RRTH", 1.0)) {   // CapKm restarted from the LP rounding
            double k0 = best.kmTot();
            CK.run(best, tm.sec() + (tl - tm.sec()) * RRF, -1, -1, -1, -1, &LAST_RR);
            if (DBG) fprintf(stderr, "  CK from rounding (km %.2f, un %d): best %.2f -> %.2f\n", LAST_RR.kmTot(), LAST_RR.nUn(), k0, best.kmTot());
            HAVE_RR = false;
        }
    }
    CK.run(best, tl);
    if (DBG) fprintf(stderr, "cols=%zu adds=%lld used=%d km=%.2f spFound=%d spBetter=%d\n", PL.cols.size(), PL.adds, best.used(), best.kmTot(), LST.spFound, LST.spBetter);
    return best;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; tl *= 0.97;
    l2::Prob Pb(I); I_P = &I; PB_P = &Pb;
    l2::Sol best = solveOnce(I, Pb, tl, seed * 1000, tm);
    lu::LocalSearch LS(I); lu::Sol x = l2::toLu(I, best); Rng rr(seed); LS.run(x, rr, best.used(), &tm, tl / 0.97 * 0.99);
    l2::Sol y; y.fromRoutes(Pb, x.r); if (y.better(best, Pb)) best = y;
    // live lower bounds (stderr): fleet LB (if computed) and km LB for "<= K vehicles, all orders" (valid Lagrangian bound)
    fprintf(stderr, "LAGLB fleet=%d km=%.3f K=%d\n", FLEET_LB, KM_LB > -1e17 ? KM_LB : -1.0, KM_LB_K);
    return l2::finalizeR(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "80_lagcg"); }
