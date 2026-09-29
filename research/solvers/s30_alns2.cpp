// 30. ALNS-2: как s31, но в фазе км адаптивный ALNS-набор (SISR-строки, случайное, худшее, Шоу, маршрут, пара маршрутов) × (жадная с морганиями, regret-2).
// Общая часть (s31):
// Фаза 1 (парк, FleetRR): адаптивный набор разрушений {SISR, случайное, худшее, Шоу, маршрут, слот} × вставок
// {жадная с морганиями по сортировке SISR, regret-2, regret-3}; приёмка по штрафу/Σabsence (как в SISR/ALNS),
// удаляемый маршрут — с наименьшей Σabsence либо случайный (вес 1/len²). Фаза 2 (км, KmSearch): SISR-отжиг при
// фиксированном парке, T в единицах среднего ребра, плюс удаление маршрута / пары маршрутов (бригада с ближайшим
// стартом) для смены набора бригад; новый рекорд полируется локальным поиском lns_util.
#include "lns2_util.hpp"
using namespace l2;

static Sol construct(const Prob& Pb, Rng& rng) {
    Regret RG; vector<int> pool;
    Sol a; a.init(Pb); pool.resize(Pb.N); iota(pool.begin(), pool.end(), 0);
    RG.run(Pb, a, pool, rng, 2, Pb.V, W_VEHICLE, nullptr);
    Sol b; b.init(Pb); pool.resize(Pb.N); iota(pool.begin(), pool.end(), 0);
    stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return Pb.b[x] - Pb.a[x] < Pb.b[y] - Pb.a[y]; });
    greedyInsert(Pb, b, pool, rng, 0, Pb.V, W_VEHICLE, nullptr);
    return a.better(b, Pb) ? a : b;
}

static Sol runOnce(const Instance& I, const Prob& Pb, uint64_t seed, double tl) {
    Timer tm; Rng rng(seed);
    KmSearch KS(I, Pb, rng, tm); if (!getenv("KMOPS")) KS.kmOps = 1; FleetRR FR(I, Pb, rng, tm, KS);
    Sol best = construct(Pb, rng);
    KS.polish(best, Pb.V, tl * 0.9);
    FR.run(best, tl * 0.15, tl * P("FLEET", 0.5), tl * 0.9);
    double tF = tm.sec();
    KS.run(best, tl * 0.97);
    if (getenv("DEBUG")) fprintf(stderr, "it1=%ld it2=%ld tF=%.3f used=%d un=%d km=%.1f\n", FR.iters, KS.iters, tF, best.used(), best.nUn(), best.kmTot());
    KS.polish(best, best.used(), tl * 0.99);
    return best;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; int R = (int)P("RST", 1);
    Prob Pb(I); Sol best; bool have = false;
    for (int r = 0; r < R; r++) {
        double seg = (tl - tm.sec()) / (R - r);
        Sol s = runOnce(I, Pb, seed * 1000 + r, seg);
        if (!have || s.better(best, Pb)) { best = s; have = true; }
    }
    return finalizeR(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "30_alns2"); }
