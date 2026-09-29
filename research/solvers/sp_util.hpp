// sp_util: пул маршрутов и решатели разбиения множества (SP) по пулу для s70_ilssp / s71_racesp (logs/ilssp.md).
// Pool: маршрут хранится по ключу (группа бригад, множество заявок), группа = (стартовая точка, вид транспорта),
// так что маршрут, найденный для одной бригады, годится для любой бригады группы, чьи навыки покрывают навыки
// маршрута (vmask). Хранится лучшая по км последовательность. Хеш Зобриста, открытая адресация.
// Recomb (рабочий): SP по окрестностям — замена k маршрутов текущего решения точным разбиением их заявок на
// столбцы пула (DFS, ≤k столбцов, последний — поиск по хешу, паросочетание бригад). Используется в s70/s71.
// Solver (лагранжиан+субградиент+DFS по всему пулу), LPSP/GSP (ЛП генерацией столбцов + DFS по rc) — точные
// глобальные SP; на бюджете 1–3 с не работают (слабая ЛП-граница 6–12%, дробное ЛП-решение), оставлены для опытов.
#pragma once
#include "lns2_util.hpp"
#include <unordered_map>

namespace sp {
using l2::Prob; using l2::Sol; using l2::MAXV;
constexpr int NW = 3;   // 192 бит
struct Col { int grp; uint32_t vmask; uint64_t bits[NW]; double km; int len, off; uint64_t key; };

struct Pool {
    const Prob& P; int G = 0; int grpOf[MAXV]; int cap[MAXV]; uint32_t gmask[MAXV];
    vector<uint64_t> zk; uint64_t zg[MAXV];
    vector<Col> cols; vector<int> seqs; vector<int> table; vector<uint64_t> tkey; size_t tmask; int maxCols;
    long adds = 0, news = 0, impr = 0;
    Pool(const Prob& p, int maxC) : P(p), maxCols(maxC) {
        for (int v = 0; v < P.V; v++) {
            int g = -1; for (int u = 0; u < v; u++) if (P.st[u] == P.st[v] && P.mode[u] == P.mode[v]) { g = grpOf[u]; break; }
            if (g < 0) { g = G++; cap[g] = 0; gmask[g] = 0; }
            grpOf[v] = g; cap[g]++; gmask[g] |= 1u << v;
        }
        uint64_t s = 0x9E3779B97F4A7C15ULL;
        auto nx = [&] { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s * 0x2545F4914F6CDD1DULL; };
        zk.resize(P.N); for (auto& z : zk) z = nx(); for (int g = 0; g < MAXV; g++) zg[g] = nx();
        size_t ts = 1; while (ts < (size_t)maxC * 2) ts <<= 1;
        table.assign(ts, -1); tkey.assign(ts, 0); tmask = ts - 1; cols.reserve(maxC); seqs.reserve((size_t)maxC * 10);
    }
    void clear() { cols.clear(); seqs.clear(); fill(table.begin(), table.end(), -1); }
    int size() const { return cols.size(); }
    // добавить маршрут бригады v; возвращает индекс столбца или -1
    int add(int v, const int* seq, int L, double km) {
        if (L <= 0) return -1;
        adds++;
        int g = grpOf[v]; uint64_t h = zg[g], b[NW] = {0, 0, 0}; int sk = 0;
        for (int i = 0; i < L; i++) { int k = seq[i]; h ^= zk[k]; b[k >> 6] |= 1ULL << (k & 63); sk |= 1 << P.skill[k]; }
        size_t pos = h & tmask;
        while (table[pos] >= 0) {
            if (tkey[pos] != h) { pos = (pos + 1) & tmask; continue; }
            int ci = table[pos]; Col& c = cols[ci];
            if (c.grp == g && c.bits[0] == b[0] && c.bits[1] == b[1] && c.bits[2] == b[2]) {
                if (km < c.km - 1e-9) { c.km = km; memcpy(&seqs[c.off], seq, L * sizeof(int)); impr++; }
                return ci;
            }
            pos = (pos + 1) & tmask;
        }
        if ((int)cols.size() >= maxCols) return -1;
        Col c; c.grp = g; c.key = h; memcpy(c.bits, b, sizeof b); c.km = km; c.len = L; c.off = seqs.size();
        c.vmask = 0; for (int u = 0; u < P.V; u++) if ((gmask[g] >> u & 1) && (P.mask[u] & sk) == sk) c.vmask |= 1u << u;
        seqs.insert(seqs.end(), seq, seq + L);
        table[pos] = cols.size(); tkey[pos] = h; cols.push_back(c); news++;
        return cols.size() - 1;
    }
    // найти столбец группы g с множеством заявок b (h — XOR zk по заявкам, без группы)
    int find(int g, uint64_t h, const uint64_t* b) const {
        h ^= zg[g]; size_t pos = h & tmask;
        while (table[pos] >= 0) {
            if (tkey[pos] != h) { pos = (pos + 1) & tmask; continue; }
            int ci = table[pos]; const Col& c = cols[ci];
            if (c.grp == g && c.bits[0] == b[0] && c.bits[1] == b[1] && c.bits[2] == b[2]) return ci;
            pos = (pos + 1) & tmask;
        }
        return -1;
    }
    void addSol(const Sol& s) { for (int v = 0; v < s.V; v++) if (s.len[v]) add(v, s.r[v], s.len[v], s.km[v]); }
    const int* seq(int ci) const { return &seqs[cols[ci].off]; }
};

struct SPStats { int cols = 0, surv = 0, sgIt = 0; long nodes = 0; double lb = 0, ub = 0, res = 0, ms = 0; bool opt = false, imp = false; };

struct Solver {
    Pool& PL; const Prob& P;
    int maxSg = (int)l2::P("SGIT", 300); long maxNodes = (long)l2::P("SPNODES", 2000000);
    vector<double> u, bu, rc, g; vector<int> cnt;
    // DFS
    vector<int> surv; vector<vector<int>> byOrd; vector<int> ordRank; vector<double> Pi;
    vector<vector<double>> grpNeg;   // по группе: отсортированные отрицательные rc выживших
    double UB; int K; long nodes; bool aborted; double tLimAbs; const Timer* tm;
    int chosen[MAXV], nch; uint32_t chMask[MAXV]; int matchOf[MAXV]; int grpUsed[MAXV];
    vector<int> bestSel; int bestMatch[MAXV]; int bestN;
    uint64_t cov[NW]; bool skipped[l2::MAXN]; vector<int> bestSkip; vector<int> skipList;
    Solver(Pool& pl) : PL(pl), P(pl.P) {}

