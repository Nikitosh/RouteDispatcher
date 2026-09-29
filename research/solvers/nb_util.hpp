// nb_util: сокращение парка по Нагате–Бройси (2009) на структурах l2 — удаление маршрута, стек выброшенных заявок (EP),
// вставка с выталкиванием до KMAX заявок из одного маршрута (набор с минимальной суммой счётчиков неудач p[]),
// случайное возмущение допустимыми перемещениями. В отличие от выталкивания глубины 1 (SA2), за один шаг
// проходит «цепочку»: дальняя машина берёт московскую заявку, освобождая утро от 2–4 своих заявок.
// Проверка допустимости выталкивания: перебор «оставить/выкинуть» по позициям маршрута с отсечением по сумме p[];
// когда вставленная заявка уже пройдена, остаток без выкидываний проверяется за O(1) по lat[] исходного маршрута.
#pragma once
#include "lns2_util.hpp"

namespace nb {
using namespace l2;

struct NB {
    const Prob& P; Rng& rng; Timer& tm;
    int kmax = (int)l2::P("NBK", 3); int irand = (int)l2::P("NBIR", 30); double pNoise = l2::P("NBNZ", 0.3);
    vector<int> pc; vector<int> ep;
    long nEject = 0, nIns = 0, nPert = 0, nSucc = 0; int minEP = 0;
    // рабочие массивы перебора
    int seq[MAXL + 2], L1, posK, kIns; double latS[MAXL + 2]; const double* Tv; int M;
    int curE[8], bestE[8], nBestE; double bestPs; int bestV, bestP; long nodes, nodeCap = (long)l2::P("NBNODE", 200000);
    std::function<bool()> abortFn;   // опрос: true — оборвать попытку (например, другой поток улучшил парк)
    NB(const Prob& p, Rng& r, Timer& t) : P(p), rng(r), tm(t), pc(p.N, 1) {}

