// Поиск маршрутов с отрицательной приведённой стоимостью fix − Σ u[k] (стоимость маршрута в оценке парка — 1,
// км не входят): эвристический (ограниченное число меток на заявку, ближайшие переходы) и точный по ng-маршрутам с
// оценкой завершения; точный даёт допустимый минимум приведённой стоимости по типу бригады.
#pragma once
#include <queue>
#include "bound/columns.hpp"

namespace dispatch::bound {

struct PricedRoute {
    int t;
    double rc, km;
    vector<int> r;
};

class HeuristicPricer {
public:
    HeuristicPricer(const Problem& p, const VehicleTypes& ty, int nNext = 25) : P(p), TY(ty), N(p.N), at_(p.N) {
        next_.assign(TY.T, vector<vector<int>>(N + 1));
        for (int t = 0; t < TY.T; t++) {
            const int v = TY.rep[t];
            for (int k = 0; k <= N; k++) {   // k == N — старт бригады
                if (k < N && !P.can(v, k)) continue;
                const int nk = k < N ? P.nd[k] : P.start[v];
                const double earliest = k < N ? P.a[k] + P.svc[k] : 0;
                vector<std::pair<double, int>> c;
                for (int j = 0; j < N; j++) {
                    if (j == k || !P.can(v, j)) continue;
                    double arrive = max(earliest + P.t(v, nk, P.nd[j]), P.a[j]);
                    if (arrive > P.b[j] + TOL) continue;
                    double wait = max(0.0, P.a[j] - (k < N ? P.b[k] + P.svc[k] : 0) - P.t(v, nk, P.nd[j]));
                    c.push_back({P.d(v, nk, P.nd[j]) + 0.02 * wait, j});
                }
                std::sort(c.begin(), c.end());
                const int limit = k < N ? nNext : 2 * nNext;
                for (int i = 0; i < (int)c.size() && i < limit; i++) next_[t][k].push_back(c[i].second);
            }
        }
    }

    // До maxCols лучших элементарных маршрутов типа t с rc < 0 (разные множества заявок) дописываются в out.
    // Не больше maxLabels меток на заявку.
    void price(int t, const double* u, double fix, int maxLabels, int maxCols, vector<PricedRoute>& out) {
        labels_.clear();
        for (vector<int>& a : at_) a.clear();
        const int v = TY.rep[t];
        std::priority_queue<std::pair<double, int>, vector<std::pair<double, int>>, std::greater<>> queue;
        vector<std::pair<double, int>> negative;
        auto tryAdd = [&](Label X) {
            vector<int>& lst = at_[X.node];
            for (int id : lst) {
                const Label& Y = labels_[id];
                if (Y.alive && Y.t <= X.t + 1e-9 && Y.cost <= X.cost + 1e-9) return;
            }
            size_t w = 0;
            for (size_t i = 0; i < lst.size(); i++) {
                Label& Y = labels_[lst[i]];
                if (!Y.alive) continue;
                if (X.t <= Y.t + 1e-9 && X.cost <= Y.cost + 1e-9) {
                    Y.alive = false;
                    continue;
                }
                lst[w++] = lst[i];
            }
            lst.resize(w);
            if ((int)lst.size() >= maxLabels) {   // вытеснить самую дорогую метку
                int worst = -1;
                double worstCost = -1e18;
                for (size_t i = 0; i < lst.size(); i++)
                    if (labels_[lst[i]].cost > worstCost) {
                        worstCost = labels_[lst[i]].cost;
                        worst = (int)i;
                    }
                if (X.cost >= worstCost) return;
                labels_[lst[worst]].alive = false;
                lst.erase(lst.begin() + worst);
            }
            int id = (int)labels_.size();
            X.alive = true;
            labels_.push_back(X);
            lst.push_back(id);
            queue.push({X.t, id});
        };
        for (int j : next_[t][N]) {
            int nj = P.nd[j];
            double begin = max(P.t(v, P.start[v], nj), P.a[j]);
            if (begin > P.b[j] + TOL) continue;
            Label X;
            X.node = j;
            X.parent = -1;
            X.t = begin + P.svc[j];
            X.km = P.d(v, P.start[v], nj);
            X.cost = -u[j];
            X.visited.clear();
            X.visited.set(j);
            tryAdd(X);
        }
        while (!queue.empty()) {
            int id = queue.top().second;
            queue.pop();
            if (!labels_[id].alive) continue;
            const Label X = labels_[id];
            double rc = fix + X.cost;
            if (rc < -1e-6) negative.push_back({rc, id});
            const int nk = P.nd[X.node];
            for (int j : next_[t][X.node]) {
                if (X.visited.has(j)) continue;
                int nj = P.nd[j];
                double begin = max(X.t + P.t(v, nk, nj), P.a[j]);
                if (begin > P.b[j] + TOL) continue;
                Label Y;
                Y.node = j;
                Y.parent = id;
                Y.t = begin + P.svc[j];
                Y.km = X.km + P.d(v, nk, nj);
                Y.cost = X.cost - u[j];
                Y.visited = X.visited;
                Y.visited.set(j);
                tryAdd(Y);
            }
        }
        std::sort(negative.begin(), negative.end());
        vector<OrderSet> seen;
        for (const auto& [rc, id] : negative) {
            if ((int)seen.size() >= maxCols) break;
            const OrderSet& b = labels_[id].visited;
            if (std::find(seen.begin(), seen.end(), b) != seen.end()) continue;
            seen.push_back(b);
            PricedRoute o{t, rc, labels_[id].km, {}};
            for (int x = id; x >= 0; x = labels_[x].parent) o.r.push_back(labels_[x].node);
            std::reverse(o.r.begin(), o.r.end());
            out.push_back(std::move(o));
        }
    }

private:
    struct Label {
        int node, parent;
        double t, cost, km;
        OrderSet visited;
        bool alive;
    };
    const Problem& P;
    const VehicleTypes& TY;
    const int N;
    vector<vector<vector<int>>> next_;   // next_[t][k] — кандидаты на следующую заявку
    vector<Label> labels_;
    vector<vector<int>> at_;
};

// Точный поиск по ng-маршрутам: память метки — только заявки из ng-окрестности текущей (8 ближайших совместимых по
// окнам), поэтому ослабление даёт нижнюю оценку минимума rc. Оценка завершения F[k][время] — минимум стоимости
// продолжения из k по времени с шагом 2 мин — отсекает метки. При переполнении числа меток возвращается оценка
// завершения от старта (тоже допустимая).
class ExactPricer {
public:
    const Timer* clock = nullptr;
    double deadline = 1e18;