    bool augment(int slot, uint32_t& vis) {
        uint32_t m = chMask[slot] & ~vis;
        while (m) { int v = __builtin_ctz(m); m &= m - 1; vis |= 1u << v;
            if (matchOf[v] < 0 || augment(matchOf[v], vis)) { matchOf[v] = slot; return true; } }
        return false;
    }
    double zrem(int krem) {
        // лучший выбор ≤krem отрицательных rc с учётом остатка ёмкости групп (жадно по слиянию голов)
        if (krem <= 0) return 0;
        int head[MAXV]; for (int q = 0; q < PL.G; q++) head[q] = 0;
        double z = 0;
        for (int t = 0; t < krem; t++) {
            int bq = -1; double bv = 0;
            for (int q = 0; q < PL.G; q++) if (head[q] < PL.cap[q] - grpUsed[q] && head[q] < (int)grpNeg[q].size() && grpNeg[q][head[q]] < bv) { bv = grpNeg[q][head[q]]; bq = q; }
            if (bq < 0) break; z += bv; head[bq]++;
        }
        return z;
    }
    void dfs(double kmSo, double sumU) {
        if (aborted) return;
        if (++nodes > maxNodes || ((nodes & 1023) == 0 && tm && tm->sec() > tLimAbs)) { aborted = true; return; }
        double bound = kmSo + sumU + zrem(K - nch);
        if (bound >= UB - 1e-6) return;
        // выбрать непокрытую заявку с наименьшим числом столбцов
        int bi = -1;
        for (int r = 0; r < P.N; r++) { int i = ordRank[r]; if (!(cov[i >> 6] >> (i & 63) & 1)) { bi = i; break; } }
        if (bi < 0) {
            UB = kmSo; bestN = nch; bestSel.assign(chosen, chosen + nch); memcpy(bestMatch, matchOf, sizeof matchOf); bestSkip = skipList;
            return;
        }
        double ui = min(u[bi], Pi[bi]);
        if (nch < K) for (int ci : byOrd[bi]) {
            const Col& c = PL.cols[ci];
            if ((c.bits[0] & cov[0]) | (c.bits[1] & cov[1]) | (c.bits[2] & cov[2])) continue;
            if (grpUsed[c.grp] >= PL.cap[c.grp]) continue;
            int saveM[MAXV]; memcpy(saveM, matchOf, sizeof matchOf);
            chosen[nch] = ci; chMask[nch] = c.vmask; uint32_t vis = 0;
            if (!augment(nch, vis)) { memcpy(matchOf, saveM, sizeof matchOf); continue; }
            nch++; grpUsed[c.grp]++;
            double su = 0; const int* s = PL.seq(ci); for (int q = 0; q < c.len; q++) su += min(u[s[q]], Pi[s[q]]);
            for (int w = 0; w < NW; w++) cov[w] |= c.bits[w];
            dfs(kmSo + c.km, sumU - su);
            for (int w = 0; w < NW; w++) cov[w] &= ~c.bits[w];
            nch--; grpUsed[c.grp]--; memcpy(matchOf, saveM, sizeof matchOf);
            if (aborted) return;
        }
        if (Pi[bi] < 1e15) {   // пропуск заявки
            cov[bi >> 6] |= 1ULL << (bi & 63); skipList.push_back(bi);
            dfs(kmSo + Pi[bi], sumU - ui);
            skipList.pop_back(); cov[bi >> 6] &= ~(1ULL << (bi & 63));
        }
    }

