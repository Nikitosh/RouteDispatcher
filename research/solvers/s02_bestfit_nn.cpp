// 02. Жадность «лучшая подходящая»: по началу окна, заявка идёт бригаде с минимальным приростом пробега
// при добавлении в конец; открытие новой бригады штрафуется. Затем базовый локальный поиск.
#include "common.hpp"
Routes solve(const Instance& I, double tl, uint64_t) {
    Timer tm;
    vector<int> ord(I.N); iota(ord.begin(), ord.end(), 0);
    stable_sort(ord.begin(), ord.end(), [&](int x, int y) {
        if (I.ord[x].b != I.ord[y].b) return I.ord[x].b < I.ord[y].b; return I.ord[x].pri < I.ord[y].pri; });
    Routes R(I.V);
    for (int k : ord) {
        int bv = -1; double best = 1e18;
        for (int v = 0; v < I.V; v++) {
            auto r = R[v]; r.push_back(k); double km;
            if (!routeFeasible(I, v, r, &km)) continue;
            double c = km - routeKm(I, v, R[v]) + (R[v].empty() ? W_VEHICLE : 0);
            if (c < best) best = c, bv = v;
        }
        if (bv >= 0) R[bv].push_back(k);
    }
    insertUnserved(I, R);
    localSearch(I, R, max(0.0, tl * 0.95 - tm.sec()));
    return R;
}
int main(int c, char** v) { return runMain(c, v, solve, "02_bestfit_nn+ls"); }
