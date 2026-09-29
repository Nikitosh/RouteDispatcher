// 70. ILS-SP матэвристика: фаза парка как в s33 (отжиг s10 + FleetRR), затем фаза км — SISR-отжиг, который
// складывает ВСЕ затронутые допустимые маршруты в пул (sp_util.hpp: ключ = группа бригад (старт, транспорт) ×
// множество заявок, хранится лучшая последовательность), и NSP раз за фазу — SP по окрестностям (sp::Recomb:
// каждый связный набор из k≤RCK маршрутов текущего рекорда заменяется лучшим точным разбиением его заявок на
// столбцы пула). Поиск продолжается от решения SP. Журнал: logs/ilssp.md.
// Опции (env): NSP (4) — число SP в фазе км, SPTL (0.03) — лимит одного SP (доля T), RCK (3), RCNODES (2e5) —
// узлы DFS на подзадачу, PMAX — размер пула, ADD (0 все затронутые маршруты, 1 только из принятых ходов, 3 не
// класть), FPOOL — маршруты фазы парка тоже в пул, INJ (1: текущее решение := решение SP), RREND — доля фазы парка.
// Диагностика/эксперименты (не дали выигрыша): MULTI — несколько независимых прогонов км на общий пул, ELSP —
// SP по элите, GSP — глобальный SP (ЛП генерацией столбцов + DFS по rc), SPMODE=0 — лагранжев SP по всему пулу,
// FINALRC/FINALSP/DUMP — долгий SP по итоговому пулу и выгрузка пула для HiGHS/CP-SAT.
#include "sa_util.hpp"
#include "lns2_util.hpp"
#include "sp_util.hpp"
#include <memory>
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