    // допустимая вставка с минимальным Δкм; открывать пустые маршруты можно, пока used < cap
    bool insertFeasible(Sol& s, int k, int cap) {
        int used = s.used(); double best = INF; int bv = -1, bp = -1;
        for (int v = 0; v < P.V; v++) {
            if (!P.can(v, k)) continue;
            int L = s.len[v]; if (L == 0 && used >= cap) continue;
            for (int p = 0; p <= L; p++) {
                if (p > 0 && s.dep[v][p - 1] > P.b[k]) break;
                double d = s.insDelta(P, v, p, k); if (d >= INF) continue;
                d += (L == 0 ? 1e4 : 0) + 1e-6 * rng.uni();
                if (d < best) { best = d; bv = v; bp = p; }
            }
        }
        if (bv < 0) return false;
        s.insertAt(P, bv, bp, k); nIns++; return true;
    }
    // ---- км-режим: цель = км нового маршрута − км старого + lam·|E|; заявки с lock[] не выталкиваются ----
    bool kmMode = false, pW = false; double lam = 0, oldKm = 0; double accP = 0; const double* Dv; double sufS[MAXL + 2]; vector<char> lock;
    void dfsKm(int i, double t, int prev, int cnt, double dist) {
        if (++nodes > nodeCap) return;
        double w = pW ? accP : cnt;
        double lb = dist - oldKm + lam * w; if (lb >= bestPs) return;
        if (i == L1) { bestPs = lb; nBestE = cnt; memcpy(bestE, curE, cnt * sizeof(int)); return; }
        int e = seq[i], ne = P.nd[e];
        double beg = max(t + Tv[prev * M + ne], P.a[e]);
        if (beg <= P.b[e] + TOL) {
            double d2 = dist + Dv[prev * M + ne];
            if (i >= posK && beg <= latS[i] + TOL) {
                double c = d2 + sufS[i] - oldKm + lam * w;
                if (c < bestPs) { bestPs = c; nBestE = cnt; memcpy(bestE, curE, cnt * sizeof(int)); }
                if (cnt < kmax) dfsKm(i + 1, beg + P.svc[e], ne, cnt, d2);   // дальше можно выкинуть ещё (км могут уменьшиться)
            } else dfsKm(i + 1, beg + P.svc[e], ne, cnt, d2);
        } else if (e == kIns) return;
        if (e != kIns && !lock[e] && cnt < kmax) { curE[cnt] = e; accP += pc[e]; dfsKm(i + 1, t, prev, cnt + 1, dist); accP -= pc[e]; }
    }
    void dfs(int i, double t, int prev, int cnt, double ps) {
        if (++nodes > nodeCap) return;
        if (i == L1) { if (ps < bestPs) { bestPs = ps; nBestE = cnt; memcpy(bestE, curE, cnt * sizeof(int)); } return; }
        int e = seq[i], ne = P.nd[e];
        double beg = max(t + Tv[prev * M + ne], P.a[e]);
        if (beg <= P.b[e] + TOL) {
            if (i >= posK && beg <= latS[i] + TOL) {   // остаток без выкидываний допустим
                if (ps < bestPs) { bestPs = ps; nBestE = cnt; memcpy(bestE, curE, cnt * sizeof(int)); }
                return;   // выкидывать дальше — только дороже
            }
            dfs(i + 1, beg + P.svc[e], ne, cnt, ps);
        } else if (e == kIns) return;   // вставляемую не выкинуть, а раньше неё время только растёт
        if (e != kIns && cnt < kmax && ps + pc[e] < bestPs) {
            curE[cnt] = e; dfs(i + 1, t, prev, cnt + 1, ps + pc[e]);
        }
    }
    // лучшая вставка k с выталкиванием (по всем непустым маршрутам и позициям); true — применена
    int skipV = -1;
    bool insertEject(Sol& s, int k) {
        bestPs = INF; bestV = -1; M = P.M; nodes = 0;
        int order[MAXV], nv = 0; for (int v = 0; v < P.V; v++) if (s.len[v] && P.can(v, k) && v != skipV) order[nv++] = v;
        for (int i = nv - 1; i > 0; i--) swap(order[i], order[rng.randint(i + 1)]);
        for (int q = 0; q < nv; q++) {
            int v = order[q], L = s.len[v]; Tv = P.T[v];
            for (int p = 0; p <= L; p++) {
                L1 = 0; for (int j = 0; j < p; j++) seq[L1++] = s.r[v][j]; posK = L1; seq[L1++] = k; for (int j = p; j < L; j++) seq[L1++] = s.r[v][j];
                for (int j = p; j < L; j++) latS[posK + 1 + (j - p)] = s.lat[v][j];
                latS[posK] = p < L ? min(P.b[k], s.lat[v][p] - P.svc[k] - Tv[P.nd[k] * M + P.nd[s.r[v][p]]]) : P.b[k];
                kIns = k; double before = bestPs;
                if (kmMode) {
                    Dv = P.D[v]; oldKm = s.km[v];
                    double acc = 0; sufS[L1 - 1] = 0;
                    for (int j = L1 - 2; j >= posK; j--) { acc += Dv[P.nd[seq[j]] * M + P.nd[seq[j + 1]]]; sufS[j] = acc; }
                    dfsKm(0, 0, P.st[v], 0, 0);
                } else dfs(0, 0, P.st[v], 0, 0);
                if (bestPs < before) { bestV = v; bestP = p; }
            }
        }
        if (bestV < 0) return false;
        // применить: выкинуть bestE из маршрута bestV, вставить k на позицию (пересчитать с учётом сдвига)
        int v = bestV; int nl = 0, tmp[MAXL + 2]; int L = s.len[v];
        auto ejected = [&](int x) { for (int j = 0; j < nBestE; j++) if (bestE[j] == x) return true; return false; };
        for (int j = 0; j <= L; j++) { if (j == bestP) tmp[nl++] = k; if (j < L && !ejected(s.r[v][j])) tmp[nl++] = s.r[v][j]; }
        for (int j = 0; j < nBestE; j++) { s.rt[bestE[j]] = -1; ep.push_back(bestE[j]); }
        s.len[v] = nl; memcpy(s.r[v], tmp, nl * sizeof(int)); s.rt[k] = v;
        vector<int> out; s.rebuild(P, v, &out); for (int x : out) ep.push_back(x);   // не должно случаться
        nEject++; return true;
    }
    // случайное возмущение: перемещение случайной заявки в лучшую допустимую позицию случайного другого маршрута
    void perturb(Sol& s, int cap) {
        int used = s.used();
        for (int it = 0; it < irand; it++) {
            int k = rng.randint(P.N); int v = s.rt[k]; if (v < 0) continue;
            int u = rng.randint(P.V); if (u == v || !P.can(u, k)) continue;
            if (s.len[u] == 0 && (used >= cap || rng.uni() > 0.2)) continue;
            double best = INF; int bp = -1;
            for (int p = 0; p <= s.len[u]; p++) { double d = s.insDelta(P, u, p, k); if (d < best) { best = d; bp = p; } }
            if (bp < 0) continue;
            double gain = s.remGain(P, v, s.posOf(k));
            if (best - gain > pNoise * P.maxD * rng.uni()) continue;   // не слишком портить км
            bool openNew = s.len[u] == 0;
            int i = s.posOf(k); memmove(s.r[v] + i, s.r[v] + i + 1, (s.len[v] - i - 1) * sizeof(int)); s.len[v]--; s.rebuild(P, v);
            s.insertAt(P, u, bp, k); nPert++;
            if (openNew) used++; if (s.len[v] == 0) used--;
        }
    }
    // ремонт: вставить все невыполненные (s.rt<0) вставкой с выталкиванием; парк не растёт сверх cap. true — всё вставлено.
    bool repair(Sol& s, int cap, double tEnd, long maxIter, double kmLam = 0) {
        bool km0 = kmMode; if (kmLam > 0) { kmMode = true; pW = true; lam = kmLam; lock.assign(P.N, 0); accP = 0; }
        struct Rst { NB& n; bool k; ~Rst() { n.kmMode = k; n.pW = false; } } rst{*this, km0};
        ep.clear(); for (int k = 0; k < P.N; k++) if (s.rt[k] < 0) ep.push_back(k);
        std::fill(pc.begin(), pc.end(), 1);
        long it = 0;
        while (!ep.empty()) {
            if ((it & 7) == 0 && tm.sec() > tEnd) return false;
            if (maxIter > 0 && it > maxIter) return false;
            it++;
            int k = ep.back(); ep.pop_back();
            if (insertFeasible(s, k, cap)) continue;
            pc[k]++;
            if (!insertEject(s, k)) { ep.insert(ep.begin(), k); continue; }
            perturb(s, cap);
        }
        return true;
    }
    // одна попытка удалить маршрут из s (в месте). true — успех (s без маршрута, всё обслужено).
    bool eliminate(Sol& s, int vRem, double tEnd, long maxIter, int vRem2 = -1) {
        int cap = s.used() - 1;
        ep.clear();
        for (int vr : {vRem, vRem2}) {
            if (vr < 0) continue;
            for (int i = 0; i < s.len[vr]; i++) { ep.push_back(s.r[vr][i]); s.rt[s.r[vr][i]] = -1; }
            s.len[vr] = 0; s.km[vr] = 0;
        }
        std::fill(pc.begin(), pc.end(), 1);
        for (int i = (int)ep.size() - 1; i > 0; i--) swap(ep[i], ep[rng.randint(i + 1)]);
        long it = 0; minEP = ep.size();
        while (!ep.empty()) {
            if ((int)ep.size() < minEP) { minEP = ep.size(); if (getenv("DEBUG3") && minEP <= 2) { fprintf(stderr, "   EP%d:", minEP); for (int x : ep) fprintf(stderr, " %d", x); fprintf(stderr, "\n"); } }
            if ((it & 7) == 0 && tm.sec() > tEnd) return false;
            if ((it & 63) == 0 && abortFn && abortFn()) return false;
            if (maxIter > 0 && it > maxIter) return false;
            it++;
            int k = ep.back(); ep.pop_back();
            if (insertFeasible(s, k, cap)) continue;
            pc[k]++;
            if (!insertEject(s, k)) { ep.insert(ep.begin(), k); continue; }
            perturb(s, cap);
        }
        nSucc++; return true;
    }
};
// ---------- км-ход «цепочка выталкиваний»: вырезать seeds, вставлять с выталкиванием (Δкм + lam·|E|) до maxSteps
// шагов; принять, если км уменьшились и парк не вырос. Возврат: true — принято (s изменён), иначе s восстановлен.
struct Chain {
    NB& E; const Prob& P; Sol bak; long nTry = 0, nOk = 0; double gain = 0;
    explicit Chain(NB& e) : E(e), P(e.P) { E.lock.assign(P.N, 0); }
    bool run(Sol& s, const int* seeds, int ns, int maxSteps, double lam, int kmax) {
        nTry++; bak = s; double km0 = s.kmTot(); int used0 = s.used();
        E.kmMode = true; E.lam = lam; int km0x = E.kmax; E.kmax = kmax;
        E.ep.clear(); vector<int> touched;
        uint32_t tch = 0;
        for (int q = 0; q < ns; q++) { int k = seeds[q]; int v = s.rt[k]; if (v < 0) continue; tch |= 1u << v; s.rt[k] = -2 - v; E.ep.push_back(k); }
        for (int v = 0; v < s.V; v++) if (tch >> v & 1) {
            int w = 0; for (int i = 0; i < s.len[v]; i++) { int k = s.r[v][i]; if (s.rt[k] >= 0) s.r[v][w++] = k; else s.rt[k] = -1; }
            s.len[v] = w; s.rebuild(P, v);
        }
        bool ok = true; int steps = 0;
        while (!E.ep.empty()) {
            if (steps++ >= maxSteps) { ok = false; break; }
            int k = E.ep.back(); E.ep.pop_back();
            E.lock[k] = 1; touched.push_back(k);
            double kb = s.kmTot();
            if (!E.insertEject(s, k)) { ok = false; if (getenv("CTRACE")) fprintf(stderr, "  step %d k=%d FAIL\n", steps, k); break; }
            if (getenv("CTRACE")) { fprintf(stderr, "  step %d k=%d -> v%d ej:", steps, k, E.bestV); for (int j = 0; j < E.nBestE; j++) fprintf(stderr, " %d", E.bestE[j]); fprintf(stderr, " dkm=%.1f tot=%.1f\n", s.kmTot() - kb, s.kmTot()); }
        }
        for (int k : touched) E.lock[k] = 0;
        E.kmMode = false; E.kmax = km0x;
        if (ok && s.used() <= used0 && s.kmTot() < km0 - 1e-6) { nOk++; gain += km0 - s.kmTot(); return true; }
        s = bak; return false;
    }
};
} // namespace nb
