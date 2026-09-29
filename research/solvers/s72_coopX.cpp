// 72. s64 + роль X: км-поиск с пулом маршрутов и рекомбинацией (как в s71: SISR-отжиг, каждый затронутый маршрут —
// в пул; раз в XSPI·T — sp::Recomb, замена ≤3 маршрутов точным разбиением по пулу). Пул дополнительно пополняется
// всеми решениями, которые предлагают в общий котёл остальные потоки, — так соединяются маршруты разных линий поиска.
// Ниже описание s64: 64. s60 + роль H: поток ищет на копии задачи, где у каждой дальней бригады своя матрица км со штрафом HPEN× за
// заявки вне её домашнего кластера (время не меняется); решения идут в общий котёл с оценкой по настоящей задаче.
// Ниже описание s63: 63. s60 + роль G: поток ищет на копии задачи со штрафом GPEN× за км между кластерами (кластер = ближайшая
// стартовая точка), предлагает решения в общий котёл (оценка по настоящей задаче), чужие решения не забирает.
// Ниже описание s60: 60. Параллельный кооперативный поиск (островная модель в одном процессе, pthread).
// Потоки с разными ролями делят одно хранилище лучшего решения (coop_util.hpp):
//  A — отжиг s10 (починка + удаление маршрутов) → R&R FleetRR до AEND·T, затем км (SISR);
//  R — построение regret/жадное → FleetRR (другая стратегия сокращения парка) до REND·T, затем км (SISR);
//  K — км SISR-отжиг от общего лучшего; при улучшении парка другим потоком — обрыв и рестарт от нового лучшего;
//  S — км ходами отжига s10 от общего лучшего (другое соседство), так же с рестартами.
//  Z — s23_sa3 целиком (SA2: отжиг, оценка снизу, смена набора бригад, венгерское переназначение) как остров.
// Потоки парка подхватывают чужой меньший парк (продолжают удаление от него). Миграция в фазе км (MIG): остров,
// отставший от общего лучшего больше чем на MTH, забирает общий лучший. Нижняя граница по нагрузке: достигнута —
// потоки парка сразу уходят в км. Роли — строка ROLES (по умолчанию ARKZ: 4 потока; для 2 потоков ROLES=ZR),
// AFTER=SK: после фазы парка A уходит в км-отжиг (S), R — в км SISR (K). Итог и журнал: logs/coop.md.
#include "coop_util.hpp"
#include "sp_util.hpp"
#include <map>

static double P(const char* n, double d) { const char* e = getenv(n); return e ? atof(e) : d; }
static string PS(const char* n, const char* d) { const char* e = getenv(n); return e ? string(e) : string(d); }

static int workLB(const Instance& I) {
    double tot = 0;
    for (int k = 0; k < I.N; k++) {
        double mi = 1e18;
        for (int v = 0; v < I.V; v++) {
            if (!I.can(v, k)) continue;
            mi = min(mi, I.t(v, I.veh[v].start, I.node(k)));
            for (int j = 0; j < I.N; j++) if (j != k) mi = min(mi, I.t(v, I.node(j), I.node(k)));
        }
        if (mi < 1e17) tot += I.ord[k].svc + mi;
    }
    return (int)ceil(tot / SHIFT - 1e-9);
}

// км-поиск с пулом (копия из s71_racesp.cpp)
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


struct Coop {
    const Instance& I; double tl; uint64_t seed; Timer& tm;
    sau::Fast F; l2::Prob Pb; coop::Shared sh; int LB;
    double mig = P("MIG", 1), mth = P("MTH", 0.003), migGap = P("MGAP", 0.05);
    std::atomic<bool> fleetDone{false}; bool trace = P("TRACE", 0) > 0;
    Coop(const Instance& in, double t, uint64_t s, Timer& m) : I(in), tl(t), seed(s), tm(m), F(in), Pb(in), sh(in), LB(workLB(in)) {}

    std::mutex qM; vector<Routes> offQ; bool hasX = false;
    bool offer(const Routes& R, int who) {
        if (hasX) { std::lock_guard<std::mutex> lk(qM); if (offQ.size() < 512) offQ.push_back(R); }
        bool ok = sh.offer(R, who, tm.sec());
        if (ok && trace) fprintf(stderr, "T %.3f %d %lld %.2f\n", tm.sec(), who, sh.fleetKey.load(), fmod(sh.bestScalar.load(), 1e4));
        if (ok && sh.fleetKey.load() <= LB) fleetDone = true;   // штраф 0 и бригад не больше оценки снизу
        return ok;
    }
    long long keyOf(const Routes& R) { return coop::fleetKeyOf(evaluate(I, R)); }