    // inc: маршруты текущего решения (в пуле) с бригадами; K — предел числа маршрутов; skipPen — штраф пропуска
    // (в единицах км; например pen*1e6). Возвращает true и out (routes по бригадам), если найдено лучше inc.
    bool solve(const Sol& inc, int Kmax, double tLimSec, const Timer& timer, Routes& out, SPStats& st, double penScale = 1e6) {
        Timer loc; tm = &timer; tLimAbs = timer.sec() + tLimSec;
        int N = P.N, C = PL.size(); K = Kmax;
        st = SPStats(); st.cols = C;
        Pi.assign(N, 0); for (int i = 0; i < N; i++) Pi[i] = P.pen[i] * penScale;
        // верхняя граница: столбцы текущего решения (км из пула ≤ фактических)
        vector<int> incCols; double incPen = 0;
        for (int v = 0; v < inc.V; v++) if (inc.len[v]) { int ci = PL.add(v, inc.r[v], inc.len[v], inc.km[v]); if (ci < 0) return false; incCols.push_back(ci); }
        for (int i = 0; i < N; i++) if (inc.rt[i] < 0) incPen += Pi[i];
        double UB0 = incPen + inc.kmTot();   // фактические км: улучшение последовательностей из пула тоже засчитывается
        C = PL.size(); st.cols = C;
        // начальные u: км/длина маршрута текущего решения
        u.assign(N, 0);
        for (int ci : incCols) { const Col& c = PL.cols[ci]; const int* s = PL.seq(ci); for (int q = 0; q < c.len; q++) u[s[q]] = c.km / c.len; }
        for (int i = 0; i < N; i++) if (inc.rt[i] < 0) u[i] = Pi[i];
        rc.assign(C, 0); g.assign(N, 0); cnt.assign(N, 0);
        vector<vector<pair<double, int>>> gn(PL.G); vector<pair<double, int>> all;
        double bestL = -1e300, lam = l2::P("SGLAM", 1.0); int noImp = 0; bu = u;
        auto lagr = [&](bool grad) {
            double L = 0; for (int i = 0; i < N; i++) L += u[i] + min(0.0, Pi[i] - u[i]);
            for (auto& x : gn) x.clear();
            for (int ci = 0; ci < C; ci++) {
                const Col& c = PL.cols[ci]; const int* s = PL.seq(ci); double r = c.km;
                for (int q = 0; q < c.len; q++) r -= u[s[q]];
                rc[ci] = r; if (r < 0) gn[c.grp].push_back({r, ci});
            }
            all.clear();
            for (int q = 0; q < PL.G; q++) {
                auto& x = gn[q]; int cq = PL.cap[q];
                if ((int)x.size() > cq) { nth_element(x.begin(), x.begin() + cq, x.end()); x.resize(cq); }
                all.insert(all.end(), x.begin(), x.end());
            }
            if ((int)all.size() > K) { nth_element(all.begin(), all.begin() + K, all.end()); all.resize(K); }
            for (auto& pr : all) L += pr.first;
            if (grad) {
                for (int i = 0; i < N; i++) g[i] = 1 - (Pi[i] - u[i] < 0 ? 1 : 0);
                for (auto& pr : all) { const Col& c = PL.cols[pr.second]; const int* s = PL.seq(pr.second); for (int q = 0; q < c.len; q++) g[s[q]] -= 1; }
            }
            return L;
        };
        int it = 0;
        for (; it < maxSg; it++) {
            double L = lagr(true);
            if (L > bestL + 1e-9) { if (L > bestL + 1e-4 * max(1.0, fabs(UB0 - bestL))) noImp = 0; else noImp++; bestL = L; bu = u; } else noImp++;
            if (UB0 - bestL < 1e-6) break;
            if (noImp >= 20) { lam *= 0.5; noImp = 0; if (lam < 1e-4) break; }
            double gg = 0; for (int i = 0; i < N; i++) gg += g[i] * g[i];
            if (gg < 1e-12) break;
            double stp = lam * (UB0 - L) / gg;
            for (int i = 0; i < N; i++) { u[i] += stp * g[i]; if (u[i] > Pi[i]) u[i] = Pi[i]; }
            if ((it & 15) == 0 && timer.sec() > tLimAbs) break;
        }
        st.sgIt = it; u = bu; double L = lagr(false); st.lb = L; st.ub = UB0;
        if (UB0 - L < 1e-6) { st.opt = true; st.res = UB0; st.ms = loc.sec() * 1000; return false; }
        // отсечение по приведённой стоимости
        surv.clear();
        for (int ci = 0; ci < C; ci++) if (L + rc[ci] < UB0 - 1e-6) surv.push_back(ci);
        st.surv = surv.size();
        sort(surv.begin(), surv.end(), [&](int a, int b) { return rc[a] < rc[b]; });
        byOrd.assign(N, {}); grpNeg.assign(PL.G, {});
        for (int ci : surv) { const Col& c = PL.cols[ci]; const int* s = PL.seq(ci); for (int q = 0; q < c.len; q++) byOrd[s[q]].push_back(ci); if (rc[ci] < 0) grpNeg[c.grp].push_back(rc[ci]); }
        ordRank.resize(N); iota(ordRank.begin(), ordRank.end(), 0);
        sort(ordRank.begin(), ordRank.end(), [&](int a, int b) { return byOrd[a].size() < byOrd[b].size(); });
        UB = UB0; nodes = 0; aborted = false; nch = 0; bestN = -1; skipList.clear();
        for (int v = 0; v < P.V; v++) matchOf[v] = -1;
        for (int q = 0; q < PL.G; q++) grpUsed[q] = 0;
        memset(cov, 0, sizeof cov);
        double sumU = 0; for (int i = 0; i < N; i++) sumU += min(u[i], Pi[i]);
        dfs(0, sumU);
        st.nodes = nodes; st.opt = !aborted; st.res = UB; st.ms = loc.sec() * 1000;
        if (bestN < 0) return false;
        out.assign(P.V, {});
        for (int v = 0; v < P.V; v++) { int slot = bestMatch[v]; if (slot < 0) continue; int ci = bestSel[slot]; const int* s = PL.seq(ci); out[v].assign(s, s + PL.cols[ci].len); }
        st.imp = true;
        return true;
    }
};
}  // namespace sp

namespace sp {
// ---------- SP по окрестностям: замена k маршрутов текущего решения лучшим точным разбиением их заявок ----------
// Столбец пула, чьи заявки лежат в маршрутах набора S текущего решения, имеет маску mask ⊆ S. Столбцы хранятся
// корзинами по маске, так что кандидаты для S — объединение корзин по подмаскам S (2^k поисков). Для каждого
// связного (по пересекающим столбцам) набора S из ≤maxK маршрутов — точный DFS: разбить объединение заявок S на
// столбцы (стоимость W·число + км) с назначением на свободные бригады (паросочетание по vmask). Число маршрутов
// может уменьшиться (сокращение парка). Первое улучшение применяется, индекс перестраивается.
struct Recomb {
    Pool& PL; const Prob& P; long maxNodesSub = (long)l2::P("RCNODES", 200000); int maxK = (int)l2::P("RCK", 3);
    // индекс
    int R = 0, vr[MAXV], rcol[MAXV]; int rid[l2::MAXN]; unordered_map<uint64_t, int> memo; uint32_t lastChanged = 0; long memoSkip = 0; bool useMemo = l2::P("MEMO", 1) > 0; vector<pair<uint32_t, int>> cm; vector<int> bstart, bfill; vector<uint32_t> bmask, colMask;
    int cross[MAXV][MAXV];
    // DFS
    vector<int> cand; vector<vector<int>> byOrd; double lbOrd[l2::MAXN], kmOrd[l2::MAXN]; int maxLenC = 1; uint64_t U[NW], cov[NW]; uint32_t avail;
    double UBs; long nodes; int chosen[MAXV], nch, bestSel[MAXV], bestN, matchOf[MAXV], bestMatch[MAXV]; uint32_t chMask[MAXV];
    int kLim = 0; uint64_t hRem = 0; const Timer* tmr = nullptr; double tEndAbs = 1e18; vector<int> uo; long totNodes = 0, subs = 0, imps = 0, totCand = 0, hitLim = 0, impK[8] = {0}, redV = 0; double tIdx = 0;
    Recomb(Pool& pl) : PL(pl), P(pl.P) {}
    void buildIndex(const Sol& s) {
        R = 0; for (int k = 0; k < P.N; k++) rid[k] = -1;
        for (int v = 0; v < s.V; v++) if (s.len[v]) { for (int i = 0; i < s.len[v]; i++) rid[s.r[v][i]] = R; rcol[R] = PL.add(v, s.r[v], s.len[v], s.km[v]); vr[R++] = v; }
        // корзины по маске: сортировка подсчётом (R ≤ 16)
        int NM = 1 << R; bstart.assign(NM + 1, 0); colMask.resize(PL.size());
        for (int ci = 0; ci < PL.size(); ci++) {
            const Col& c = PL.cols[ci]; const int* q = PL.seq(ci); uint32_t m = 0;
            for (int t = 0; t < c.len; t++) { int r = rid[q[t]]; if (r < 0) { m = 0; break; } m |= 1u << r; }
            colMask[ci] = m; if (m) bstart[m + 1]++;
        }
        for (int m = 0; m < NM; m++) bstart[m + 1] += bstart[m];
        cm.resize(bstart[NM]); vector<int>& fill = bfill; fill.assign(bstart.begin(), bstart.end() - 1);
        for (int ci = 0; ci < PL.size(); ci++) if (colMask[ci]) cm[fill[colMask[ci]]++] = {colMask[ci], ci};
        bmask.clear(); for (int m = 1; m < NM; m++) if (bstart[m + 1] > bstart[m]) bmask.push_back(m);
        memset(cross, 0, sizeof cross);
        for (uint32_t m : bmask) {
            if (__builtin_popcount(m) < 2) continue;
            for (int x = 0; x < R; x++) if (m >> x & 1) for (int y = 0; y < R; y++) if (y != x && (m >> y & 1)) cross[x][y] += bstart[m + 1] - bstart[m];
        }
    }

