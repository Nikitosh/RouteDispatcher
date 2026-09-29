// 11. Гранулярный табу-поиск. Итерация — лучший ход по окрестности «заявка k × её K=12 ближайших соседей n»:
// relocate (до/после n), or-opt (2–3 подряд), swap, 2-opt* (k→n и n→k), обмен/перенос бригад целиком,
// плюс ходы пула (лучшая вставка; вставка с вытеснением одной заявки по всем позициям).
// Табу на атрибуты (заявка, маршрут) и на внутримаршрутные перестановки заявки; tenure 5..15; аспирация —
// суррогат лучше лучшего в фазе; частотная диверсификация (Cordeau–Laporte); в фазе км нулевые ходы
// пропускаются и при застое (100 итераций) — рестарт от лучшего с разрушением ~10% соседних заявок.
// Фазы как в s10: починка (пул с растущими штрафами), удаление маршрутов (удалить маршрут → табу с пулом без
// открытия бригад, пока пул не опустеет, иначе откат), затем минимизация км. Лучшее — по Score::scalar().
#include "sa_util.hpp"
using namespace sau;

struct Tabu {
    const Fast& F; Sol S; Rng rng;
    int K = 12;
    double Wv = 100, beta = 0, Wp0 = 30, Winc = 1, wpScale = 1;
    bool allowOpen = true, allowPool = true;
    double wp[MAXN];
    long long tabuT[MAXN][MAXV], tabuMv[MAXN];
    long long it = 0, evals = 0;
    int tmp[MAXL], bufA[MAXL], bufB[MAXL];
    double bestScore = 1e300; int bestLen[MAXV], bestSeq[MAXV][MAXL];
    int tenMin = 5, tenMax = 15; double noise = 0.5;
    bool dbg = getenv("TB_DBG") != nullptr, dbg2 = getenv("TB_DBG2") != nullptr;
    Tabu(const Fast& f, uint64_t seed) : F(f), rng(seed) { memset(tabuT, 0, sizeof(tabuT)); memset(tabuMv, 0, sizeof(tabuMv)); memset(freq, 0, sizeof(freq)); }

    inline double press(int len) const { return -beta * len * len; }
    void checkBest() {
        double s = S.trueScore();
        if (s < bestScore - 1e-9) {
            bestScore = s;
            for (int v = 0; v < F.V; v++) { bestLen[v] = S.r[v].len; memcpy(bestSeq[v], S.r[v].seq, sizeof(int) * S.r[v].len); }
        }
    }
    Routes bestRoutes() const { Routes R(F.V); for (int v = 0; v < F.V; v++) R[v].assign(bestSeq[v], bestSeq[v] + bestLen[v]); return R; }
    void restoreBest() { S.fromRoutes(F, bestRoutes()); }
    double surrogate() const {
        double c = S.km + Wv * S.used;
        for (int q = 0; q < S.np; q++) c += wp[S.pool[q]];
        for (int v = 0; v < F.V; v++) c += press(S.r[v].len);
        return c;
    }
    void resetWp() { for (int k = 0; k < F.N; k++) wp[k] = wpScale * Wp0 * F.pen[k] / 50; }
    void growWp() { for (int q = 0; q < S.np; q++) wp[S.pool[q]] += wpScale * Winc * F.pen[S.pool[q]] / 50; }
    int tenure() { return tenMin + rng.randint(tenMax - tenMin + 1); }

