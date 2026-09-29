// Базовый вариант из раздела 2.3 ТЗ: заявки по порядку во входных данных, каждая — первой по порядку бригаде, которой
// её можно дописать в конец маршрута без нарушений. Порядок визитов равен порядку назначения, оптимизации нет.
#include "core/problem.hpp"

using namespace dispatch;

static Routes greedyBaseline(const Instance& I) {
    Routes R(I.V);
    for (int k = 0; k < I.N; k++) {
        for (int v = 0; v < I.V; v++) {
            R[v].push_back(k);
            if (routeFeasible(I, v, R[v])) break;
            R[v].pop_back();
        }
    }
    return R;
}

int main(int argc, char** argv) {
    Args a = parseArgs(argc, argv);
    Timer timer;
    Routes R = greedyBaseline(a.instance);
    printAnswer(a.instance, R, "baseline", timer.sec());
    return 0;
}