namespace l2 {
struct FleetRRP {   // копия l2::FleetRR + маршруты в пул
    sp::Pool* PL = nullptr;
    const Instance& I; const Prob& Pb; Rng& rng; Timer& tm; KmSearch& KS; Ruins RU; Regret RG; Undo U; vector<int> pool; vector<double> absence;
    enum { D_SISR, D_RANDOM, D_WORST, D_SHAW, D_ROUTE, D_SLOT, ND };
    enum { C_GREEDY, C_REG2, C_REG3, NC };
    Adaptive AD{ND}, AC{NC};
    double blink = P("BLINK", 0.01), ernd = P("ERND", 0.5); int fleetOps = (int)P("FOPS", 1); long ERST = (long)P("ERST", 0);
    long iters = 0; int nAttempt = 0; bool stopOnSuccess = false; vector<int> prevAbs; int freshAbs = (int)P("FRESH", 1);
    FleetRRP(const Instance& in, const Prob& p, Rng& r, Timer& t, KmSearch& ks) : I(in), Pb(p), rng(r), tm(t), KS(ks), RU(p), absence(in.N, 0) {
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
            if (PL) for (int v = 0; v < Pb.V; v++) if ((U.touched >> v & 1) && cur.len[v]) PL->add(v, cur.r[v], cur.len[v], cur.km[v]);
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
}  // namespace l2

// Фаза парка: отжиг s10 (починка + удаление маршрутов) до доли SAF отрезка, затем R&R с Σabsence до tEnd.
static l2::Sol fleetOnce(const Instance& I, const l2::Prob& Pb, double tEnd, uint64_t seed, Timer& tm, l2::KmSearch& KS, Rng& rng2, sp::Pool* FPL = nullptr) {
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
    if (tm.sec() < tEnd) { l2::FleetRRP FR(I, Pb, rng2, tm, KS); FR.PL = FPL; double tS = best.nUn() ? tm.sec() + 0.5 * (tEnd - tm.sec()) : 0; FR.run(best, tS, tEnd, tEnd); }
    return best;
}

// ---------- фаза км с пулом маршрутов ----------
struct PoolKm {
    const Instance& I; const l2::Prob& Pb; Rng& rng; Timer& tm; lu::LocalSearch LS; l2::Ruins RU; l2::Regret RG; l2::Undo U;
    vector<int> pool; sp::Pool& PL; vector<int>* elite = nullptr;
    l2::Sol cur, best; double curCost = 0, bestCost = 0, T0 = 1, Tf = 1, t1 = 0, span = 1; int cap = 0; long iters = 0;
    double cbar = l2::P("CBAR2", 10), T0f = l2::P("T0", 5.0), Tff = l2::P("TF", 0.05), blink = l2::P("BLINK", 0.01);
    double pRoute = l2::P("PROUTE", 0.05), pPair = l2::P("PPAIR", 0.05); int addMode = (int)l2::P("ADD", 0);
    PoolKm(const Instance& in, const l2::Prob& p, Rng& r, Timer& t, sp::Pool& pl) : I(in), Pb(p), rng(r), tm(t), LS(in), RU(p), PL(pl) { pool.reserve(in.N); RU.cbar = cbar; }
    void polish(l2::Sol& s, double tEnd) {
        lu::Sol x = l2::toLu(I, s); LS.run(x, rng, cap, &tm, tEnd);
        l2::Sol y; y.fromRoutes(Pb, x.r);
        if (y.better(s, Pb) || (y.penSum(Pb) == s.penSum(Pb) && y.used() == s.used() && y.kmTot() <= s.kmTot())) s = y;
    }
    void start(const l2::Sol& init, double tS, double tE) {
        best = init; cur = init; cap = best.used(); if (best.nUn() > 0) cap = Pb.V;
        int served = Pb.N - best.nUn(); double edge = max(1.0, best.kmTot()) / max(1, served);
        T0 = T0f * edge; Tf = Tff * edge; t1 = tS; span = max(1e-9, tE - tS);
        curCost = cur.cost(Pb); bestCost = best.cost(Pb); PL.addSol(best);
    }
    int injMode = (int)l2::P("INJ", 1);
    void inject(const l2::Sol& s) { best = s; bestCost = s.cost(Pb); if (injMode) { cur = s; curCost = bestCost; } PL.addSol(s); }
    void run(double tEnd) {
        const int N = Pb.N; const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30)); double T = T0;
        for (long it = 0;; it++) {
            if ((it & 15) == 0) { double el = tm.sec(); if (el > tEnd) break; T = T0 * pow(Tf / T0, min(1.0, (el - t1) / span)); }
            iters++;
            U.begin(cur); pool.clear(); l2::collectAbsent(cur, pool); RU.clearMarks(); bool big = false;
            double uu = rng.uni();
            if (uu < pRoute) RU.route(cur, rng, qmin + rng.randint(qmax - qmin + 1));
            else if (uu < pRoute + pPair) { RU.routePair(cur, rng); big = true; }
            else RU.sisr(cur, rng);
            l2::removeSet(Pb, cur, RU.rem.data(), RU.rem.size(), pool, &U);
            if (big && rng.uni() < 0.5) { for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); RG.run(Pb, cur, pool, rng, 2, cap, W_VEHICLE, &U); }
            else { l2::sisrSort(Pb, pool, rng); l2::greedyInsert(Pb, cur, pool, rng, blink, cap, W_VEHICLE, &U); }
            uint32_t tch = U.touched;
            if (addMode == 0) for (int v = 0; v < Pb.V; v++) if ((tch >> v & 1) && cur.len[v]) PL.add(v, cur.r[v], cur.len[v], cur.km[v]);
            double c = cur.cost(Pb);
            if (c < curCost - T * log(rng.uni() + 1e-300)) {
                if (addMode == 1) for (int v = 0; v < Pb.V; v++) if ((tch >> v & 1) && cur.len[v]) PL.add(v, cur.r[v], cur.len[v], cur.km[v]);
                curCost = c;
                if (c < bestCost - 1e-9) {
                    best = cur; bestCost = c; polish(best, tEnd); PL.addSol(best);
                    if (elite) for (int v = 0; v < Pb.V; v++) if (best.len[v]) { int ci = PL.add(v, best.r[v], best.len[v], best.km[v]); if (ci >= 0) elite->push_back(ci); }
                    double c2 = best.cost(Pb); if (c2 < bestCost - 1e-9) { bestCost = c2; cur = best; curCost = c2; }
                }
            } else U.restore(Pb, cur);
        }
    }
};

static bool DBG = getenv("DEBUG") != nullptr;
static long RCs, RCn, RCc, RCi, RCh, RCk[8], RCr; static double RCt;
static long spCalls = 0, spImp = 0; static double spGain = 0, spMs = 0;