    struct C { int v, i; const int* mid; int m; int u, j; };
    inline double rdelta(const C& c, double km) const {
        const Route& R = S.r[c.v];
        int nl = candLen(S, c.i, c.m, c.u, c.j);
        return km - R.km + Wv * ((nl > 0) - (R.len > 0)) + press(nl) - press(R.len);
    }
    // Оценка двух изменённых маршрутов; apply — применить. INF если недопустимо.
    double two(const C& A, const C& B, bool apply) {
        evals++;
        double kmA = evalCand(F, S, A.v, A.i, A.mid, A.m, A.u, A.j); if (kmA < 0) return 1e18;
        double kmB = evalCand(F, S, B.v, B.i, B.mid, B.m, B.u, B.j); if (kmB < 0) return 1e18;
        double d = rdelta(A, kmA) + rdelta(B, kmB);
        if (apply) {
            int LA = compose(S, bufA, A.v, A.i, A.mid, A.m, A.u, A.j), LB = compose(S, bufB, B.v, B.i, B.mid, B.m, B.u, B.j);
            S.setRoute(A.v, bufA, LA); S.setRoute(B.v, bufB, LB);
        }
        return d;
    }
    double intra(int v, const int* ns, int nl, bool apply) {
        evals++;
        const Route& R = S.r[v];
        int pre = 0; while (pre < nl && pre < R.len && ns[pre] == R.seq[pre]) pre++;
        if (pre == nl && nl == R.len) return 1e18;
        int suf = 0; while (suf < nl - pre && suf < R.len - pre && ns[nl - 1 - suf] == R.seq[R.len - 1 - suf]) suf++;
        double km = evalCand(F, S, v, pre, ns + pre, nl - pre - suf, v, R.len - suf);
        if (km < 0) return 1e18;
        if (apply) { memcpy(bufA, ns, sizeof(int) * nl); S.setRoute(v, bufA, nl); }
        return km - R.km;
    }
    bool isTabu(int k, int v) const { return tabuT[k][v] > it; }
    // type: 0 relocate (var 0 — перед n, 1 — после), 1 swap, 2 2-opt* (var 0: k→n, 1: n→k), 3 вытеснение (k из пула заменяет n)
    double move(int type, int k, int n, int var, bool& tb, bool apply) {
        tb = false;
        int b = S.where[n]; if (b < 0) return 1e18;
        int j = S.pos[n]; const Route& RB = S.r[b];
        if (type == 3) {
            tb = isTabu(k, b);
            C c{b, j, &k, 1, b, j + 1};
            evals++;
            double km = evalCand(F, S, c.v, c.i, c.mid, c.m, c.u, c.j); if (km < 0) return 1e18;
            double d = rdelta(c, km) + wp[n] - wp[k];
            if (apply) {
                int L = compose(S, bufA, c.v, c.i, c.mid, c.m, c.u, c.j);
                S.poolRemove(k); S.setRoute(b, bufA, L); S.poolAdd(n); tabuT[n][b] = it + tenure();
            }
            return d;
        }
        int a = S.where[k]; if (a < 0) return 1e18;
        int i = S.pos[k]; const Route& RA = S.r[a];
        if (type == 0) {
            int p = j + var;
            if (a != b) {
                tb = isTabu(k, b);
                double d = two(C{b, p, &k, 1, b, p}, C{a, i, nullptr, 0, a, i + 1}, apply);
                if (apply) tabuT[k][a] = it + tenure();
                return d;
            }
            if (p == i || p == i + 1) return 1e18;
            tb = tabuMv[k] > it;
            int nl = 0;
            for (int t = 0; t <= RA.len; t++) { if (t == p) tmp[nl++] = k; if (t < RA.len && t != i) tmp[nl++] = RA.seq[t]; }
            if (apply) tabuMv[k] = it + tenure();
            return intra(a, tmp, nl, apply);
        }
        if (type == 1) {
            if (a != b) {
                tb = isTabu(k, b) || isTabu(n, a);
                double d = two(C{a, i, &n, 1, a, i + 1}, C{b, j, &k, 1, b, j + 1}, apply);
                if (apply) { tabuT[k][a] = it + tenure(); tabuT[n][b] = it + tenure(); }
                return d;
            }
            tb = tabuMv[k] > it || tabuMv[n] > it;
            if (apply) { tabuMv[k] = it + tenure(); tabuMv[n] = it + tenure(); }
            memcpy(tmp, RA.seq, sizeof(int) * RA.len); swap(tmp[i], tmp[j]);
            return intra(a, tmp, RA.len, apply);
        }
        if (type == 7) {   // or-opt: сегмент [i, i+L) в другой маршрут рядом с n; var: L = 2 + var/2, после n если var нечётный
            int L = 2 + (var >> 1), p = j + (var & 1);
            if (a == b || i + L > RA.len) return 1e18;
            int seg[3]; for (int q = 0; q < L; q++) seg[q] = RA.seq[i + q];
            tb = isTabu(k, b);
            double d = two(C{b, p, seg, L, b, p}, C{a, i, nullptr, 0, a, i + L}, apply);
            if (apply) for (int q = 0; q < L; q++) tabuT[seg[q]][a] = it + tenure();
            return d;
        }
        if (type == 2) {
            if (a == b) return 1e18;
            if (var == 0) {   // a' = a[..i] + b[j..], b' = b[..j) + a[i+1..]
                int x = i + 1 < RA.len ? RA.seq[i + 1] : -1;
                tb = isTabu(n, a) || (x >= 0 && isTabu(x, b));
                double d = two(C{a, i + 1, nullptr, 0, b, j}, C{b, j, nullptr, 0, a, i + 1}, apply);
                if (apply) { tabuT[n][b] = it + tenure(); if (x >= 0) tabuT[x][a] = it + tenure(); }
                return d;
            } else {          // b' = b[..j] + a[i..], a' = a[..i) + b[j+1..]
                int y = j + 1 < RB.len ? RB.seq[j + 1] : -1;
                tb = isTabu(k, b) || (y >= 0 && isTabu(y, a));
                double d = two(C{b, j + 1, nullptr, 0, a, i}, C{a, i, nullptr, 0, b, j + 1}, apply);
                if (apply) { tabuT[k][a] = it + tenure(); if (y >= 0) tabuT[y][b] = it + tenure(); }
                return d;
            }
        }
        return 1e18;
    }

