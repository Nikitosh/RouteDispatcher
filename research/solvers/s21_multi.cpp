// 21. Офлайн-режим: несколько прогонов SA2 (s20) подряд, лучший по Score::scalar(). Прогоны чередуются:
// с новой жадности (разные сиды и цена открытия бригады) и с лучшего найденного (повторные попытки удалить
// маршрут + отжиг км с новым сидом). Один поток.
#include "sa2_util.hpp"

static Routes startGreedy(const Fast& F, Rng& rng, double oc, Sol& g, double budget) {
    Routes init = multiGreedy(F, rng, budget, g);
    if (oc < 0) return init;
    vector<int> o(F.N); iota(o.begin(), o.end(), 0);
    vector<double> key(F.N); for (int k = 0; k < F.N; k++) key[k] = F.b[k] * 1000 + F.a[k] + rng.uni() * 30000;
    sort(o.begin(), o.end(), [&](int x, int y) { return key[x] < key[y]; });
    Sol h; greedyBuild(F, h, o, oc);
    if (h.poolPen + h.constPen <= g.poolPen + g.constPen + 1e-9) return h.toRoutes();
    return init;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    STimer tm;
    tl *= 0.98;
    Fast F(I);
    Rng rng(seed);
    double runLen = getenv("SA_RUNLEN") ? atof(getenv("SA_RUNLEN")) : max(1.0, tl / 6);
    double fromBest = getenv("SA_FROMBEST") ? atof(getenv("SA_FROMBEST")) : 0.5;
    double fbElim = getenv("SA_FBELIM") ? atof(getenv("SA_FBELIM")) : 0.85;
    double fbElimP = getenv("SA_FBELIMP") ? atof(getenv("SA_FBELIMP")) : 0.5;
    Routes best; double bestS = 1e300; int run = 0;
    while (true) {
        double left = tl - tm.sec();
        if (run > 0 && left < 0.3 * runLen) break;
        double len = left < 1.7 * runLen ? left : runLen;
        STimer t2;
        Sol g; Routes start;
        bool fb = run > 0 && rng.uni() < fromBest;
        if (fb) start = best;
        else { static const double ocs[] = {-1, 20, -1, 0, 60}; start = startGreedy(F, rng, ocs[run % 5], g, len * 0.05); }
        SA2 sa(F, seed * 1000003ULL + run * 7919 + 3);
        sa2Defaults(sa);
        sa.T0 = (run & 1) ? 10 : 5;   // портфель температур: T0=10 лучше на трудных control_road, T0=5 — на instances_road
        sa2Env(sa);
        if (fb && rng.uni() < fbElimP) { sa.fElim = fbElim; sa.maxFails = 1000; }
        Routes R = sa.run(start, len, t2);
        Score s = evaluate(I, R);
        if (s.feasible && s.scalar() < bestS - 1e-9) { bestS = s.scalar(); best = R; }
        if (getenv("SA_STAT")) fprintf(stderr, "run %d fb=%d len=%.2f -> used=%d km=%.1f | best %.1f\n", run, fb, len, s.used, s.km, bestS);
        run++;
    }
    return best;
}
int main(int c, char** v) { return runMain(c, v, solve, "21_multi"); }
