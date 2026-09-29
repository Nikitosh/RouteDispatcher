// Сфокусированный удар для сокращения парка: убрать короткий маршрут вместе с большой долей «родственных» заявок
// (близких по месту и времени) из остальных маршрутов и вставить всё обратно regret-вставкой с пределом K-1 бригад.
#pragma once
#include "lns2_util.hpp"
namespace kick {
// Один удар: вернуть решение после удаления короткого маршрута и родственных заявок и regret-вставки с пределом K-1.
inline l2::Sol kickOnce(const Instance& I, const l2::Prob& Pb, const l2::Sol& best, Rng& rng, double maxFrac = 0.35) {
    const int N = Pb.N; const auto& D = I.D[0]; int M = I.M; int K = best.used();
    double sw = 0, w[64]; for (int u = 0; u < Pb.V; u++) { w[u] = best.len[u] ? 1.0 / (best.len[u] * best.len[u]) : 0; sw += w[u]; }
    double x = rng.uni() * sw; int r = -1; for (int u = 0; u < Pb.V; u++) { x -= w[u]; if (w[u] > 0 && x <= 0) { r = u; break; } }
    l2::Sol c = best; if (r < 0) return c;
    vector<double> rel(N); vector<int> ord(N), rem, pool; double tw = 0.5 + rng.uni() * 3;
    for (int j = 0; j < N; j++) { double m = 1e18;
        for (int q = 0; q < best.len[r]; q++) { int k = best.r[r][q]; m = min(m, D[I.node(j) * M + I.node(k)] + tw * fabs(I.ord[j].a - I.ord[k].a) / 60.0); }
        rel[j] = m * (0.8 + 0.4 * rng.uni()); }
    iota(ord.begin(), ord.end(), 0); sort(ord.begin(), ord.end(), [&](int a, int b) { return rel[a] < rel[b]; });
    int q = best.len[r] + (int)(rng.uni() * maxFrac * N);
    for (int i = 0; i < min(N, q); i++) rem.push_back(ord[i]);
    l2::removeSet(Pb, c, rem.data(), (int)rem.size(), pool, nullptr);
    if (rng.uni() < 0.5) for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
    else sort(pool.begin(), pool.end(), [&](int a, int b) { return I.ord[a].b - I.ord[a].a < I.ord[b].b - I.ord[b].a; });
    l2::Regret RG; RG.run(Pb, c, pool, rng, 1 + rng.randint(3), K - 1, W_VEHICLE, nullptr, rng.uni() * 0.2);
    return c;
}
// Возвращает true, если удалось уменьшить парк; best меняется на новое решение.
inline bool fleetKick(const Instance& I, const l2::Prob& Pb, l2::Sol& best, Rng& rng, Timer& tm, double tEnd, long* attempts = nullptr, double maxFrac = 0.35, int* minLeft = nullptr) {
    if (best.nUn() > 0 || best.used() <= 1) return false;
    const int N = Pb.N; const auto& D = I.D[0]; int M = I.M;
    vector<double> rel(N); vector<int> ord(N), rem; rem.reserve(N); vector<int> pool; pool.reserve(N);
    bool any = false;
    while (tm.sec() < tEnd) {
        int K = best.used();
        double sw = 0, w[64]; for (int u = 0; u < Pb.V; u++) { w[u] = best.len[u] ? 1.0 / (best.len[u] * best.len[u]) : 0; sw += w[u]; }
        double x = rng.uni() * sw; int r = -1; for (int u = 0; u < Pb.V; u++) { x -= w[u]; if (w[u] > 0 && x <= 0) { r = u; break; } }
        if (r < 0) break;
        double tw = 0.5 + rng.uni() * 3;   // вес времени в родстве
        for (int j = 0; j < N; j++) {
            double m = 1e18;
            for (int q = 0; q < best.len[r]; q++) { int k = best.r[r][q];
                double d = D[I.node(j) * M + I.node(k)] + tw * fabs(I.ord[j].a - I.ord[k].a) / 60.0; m = min(m, d); }
            rel[j] = m * (0.8 + 0.4 * rng.uni());
        }
        iota(ord.begin(), ord.end(), 0); sort(ord.begin(), ord.end(), [&](int a, int b) { return rel[a] < rel[b]; });
        int q = best.len[r] + (int)(rng.uni() * maxFrac * N);
        l2::Sol c = best; rem.clear(); for (int i = 0; i < min(N, q); i++) rem.push_back(ord[i]);
        pool.clear(); l2::removeSet(Pb, c, rem.data(), (int)rem.size(), pool, nullptr);
        if (rng.uni() < 0.5) for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
        else sort(pool.begin(), pool.end(), [&](int a, int b) { return I.ord[a].b - I.ord[a].a < I.ord[b].b - I.ord[b].a; });
        l2::Regret RG; RG.run(Pb, c, pool, rng, 1 + rng.randint(3), K - 1, W_VEHICLE, nullptr, rng.uni() * 0.2);
        if (attempts) (*attempts)++;
        if (minLeft) *minLeft = min(*minLeft, c.nUn());
        if (c.nUn() == 0 && c.used() < K) { best = c; any = true; }
    }
    return any;
}
}
