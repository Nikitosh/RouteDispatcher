// 32. HGS-2 (меметический поиск по км): фазы парка как в s33 (отжиг s10 до 0.45·T, затем R&R с Σabsence),
// затем популяция при фиксированном парке: родители — турнир по смещённой приспособленности (стоимость + вклад в
// разнообразие, broken-pairs с учётом бригады), скрещивание SREX для разнородного парка, починка regret-2,
// обучение — короткий SISR-отжиг низкой температуры (KmSearch с лимитом итераций) + ЛП; отбор без клонов.
// База (s33): фазы 1–2 отжига s10 (починка, сокращение бригад), затем фаза км — SISR-отжиг с полировкой (lns2_util).
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

// ---------- HGS по км при фиксированном парке ----------
struct Ind { l2::Sol s; double cost = 0; vector<int> succ, pred; };
static void finishInd(const l2::Prob& Pb, Ind& x) {
    x.cost = x.s.cost(Pb); x.succ.assign(Pb.N, -1); x.pred.assign(Pb.N, -1);
    for (int v = 0; v < Pb.V; v++) for (int i = 0; i < x.s.len[v]; i++) {
        int k = x.s.r[v][i]; x.pred[k] = i ? x.s.r[v][i - 1] : -2 - v; x.succ[k] = i + 1 < x.s.len[v] ? x.s.r[v][i + 1] : -1;
    }
}
static double bpd(const Ind& a, const Ind& b) {
    int N = a.succ.size(), c = 0;
    for (int i = 0; i < N; i++) { if (a.succ[i] != b.succ[i]) c++; if (a.pred[i] != b.pred[i]) c++; }
    return c / (2.0 * N);
}
static l2::Sol hgsKm(const Instance& I, const l2::Prob& Pb, const l2::Sol& start, double tEnd, Rng& rng, Timer& tm, l2::KmSearch& KS) {
    using namespace l2;
    const int N = Pb.N, V = Pb.V; int cap = start.used();
    const double PEL = P("PEL", 0.15); const long ELIT = (long)P("ELIT", 400);
    FleetRR FR(I, Pb, rng, tm, KS); FR.stopOnSuccess = true;
    const int MU = (int)P("MU", 8), LAMBDA = (int)P("LAMBDA", 8), NCLOSE = 3, NELITE = 3;
    const long EIT = (long)P("EIT", 2000); const double T0e = P("T0E", 1.0), Tfe = P("TFE", 0.02);
    Ruins RU(Pb); Regret RG; vector<int> pool; pool.reserve(N);
    vector<Ind> pop; vector<vector<double>> D;
    l2::Sol best = start;
    auto educate = [&](l2::Sol& x) { KS.run(x, tEnd, EIT, T0e, Tfe, cap); KS.polish(x, cap, tEnd); };
    auto add = [&](l2::Sol&& x) {
        if (x.better(best, Pb)) best = x;
        Ind in; in.s = x; finishInd(Pb, in);
        int n = pop.size(); vector<double> row(n + 1, 0);
        for (int i = 0; i < n; i++) { row[i] = bpd(in, pop[i]); D[i].push_back(row[i]); }
        D.push_back(row); pop.push_back(std::move(in));
    };
    auto biased = [&]() {
        int n = pop.size(); vector<double> bf(n, 0); if (n <= 1) return bf;
        vector<int> idx(n); iota(idx.begin(), idx.end(), 0);
        sort(idx.begin(), idx.end(), [&](int a, int b) { return pop[a].cost < pop[b].cost; });
        vector<double> fr(n), dv(n), dr(n);
        for (int r = 0; r < n; r++) fr[idx[r]] = (double)r / (n - 1);
        for (int i = 0; i < n; i++) {
            vector<double> d = D[i]; d.erase(d.begin() + i); int m = min(NCLOSE, (int)d.size());
            partial_sort(d.begin(), d.begin() + m, d.end()); double s = 0; for (int j = 0; j < m; j++) s += d[j]; dv[i] = s / max(1, m);
        }
        sort(idx.begin(), idx.end(), [&](int a, int b) { return dv[a] > dv[b]; });
        for (int r = 0; r < n; r++) dr[idx[r]] = (double)r / (n - 1);
        for (int i = 0; i < n; i++) bf[i] = fr[i] + (1.0 - (double)NELITE / n) * dr[i];
        return bf;
    };
    auto removeInd = [&](int i) { pop.erase(pop.begin() + i); D.erase(D.begin() + i); for (auto& row : D) row.erase(row.begin() + i); };
    auto survivors = [&]() {
        while ((int)pop.size() > MU) {
            int n = pop.size(), worst = -1;
            for (int i = 0; i < n && worst < 0; i++) for (int j = 0; j < n; j++) if (j != i && D[i][j] < 1e-9 && pop[i].cost >= pop[j].cost) { worst = i; break; }
            if (worst < 0) { auto bf = biased(); worst = max_element(bf.begin(), bf.end()) - bf.begin(); }
            removeInd(worst);
        }
    };
    auto repair = [&](l2::Sol& ch) {
        pool.clear(); collectAbsent(ch, pool);
        for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
        RG.run(Pb, ch, pool, rng, 2, cap, W_VEHICLE, nullptr);
        if (!pool.empty()) greedyInsert(Pb, ch, pool, rng, 0, cap, W_VEHICLE, nullptr);
        return pool.empty() && ch.used() <= cap;
    };
    // начальная популяция: старт + возмущения (крупное разрушение и починка)
    { l2::Sol x = start; educate(x); add(std::move(x)); }
    for (int i = 1; i < MU && tm.sec() < tEnd; i++) {
        l2::Sol x = pop[0].s; RU.clearMarks();
        if (rng.uni() < 0.5) RU.random(x, rng, max(4, (int)(N * (0.2 + 0.3 * rng.uni())))); else { RU.cbar = 3 * RU.cbar; RU.sisr(x, rng); RU.cbar /= 3; }
        pool.clear(); removeSet(Pb, x, RU.rem.data(), RU.rem.size(), pool, nullptr);
        if (!repair(x)) continue;
        educate(x); add(std::move(x));
    }
    long gen = 0;
    while (tm.sec() < tEnd && pop.size() >= 2) {
        gen++;
        auto bf = biased(); int n = pop.size();
        auto tour = [&]() { int a = rng.randint(n), b = rng.randint(n); return bf[a] < bf[b] ? a : b; };
        int ia = tour(), ib = tour(); for (int t = 0; t < 5 && ib == ia; t++) ib = rng.randint(n);
        if (ia == ib) ib = (ia + 1) % n;
        const l2::Sol& A = pop[ia].s; const l2::Sol& B = pop[ib].s;
        l2::Sol ch = A;
        int c = rng.randint(N); for (int t = 0; t < 4 * N && A.rt[c] < 0; t++) c = rng.randint(N);
        int k = 1 + rng.randint(max(1, A.used() / 2));
        uint32_t take = 0; int taken = 0;
        if (A.rt[c] >= 0) { take |= 1u << A.rt[c]; taken++; }
        for (int x : Pb.nbr[c]) { if (taken >= k) break; int v = A.rt[x]; if (v >= 0 && !(take >> v & 1)) { take |= 1u << v; taken++; } }
        vector<char> freeC(N, 0);
        for (int v = 0; v < V; v++) if (take >> v & 1) { for (int i = 0; i < ch.len[v]; i++) { freeC[ch.r[v][i]] = 1; ch.rt[ch.r[v][i]] = -1; } ch.len[v] = 0; ch.rebuild(Pb, v); }
        vector<pair<int, int>> ov;
        for (int v = 0; v < V; v++) if (B.len[v]) { int o = 0; for (int i = 0; i < B.len[v]; i++) o += freeC[B.r[v][i]]; if (o) ov.push_back({-o, v}); }
        sort(ov.begin(), ov.end());
        int putB = 0;
        for (auto& [o, v] : ov) {
            if (putB >= taken) break;
            if (ch.len[v]) continue;
            int L = 0; for (int i = 0; i < B.len[v]; i++) { int x = B.r[v][i]; if (ch.rt[x] < 0) ch.r[v][L++] = x; }
            ch.len[v] = L; ch.rebuild(Pb, v); putB++;
        }
        if (!repair(ch)) continue;
        educate(ch);
        // мутация: попытка сократить парк у потомка (R&R с Σabsence, короткая)
        if (rng.uni() < PEL && ch.nUn() == 0 && ch.used() > 1 && tm.sec() < tEnd) {
            l2::Sol t = ch; FR.run(t, 0, tEnd, tEnd, ELIT);
            if (t.nUn() == 0 && t.used() < ch.used()) {
                cap = t.used(); educate(t);
                if (getenv("DEBUG")) fprintf(stderr, "  hgs elim -> %d t=%.2f\n", t.used(), tm.sec());
                ch = t;
            }
        }
        add(std::move(ch));
        if ((int)pop.size() >= MU + LAMBDA) survivors();
    }
    if (getenv("DEBUG")) fprintf(stderr, "hgs gen=%ld pop=%d\n", gen, (int)pop.size());
    return best;
}

