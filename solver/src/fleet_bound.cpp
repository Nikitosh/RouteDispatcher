// Нижняя граница числа бригад для заявок, которые обслуживает данный план (bound/fleet_cg.hpp).
// Использование: fleet_bound <задача> <ответ plan> [секунды=3]. Вывод — одна строка JSON (docs/SOLVER_IO.md):
// {"fleet_lower_bound": n или null, "lagrangian": ..., "converged": ..., "used": ..., "ms": ...}.
#include "bound/fleet_cg.hpp"

using namespace dispatch;

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "использование: %s <задача> <ответ> [секунды]\n", argv[0]);
        return 1;
    }
    Timer clock;
    const Instance I = loadInstance(argv[1]);
    const double seconds = argc > 3 ? atof(argv[3]) : 3.0;
    const lns::Problem P(I);
    const lns::Solution plan(P, readRoutes(argv[2], I.V));
    bound::VehicleTypes types(I);
    bound::ColumnPool pool;
    for (int v = 0; v < P.V; v++)
        if (plan.len[v]) pool.add(types.typeOf[v], plan.r[v], plan.len[v], plan.km[v]);
    bound::HeuristicPricer heuristic(P, types);
    bound::ExactPricer exact(P, types);
    bound::FleetBound cg(P, types, pool, heuristic, exact);
    const int used = plan.used();
    const double L = cg.run(plan, used - 1 + 1e-6, clock, seconds);   // выше used − 1 — план уже оптимален по парку
    const double ms = clock.sec() * 1000;
    if (L > -1e17)
        printf("{\"fleet_lower_bound\": %d, \"lagrangian\": %.4f, \"converged\": %s, \"used\": %d, \"ms\": %.1f}\n",
               (int)ceil(L - 1e-6), L, cg.converged ? "true" : "false", used, ms);
    else
        printf("{\"fleet_lower_bound\": null, \"lagrangian\": null, \"converged\": false, \"used\": %d, \"ms\": %.1f}\n", used, ms);
    return 0;
}