static l2::Sol solveOnce(const Instance& I, const l2::Prob& Pb, double tl, uint64_t seed, Timer& tm, sp::Pool& PL) {
    double tBeg = tm.sec();
    Rng rng2(seed * 31 + 7);
    l2::KmSearch KS(I, Pb, rng2, tm);
    double rrEnd = tBeg + (tl - tBeg) * l2::P("RREND", 0.6);
    l2::Sol best = fleetOnce(I, Pb, rrEnd, seed * 7919, tm, KS, rng2, l2::P("FPOOL", 0) ? &PL : nullptr);
    if (DBG) fprintf(stderr, "fleet: used=%d un=%d km=%.1f t=%.3f\n", best.used(), best.nUn(), best.kmTot(), tm.sec());
    int NSP = (int)l2::P("NSP", 4); double spTL = l2::P("SPTL", 0.03) * tl;
    sp::Solver SV(PL); sp::Recomb RC(PL); int MODE = (int)l2::P("SPMODE", 1);
    auto doSP = [&](PoolKm& K, double lim, int tag) {
        Routes R; sp::SPStats st; bool ok = false;
        if (MODE == 1) {
            Timer t0; l2::Sol c = K.best; int ni = RC.run(c, tm, tm.sec() + lim); st.ms = t0.sec() * 1000; st.nodes = RC.totNodes; st.cols = RC.subs;
            if (ni) { R = c.routes(); ok = true; st.imp = true; st.res = c.kmTot(); }
        } else ok = SV.solve(K.best, K.best.used(), lim, tm, R, st);
        spCalls++; spMs += st.ms;
        if (ok) {
            l2::Sol c; c.fromRoutes(Pb, R);
            Score sc = evaluate(I, R);
            if (sc.feasible && c.better(K.best, Pb)) { spImp++; spGain += K.best.kmTot() - c.kmTot(); K.polish(c, tl); K.inject(c); }
        }
        if (DBG) fprintf(stderr, "SP%d t=%.3f pool=%d(adds %ld) it=%ld subs=%d nodes=%ld imp=%d ms=%.1f best=%.2f\n",
                         tag, tm.sec(), PL.size(), PL.adds, K.iters, st.cols, st.nodes, st.imp, st.ms, K.best.kmTot());
    };
    int MULTI = (int)l2::P("MULTI", 1); double tS0 = tm.sec();
    l2::Sol fleetBest = best; bool have = false;
    unique_ptr<PoolKm> Kp; vector<int> elite;
    for (int m = 0; m < MULTI; m++) {
        double tS = tm.sec(), tE = tS0 + (tl - tS0) * (m + 1) / MULTI;
        if (m == MULTI - 1) tE = tl - (MULTI > 1 ? spTL : 0);
        Rng* rr = new Rng(seed * 101 + m * 7 + 3);
        Kp.reset(new PoolKm(I, Pb, m == 0 ? rng2 : *rr, tm, PL)); PoolKm& K = *Kp;
        K.start(l2::P("MSTART", 0) && have ? best : fleetBest, tS, tE); K.elite = &elite;
        for (int s = 0; s < NSP; s++) {
            double segEnd = tS + (tE - tS) * (s + 1) / NSP - spTL;
            K.run(segEnd);
            doSP(K, spTL, m * 100 + s);
        }
        K.run(tE);
        if (DBG) fprintf(stderr, "run %d best=%.2f\n", m, K.best.kmTot());
        if (!have || K.best.better(best, Pb)) { best = K.best; have = true; }
    }
    if (MULTI > 1) {   // итоговый SP по общему пулу всех прогонов
        PoolKm& K = *Kp; K.inject(best);
        if (l2::P("ELSP", 1)) {
            Timer t0; l2::Sol c = K.best; bool ok = RC.solveWith(c, elite, tm, tm.sec() + spTL);
            if (DBG) fprintf(stderr, "ELSP ok=%d elite=%d nodes=%ld ms=%.1f before=%.2f after=%.2f\n", ok, (int)elite.size(), RC.eliteNodes, t0.sec() * 1000, K.best.kmTot(), c.kmTot());
            if (ok && c.better(K.best, Pb)) { K.polish(c, tl); K.inject(c); }
        }
        if (l2::P("GSP", 0)) {
            sp::GSP GS(PL); Routes R; Timer t0;
            bool ok = GS.solve(K.best, tm, tm.sec() + l2::P("GSPTL", 0.1) * tl, R);
            if (DBG) fprintf(stderr, "GSP ok=%d lb=%.2f ub=%.2f cg=%d piv=%ld lpms=%.1f cand=%d divems=%.1f res=%.2f\n", ok, GS.lb, K.best.kmTot(), GS.cgRounds, GS.piv, GS.lpMs, GS.nCand, GS.diveMs, ok ? evaluate(I, R).km : 0.0);
            if (ok) { l2::Sol c; c.fromRoutes(Pb, R); if (evaluate(I, R).feasible && c.better(K.best, Pb)) { K.polish(c, tl); K.inject(c); } }
        }
        doSP(K, spTL, 999); best = K.best; K.run(tl); if (K.best.better(best, Pb)) best = K.best;
    }
    PoolKm& K = *Kp; K.best = best;
    RCs = RC.subs; RCn = RC.totNodes; RCc = RC.totCand; RCt = RC.tIdx; RCi = RC.imps; RCh = RC.hitLim; RCr = RC.redV; for (int q = 0; q < 8; q++) RCk[q] = RC.impK[q];
    if (l2::P("FINALRC", 0) > 0) {
        Timer t2; l2::Sol c = K.best; RC.maxK = (int)l2::P("FRCK", 4); int ni = RC.run(c, t2, l2::P("FINALRC", 0));
        fprintf(stderr, "FINALRC imps=%d before=%.2f after=%.2f gain=%.3f%% ms=%.1f\n", ni, K.best.kmTot(), c.kmTot(), 100 * (1 - c.kmTot() / K.best.kmTot()), t2.sec() * 1000);
    }
    if (getenv("DUMP")) {
        FILE* f = fopen(getenv("DUMP"), "w"); fprintf(f, "%d %d %d %.6f\n", Pb.N, Pb.V, K.best.used(), K.best.kmTot());
        for (int v = 0; v < Pb.V; v++) fprintf(f, "%d ", PL.grpOf[v]); fprintf(f, "\n");
        for (int ci = 0; ci < PL.size(); ci++) { auto& c = PL.cols[ci]; fprintf(f, "%u %.6f %d", c.vmask, c.km, c.len); for (int q = 0; q < c.len; q++) fprintf(f, " %d", PL.seq(ci)[q]); fprintf(f, "\n"); }
        fprintf(f, "INC"); for (int v = 0; v < Pb.V; v++) if (K.best.len[v]) fprintf(f, " %d", PL.add(v, K.best.r[v], K.best.len[v], K.best.km[v])); fprintf(f, "\n");
        fclose(f);
    }
    if (l2::P("FINALSP", 0) > 0) {   // диагностика: долгий SP по итоговому пулу
        Routes R; sp::SPStats st; Timer t2; SV.maxSg = (int)l2::P("FSGIT", 5000); SV.maxNodes = 1L << 40;
        bool ok = SV.solve(K.best, K.best.used(), l2::P("FINALSP", 0), t2, R, st);
        fprintf(stderr, "FINAL pool=%d cols=%d surv=%d sg=%d nodes=%ld lb=%.2f ub=%.2f res=%.2f opt=%d imp=%d ms=%.1f\n", PL.size(), st.cols, st.surv, st.sgIt, st.nodes, st.lb, st.ub, st.res, st.opt, ok, st.ms);
    }
    return K.best;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; tl *= 0.97;
    l2::Prob Pb(I); sp::Pool PL(Pb, (int)l2::P("PMAX", 100000));
    l2::Sol best = solveOnce(I, Pb, tl, seed * 1000, tm, PL);   // как в s33 (RST=1, r=0)
    lu::LocalSearch LS(I); lu::Sol x = l2::toLu(I, best); Rng rr(seed); LS.run(x, rr, best.used(), &tm, tl / 0.97 * 0.99);
    l2::Sol y; y.fromRoutes(Pb, x.r); if (y.better(best, Pb)) best = y;
    if (DBG) fprintf(stderr, "RC subs=%ld nodes=%ld cand=%ld tIdx=%.1fms imps=%ld hitLim=%ld impK=%ld,%ld,%ld,%ld redV=%ld\n", RCs, RCn, RCc, RCt * 1000, RCi, RCh, RCk[1], RCk[2], RCk[3], RCk[4], RCr);
    if (DBG) fprintf(stderr, "SPsum calls=%ld imp=%ld gain=%.2f ms=%.1f\n", spCalls, spImp, spGain, spMs);
    return l2::finalizeR(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "70_ilssp"); }