    ExactPricer(const Problem& p, const VehicleTypes& ty, int ngSize = 8) : P(p), TY(ty), N(p.N) {
        allowed_.resize(TY.T);
        for (int t = 0; t < TY.T; t++)
            for (int k = 0; k < N; k++)
                if (P.can(TY.rep[t], k)) allowed_[t].push_back(k);
        ng_.resize(N);
        for (int k = 0; k < N; k++) {
            vector<std::pair<double, int>> c;
            for (int j = 0; j < N; j++) {
                if (j == k) continue;
                bool kj = P.a[k] + P.svc[k] <= P.b[j] + 1e-9, jk = P.a[j] + P.svc[j] <= P.b[k] + 1e-9;
                if (kj && jk) c.push_back({P.g(k, j), j});
            }
            std::sort(c.begin(), c.end());
            ng_[k].clear();
            ng_[k].set(k);
            for (int i = 0; i < (int)c.size() && i < ngSize - 1; i++) ng_[k].set(c[i].second);
        }
        F_.resize(N);
        F0_.resize(N);
    }

    // Минимум rc маршрутов типа t (точный, если complete, иначе допустимая оценка снизу). Элементарные маршруты с
    // rc < 0 (до maxOut, разные множества, лучшие первыми) дописываются в out как (км, последовательность).
    double minReducedCost(int t, const double* u, double fix, size_t labelCap, bool& complete,
                          vector<std::pair<double, vector<int>>>& out, int maxOut) {
        u_ = u;
        fix_ = fix;
        if (!computeCompletion(t)) {
            complete = false;
            return -1e18;
        }
        const int v = TY.rep[t];
        labels_.clear();
        vector<vector<int>> at(N);
        std::priority_queue<std::pair<double, int>, vector<std::pair<double, int>>, std::greater<>> queue;
        double minRc = 1e18;
        auto tryAdd = [&](Label X) {
            double lb = fix_ + X.cost + completion(X.node, X.t);
            if (lb >= -1e-9) {
                minRc = min(minRc, lb);
                return;
            }
            vector<int>& lst = at[X.node];
            for (int id : lst) {
                const Label& Y = labels_[id];
                if (Y.alive && Y.t <= X.t + 1e-9 && Y.cost <= X.cost + 1e-9 && Y.memory.subsetOf(X.memory)) return;
            }
            size_t w = 0;
            for (size_t i = 0; i < lst.size(); i++) {
                Label& Y = labels_[lst[i]];
                if (!Y.alive) continue;
                if (X.t <= Y.t + 1e-9 && X.cost <= Y.cost + 1e-9 && X.memory.subsetOf(Y.memory)) {
                    Y.alive = false;
                    continue;
                }
                lst[w++] = lst[i];
            }
            lst.resize(w);
            int id = (int)labels_.size();
            X.alive = true;
            labels_.push_back(X);
            lst.push_back(id);
            queue.push({X.t, id});
        };
        for (int j : allowed_[t]) {
            double begin = max(P.t(v, P.start[v], P.nd[j]), P.a[j]);
            if (begin > P.b[j] + TOL) continue;
            Label X;
            X.node = j;
            X.parent = -1;
            X.t = begin + P.svc[j];
            X.km = P.d(v, P.start[v], P.nd[j]);
            X.cost = -u_[j];
            X.memory.clear();
            X.memory.set(j);
            tryAdd(X);
        }
        complete = true;
        vector<std::pair<double, int>> negative;
        while (!queue.empty()) {
            int id = queue.top().second;
            queue.pop();
            if (!labels_[id].alive) continue;
            if (labels_.size() > labelCap || (clock && (labels_.size() & 255) == 0 && clock->sec() > deadline)) {
                complete = false;
                break;
            }
            const Label X = labels_[id];
            double rc = fix_ + X.cost;
            minRc = min(minRc, rc);
            if (rc < -1e-6) negative.push_back({rc, id});
            const int nk = P.nd[X.node];
            for (int j : allowed_[t]) {
                if (X.memory.has(j)) continue;
                double begin = max(X.t + P.t(v, nk, P.nd[j]), P.a[j]);
                if (begin > P.b[j] + TOL) continue;
                Label Y;
                Y.node = j;
                Y.parent = id;
                Y.t = begin + P.svc[j];
                Y.km = X.km + P.d(v, nk, P.nd[j]);
                Y.cost = X.cost - u_[j];
                for (int w = 0; w < OrderSet::WORDS; w++) Y.memory.w[w] = X.memory.w[w] & ng_[j].w[w];
                Y.memory.set(j);
                tryAdd(Y);
            }
        }
        std::sort(negative.begin(), negative.end());
        vector<OrderSet> seen;
        for (const auto& [rc, id] : negative) {
            if ((int)seen.size() >= maxOut) break;
            vector<int> r;
            OrderSet b;
            b.clear();
            bool elementary = true;
            for (int x = id; x >= 0 && elementary; x = labels_[x].parent) {
                int k = labels_[x].node;
                elementary = !b.has(k);
                b.set(k);
                r.push_back(k);
            }
            if (!elementary || std::find(seen.begin(), seen.end(), b) != seen.end()) continue;
            seen.push_back(b);
            std::reverse(r.begin(), r.end());
            out.push_back({labels_[id].km, r});
        }
        if (!complete) return depotBound(t);
        return minRc > 1e17 ? 0 : minRc;
    }

private:
    static constexpr int BUCKET = 2;   // шаг оценки завершения, мин
    struct Label {
        int node, parent;
        double t, cost, km;
        OrderSet memory;
        bool alive;
    };
    const Problem& P;
    const VehicleTypes& TY;
    const int N;
    vector<OrderSet> ng_;
    vector<vector<int>> allowed_;
    vector<vector<double>> F_;   // F_[k][(время окончания − F0_[k]) / BUCKET]
    vector<int> F0_;
    const double* u_ = nullptr;
    double fix_ = 0;
    vector<Label> labels_;

