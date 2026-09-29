// 23. Продуктовый вариант (1 с): SA2 из sa2_util.hpp = s10_sa +
//  * нижняя граница числа бригад по нагрузке (Σ(обслуживание + минимальный заезд)/720): при её достижении фаза
//    удаления маршрутов не запускается, всё время идёт на км;
//  * в фазе км вторая половина времени — циклы «разбиение + удаление»: хвост маршрута (или весь маршрут) на пустую
//    бригаду, удаление малого маршрута через пул, короткий отжиг — смена НАБОРА бригад при том же их числе
//    (разнородный парк с дальними стартами: без этого отжиг застревает на +50..+200 км);
//  * венгерское переназначение маршрутов бригадам целиком (в начале фазы км, после циклов и в конце).
// Есть и выключенные по умолчанию: time warp в фазе удаления (SA_TW=1), разрушение-восстановление (SA_PRUIN).
// SA_CPU=1 — лимит по процессорному времени (для сравнений на загруженной машине).
#include "sa2_util.hpp"
Routes solve(const Instance& I, double tl, uint64_t seed) {
    STimer tm;
    tl *= 0.97;
    Fast F(I);
    Rng rng(seed);
    Sol g; Routes init = multiGreedy(F, rng, tl * 0.05, g);
    SA2 sa(F, seed * 7 + 3);
    sa2Defaults(sa);
    sa2Env(sa);
    Routes R = sa.run(init, tl, tm);
    if (getenv("SA_STAT")) fprintf(stderr, "reasg %lld se %lld/%lld/%lld iters=%lld (%.2fM/s) elim %lld/%lld lb=%d used=%d final=%.1f\n", sa.reassignImp, sa.seImp, sa.seOk, sa.seTry, sa.iters, sa.iters / tm.sec() / 1e6, sa.elimAcc, sa.elimTry, sa.workLB, sa.usedAfterElim, sa.bestScore);
    Score s = evaluate(I, R), s0 = evaluate(I, init);
    if (!s.feasible || (s0.feasible && s0.scalar() < s.scalar())) return init;
    return R;
}
int main(int c, char** v) { return runMain(c, v, solve, "23_sa3"); }
