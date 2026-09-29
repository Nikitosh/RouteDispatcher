// 01. Та же жадность, но заявки отсортированы по началу окна (затем приоритет). Честный «умный» базовый вариант.
#include "common.hpp"
Routes solve(const Instance& I, double, uint64_t) {
    vector<int> ord(I.N); iota(ord.begin(), ord.end(), 0);
    stable_sort(ord.begin(), ord.end(), [&](int x, int y) {
        if (I.ord[x].a != I.ord[y].a) return I.ord[x].a < I.ord[y].a; return I.ord[x].pri < I.ord[y].pri; });
    Routes R(I.V);
    for (int k : ord)
        for (int v = 0; v < I.V; v++) { auto r = R[v]; r.push_back(k); if (routeFeasible(I, v, r)) { R[v] = r; break; } }
    return R;
}
int main(int c, char** v) { return runMain(c, v, solve, "01_sorted_firstfit"); }
