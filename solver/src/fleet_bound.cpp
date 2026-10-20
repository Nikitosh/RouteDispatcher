// Нижняя граница числа бригад для заявок, которые обслуживает данный план (bound/fleet_cg.hpp).
// Использование: fleet_bound <задача> <ответ plan> [секунды=3] [ng=8]. ng — сколько ближайших заявок помнит метка точного
// поиска: больше — оценка ближе к ЛП на элементарных маршрутах, но поиск дольше. Вывод — одна строка JSON (docs/SOLVER_IO.md):
// {"fleet_lower_bound": n или null, "lagrangian": ..., "converged": ..., "used": ..., "ms": ...}.
// Маршруты в оценке проверяются теми же правилами, что в core/problem.hpp: окна, конец смены бригады, обед (перебор
// всех промежутков для него) и запас оборудования (bound/pricing.hpp). Каждый допустимый маршрут — столбец, поэтому
// оценка — нижняя граница; без обеда и оборудования она та же, что раньше.
#include "bound/fleet_cg.hpp"

using namespace dispatch;

// Ослабленная задача: у всех бригад одна стартовая точка, от которой до каждой заявки столько же, сколько от
// ближайшей из стартовых точек бригад. Каждая бригада успевает не хуже, чем из своего дома, поэтому оценка для неё —
// тоже нижняя граница исходной задачи (обед тоже: начало работ с обедом в промежутке не убывает с приездом, начало
// смены у бригады своё); зато бригады с одинаковым транспортом и навыками снова один тип, и генерация
// столбцов сходится быстрее, когда у каждой бригады свой дом.
static Instance commonStart(const Instance& I) {
    Instance R = I;
    vector<int> starts;
    for (const Vehicle& v : I.veh) starts.push_back(v.start);
    std::sort(starts.begin(), starts.end());
    starts.erase(std::unique(starts.begin(), starts.end()), starts.end());
    for (int m = 0; m < MAX_MODES; m++) {
        if (I.T[m].empty()) continue;
        for (int j = 0; j < I.M; j++) {
            double t = 1e18, d = 1e18;
            for (int s : starts) {
                t = min(t, I.T[m][s * I.M + j]);
                d = min(d, I.D[m][s * I.M + j]);
            }
            R.T[m][j] = t;
            R.D[m][j] = d;
        }
    }
    for (Vehicle& v : R.veh) v.start = 0;
    return R;
}

struct Estimate {
    double L = -1e18;
    bool converged = false;
};

static Estimate estimate(const Instance& I, const Routes& routes, const Timer& clock, double tEnd, int ng) {
    const lns::Problem P(I);
    const lns::Solution plan(P, routes);
    bound::VehicleTypes types(I);
    bound::ColumnPool pool;
    for (int v = 0; v < P.V; v++)
        if (plan.len[v]) pool.add(types.typeOf[v], plan.r[v], plan.len[v], plan.km[v]);
    bound::HeuristicPricer heuristic(P, types);
    bound::ExactPricer exact(P, types, ng);
    bound::FleetBound cg(P, types, pool, heuristic, exact);
    Estimate e;
    e.L = cg.run(plan, plan.used() - 1 + 1e-6, clock, tEnd);   // выше used − 1 — план уже оптимален по парку
    e.converged = cg.converged;
    return e;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "использование: %s <задача> <ответ> [секунды] [ng]\n", argv[0]);
        return 1;
    }
    Timer clock;
    const Instance I = loadInstance(argv[1]);
    const double seconds = argc > 3 ? atof(argv[3]) : 3.0;
    const int ng = argc > 4 ? atoi(argv[4]) : 8;
    const Routes routes = readRoutes(argv[2], I.V);
    const int used = lns::Solution(lns::Problem(I), routes).used();
    // разные дома у бригад: половина времени на исходную задачу, половина — на ослабленную с общим стартом
    const bool homes = I.hasFarHomes();
    Estimate e = estimate(I, routes, clock, homes ? seconds / 2 : seconds, ng);
    if (homes && (int)ceil(e.L - 1e-6) < used) {
        const Instance R = commonStart(I);
        const Estimate r = estimate(R, routes, clock, seconds, ng);
        if (r.L > e.L) e = r;
    }
    const double ms = clock.sec() * 1000;
    if (e.L > -1e17)
        printf("{\"fleet_lower_bound\": %d, \"lagrangian\": %.4f, \"converged\": %s, \"used\": %d, \"ms\": %.1f}\n",
               (int)ceil(e.L - 1e-6), e.L, e.converged ? "true" : "false", used, ms);
    else
        printf("{\"fleet_lower_bound\": null, \"lagrangian\": null, \"converged\": false, \"used\": %d, \"ms\": %.1f}\n", used, ms);
    return 0;
}
