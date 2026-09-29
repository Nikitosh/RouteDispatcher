// 13. SISR (Christiaens & Vanden Berghe 2020): разрушение смежными строками вокруг случайной заявки в нескольких
// маршрутах, восстановление жадной вставкой с «морганиями», сортировки заявок (случайно/окно/дальние/ближние).
// Фаза 1 — сокращение парка: удаляем маршрут с наименьшей суммой absence, R&R с отсутствующими, принимаем, если
// меньше штраф или меньше Σabsence. Фаза 2 — отжиг по км (температура по времени) при фиксированном парке.
#include "lns_util.hpp"
using namespace lu;
static double P(const char* n, double d) { const char* e = getenv(n); return e ? atof(e) : d; }

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; Ctx C(I); Rng rng(seed);
    SisrRuin ruin; ruin.cbar = P("CBAR", 15); double blink = P("BLINK", 0.01);
    // --- построение ---
    Sol best(I);
    {
        Sol a(I); vector<int> pool(I.N); iota(pool.begin(), pool.end(), 0);
        regretInsert(a, pool, rng, 2, I.V, W_VEHICLE);
        Sol b(I); pool.assign(I.N, 0); iota(pool.begin(), pool.end(), 0);
        stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return I.ord[x].b - I.ord[x].a < I.ord[y].b - I.ord[y].a; });
        greedyInsert(b, pool, rng, 0, I.V, W_VEHICLE);
        best = a.better(b) ? a : b;
    }
    LocalSearch LS(I);
    LS.run(best, rng, I.V, &tm, tl * 0.9);

    vector<double> absence(I.N, 0);
    auto sumAbs = [&](const Sol& s) { double x = 0; for (int k = 0; k < I.N; k++) if (s.rt[k] < 0) x += absence[k]; return x; };
    Sol cur = best, s(I);
    vector<int> pool; pool.reserve(I.N);

    // --- фаза 1: минимизация невыполненных и парка ---
    const double tFleet = tl * P("FLEET", 0.55), tServe = tl * 0.15;
    int cap = I.V;
    bool elim = best.nUn() == 0;
    auto startElim = [&] {
        cur = best; cap = best.used() - 1;
        int v = pickRouteToRemove(cur, rng, &absence);
        vector<int> rem = cur.r[v], tmp; removeSet(cur, rem, tmp);
    };
    if (elim && best.used() > 1) startElim(); else if (elim) cap = 0;
    double curPen = cur.pen(), curAbs = sumAbs(cur);
    while (cap > 0 && tm.sec() < tFleet) {
        if (!elim && tm.sec() > tServe) { elim = true; startElim(); curPen = cur.pen(); curAbs = sumAbs(cur); }
        s = cur; pool.clear(); collectAbsent(s, pool);
        ruin.apply(s, C, rng, pool);
        sisrSort(pool, C, rng);
        greedyInsert(s, pool, rng, blink, cap, W_VEHICLE);
        for (int k : pool) absence[k] += 1;
        double p = s.pen(), a = sumAbs(s);
        if (p < curPen - 1e-9 || a < curAbs) { cur = s; curPen = p; curAbs = a; }
        if (s.better(best)) {
            best = s; LS.run(best, rng, cap, &tm, tl * 0.9);
            if (best.nUn() == 0 || elim) {
                elim = true;
                if (best.used() <= 1) break;
                startElim(); curPen = cur.pen(); curAbs = sumAbs(cur);
            } else { cur = best; curPen = cur.pen(); curAbs = sumAbs(cur); }
        }
    }

    // --- фаза 2: отжиг по км ---
    cap = best.used(); cur = best; ruin.cbar = P("CBAR2", 10);
    double km0 = max(1.0, best.kmTot());
    const double T0 = P("T0", 0.04) * km0, Tf = P("TF", 0.004) * km0;
    double t1 = tm.sec(), span = max(1e-9, tl * 0.97 - t1);
    double curCost = cur.cost();
    long it = 0; double T = T0;
    while (true) {
        if ((it & 15) == 0) { double el = tm.sec(); if (el > tl * 0.97) break; T = T0 * pow(Tf / T0, (el - t1) / span); }
        it++;
        s = cur; pool.clear(); collectAbsent(s, pool);
        ruin.apply(s, C, rng, pool);
        sisrSort(pool, C, rng);
        greedyInsert(s, pool, rng, blink, cap, W_VEHICLE);
        double c = s.cost();
        if (c < curCost - T * log(rng.uni() + 1e-300)) { cur = s; curCost = c; }
        if (s.better(best)) best = s;
    }
    if (getenv("DEBUG")) fprintf(stderr, "it=%ld fleetEnd=%.3f used=%d un=%d km=%.1f\n", it, t1, best.used(), best.nUn(), best.kmTot());
    LS.run(best, rng, best.used(), &tm, tl * 0.99);
    return finalize(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "13_sisr"); }