    // Одна итерация табу: лучший допустимый (не табу или с аспирацией) ход. Возвращает false, если ходов нет.
    double fCur = 0, fBest = 1e300;
    int prevWhere[MAXN]; long long freq[MAXN][MAXV]; long long lastImp = 0; double divScale = 0, lambdaDiv = 0.015;
    bool iterate() {
        it++;
        divScale = lambdaDiv * sqrt((double)F.N * F.V) * S.km / (double)it;
        double best = 1e18; int bt = -1, bk = -1, bn = -1, bv = -1, bp = -1; Ins bi;
        bool tb;
        for (int k = 0; k < F.N; k++) {
            if (S.where[k] < 0) continue;
            int kk = min<int>(K, F.nb[k].size());
            for (int q = 0; q < kk; q++) {
                int n = F.nb[k][q]; if (S.where[n] < 0) continue;
                for (int ti = 0; ti < 4; ti++) for (int var = 0; var < (ti == 1 ? 1 : ti == 3 ? 4 : 2); var++) {
                    int type = ti == 3 ? 7 : ti;
                    double d = move(type, k, n, var, tb, false);
                    if (d >= 1e17 || (skipZero && fabs(d) < 1e-6)) continue;   // нулевые ходы (совпадающие точки) — плато, пропускаем
                    if (tb && fCur + d >= fBest - 1e-6) continue;
                    if (d > 0 && S.where[n] != S.where[k]) d += divScale * freq[k][S.where[n]];   // долгосрочная диверсификация
                    d += noise * rng.uni();
                    if (d < best) { best = d; bt = type; bk = k; bn = n; bv = var; }
                }
            }
        }
        // обмен бригад целиком / перенос маршрута на пустую бригаду (разнородный парк)
        for (int a = 0; a < F.V; a++) {
            if (S.r[a].len == 0) continue;
            int fa = S.r[a].seq[0];
            for (int b = 0; b < F.V; b++) {
                if (b == a || (S.r[b].len && b < a)) continue;
                if (F.st[a] == F.st[b] && F.mode[a] == F.mode[b] && F.mask[a] == F.mask[b]) continue;   // одинаковые бригады
                int fb = S.r[b].len ? S.r[b].seq[0] : -1;
                bool t2 = isTabu(fa, b) || (fb >= 0 && isTabu(fb, a));
                double d = two(C{b, 0, nullptr, 0, a, 0}, C{a, 0, nullptr, 0, b, 0}, false);
                if (d >= 1e17 || (skipZero && fabs(d) < 1e-6)) continue;
                d += noise * rng.uni();
                if (t2 && fCur + d >= fBest - 1e-6) continue;
                if (d < best) { best = d; bt = 6; bk = a; bn = b; }
            }
        }
        if (allowPool) for (int q = 0; q < S.np; q++) {
            int k = S.pool[q];
            Ins in = bestIns(F, S, k, Wv, allowOpen, -1, [&](int l) { return press(l); });
            evals += 10;
            if (in.v >= 0) { double d = in.cost - wp[k]; if (d < best) { best = d; bt = 4; bk = k; bi = in; } }
            // вставка с вытеснением одной заявки: все маршруты, позиции вставки и вытеснения
            for (int v = 0; v < F.V; v++) {
                if (!(F.skb[k] & F.mask[v])) continue;
                const Route& R = S.r[v];
                if (R.len == 0) continue;
                bool tk = isTabu(k, v);
                for (int e = 0; e < R.len; e++) {
                    int ek = R.seq[e];
                    double base = wp[ek] - wp[k] + press(R.len) - press(R.len) - R.km;
                    if (base + R.km - 50 >= best) continue;
                    for (int p = 0; p <= R.len; p++) {
                        if (p == e + 1) continue;   // то же, что p == e
                        int nl = 0;
                        for (int t = 0; t <= R.len; t++) { if (t == p) tmp[nl++] = k; if (t < R.len && t != e) tmp[nl++] = R.seq[t]; }
                        evals++;
                        const Route& RR = S.r[v];
                        int pre = min(p, e); int suf = R.len - max(p, e + 1);
                        double km = evalCand(F, S, v, pre, tmp + pre, nl - pre - suf, v, RR.len - suf);
                        if (km < 0) continue;
                        double d = km + base + noise * rng.uni();
                        if (tk && fCur + d >= fBest - 1e-6) continue;
                        if (d < best) { best = d; bt = 5; bk = k; bv = v; bn = e; bp = p; }
                    }
                }
            }
        }
        if (bt < 0) return false;
        memcpy(prevWhere, S.where, sizeof(int) * F.N);
        if (dbg2) fprintf(stderr, "it=%lld bt=%d best=%.2f np=%d k=%d\n", it, bt, best, S.np, bk);
        if (bt == 4) {
            int L = compose(S, bufA, bi.v, bi.p, &bk, 1, bi.v, bi.p);
            S.poolRemove(bk); S.setRoute(bi.v, bufA, L);
        } else if (bt == 5) {
            const Route& R = S.r[bv]; int ek = R.seq[bn], nl = 0;
            for (int t = 0; t <= R.len; t++) { if (t == bp) tmp[nl++] = bk; if (t < R.len && t != bn) tmp[nl++] = R.seq[t]; }
            S.poolRemove(bk); memcpy(bufA, tmp, sizeof(int) * nl); S.setRoute(bv, bufA, nl); S.poolAdd(ek);
            tabuT[ek][bv] = it + tenure();
        } else if (bt == 6) {
            int a = bk, b = bn, fa = S.r[a].seq[0], fb = S.r[b].len ? S.r[b].seq[0] : -1;
            two(C{b, 0, nullptr, 0, a, 0}, C{a, 0, nullptr, 0, b, 0}, true);
            tabuT[fa][a] = it + tenure(); if (fb >= 0) tabuT[fb][b] = it + tenure();
        } else move(bt, bk, bn, bv, tb, true);
        for (int k = 0; k < F.N; k++) if (S.where[k] != prevWhere[k] && S.where[k] >= 0) freq[k][S.where[k]]++;
        fCur = surrogate();
        if (fCur < fBest - 1e-6) { fBest = fCur; lastImp = it; }
        checkBest();
        return true;
    }
    // Табу-отрезок: до maxIt итераций или tEnd; stopPen>=0 — выйти, как только штраф пула ≤ stopPen.
    int incPeriod = 20;
    // Разрушение и восстановление вокруг случайной заявки (для выхода из застоя в фазе км)
    void perturb() {
        restoreBest();
        memset(tabuT, 0, sizeof(tabuT)); memset(tabuMv, 0, sizeof(tabuMv));
        int c = rng.randint(F.N), R = max(3, F.N / 10), rem[MAXN], nr = 0;
        rem[nr++] = c;
        for (int q = 0; q < (int)F.nb[c].size() && nr < R; q++) rem[nr++] = F.nb[c][q];
        for (int q = 0; q < nr; q++) {
            int k = rem[q], v = S.where[k]; if (v < 0) continue;
            int L = 0; for (int t = 0; t < S.r[v].len; t++) if (S.r[v].seq[t] != k) bufA[L++] = S.r[v].seq[t];
            S.setRoute(v, bufA, L); S.poolAdd(k);
        }
        for (int q = nr - 1; q > 0; q--) swap(rem[q], rem[rng.randint(q + 1)]);
        for (int q = 0; q < nr; q++) {
            int k = rem[q]; if (S.pidx[k] < 0) continue;
            Ins in = bestIns(F, S, k, Wv, false, -1, [&](int l) { return press(l); });
            if (in.v < 0) continue;
            int L = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
            S.poolRemove(k); S.setRoute(in.v, bufA, L);
        }
        fCur = surrogate(); fBest = fCur; lastImp = it;
    }
    long long stagn = 100; bool skipZero = false;
    bool segment(long long maxIt, double tEnd, double stopPen, Timer& tm) {
        fCur = surrogate(); fBest = fCur; lastImp = it;
        for (long long q = 0; q < maxIt; q++) {
            if (tm.sec() >= tEnd) return false;
            iterate();
            if (stopPen < 0 && it - lastImp > stagn) perturb();
            if (allowPool && (q + 1) % incPeriod == 0) { growWp(); fCur = surrogate(); fBest = min(fBest, fCur); }
            if (dbg && stopPen < 0 && q % 500 == 0) fprintf(stderr, "  km-phase q=%lld km=%.1f used=%d best=%.1f t=%.3f\n", q, S.km, S.used, bestScore, tm.sec());
            if (dbg && stopPen >= 0 && q % 50 == 0) fprintf(stderr, "  q=%lld np=%d km=%.1f f=%.1f\n", q, S.np, S.km, fCur);
            if (stopPen >= 0 && S.poolPen <= stopPen + 1e-9) return true;
        }
        return false;
    }
    int elimOrd[MAXL];
    int pickSmallRoute() {
        double wsum = 0; int nz = 0;
        for (int v = 0; v < F.V; v++) if (S.r[v].len) { wsum += 1.0 / (S.r[v].len * S.r[v].len); nz++; }
        if (nz <= 1) return -1;
        double x = rng.uni() * wsum;
        for (int v = 0; v < F.V; v++) if (S.r[v].len) { x -= 1.0 / (S.r[v].len * S.r[v].len); if (x <= 0) return v; }
        return -1;
    }
    void eliminate(int r) {
        int L = S.r[r].len; memcpy(elimOrd, S.r[r].seq, sizeof(int) * L);
        if (rng.next() & 1) sort(elimOrd, elimOrd + L, [&](int p, int q) { return F.b[p] - F.a[p] < F.b[q] - F.a[q]; });
        else for (int q = L - 1; q > 0; q--) swap(elimOrd[q], elimOrd[rng.randint(q + 1)]);
        S.setRoute(r, nullptr, 0);
        for (int q = 0; q < L; q++) {
            int k = elimOrd[q];
            tabuT[k][r] = it + 1000000;   // в удалённый маршрут не возвращаться
            Ins in = bestIns(F, S, k, Wv, false, r, [&](int l) { return press(l); });
            if (in.v < 0) { S.poolAdd(k); continue; }
            int nl = compose(S, bufA, in.v, in.p, &k, 1, in.v, in.p);
            S.setRoute(in.v, bufA, nl);
        }
    }

