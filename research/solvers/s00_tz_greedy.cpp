// 00. Базовый вариант из раздела 2.3 ТЗ: заявки по порядку во входных данных, каждая первому по порядку
// инженеру, который проходит ограничения при добавлении В КОНЕЦ маршрута. Порядок визитов = порядок назначения.
#include "common.hpp"
Routes solve(const Instance& I, double, uint64_t) {
    Routes R(I.V);
    for (int k = 0; k < I.N; k++)
        for (int v = 0; v < I.V; v++) {
            auto r = R[v]; r.push_back(k);
            if (routeFeasible(I, v, r)) { R[v] = r; break; }
        }
    return R;
}
int main(int c, char** v) { return runMain(c, v, solve, "00_tz_greedy"); }