    bool augment(int slot, uint32_t& vis) {
        uint32_t m = chMask[slot] & ~vis;
        while (m) { int v = __builtin_ctz(m); m &= m - 1; vis |= 1u << v;
            if (matchOf[v] < 0 || augment(matchOf[v], vis)) { matchOf[v] = slot; return true; } }
        return false;
    }
    void dfs(double cost) {
        if (++nodes > maxNodesSub) return;
        if ((nodes & 255) == 0 && tmr && tmr->sec() > tEndAbs) { nodes = maxNodesSub + 1; return; }
        double bnd = cost, bk = 0; int bi = -1, nun = 0; size_t bc = SIZE_MAX;
        for (int i : uo) if (!(cov[i >> 6] >> (i & 63) & 1)) { nun++; bnd += lbOrd[i]; bk += kmOrd[i]; if (byOrd[i].size() < bc) { bc = byOrd[i].size(); bi = i; } }
        if (bnd >= UBs - 1e-6) return;
        if (nun && cost + W_VEHICLE * ((nun + maxLenC - 1) / maxLenC) + bk >= UBs - 1e-6) return;
        if (bi < 0) { UBs = cost; bestN = nch; memcpy(bestSel, chosen, sizeof(int) * nch); memcpy(bestMatch, matchOf, sizeof matchOf); return; }
        if (nch >= kLim) return;
        if (nch == kLim - 1) {   // последний столбец обязан совпасть с остатком: поиск по хешу в каждой группе
            uint64_t rb[NW]; for (int w = 0; w < NW; w++) rb[w] = U[w] & ~cov[w];
            for (int g = 0; g < PL.G; g++) {
                if (!(PL.gmask[g] & avail)) continue;
                int ci = PL.find(g, hRem, rb); if (ci < 0) continue; const Col& c = PL.cols[ci];
                if (cost + W_VEHICLE + c.km >= UBs - 1e-6 || !(c.vmask & avail)) continue;
                int saveM[MAXV]; memcpy(saveM, matchOf, sizeof matchOf);
                chosen[nch] = ci; chMask[nch] = c.vmask & avail; uint32_t vis = 0;
                if (augment(nch, vis)) { UBs = cost + W_VEHICLE + c.km; bestN = nch + 1; memcpy(bestSel, chosen, sizeof(int) * (nch + 1)); memcpy(bestMatch, matchOf, sizeof matchOf); }
                memcpy(matchOf, saveM, sizeof matchOf);
            }
            return;
        }
        for (int ci : byOrd[bi]) {
            const Col& c = PL.cols[ci];
            if ((c.bits[0] & cov[0]) | (c.bits[1] & cov[1]) | (c.bits[2] & cov[2])) continue;
            if (cost + W_VEHICLE + c.km >= UBs - 1e-6) break;   // список отсортирован по км
            int saveM[MAXV]; memcpy(saveM, matchOf, sizeof matchOf);
            chosen[nch] = ci; chMask[nch] = c.vmask & avail; uint32_t vis = 0;
            if (!augment(nch, vis)) { memcpy(matchOf, saveM, sizeof matchOf); continue; }
            uint64_t hc = c.key ^ PL.zg[c.grp];
            nch++; for (int w = 0; w < NW; w++) cov[w] |= c.bits[w]; hRem ^= hc;
            dfs(cost + W_VEHICLE + c.km);
            nch--; for (int w = 0; w < NW; w++) cov[w] &= ~c.bits[w]; hRem ^= hc; memcpy(matchOf, saveM, sizeof matchOf);
            if (nodes > maxNodesSub) return;
        }
    }
    // решить подзадачу для набора маршрутов S (маска по индексам R); true и применение к s при улучшении
    static uint64_t mix(uint64_t x) { x += 0x9E3779B97F4A7C15ULL; x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL; x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL; return x ^ (x >> 31); }
    bool solveSub(Sol& s, uint32_t S) {
        uint64_t mkey = 0; for (int r = 0; r < R; r++) if (S >> r & 1) mkey ^= mix((uint64_t)rcol[r] + 1);
        if (useMemo) {
            auto it = memo.find(mkey);
            if (it != memo.end()) {
                bool fresh = false;
                for (uint32_t sub = S; sub && !fresh; sub = (sub - 1) & S) if (bstart[sub + 1] > bstart[sub] && cm[bstart[sub + 1] - 1].second >= it->second) fresh = true;
                if (!fresh) { memoSkip++; return false; }
            }
        }
        subs++;
        memset(U, 0, sizeof U); double ub = 0; avail = 0;
        for (int r = 0; r < R; r++) if (S >> r & 1) { int v = vr[r]; ub += W_VEHICLE + s.km[v]; avail |= 1u << v; for (int i = 0; i < s.len[v]; i++) { int k = s.r[v][i]; U[k >> 6] |= 1ULL << (k & 63); } }
        for (int v = 0; v < P.V; v++) if (!s.len[v]) avail |= 1u << v;
        cand.clear();
        for (uint32_t sub = S; sub; sub = (sub - 1) & S) { for (int i = bstart[sub]; i < bstart[sub + 1]; i++) if (PL.cols[cm[i].second].vmask & avail) cand.push_back(cm[i].second); }
        totCand += cand.size();
        if (cand.size() <= (size_t)__builtin_popcount(S)) return false;
        uo.clear(); for (int w = 0; w < NW; w++) { uint64_t x = U[w]; while (x) { int b = __builtin_ctzll(x); x &= x - 1; uo.push_back(w * 64 + b); } }
        for (int i : uo) { byOrd[i].clear(); lbOrd[i] = 1e18; kmOrd[i] = 1e18; }
        maxLenC = 1;
        sort(cand.begin(), cand.end(), [&](int a, int b) { return PL.cols[a].km < PL.cols[b].km; });
        for (int ci : cand) { const Col& c = PL.cols[ci]; const int* q = PL.seq(ci); double sh = (W_VEHICLE + c.km) / c.len;
            double sk = c.km / c.len; maxLenC = max(maxLenC, c.len);
            for (int t = 0; t < c.len; t++) { byOrd[q[t]].push_back(ci); lbOrd[q[t]] = min(lbOrd[q[t]], sh); kmOrd[q[t]] = min(kmOrd[q[t]], sk); } }
        kLim = __builtin_popcount(S); hRem = 0; for (int i : uo) hRem ^= PL.zk[i];
        UBs = ub; nodes = 0; nch = 0; bestN = -1; memset(cov, 0, sizeof cov); for (int v = 0; v < P.V; v++) matchOf[v] = -1;
        dfs(0); totNodes += nodes; if (nodes > maxNodesSub) hitLim++;
        if (bestN < 0) { if (nodes <= maxNodesSub) memo[mkey] = PL.size(); return false; }
        lastChanged = 0; for (int r = 0; r < R; r++) if (S >> r & 1) lastChanged |= 1u << vr[r];
        for (int v = 0; v < P.V; v++) if (bestMatch[v] >= 0) lastChanged |= 1u << v;
        // применить
        for (int r = 0; r < R; r++) if (S >> r & 1) { int v = vr[r]; s.len[v] = 0; s.km[v] = 0; }
        for (int v = 0; v < P.V; v++) { int sl = bestMatch[v]; if (sl < 0) continue; int ci = bestSel[sl]; const Col& c = PL.cols[ci];
            s.len[v] = c.len; memcpy(s.r[v], PL.seq(ci), sizeof(int) * c.len); }
        for (int v = 0; v < P.V; v++) s.rebuild(P, v);
        for (int v = 0; v < P.V; v++) for (int i = 0; i < s.len[v]; i++) s.rt[s.r[v][i]] = v;
        imps++; impK[min(7, __builtin_popcount(S))]++; if (bestN < __builtin_popcount(S)) redV++;
        return true;
    }
    // глобальная подзадача: все маршруты s заменяются столбцами из явного списка (элита), последний — по хешу из пула
    bool solveWith(Sol& s, const vector<int>& list, const Timer& tm, double tEnd) {
        buildIndex(s); byOrd.resize(P.N); tmr = &tm; tEndAbs = tEnd;
        uint32_t S = R >= 32 ? ~0u : ((1u << R) - 1);
        memset(U, 0, sizeof U); double ub = 0; avail = 0;
        for (int r = 0; r < R; r++) { int v = vr[r]; ub += W_VEHICLE + s.km[v]; avail |= 1u << v; for (int i = 0; i < s.len[v]; i++) { int k = s.r[v][i]; U[k >> 6] |= 1ULL << (k & 63); } }
        for (int v = 0; v < P.V; v++) if (!s.len[v]) avail |= 1u << v;
        cand.clear(); for (int ci : list) if ((PL.cols[ci].vmask & avail) && colMask[ci]) cand.push_back(ci);
        sort(cand.begin(), cand.end()); cand.erase(unique(cand.begin(), cand.end()), cand.end());
        uo.clear(); for (int w = 0; w < NW; w++) { uint64_t x = U[w]; while (x) { int b = __builtin_ctzll(x); x &= x - 1; uo.push_back(w * 64 + b); } }
        for (int i : uo) { byOrd[i].clear(); lbOrd[i] = 1e18; kmOrd[i] = 1e18; }
        maxLenC = 1;
        sort(cand.begin(), cand.end(), [&](int a, int b) { return PL.cols[a].km < PL.cols[b].km; });
        for (int ci : cand) { const Col& c = PL.cols[ci]; const int* q = PL.seq(ci); double sh = (W_VEHICLE + c.km) / c.len; double sk = c.km / c.len; maxLenC = max(maxLenC, c.len);
            for (int t = 0; t < c.len; t++) { byOrd[q[t]].push_back(ci); lbOrd[q[t]] = min(lbOrd[q[t]], sh); kmOrd[q[t]] = min(kmOrd[q[t]], sk); } }
        for (int i : uo) if (byOrd[i].empty()) lbOrd[i] = kmOrd[i] = 0;   // покрыть можно только последним столбцом
        kLim = R; hRem = 0; for (int i : uo) hRem ^= PL.zk[i];
        UBs = ub; nodes = 0; nch = 0; bestN = -1; memset(cov, 0, sizeof cov); for (int v = 0; v < P.V; v++) matchOf[v] = -1;
        long save = maxNodesSub; maxNodesSub = (long)l2::P("ELNODES", 2000000);
        dfs(0); totNodes += nodes; maxNodesSub = save; eliteNodes = nodes;
        if (bestN < 0) return false;
        for (int r = 0; r < R; r++) { int v = vr[r]; s.len[v] = 0; s.km[v] = 0; }
        for (int v = 0; v < P.V; v++) { int sl = bestMatch[v]; if (sl < 0) continue; int ci = bestSel[sl]; const Col& c = PL.cols[ci];
            s.len[v] = c.len; memcpy(s.r[v], PL.seq(ci), sizeof(int) * c.len); }
        for (int v = 0; v < P.V; v++) s.rebuild(P, v);
        for (int v = 0; v < P.V; v++) for (int i = 0; i < s.len[v]; i++) s.rt[s.r[v][i]] = v;
        (void)S; return true;
    }
    long eliteNodes = 0;
    bool connected(uint32_t S) const {
        int f = __builtin_ctz(S); uint32_t seen = 1u << f, st = seen;
        while (st) { int x = __builtin_ctz(st); st &= st - 1; for (int y = 0; y < R; y++) if ((S >> y & 1) && !(seen >> y & 1) && cross[x][y]) { seen |= 1u << y; st |= 1u << y; } }
        return seen == S;
    }
    // улучшать s, пока находятся улучшения (или до tEnd). Возвращает число улучшений.
    int run(Sol& s, const Timer& tm, double tEnd) {
        byOrd.resize(P.N); tmr = &tm; tEndAbs = tEnd;
        int nimp = 0;
        for (int pass = 0; pass < 50; pass++) {
            { Timer ti; buildIndex(s); tIdx += ti.sec(); } bool imp = false; uint32_t dirty = 0;
            for (int k = 1; k <= min(maxK, R); k++) {
                // перебор наборов размера k (комбинации); наборы с изменёнными в этом проходе маршрутами — в следующем
                int idx[MAXV]; for (int i = 0; i < k; i++) idx[i] = i;
                while (true) {
                    uint32_t S = 0; bool dr = false; for (int i = 0; i < k; i++) { S |= 1u << idx[i]; if (dirty >> vr[idx[i]] & 1) dr = true; }
                    if (!dr && (k == 1 || connected(S)) && solveSub(s, S)) { imp = true; nimp++; dirty |= lastChanged; }
                    if (tm.sec() > tEnd) return nimp;
                    int i = k - 1; while (i >= 0 && idx[i] == R - k + i) i--;
                    if (i < 0) break;
                    idx[i]++; for (int j = i + 1; j < k; j++) idx[j] = idx[j - 1] + 1;
                }
            }
            if (!imp) break;
        }
        return nimp;
    }
};
}  // namespace sp