    // ---- стартовая точка км-острова: общий лучший (DIV=0) / лучший из элиты по набору бригад (DIV=1) /
    //      общий лучший с исключением случайной бригады и перевставкой её заявок regret-2 (DIV=2) ----
    int div = (int)P("DIV", 0); std::atomic<int> kmSlots{0};
    void startPoint(int slot, Rng& rng, Routes& R, l2::Prob& P2, int restarts) {
        sh.get(R);
        if (div == 0 || slot == 0) return;
        if (div == 1) { Routes E; if (sh.getEliteIdx(E, slot)) R = E; return; }
        if (div == 2) {
            l2::Sol s; s.fromRoutes(Pb, R); int used = s.used();
            if (s.nUn() || used < 2) return;
            int us[l2::MAXV], n = 0; for (int v = 0; v < Pb.V; v++) if (s.len[v]) us[n++] = v;
            int u = us[rng.randint(n)];
            for (int k = 0; k < Pb.N; k++) P2.canMask[k] = Pb.canMask[k] & ~(1 << u);
            l2::Sol c; c.fromRoutes(P2, R);
            vector<int> pool; l2::collectAbsent(c, pool); l2::Regret RG;
            for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
            RG.run(P2, c, pool, rng, 2, used, W_VEHICLE, nullptr);
            for (int k = 0; k < Pb.N; k++) P2.canMask[k] = Pb.canMask[k];
            if (pool.empty() && c.used() <= used) R = c.routes();
        }
    }
    // ---- км-работник SISR: от общего лучшего до tEnd, рестарт при улучшении парка ----
    void kmSisr(int id, Rng& rng, double tEnd) {
        coop2::CKm KS(I, Pb, rng, tm);
        auto P2 = std::make_unique<l2::Prob>(Pb); int slot = kmSlots.fetch_add(1), rs = 0;
        while (tm.sec() < tEnd) {
            Routes R; if (!sh.get(R)) { std::this_thread::sleep_for(std::chrono::microseconds(200)); continue; }
            startPoint(slot, rng, R, *P2, rs++);
            long long myKey = keyOf(R);
            l2::Sol s; s.fromRoutes(Pb, R);
            double lastMig = tm.sec();
            auto poll = [&](l2::Sol& best, l2::Sol& cur) -> int {
                if (KS.newBest) { KS.newBest = false; offer(best.routes(), id); }
                if (sh.fleetKey.load() < myKey) return 1;
                if (mig > 0 && tm.sec() - lastMig > migGap * tl) {
                    lastMig = tm.sec();
                    double g = sh.bestScalar.load();
                    if (g < best.cost(Pb) - mth * best.kmTot()) {
                        Routes G; if (sh.get(G) && keyOf(G) == myKey) { best.fromRoutes(Pb, G); cur = best; return 2; }
                    }
                }
                return 0;
            };
            KS.run(s, tEnd, poll);
            if (KS.newBest) KS.newBest = false;
            offer(s.routes(), id);
        }
    }
    // ---- км-работник на ходах отжига s10 ----
    void kmSA(int id, uint64_t sd, double tEnd) {
        auto sa = std::make_unique<coop::SA>(F, sd);
        auto P2 = std::make_unique<l2::Prob>(Pb); int slot = kmSlots.fetch_add(1), rs = 0; Rng rng(sd * 3 + 1);
        while (tm.sec() < tEnd) {
            Routes R; if (!sh.get(R)) { std::this_thread::sleep_for(std::chrono::microseconds(200)); continue; }
            startPoint(slot, rng, R, *P2, rs++);
            long long myKey = keyOf(R);
            double lastPub = 1e300, lastPubT = tm.sec(), lastMig = tm.sec();
            sa->aborted = false;
            coop::SA* p = sa.get();
            p->poll = [&, p]() -> bool {
                double now = tm.sec();
                if (p->bestScore < lastPub - 1e-9 && now - lastPubT > 0.004 * tl) { lastPub = p->bestScore; lastPubT = now; offer(p->bestRoutes(), id); }
                if (sh.fleetKey.load() < myKey) return true;
                if (mig > 0 && now - lastMig > migGap * tl) {
                    lastMig = now;
                    if (sh.bestScalar.load() < p->bestScore - mth * (fmod(p->bestScore, 1e4))) {
                        Routes G; if (sh.get(G) && keyOf(G) == myKey) p->loadBest(G);
                    }
                }
                return false;
            };
            Routes out = p->kmOnly(R, tEnd, tm);
            p->poll = nullptr;
            offer(out, id);
        }
    }
    // ---- A: отжиг s10 (починка + удаление маршрутов), затем FleetRR ----
    void roleA(int id, double tFleet) {
        Rng rng(seed * 7919 + id);
        {
            auto sa = std::make_unique<coop::SA>(F, seed * 7 + 3 + id * 101);
            coop::SA* p = sa.get();
            sau::Sol g; Routes init = sau::multiGreedy(F, rng, tFleet * 0.08, g);
            offer(init, id);
            p->fElim = P("ASAF", 0.75); p->stopAfterElim = true;
            double lastPub = 1e300;
            p->poll = [&, p]() -> bool {
                if (fleetDone) return true;
                if (p->bestScore < lastPub - 1e4 + 1) { lastPub = p->bestScore; offer(p->bestRoutes(), id); }   // только улучшение парка/штрафа
                return false;
            };
            p->onAttempt = [&, p]() {
                if (p->bestScore < lastPub - 1e-9) { lastPub = p->bestScore; offer(p->bestRoutes(), id); }
                if (sh.fleetKey.load() * 1.0 < floor(p->bestScore / 1e4) - 0.5) {   // чужой парк меньше
                    Routes G; if (sh.get(G)) { Score s = evaluate(I, G); if (s.scalar() < p->bestScore) p->loadBest(G); }
                }
            };
            Routes R = p->run(init, tFleet, tm);
            offer(R, id);
        }
        fleetRR(id, rng, tFleet, 0.5);
    }
    // FleetRR от общего лучшего (или своего) до tEnd; при чужом меньшем парке — подхват
    double frst = P("FRST", 0);
    void fleetRR(int id, Rng& rng, double tEnd, double serveFrac, const l2::Sol* start = nullptr, double ernd = 0.5, double stall = 0, double tMin = 0) {
        if (tm.sec() >= tEnd || fleetDone) return;
        coop2::CKm KS(I, Pb, rng, tm);
        coop2::CFleet FR(I, Pb, rng, tm, KS);
        l2::Sol best;
        if (start) best = *start; else { Routes G; if (!sh.get(G)) return; best.fromRoutes(Pb, G); }
        long long myKey = keyOf(best.routes());
        FR.ernd = ernd;
        double lastRst = tm.sec();
        auto poll = [&](l2::Sol& b, l2::Sol& cur) -> int {
            if (fleetDone) return 1;
            // адаптивный выход: парк давно не улучшался никем (другие острова парка продолжают)
            if (stall > 0 && tm.sec() > tMin * tl && tm.sec() > sh.lastFleetT() + stall * tl) return 1;
            if (sh.fleetKey.load() < myKey) {
                Routes G; if (sh.get(G)) { long long k = keyOf(G); if (k < myKey) { b.fromRoutes(Pb, G); myKey = k; lastRst = tm.sec(); return 2; } }
            }
            // рестарт попытки удаления от общего лучшего (тот же парк, лучше км) — раз в FRST·T
            if (frst > 0 && tm.sec() - lastRst > frst * tl) {
                lastRst = tm.sec();
                Routes G; if (sh.get(G)) { long long k = keyOf(G); if (k <= myKey) { l2::Sol g; g.fromRoutes(Pb, G); if (g.better(b, Pb) || k == myKey) { b = g; myKey = k; return 2; } } }
            }
            return 0;
        };
        auto onImp = [&](const l2::Sol& b) { offer(b.routes(), id); myKey = min(myKey, keyOf(b.routes())); };
        double tS = best.nUn() ? tm.sec() + serveFrac * (tEnd - tm.sec()) : 0;
        FR.run(best, tS, tEnd, tEnd, poll, onImp);
        offer(best.routes(), id);
    }
    // ---- Z: s23_sa3 (SA2: отжиг + оценка снизу + смена набора бригад + венгерское переназначение) целиком ----
    void roleZ(int id) {
        double off = tm.sec(); coopsa2::STimer st;
        coopsa2::Fast F2(I); Rng rng(seed * 15485863 + id);
        auto g = std::make_unique<coopsa2::Sol>();
        Routes init = coopsa2::multiGreedy(F2, rng, (tl - off) * 0.05, *g);
        offer(init, id);
        auto sa = std::make_unique<coopsa2::SA2>(F2, seed * 7 + 3 + id * 131);
        coopsa2::sa2Defaults(*sa); coopsa2::sa2Env(*sa);
        coopsa2::SA2* p = sa.get(); double lastPub = 1e300, lastPubT = 0;
        p->poll = [&, p]() -> bool {
            double now = tm.sec();
            if (p->bestScore < lastPub - 1e-9 && (p->bestScore < lastPub - 5e3 || now - lastPubT > 0.004 * tl)) { lastPub = p->bestScore; lastPubT = now; offer(p->bestRoutes(), id); }
            if (p->tElimEnd > 0 && sh.fleetKey.load() * 1.0 < floor(p->bestScore / 1e4) - 0.5) return true;   // в км-фазе, а у других парк меньше
            return false;
        };
        p->onAttempt = [&, p]() {
            if (p->bestScore < lastPub - 1e-9) { lastPub = p->bestScore; offer(p->bestRoutes(), id); }
            if (sh.fleetKey.load() * 1.0 < floor(p->bestScore / 1e4) - 0.5) { Routes G; if (sh.get(G)) { if (evaluate(I, G).scalar() < p->bestScore) p->loadBest(G); } }
        };
        Routes R = p->run(init, tl - off, st);
        offer(R, id);
    }
    // ---- G: s23 на задаче с подсказкой-штрафом за переезды между кластерами ----
    Instance guided() {
        Instance G = I; int S = I.S, M = I.M; double pen = P("GPEN", 2.0);
        vector<int> cls(M); for (int i = 0; i < S; i++) cls[i] = i;
        for (int k = 0; k < I.N; k++) { int b = 0; for (int s2 = 1; s2 < S; s2++) if (I.T[0][s2 * M + I.node(k)] < I.T[0][b * M + I.node(k)]) b = s2; cls[I.node(k)] = b; }
        for (int m = 0; m < 4; m++) for (int i = 0; i < M; i++) for (int j = S; j < M; j++)
            if (cls[i] != cls[j] && !(cls[i] == 0 && i < S)) G.D[m][i * M + j] *= pen;
        return G;
    }
    Instance homeGuided(double pen) {
        Instance H = I; int S = I.S, M = I.M;
        vector<int> rep(S); for (int s2 = 0; s2 < S; s2++) { rep[s2] = s2; for (int q = 0; q < s2; q++) if (I.T[0][q * M + s2] < 3 && I.T[0][s2 * M + q] < 3) { rep[s2] = rep[q]; break; } }
        vector<int> cl(I.N); for (int k = 0; k < I.N; k++) { int b = 0; for (int s2 = 1; s2 < S; s2++) if (I.T[0][s2 * M + I.node(k)] < I.T[0][b * M + I.node(k)]) b = s2; cl[k] = rep[b]; }
        int office = rep[0], slot = 4;
        std::map<pair<int,int>, int> made;   // (дом, исходный вид) -> служебный вид
        for (int v = 0; v < I.V; v++) {
            int home = rep[I.veh[v].start]; if (home == office) continue;
            auto key = make_pair(home, I.veh[v].mode);
            if (!made.count(key)) {
                if (slot >= 8) continue;
                int m0 = I.veh[v].mode; H.T[slot] = I.T[m0]; H.D[slot] = I.D[m0];
                for (int i = 0; i < M; i++) for (int k = 0; k < I.N; k++) if (cl[k] != home) H.D[slot][i * M + I.node(k)] *= pen;
                made[key] = slot++;
            }
            H.veh[v].mode = made[key];
        }
        return H;
    }
    void roleH(int id) {
        bool far = false; for (int v = 0; v < I.V; v++) if (I.veh[v].start != 0) far = true;
        if (!far) { roleZ(id); return; }
        if (P("HTIGHT", 1) > 0) {   // тесная задача (жадное решение не выполняет все заявки) — работать как Z
            coopsa2::Fast F0(I); Rng r0(seed * 7 + id); auto g0 = std::make_unique<coopsa2::Sol>();
            Routes init0 = coopsa2::multiGreedy(F0, r0, tl * 0.05, *g0);
            if (evaluate(I, init0).nUnserved > 0) { roleZ(id); return; }
        }
        Instance Ih = homeGuided(P(id % 2 ? "HPEN2" : "HPEN", id % 2 ? 5.0 : 2.0));
        double off = tm.sec(); coopsa2::STimer st;
        coopsa2::Fast F2(Ih); Rng rng(seed * 15485869 + id);
        auto g = std::make_unique<coopsa2::Sol>();
        Routes init = coopsa2::multiGreedy(F2, rng, (tl - off) * 0.05, *g);
        offer(init, id);
        auto sa = std::make_unique<coopsa2::SA2>(F2, seed * 7 + 9 + id * 131);
        coopsa2::sa2Defaults(*sa); coopsa2::sa2Env(*sa);
        coopsa2::SA2* p = sa.get(); double lastPub = 1e300, lastPubT = 0;
        p->poll = [&, p]() -> bool {
            double now = tm.sec();
            if (p->bestScore < lastPub - 1e-9 && (p->bestScore < lastPub - 5e3 || now - lastPubT > 0.004 * tl)) { lastPub = p->bestScore; lastPubT = now; offer(p->bestRoutes(), id); }
            return false;
        };
        p->onAttempt = [&, p]() { if (p->bestScore < lastPub - 1e-9) { lastPub = p->bestScore; offer(p->bestRoutes(), id); } };
        Routes R = p->run(init, tl - off, st);
        offer(R, id);
    }
    void roleG(int id) {
        if (I.S <= 1) { roleZ(id); return; }   // нет дальних домов — подсказка ничего не меняет
        Instance Ig = guided();
        double off = tm.sec(); coopsa2::STimer st;
        coopsa2::Fast F2(Ig); Rng rng(seed * 15485867 + id);
        auto g = std::make_unique<coopsa2::Sol>();
        Routes init = coopsa2::multiGreedy(F2, rng, (tl - off) * 0.05, *g);
        offer(init, id);
        auto sa = std::make_unique<coopsa2::SA2>(F2, seed * 7 + 5 + id * 131);
        coopsa2::sa2Defaults(*sa); coopsa2::sa2Env(*sa);
        coopsa2::SA2* p = sa.get(); double lastPub = 1e300, lastPubT = 0;
        p->poll = [&, p]() -> bool {
            double now = tm.sec();
            if (p->bestScore < lastPub - 1e-9 && (p->bestScore < lastPub - 5e3 || now - lastPubT > 0.004 * tl)) { lastPub = p->bestScore; lastPubT = now; offer(p->bestRoutes(), id); }
            return false;
        };
        p->onAttempt = [&, p]() { if (p->bestScore < lastPub - 1e-9) { lastPub = p->bestScore; offer(p->bestRoutes(), id); } };
        Routes R = p->run(init, tl - off, st);
        offer(R, id);
    }
    // ---- X: км-поиск с пулом маршрутов всех потоков + рекомбинация ----
    void roleX(int id) {
        Rng rng(seed * 7777 + id * 13 + 5);
        sp::Pool PL(Pb, (int)P("PMAX", 200000)); sp::Recomb RC(PL); PoolKm K(I, Pb, rng, tm, PL);
        double spInt = P("XSPI", 0.05) * tl, spTL = P("XSPTL", 0.015) * tl; long nImp = 0, nRc = 0;
        vector<Routes> q;
        while (tm.sec() < tl) {
            Routes G; if (!sh.get(G)) { std::this_thread::sleep_for(std::chrono::microseconds(200)); continue; }
            l2::Sol x; x.fromRoutes(Pb, G); long long myKey = keyOf(G);
            K.start(x, tm.sec(), tl);
            while (tm.sec() < tl) {
                K.run(min(tl, tm.sec() + spInt));
                offer(K.best.routes(), id);
                { std::lock_guard<std::mutex> lk(qM); q.swap(offQ); }
                for (auto& R : q) { l2::Sol y; y.fromRoutes(Pb, R); if (y.nUn() == 0) PL.addSol(y); }
                q.clear();
                l2::Sol c = K.best; nRc++;
                int ni = RC.run(c, tm, min(tl, tm.sec() + spTL));
                if (ni && c.better(K.best, Pb) && evaluate(I, c.routes()).feasible) { nImp++; K.polish(c, tl); K.inject(c); offer(c.routes(), id); }
                if (sh.fleetKey.load() < myKey) break;
                double g = sh.bestScalar.load();
                if (mig > 0 && g < K.bestCost - mth * K.best.kmTot()) { Routes H; if (sh.get(H) && keyOf(H) == myKey) { l2::Sol h; h.fromRoutes(Pb, H); PL.addSol(h); K.inject(h); } }
            }
        }
        if (getenv("DEBUG")) fprintf(stderr, "X: rc=%ld imp=%ld pool=%d\n", nRc, nImp, PL.size());
    }
    // ---- R: построение + FleetRR ----
    void roleR(int id, double tFleet) {
        Rng rng(seed * 104729 + id);
        l2::Sol b = coop2::construct(Pb, rng);
        { coop2::CKm KS(I, Pb, rng, tm); KS.polish(b, Pb.V, tFleet); }
        offer(b.routes(), id);
        fleetRR(id, rng, tFleet, 0.3, &b, P("RERND", 0.5), P("RSTALL", 0), P("RMIN", 0.2));
    }