static l2::Sol solveOnce(const Instance& I, const l2::Prob& Pb, double tl, uint64_t seed, Timer& tm) {
    double tBeg = tm.sec();
    Fast F(I);
    Rng rng(seed);
    Sol g; Routes init = multiGreedy(F, rng, (tl - tBeg) * 0.05, g);
    SA sa(F, seed * 7 + 3); sa.fElim = 0.45;
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
    Routes R = sa.run(init, tl, tm);
    Score s = evaluate(I, R), s0 = evaluate(I, init);
    if (!s.feasible || (s0.feasible && s0.scalar() < s.scalar())) R = init;
    double tSA = tm.sec();
    // 4) км: SISR-отжиг (lns2_util) от лучшего решения SA
    l2::Sol best; best.fromRoutes(Pb, R);
    Rng rng2(seed * 31 + 7);
    l2::KmSearch KS(I, Pb, rng2, tm);
    double rrEnd = tBeg + (tl - tBeg) * l2::P("RREND", 0.6);
    if (tm.sec() < rrEnd) { l2::FleetRR FR(I, Pb, rng2, tm, KS); double tS = best.nUn() ? tm.sec() + 0.5 * (rrEnd - tm.sec()) : 0; FR.run(best, tS, rrEnd, tl); }
    double tRR = tm.sec();
    best = hgsKm(I, Pb, best, tl, rng2, tm, KS);
    if (getenv("DEBUG")) fprintf(stderr, "tRR=%.3f tSA=%.3f it=%ld used=%d km=%.1f\n", tRR, tSA, KS.iters, best.used(), best.kmTot());
    return best;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; tl *= 0.97;
    l2::Prob Pb(I); l2::Sol best; bool have = false;
    int R = (int)l2::P("RST", 1);
    for (int r = 0; r < R; r++) {
        double segEnd = tm.sec() + (tl - tm.sec()) / (R - r);
        l2::Sol s = solveOnce(I, Pb, segEnd, seed * 1000 + r, tm);
        if (!have || s.better(best, Pb)) { best = s; have = true; }
    }
    lu::LocalSearch LS(I); lu::Sol x = l2::toLu(I, best); Rng rr(seed); LS.run(x, rr, best.used(), &tm, tl / 0.97 * 0.99);
    l2::Sol y; y.fromRoutes(Pb, x.r); if (y.better(best, Pb)) best = y;
    return l2::finalizeR(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "32_hgs2"); }