namespace sp {
// ---------- LP-релаксация SP (пересмотренный симплекс, явная B^-1) + генерация столбцов из пула + ныряние ----------
// Строки: N покрытий (=1), G групп (≤cap_g), 1 кардинальность (≤K). Столбцы: маршруты пула, пропуски (цена Pskip),
// слабые переменные групп/кардинальности. Старт: базис из пропусков и слабых (B = I).
struct LPSP {
    Pool& PL; const Prob& P; int N, G, m;
    double Pskip = l2::P("PSKIP", 1e4);
    // локальные столбцы: j < N — пропуск строки j, N ≤ j < m — слабая строки j, дальше — маршруты (индекс пула)
    vector<int> colPool; vector<double> cost; vector<char> isBasic;
    vector<double> Binv, xB, y, d; vector<int> basis; vector<double> rhs;
    vector<char> rowOn;   // строки, участвующие в задаче (при нырянии покрытые строки выключаются)
    long pivots = 0; int degRun = 0;
    LPSP(Pool& pl) : PL(pl), P(pl.P) {}
    inline int nnzRows(int j, int* rows) const {   // строки столбца j
        if (j < m) { rows[0] = j; return 1; }
        const Col& c = PL.cols[colPool[j - m]]; const int* s = PL.seq(colPool[j - m]); int n = 0;
        for (int q = 0; q < c.len; q++) rows[n++] = s[q];
        rows[n++] = N + c.grp; rows[n++] = N + G; return n;
    }
    void init(int K, const vector<double>& capRem, const vector<char>& on) {
        N = P.N; G = PL.G; m = N + G + 1;
        colPool.clear(); cost.assign(m, 0); isBasic.assign(m, 1);
        for (int j = 0; j < N; j++) cost[j] = Pskip * P.pen[j] / 20;
        rowOn = on;
        rhs.assign(m, 0); for (int i = 0; i < N; i++) rhs[i] = on[i] ? 1 : 0;
        for (int g = 0; g < G; g++) rhs[N + g] = capRem[g]; rhs[N + G] = K;
        Binv.assign((size_t)m * m, 0); for (int i = 0; i < m; i++) Binv[(size_t)i * m + i] = 1;
        basis.resize(m); for (int i = 0; i < m; i++) basis[i] = i;
        xB = rhs; y.assign(m, 0); d.assign(m, 0);
    }
    int addCol(int ci) { colPool.push_back(ci); const Col& c = PL.cols[ci]; cost.push_back(c.km); isBasic.push_back(0); return cost.size() - 1; }
    void computeY() {
        fill(y.begin(), y.end(), 0.0);
        for (int r = 0; r < m; r++) { double cb = cost[basis[r]]; if (cb == 0) continue; const double* row = &Binv[(size_t)r * m]; for (int k = 0; k < m; k++) y[k] += cb * row[k]; }
    }
    double rcOf(int j) const { int rows[l2::MAXL + 4]; int n = nnzRows(j, rows); double r = cost[j]; for (int q = 0; q < n; q++) r -= y[rows[q]]; return r; }
    double obj() const { double s = 0; for (int r = 0; r < m; r++) s += cost[basis[r]] * xB[r]; return s; }
    void refactor() {
        // Гаусс–Жордан по матрице базиса
        vector<double> Bm((size_t)m * m, 0); int rows[l2::MAXL + 4];
        for (int r = 0; r < m; r++) { int n = nnzRows(basis[r], rows); for (int q = 0; q < n; q++) Bm[(size_t)rows[q] * m + r] = 1; }
        vector<double>& A = Bm; vector<double> Inv((size_t)m * m, 0); for (int i = 0; i < m; i++) Inv[(size_t)i * m + i] = 1;
        for (int c = 0; c < m; c++) {
            int pr = c; double bv = fabs(A[(size_t)c * m + c]);
            for (int r = c + 1; r < m; r++) if (fabs(A[(size_t)r * m + c]) > bv) { bv = fabs(A[(size_t)r * m + c]); pr = r; }
            if (bv < 1e-12) return;   // вырождение — оставляем старую B^-1
            if (pr != c) for (int k = 0; k < m; k++) { swap(A[(size_t)pr * m + k], A[(size_t)c * m + k]); swap(Inv[(size_t)pr * m + k], Inv[(size_t)c * m + k]); }
            double iv = 1 / A[(size_t)c * m + c];
            for (int k = 0; k < m; k++) { A[(size_t)c * m + k] *= iv; Inv[(size_t)c * m + k] *= iv; }
            for (int r = 0; r < m; r++) if (r != c) { double f = A[(size_t)r * m + c]; if (f == 0) continue;
                for (int k = 0; k < m; k++) { A[(size_t)r * m + k] -= f * A[(size_t)c * m + k]; Inv[(size_t)r * m + k] -= f * Inv[(size_t)c * m + k]; } }
        }
        // Inv = B^-1 (строки — позиции базиса)
        Binv.swap(Inv);
        for (int r = 0; r < m; r++) { double s = 0; const double* row = &Binv[(size_t)r * m]; for (int k = 0; k < m; k++) s += row[k] * rhs[k]; xB[r] = s; }
    }
    // прямой симплекс до оптимума (или лимита); возвращает false по времени
    bool simplex(const Timer& tm, double tEnd, int maxPiv = 1 << 30) {
        int rows[l2::MAXL + 4]; int sinceRef = 0;
        for (int it = 0; it < maxPiv; it++) {
            if ((it & 31) == 0 && tm.sec() > tEnd) return false;
            computeY();
            bool bland = degRun > 30;
            int ent = -1; double best = -1e-7;
            for (int j = 0; j < (int)cost.size(); j++) {
                if (isBasic[j]) continue;
                if (j < N && !rowOn[j]) continue;
                double r = rcOf(j);
                if (r < best) { ent = j; if (bland) break; best = r; }
            }
            if (ent < 0) return true;
            int n = nnzRows(ent, rows);
            for (int r = 0; r < m; r++) { double s = 0; const double* row = &Binv[(size_t)r * m]; for (int q = 0; q < n; q++) s += row[rows[q]]; d[r] = s; }
            int lv = -1; double th = 1e300;
            for (int r = 0; r < m; r++) if (d[r] > 1e-9) {
                double t = max(0.0, xB[r]) / d[r];
                if (t < th - 1e-12 || (t < th + 1e-12 && lv >= 0 && (bland ? basis[r] < basis[lv] : d[r] > d[lv]))) { th = t; lv = r; }
            }
            if (lv < 0) return true;   // неограниченность (не бывает)
            degRun = th < 1e-12 ? degRun + 1 : 0;
            double piv = d[lv]; double* prow = &Binv[(size_t)lv * m];
            for (int k = 0; k < m; k++) prow[k] /= piv;
            xB[lv] = th;
            for (int r = 0; r < m; r++) if (r != lv) {
                double f = d[r]; if (fabs(f) < 1e-14) continue;
                double* row = &Binv[(size_t)r * m]; for (int k = 0; k < m; k++) row[k] -= f * prow[k];
                xB[r] -= f * th; if (xB[r] < 0 && xB[r] > -1e-9) xB[r] = 0;
            }
            isBasic[basis[lv]] = 0; basis[lv] = ent; isBasic[ent] = 1; pivots++;
            if (++sinceRef >= 150) { refactor(); sinceRef = 0; }
        }
        return true;
    }
};
}  // namespace sp