    double noiseRepair = 0.5, wpScaleElim = 30, fRepair = 0.3, fElim = 0.5, betaElim = 0.3, wpScaleRepair = 10, WvRepair = 5;
    long long elimIters = 300, repIters = 2000; int maxFails = 30;
    Routes run(const Routes& init, double tl, Timer& tm) {
        S.fromRoutes(F, init);
        resetWp(); checkBest();
        double tStart = tm.sec(), span = max(1e-3, tl - tStart);
        if (S.np > 0) {   // 1) починка
            allowOpen = true; allowPool = true; beta = 0;
            double wv = Wv, nz = noise; Wv = WvRepair; wpScale = wpScaleRepair; noise = noiseRepair; resetWp();
            segment(1LL << 60, tStart + span * fRepair, 0, tm);
            Wv = wv; wpScale = 1; noise = nz;
            restoreBest();
            if (dbg) fprintf(stderr, "repair done t=%.3f best=%.1f it=%lld\n", tm.sec(), bestScore, it);
        }
        int fails = 0;     // 2) удаление маршрутов
        while (tm.sec() < tStart + span * fElim && fails < maxFails) {
            restoreBest();
            memset(tabuT, 0, sizeof(tabuT)); memset(tabuMv, 0, sizeof(tabuMv));
            int r = pickSmallRoute(); if (r < 0) break;
            double target = S.poolPen;
            eliminate(r);
            wpScale = wpScaleElim; resetWp();
            allowOpen = false; allowPool = true; beta = betaElim;
            bool ok = S.poolPen <= target + 1e-9 || segment(elimIters, tStart + span * fElim, target, tm);
            wpScale = 1;
            checkBest();
            if (ok) fails = 0; else fails++;
            if (dbg) fprintf(stderr, "elim r=%d ok=%d t=%.3f best=%.1f it=%lld\n", r, ok, tm.sec(), bestScore, it);
        }
        restoreBest();     // 3) км
        memset(tabuT, 0, sizeof(tabuT)); memset(tabuMv, 0, sizeof(tabuMv));
        allowOpen = S.np > 0; allowPool = true; beta = 0; skipZero = true;
        for (int k = 0; k < F.N; k++) wp[k] = 1e5 * F.pen[k] / 50;
        segment(1LL << 60, tl, -1, tm);
        return bestRoutes();
    }
};

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm;
    tl *= 0.97;
    Fast F(I, 16);
    Rng rng(seed);
    Sol g; Routes init = multiGreedy(F, rng, tl * 0.05, g);
    Tabu tb(F, seed * 11 + 5);
    if (getenv("TB_K")) tb.K = atoi(getenv("TB_K"));
    if (getenv("TB_TMIN")) tb.tenMin = atoi(getenv("TB_TMIN"));
    if (getenv("TB_TMAX")) tb.tenMax = atoi(getenv("TB_TMAX"));
    if (getenv("TB_EIT")) tb.elimIters = atoll(getenv("TB_EIT"));
    if (getenv("TB_FELIM")) tb.fElim = atof(getenv("TB_FELIM"));
    if (getenv("TB_BETA")) tb.betaElim = atof(getenv("TB_BETA"));
    if (getenv("TB_INC")) tb.incPeriod = atoi(getenv("TB_INC"));
    if (getenv("TB_NOISE")) tb.noise = atof(getenv("TB_NOISE"));
    if (getenv("TB_WPSE")) tb.wpScaleElim = atof(getenv("TB_WPSE"));
    if (getenv("TB_STAGN")) tb.stagn = atoll(getenv("TB_STAGN"));
    if (getenv("TB_LDIV")) tb.lambdaDiv = atof(getenv("TB_LDIV"));
    if (getenv("TB_WPSR")) tb.wpScaleRepair = atof(getenv("TB_WPSR"));
    if (getenv("TB_FREP")) tb.fRepair = atof(getenv("TB_FREP"));
    if (getenv("TB_NZR")) tb.noiseRepair = atof(getenv("TB_NZR"));
    if (getenv("TB_WINC")) tb.Winc = atof(getenv("TB_WINC"));
    Routes R = tb.run(init, tl, tm);
    if (getenv("TB_STAT")) fprintf(stderr, "iters=%lld (%.0f/s) evals=%.2fM/s\n", tb.it, tb.it / tm.sec(), tb.evals / tm.sec() / 1e6);
    Score s = evaluate(I, R), s0 = evaluate(I, init);
    if (!s.feasible || (s0.feasible && s0.scalar() < s.scalar())) return init;
    return R;
}
int main(int c, char** v) { return runMain(c, v, solve, "11_tabu"); }
