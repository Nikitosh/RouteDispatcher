// 14. Облегчённый гибридный генетический поиск (в духе Vidal HGS).
// Популяция: рандомизированные построения + быстрый ЛП (relocate/swap/2-opt*/or-opt/перенос маршрута на бригаду).
// Родители — бинарный турнир по «смещённой приспособленности» (ранг стоимости + ранг вклада в разнообразие,
// расстояние broken-pairs с учётом бригады). Скрещивание SREX для разнородного парка: из A убираются маршруты
// возле случайной заявки, на их место ставятся маршруты B (той же бригады), покрывающие те же заявки;
// недостающие — regret-вставкой. Обучение — ЛП, с вероятностью — мутация «удаление маршрута» (короткий SISR-R&R
// при парке на 1 меньше). Отбор выживших: удаляются клоны и худшие по смещённой приспособленности.
#include "lns_util.hpp"
using namespace lu;
static double P(const char* n, double d) { const char* e = getenv(n); return e ? atof(e) : d; }

struct Indiv {
    Sol s; double cost = 0; vector<int> succ, pred;
    void finish() {
        const Instance& I = *s.I; cost = s.cost();
        succ.assign(I.N, -1); pred.assign(I.N, -1);
        for (int v = 0; v < I.V; v++) {
            const auto& r = s.r[v];
            for (int i = 0; i < (int)r.size(); i++) {
                pred[r[i]] = i ? r[i - 1] : -2 - v;      // начало маршрута — «узел бригады»
                succ[r[i]] = i + 1 < (int)r.size() ? r[i + 1] : -1;
            }
        }
    }
};
static double bpd(const Indiv& a, const Indiv& b) {
    int N = a.succ.size(), c = 0;
    for (int i = 0; i < N; i++) {
        if (a.succ[i] != b.succ[i]) c++;
        if (a.pred[i] < -1 && a.pred[i] != b.pred[i]) c++;
    }
    return (double)c / N;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; Ctx C(I); Rng rng(seed); const int N = I.N, V = I.V;
    LocalSearch LS(I); SisrRuin ruin;
    const int MU = (int)P("MU", 25), LAMBDA = (int)P("LAMBDA", 40), NELITE = 4, NCLOSE = 5;
    const double pElim = P("PELIM", 0.6); const int elimIters = (int)P("EITERS", 100);
    const double eAlns = P("EALNS", 1); ruin.cbar = P("CBAR", 10);
    const double eFrac = P("EFRAC", 0.5); const int elimEvery2 = (int)P("EEVERY2", 100); const int elimEvery = (int)P("EEVERY", 3), elimBest = (int)P("EBEST", 500);
    const double tEnd = tl * 0.97;

    vector<Indiv> pop; vector<vector<double>> D;   // D — попарные расстояния
    Indiv best; bool haveBest = false;
    vector<int> pool; Sol tmpS(I);
    vector<double> absence(N, 0);

    auto addIndiv = [&](Indiv&& x) {
        if (!haveBest || x.s.better(best.s)) { best = x; haveBest = true; }
        int n = pop.size(); vector<double> row(n + 1, 0);
        for (int i = 0; i < n; i++) { row[i] = bpd(x, pop[i]); D[i].push_back(row[i]); }
        D.push_back(row); pop.push_back(std::move(x));
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
    auto removeIndiv = [&](int i) {
        pop.erase(pop.begin() + i); D.erase(D.begin() + i); for (auto& row : D) row.erase(row.begin() + i);
    };
    auto survivors = [&]() {
        while ((int)pop.size() > MU) {
            int n = pop.size(), worst = -1;
            // сначала клоны
            for (int i = 0; i < n && worst < 0; i++) for (int j = 0; j < n; j++) if (j != i && D[i][j] < 1e-9 && pop[i].cost >= pop[j].cost) { worst = i; break; }
            if (worst < 0) { auto bf = biased(); worst = max_element(bf.begin(), bf.end()) - bf.begin(); }
            removeIndiv(worst);
        }
    };

    // Разрушение-восстановление для сокращения парка: SISR-строки + вставка с морганиями, либо (eAlns)
    // операторы ALNS (случайный выбор) + regret-2.
    Destroyer DS(C); const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30));
    auto ruinRecreate = [&](Sol& t, int cap) {
        if (eAlns && rng.uni() < eAlns) {
            DS.apply(t, rng, rng.randint(NR), qmin + rng.randint(qmax - qmin + 1), pool);
            for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
            regretInsert(t, pool, rng, 2, cap, W_VEHICLE);
        } else {
            ruin.apply(t, C, rng, pool); sisrSort(pool, C, rng);
            greedyInsert(t, pool, rng, 0.01, cap, W_VEHICLE);
        }
    };
    // Мутация «удаление маршрута»: короткий SISR-R&R при парке на 1 меньше. true — удалось.
    auto elimRoute = [&](Sol& s0, int iters) {
        if (s0.used() <= 1 || s0.nUn() > 0) return false;
        Sol cur = s0; int cap = cur.used() - 1;
        int v = pickRouteToRemove(cur, rng, &absence); vector<int> rr = cur.r[v], tmp; removeSet(cur, rr, tmp);
        auto sumAbs = [&](const Sol& s) { double x = 0; for (int k = 0; k < N; k++) if (s.rt[k] < 0) x += absence[k]; return x; };
        double cp = cur.pen(), ca = sumAbs(cur);
        for (int it = 0; it < iters; it++) {
            tmpS = cur; pool.clear(); collectAbsent(tmpS, pool);
            ruinRecreate(tmpS, cap);
            for (int k : pool) absence[k] += 1;
            double p = tmpS.pen(), a = sumAbs(tmpS);
            if (p < cp - 1e-9 || a < ca) { cur = tmpS; cp = p; ca = a; }
            if (p == 0) { s0 = tmpS; return true; }
            if ((it & 15) == 15 && tm.sec() > tEnd) break;
        }
        return false;
    };
    auto educate = [&](Sol& s) {
        LS.run(s, rng, V, &tm, tEnd);
        if (rng.uni() < pElim && tm.sec() < tEnd) {
            Sol t = s;
            if (elimRoute(t, elimIters)) { LS.run(t, rng, t.used(), &tm, tEnd); if (t.better(s)) s = t; }
        }
    };

    // --- начальная популяция ---
    auto randomBuild = [&]() {
        Sol s(I); pool.resize(N); iota(pool.begin(), pool.end(), 0);
        int mode = rng.randint(3);
        if (mode == 0) { sisrSort(pool, C, rng); greedyInsert(s, pool, rng, 0.1, V, W_VEHICLE); }
        else if (mode == 1) { for (int i = N - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]); regretInsert(s, pool, rng, 2, V, W_VEHICLE, 0.05 * C.maxD); }
        else { sisrSort(pool, C, rng); greedyInsert(s, pool, rng, 0.02, V, W_VEHICLE, 0.05 * C.maxD); }
        return s;
    };
    int initN = (int)P("INIT", 2 * MU);
    for (int i = 0; i < initN && (i < 4 || tm.sec() < tl * 0.25); i++) {
        Indiv x; x.s = randomBuild(); educate(x.s); x.finish(); addIndiv(std::move(x));
    }
    survivors();

    // --- поколения ---
    auto tournament = [&](const vector<double>& bf) {
        int a = rng.randint(pop.size()), b = rng.randint(pop.size()); return bf[a] < bf[b] ? a : b;
    };
    long gen = 0; Sol ecur(I); int ecap = -1;
    while (tm.sec() < tEnd) {
        gen++;
        auto bf = biased();
        int ia = tournament(bf), ib = tournament(bf); if (pop.size() > 1) while (ib == ia) ib = rng.randint(pop.size());
        const Sol& A = pop[ia].s; const Sol& B = pop[ib].s;
        // SREX для разнородного парка
        Sol ch = A;
        int c = rng.randint(N); for (int t = 0; t < 4 * N && A.rt[c] < 0; t++) c = rng.randint(N);
        int nA = A.used(); int k = 1 + rng.randint(max(1, nA / 2));
        vector<char> takeA(V, 0); int taken = 0;
        if (A.rt[c] >= 0) { takeA[A.rt[c]] = 1; taken++; }
        for (int x : C.nbr[c]) { if (taken >= k) break; int v = A.rt[x]; if (v >= 0 && !takeA[v]) { takeA[v] = 1; taken++; } }
        vector<char> freeC(N, 0); vector<int> X;
        for (int v = 0; v < V; v++) if (takeA[v]) { for (int x : ch.r[v]) { freeC[x] = 1; X.push_back(x); ch.rt[x] = -1; } ch.r[v].clear(); ch.rebuild(v); }
        // маршруты B по перекрытию с X
        vector<pair<int, int>> ov;
        for (int v = 0; v < V; v++) if (!B.r[v].empty()) { int o = 0; for (int x : B.r[v]) o += freeC[x]; if (o) ov.push_back({-o, v}); }
        sort(ov.begin(), ov.end());
        int putB = 0;
        for (auto& [o, v] : ov) {
            if (putB >= taken) break;
            if (!ch.r[v].empty()) continue;         // бригада уже занята маршрутом A
            for (int x : B.r[v]) if (ch.rt[x] < 0) ch.r[v].push_back(x);
            vector<int> dropped; ch.rebuild(v, &dropped); putB++;
        }
        pool.clear(); collectAbsent(ch, pool);
        for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
        int cap = max(A.used(), B.used());
        if (rng.uni() < 0.5) regretInsert(ch, pool, rng, 2, cap, W_VEHICLE); else greedyInsert(ch, pool, rng, 0.01, cap, W_VEHICLE);
        if (!pool.empty()) greedyInsert(ch, pool, rng, 0, V, W_VEHICLE);
        educate(ch);
        Indiv x; x.s = std::move(ch); x.finish(); addIndiv(std::move(x));
        if ((int)pop.size() >= MU + LAMBDA) survivors();
        // интенсификация: периодически пытаемся сократить парк у лучшего решения
        if (gen % (tm.sec() < tl * eFrac ? elimEvery : elimEvery2) == 0 && tm.sec() < tEnd && best.s.nUn() == 0 && best.s.used() > 1) {
            // постоянное состояние: продолжаем попытку с прошлого раза, пока у лучшего то же число бригад
            if (ecap != best.s.used() - 1) {
                ecur = best.s; ecap = ecur.used() - 1;
                int v = pickRouteToRemove(ecur, rng, &absence); vector<int> rr = ecur.r[v], tmp; removeSet(ecur, rr, tmp);
            }
            auto sumAbs = [&](const Sol& s) { double x = 0; for (int k = 0; k < N; k++) if (s.rt[k] < 0) x += absence[k]; return x; };
            double cp = ecur.pen(), ca = sumAbs(ecur);
            for (int it = 0; it < elimBest; it++) {
                tmpS = ecur; pool.clear(); collectAbsent(tmpS, pool);
                ruinRecreate(tmpS, ecap);
                for (int k : pool) absence[k] += 1;
                double p = tmpS.pen(), a = sumAbs(tmpS);
                if (p < cp - 1e-9 || a < ca) { ecur = tmpS; cp = p; ca = a; }
                if (p == 0) {
                    LS.run(tmpS, rng, tmpS.used(), &tm, tEnd); Indiv y; y.s = tmpS; y.finish(); addIndiv(std::move(y));
                    break;
                }
                if ((it & 15) == 15 && tm.sec() > tEnd) break;
            }
        }
    }
    Sol res = best.s;
    if (getenv("DEBUG")) fprintf(stderr, "gen=%ld pop=%d used=%d un=%d km=%.1f\n", gen, (int)pop.size(), res.used(), res.nUn(), res.kmTot());
    LS.run(res, rng, res.used(), &tm, tl * 0.99);
    return finalize(I, res);
}
int main(int c, char** v) { return runMain(c, v, solve, "14_hgs_lite"); }
