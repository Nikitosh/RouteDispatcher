// 22. s20 (SA2) через общий заголовок sa2_util.hpp: разрушение-восстановление, разбиение+удаление маршрута в фазе км.
#include "sa2_util.hpp"
Routes solve(const Instance& I, double tl, uint64_t seed) {
    STimer tm;
    tl *= 0.97;
    Fast F(I);
    Rng rng(seed);
    Sol g; Routes init = multiGreedy(F, rng, tl * 0.05, g);
    Routes start = init;
    if (getenv("SA_INITOPEN")) {
        double oc = atof(getenv("SA_INITOPEN"));
        vector<int> o(F.N); iota(o.begin(), o.end(), 0);
        sort(o.begin(), o.end(), [&](int x, int y) { return F.b[x] * 1000 + F.a[x] < F.b[y] * 1000 + F.a[y]; });
        Sol h; greedyBuild(F, h, o, oc);
        if (h.poolPen + h.constPen <= g.poolPen + g.constPen + 1e-9) start = h.toRoutes();
    }
    SA2 sa(F, seed * 7 + 3);
    sa.fSE = 0.5;
    sa2Env(sa);
    Routes R = sa.run(start, tl, tm);
    if (getenv("SA_STAT")) fprintf(stderr, "reasg %lld se %lld/%lld/%lld ruin %lld/%lld iters=%lld (%.2fM/s) elim %lld/%lld rep_end=%.3f elim_end=%.3f used=%d km_after_elim=%.1f final=%.1f\n", sa.reassignImp, sa.seImp, sa.seOk, sa.seTry, sa.ruinAcc, sa.ruinTry, sa.iters, sa.iters / tm.sec() / 1e6, sa.elimAcc, sa.elimTry, sa.tRepEnd, sa.tElimEnd, sa.usedAfterElim, sa.kmAfterElim, sa.bestScore);
    Score s = evaluate(I, R), s0 = evaluate(I, init);
    if (!s.feasible || (s0.feasible && s0.scalar() < s.scalar())) return init;
    return R;
}
int main(int c, char** v) { return runMain(c, v, solve, "22_se"); }