    double completion(int k, double finish) const {
        int i = ((int)floor(finish + 1e-9) - F0_[k]) / BUCKET;
        i = std::clamp(i, 0, (int)F_[k].size() - 1);
        return F_[k][i];
    }
    // Оценка завершения обходом времени от конца смены к началу; false — не успели до deadline.
    bool computeCompletion(int t) {
        const int v = TY.rep[t];
        const vector<int>& al = allowed_[t];
        for (int k : al) {
            F0_[k] = (int)floor(P.a[k] + P.svc[k]);
            int hi = (int)floor(P.b[k] + P.svc[k] + 1e-9);
            F_[k].assign((hi - F0_[k]) / BUCKET + 1, 0.0);
        }
        const int maxT = (int)SHIFT + 1;
        vector<vector<std::pair<int, int>>> byMinute(maxT + 1);   // (заявка, ячейка), начинающиеся в эту минуту
        for (int k : al)
            for (int i = 0; i < (int)F_[k].size(); i++) byMinute[min(F0_[k] + i * BUCKET, (int)SHIFT)].push_back({k, i});
        for (int m = maxT; m >= 0; m--) {
            if (clock && (m & 15) == 0 && clock->sec() > deadline) return false;
            for (auto [k, i] : byMinute[m]) {
                double best = 0;
                const int nk = P.nd[k];
                for (int j : al) {
                    if (j == k) continue;
                    double begin = max(m + P.t(v, nk, P.nd[j]), P.a[j]);
                    if (begin > P.b[j] + TOL) continue;
                    best = min(best, -u_[j] + completion(j, begin + P.svc[j]));   // окончание позже m: svc > 0
                }
                F_[k][i] = best;
            }
        }
        return true;
    }
    double depotBound(int t) const {
        const int v = TY.rep[t];
        double best = 0;
        for (int j : allowed_[t]) {
            double begin = max(P.t(v, P.start[v], P.nd[j]), P.a[j]);
            if (begin > P.b[j] + TOL) continue;
            best = min(best, -u_[j] + completion(j, begin + P.svc[j]));
        }
        return fix_ + best;
    }
};

}  // namespace dispatch::bound