namespace sp {
// Глобальный SP: ЛП по пулу генерацией столбцов → отбор M столбцов с наименьшей rc → ныряние (фиксация столбца с
// наибольшим x, пересчёт ЛП на оставшихся строках) → проверка назначения бригад паросочетанием.
struct GSP {
    Pool& PL; const Prob& P; LPSP lp;
    int M = (int)l2::P("GSPM", 1000), cgAdd = (int)l2::P("CGADD", 200), dives = (int)l2::P("DIVES", 3);
    double lb = 0, lpMs = 0, diveMs = 0; int cgRounds = 0, nCand = 0; long piv = 0;
    GSP(Pool& pl) : PL(pl), P(pl.P), lp(pl) {}
    vector<double> rcAll; vector<int> cand; bool dbg = getenv("GDBG") != nullptr;
    double gap = 0, UBd = 0, tEndD = 0; long nRcGap = 0, nodes = 0, maxNodes = (long)l2::P("GNODES", 3000000); bool aborted = false, found = false; const Timer* tmD = nullptr;
    vector<vector<int>> byOrd; vector<double> minRc; vector<int> rank; int Kl = 0, nch = 0, chosen[MAXV], bestSel[MAXV], bestN = 0, matchOf[MAXV], bestMatch[MAXV]; uint32_t chMask[MAXV];
    uint64_t cov[NW], hRem = 0;
    bool augment(int slot, uint32_t& vis) {
        uint32_t mk = chMask[slot] & ~vis;
        while (mk) { int v = __builtin_ctz(mk); mk &= mk - 1; vis |= 1u << v;
            if (matchOf[v] < 0 || augment(matchOf[v], vis)) { matchOf[v] = slot; return true; } }
        return false;
    }
    bool tryPush(int ci) {
        int saveM[MAXV]; memcpy(saveM, matchOf, sizeof matchOf);
        chosen[nch] = ci; chMask[nch] = PL.cols[ci].vmask; uint32_t vis = 0;
        if (!augment(nch, vis)) { memcpy(matchOf, saveM, sizeof matchOf); return false; }
        return true;
    }
    void record(double km, int n) { UBd = km; found = true; bestN = n; memcpy(bestSel, chosen, sizeof(int) * n); memcpy(bestMatch, matchOf, sizeof matchOf); gap = UBd - lb; }
    void dfs(double rcSum, double km) {
        if (aborted) return;
        if (++nodes > maxNodes || ((nodes & 1023) == 0 && tmD->sec() > tEndD)) { aborted = true; return; }
        int bi = -1; double mx = 0;
        for (int r = 0; r < P.N; r++) { int i = rank[r]; if (!(cov[i >> 6] >> (i & 63) & 1)) { if (bi < 0) bi = i; mx = max(mx, minRc[i]); } }
        if (bi < 0) { if (km < UBd - 1e-6) record(km, nch); return; }
        if (rcSum + mx >= gap - 1e-9) return;
        if (nch >= Kl) return;
        if (nch == Kl - 1) {   // последний столбец — остаток целиком (любой столбец пула)
            uint64_t rb[NW]; for (int w = 0; w < NW; w++) rb[w] = ~cov[w];
            // обрезать биты за N
            for (int w = 0; w < NW; w++) { int lo = w * 64; if (lo >= P.N) rb[w] = 0; else if (lo + 64 > P.N) rb[w] &= (P.N - lo == 64 ? ~0ULL : ((1ULL << (P.N - lo)) - 1)); }
            for (int g = 0; g < PL.G; g++) {
                int ci = PL.find(g, hRem, rb); if (ci < 0) continue;
                if (km + PL.cols[ci].km >= UBd - 1e-6) continue;
                if (tryPush(ci)) { record(km + PL.cols[ci].km, nch + 1); memcpy(matchOf, matchOf, 0); }
                // откат паросочетания: пересобрать из выбранных
                for (int v = 0; v < P.V; v++) matchOf[v] = -1;
                for (int s2 = 0; s2 < nch; s2++) { uint32_t vis = 0; augment(s2, vis); }
            }
            return;
        }
        for (int ci : byOrd[bi]) {
            double r = rcAll[ci]; if (rcSum + r >= gap - 1e-9) break;
            const Col& c = PL.cols[ci];
            if ((c.bits[0] & cov[0]) | (c.bits[1] & cov[1]) | (c.bits[2] & cov[2])) continue;
            int saveM[MAXV]; memcpy(saveM, matchOf, sizeof matchOf);
            if (!tryPush(ci)) continue;
            uint64_t hc = c.key ^ PL.zg[c.grp];
            nch++; for (int w = 0; w < NW; w++) cov[w] |= c.bits[w]; hRem ^= hc;
            dfs(rcSum + r, km + c.km);
            nch--; for (int w = 0; w < NW; w++) cov[w] &= ~c.bits[w]; hRem ^= hc; memcpy(matchOf, saveM, sizeof matchOf);
            if (aborted) return;
        }
    }
    bool matchOK(const vector<int>& sel, int* veh) {
        int matchOf[MAXV]; for (int v = 0; v < P.V; v++) matchOf[v] = -1;
        function<bool(int, uint32_t&)> aug = [&](int s, uint32_t& vis) {
            uint32_t mk = PL.cols[sel[s]].vmask & ~vis;
            while (mk) { int v = __builtin_ctz(mk); mk &= mk - 1; vis |= 1u << v; if (matchOf[v] < 0 || aug(matchOf[v], vis)) { matchOf[v] = s; return true; } }
            return false;
        };
        for (int s = 0; s < (int)sel.size(); s++) { uint32_t vis = 0; if (!aug(s, vis)) return false; }
        for (int v = 0; v < P.V; v++) if (matchOf[v] >= 0) veh[matchOf[v]] = v;
        return true;
    }
    bool solve(const Sol& inc, const Timer& tm, double tEnd, Routes& out) {
        int N = P.N, G = PL.G, K = inc.used();
        vector<int> incCols;
        for (int v = 0; v < inc.V; v++) if (inc.len[v]) { int ci = PL.add(v, inc.r[v], inc.len[v], inc.km[v]); if (ci < 0) return false; incCols.push_back(ci); }
        if (inc.nUn()) return false;   // только при полном обслуживании
        double UB = inc.kmTot();
        Timer t0;
        vector<double> cap(G); for (int g = 0; g < G; g++) cap[g] = PL.cap[g];
        vector<char> on(N, 1);
        lp.init(K, cap, on); vector<char> inR(PL.size(), 0);
        for (int ci : incCols) if (!inR[ci]) { inR[ci] = 1; lp.addCol(ci); }
        cgRounds = 0;
        rcAll.assign(PL.size(), 0);
        while (true) {
            if (!lp.simplex(tm, tEnd)) return false;
            cgRounds++;
            lp.computeY();
            vector<pair<double, int>> neg;
            for (int ci = 0; ci < PL.size(); ci++) {
                const Col& c = PL.cols[ci]; const int* s = PL.seq(ci); double r = c.km - lp.y[N + c.grp] - lp.y[N + G];
                for (int q = 0; q < c.len; q++) r -= lp.y[s[q]];
                rcAll[ci] = r; if (r < -1e-6 && !inR[ci]) neg.push_back({r, ci});
            }
            if (neg.empty()) break;
            if ((int)neg.size() > cgAdd) { nth_element(neg.begin(), neg.begin() + cgAdd, neg.end()); neg.resize(cgAdd); }
            for (auto& pr : neg) { inR[pr.second] = 1; lp.addCol(pr.second); }
            if (tm.sec() > tEnd) return false;
        }
        lb = lp.obj(); piv = lp.pivots; lpMs = t0.sec() * 1000;
        if (dbg) { int n1 = 0, nf = 0, ns = 0; double sf = 0; for (int r = 0; r < lp.m; r++) { int j = lp.basis[r]; double x = lp.xB[r]; if (j >= lp.m) { if (x > 1 - 1e-6) n1++; else if (x > 1e-6) { nf++; sf += x; } } else if (j < N && x > 1e-6) ns++; }
            fprintf(stderr, " LP x=1: %d, frac: %d (sum %.2f), skip>0: %d\n", n1, nf, sf, ns); }
        if (UB - lb < 1e-6) return false;
        // отбор кандидатов
        vector<int> idx(PL.size()); iota(idx.begin(), idx.end(), 0);
        int Mx = min((int)idx.size(), M);
        nth_element(idx.begin(), idx.begin() + Mx - 1, idx.end(), [&](int a, int b) { return rcAll[a] < rcAll[b]; });
        cand.assign(idx.begin(), idx.begin() + Mx);
        for (int ci : incCols) if (find(cand.begin(), cand.end(), ci) == cand.end()) cand.push_back(ci);
        nCand = cand.size();
        // точный DFS по столбцам с rc < UB − LB (ограничение по сумме rc), последний столбец — поиск по хешу в пуле
        Timer t1;
        if (getenv("GSPSAN")) UB += 0.5;
        gap = UB - lb; nRcGap = 0; for (int ci = 0; ci < PL.size(); ci++) if (rcAll[ci] < gap) nRcGap++;
        {
            vector<int> c2; for (int ci : cand) if (rcAll[ci] < gap) c2.push_back(ci); cand.swap(c2);
        }
        sort(cand.begin(), cand.end(), [&](int a, int b) { return rcAll[a] < rcAll[b]; });
        byOrd.assign(N, {}); minRc.assign(N, 1e18);
        for (int ci : cand) { const Col& c = PL.cols[ci]; const int* s = PL.seq(ci); for (int q = 0; q < c.len; q++) { byOrd[s[q]].push_back(ci); minRc[s[q]] = min(minRc[s[q]], rcAll[ci]); } }
        rank.resize(N); iota(rank.begin(), rank.end(), 0);
        sort(rank.begin(), rank.end(), [&](int a, int b) { return byOrd[a].size() < byOrd[b].size(); });
        Kl = K; UBd = UB; found = false; nodes = 0; aborted = false; tEndD = tEnd; tmD = &tm; nch = 0; hRem = 0;
        for (int i = 0; i < N; i++) hRem ^= PL.zk[i];
        memset(cov, 0, sizeof cov); for (int v = 0; v < P.V; v++) matchOf[v] = -1;
        dfs(0, 0);
        bool found2 = found;
        if (found2) {
            out.assign(P.V, {});
            for (int v = 0; v < P.V; v++) { int sl = bestMatch[v]; if (sl < 0) continue; int ci = bestSel[sl]; const int* q = PL.seq(ci); out[v].assign(q, q + PL.cols[ci].len); }
        }
        bool found = found2;
        diveMs = t1.sec() * 1000;
        if (dbg) fprintf(stderr, " GSP dfs: gap=%.2f nRcGap=%ld cand=%d nodes=%ld aborted=%d found=%d\n", UB - lb, nRcGap, (int)cand.size(), nodes, aborted, found);
        return found;
    }
};
}  // namespace sp