    Routes run() {
        unsigned hc = std::thread::hardware_concurrency(); string defRoles = hc >= 8 ? "ARKZARKH" : hc >= 4 ? "ARKH" : "ZR";
        string roles = PS("ROLES", defRoles.c_str()); int thr = (int)P("THR", roles.size()); roles = roles.substr(0, thr);
        double aEnd = P("AEND", 0.6) * tl, rEnd = P("REND", 0.5) * tl;
        string after = PS("AFTER", "SK");   // чем заняться потокам A и R после фазы парка: K (SISR) или S (отжиг)
        hasX = roles.find('X') != string::npos;
        coop::ThreadPool pool;
        for (int id = 0; id < (int)roles.size(); id++) {
            char r = roles[id];
            pool.spawn([this, id, r, aEnd, rEnd, after] {
                Rng rng(seed * 1000003 + id * 7777);
                uint64_t sd = seed * 31 + id * 1009;
                if (r == 'A') { roleA(id, aEnd); after[0] == 'S' ? kmSA(id, sd, tl) : kmSisr(id, rng, tl); }
                else if (r == 'R') { roleR(id, rEnd); after.size() > 1 && after[1] == 'S' ? kmSA(id, sd, tl) : kmSisr(id, rng, tl); }
                else if (r == 'K') kmSisr(id, rng, tl);
                else if (r == 'S') kmSA(id, sd, tl);
                else if (r == 'Z') { roleZ(id); kmSisr(id, rng, tl); }
                else if (r == 'G') { roleG(id); kmSisr(id, rng, tl); }
                else if (r == 'H') { roleH(id); kmSisr(id, rng, tl); }
                else if (r == 'X') roleX(id);
            });
        }
        pool.join();
        Routes R; sh.get(R);
        if (getenv("DEBUG")) {
            fprintf(stderr, "LB=%d used=%d km=%.1f offers=%ld acc=%ld tFirst=%.3f tLastFleet=%.3f by:", LB, sh.bestS.used, sh.bestS.km, sh.offers, sh.accepts, sh.tFirst, sh.tLastFleet);
            for (int i = 0; i < (int)roles.size(); i++) fprintf(stderr, " %c=%ld", roles[i], sh.acceptBy[i]);
            fprintf(stderr, " owner=%d\n", sh.owner);
        }
        return R;
    }
};

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; double T = tl * 0.97;
    Coop C(I, T, seed, tm);
    Routes R = C.run();
    l2::Sol best; best.fromRoutes(C.Pb, R);
    lu::LocalSearch LS(I); lu::Sol x = l2::toLu(I, best); Rng rr(seed); LS.run(x, rr, best.used(), &tm, tl * 0.99);
    l2::Sol y; y.fromRoutes(C.Pb, x.r); if (y.better(best, C.Pb)) best = y;
    return l2::finalizeR(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "72_coopX"); }
