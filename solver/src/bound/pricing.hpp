// Поиск маршрутов с отрицательной приведённой стоимостью fix − Σ u[k] (стоимость маршрута в оценке парка — 1,
// км не входят): эвристический (ограниченное число меток на заявку, ближайшие переходы) и точный по ng-маршрутам с
// оценкой завершения; точный даёт допустимый минимум приведённой стоимости по типу бригады.
//
// Метка, кроме заявки и времени окончания последней работы, помнит, был ли обед, и сколько оборудования уже взято.
// Обед (core/problem.hpp, правило в docs/SOLVER_IO.md) перебирается во всех промежутках: из метки «обед впереди»
// (A) переход к заявке j даёт метку A (без обеда) и метку B «обед был» — обед в этом промежутке по lunchGap, на месте
// j или перед выездом. Расписание с обедом в данном промежутке — ровно то, что строит core/problem.hpp, поэтому
// маршрут допустим по правилу тогда и только тогда, когда его можно пройти метками и закончить меткой B или меткой A,
// после которой обед ещё помещается (settle). Метка A с окончанием позже обед_до мертва: обеду больше негде быть.
// Все переходы монотонны по времени, поэтому метка с более ранним временем, не большей стоимостью, не меньшим «обед
// был» и не большим оборудованием (и меньшей ng-памятью) доминирует: всё, что можно продолжить из второй, можно и
// из первой. Оборудование — сумма по маршруту, проверяется запасом типа после каждой заявки. Длительности работ —
// бригады-представителя типа (темп входит в ключ типа, VehicleTypes).
#pragma once
#include <unordered_map>
#include <queue>
#include "bound/columns.hpp"

namespace dispatch::bound {

struct PricedRoute {
    int t;
    double rc, km;
    vector<int> r;
};

// Правила маршрута одного типа бригад: окна с концом смены, обед, запас оборудования.
struct TypeRules {
    int v = 0;              // представитель типа
    bool lunch = false;     // обед нужен
    bool equip = false;     // оборудование ограничено
    Lunch L;
    double end = SHIFT, begin = 0;
    int cap[NITEMS] = {UNLIMITED, UNLIMITED}, capTot = UNLIMITED;

    TypeRules() = default;
    TypeRules(const Problem& P, const VehicleTypes& TY, int t) : v(TY.rep[t]), lunch(TY.lunch[t]), equip(TY.equip[t]) {
        const Vehicle& x = P.I.veh[v];
        L = x.lunch;
        end = x.end;
        begin = x.begin;
        for (int i = 0; i < NITEMS; i++) cap[i] = x.cap[i];
        capTot = x.capTot;
    }
    // Состояние после работы: окончание t, обед был (fed), оборудование q.
    struct State {
        double t;
        bool fed;
    };
    // Состояние на старте: выезд уже в матрице (t = 0), свободна с begin; обед впереди, если он нужен.
    State origin() const { return {0, !lunch}; }
    double freeAt(const State& s, bool atStart) const { return atStart ? begin : s.t; }
    // Переходы из s (свободна с free) к работе: переезд tr, окно a..last, длительность svc. До двух состояний в out.
    int step(const State& s, double free, double tr, double a, double last, double svc, State out[2]) const {
        int n = 0;
        const double arr = s.t + tr, st = max(arr, a);
        if (s.fed) {
            if (st <= last + TOL) out[n++] = {st + svc, true};
            return n;
        }
        const double sL = lunchGap(free, arr, a, L);
        if (st <= last + TOL && st + svc <= L.lb + LUNCH_TOL && sL > st + 1e-9) out[n++] = {st + svc, false};
        if (sL <= last + TOL) out[n++] = {sL + svc, true};
        return n;
    }
    // Можно ли закончить маршрут в состоянии s (непустой маршрут): обед был или помещается после последней работы.
    bool closes(const State& s) const {
        if (s.fed) return true;
        const double x = max(s.t, L.la);
        return x <= L.lb + LUNCH_TOL && x + L.ld <= end + TOL;
    }
    // Оборудование: добавить заявку с упакованной потребностью e к взятому q (по видам); false — не помещается.
    bool take(const int16_t* q, int e, int16_t* out) const {
        if (!equip) {
            for (int i = 0; i < NITEMS; i++) out[i] = 0;
            return true;
        }
        int tot = 0;
        for (int i = 0; i < NITEMS; i++) {
            int x = q[i] + eqItem(e, i);
            if (x > cap[i]) return false;
            out[i] = (int16_t)min(x, 30000);
            tot += x;
        }
        return tot <= capTot;
    }
};

inline bool eqLeq(const int16_t* a, const int16_t* b) {
    for (int i = 0; i < NITEMS; i++)
        if (a[i] > b[i]) return false;
    return true;
}

class HeuristicPricer {
public:
    HeuristicPricer(const Problem& p, const VehicleTypes& ty, int nNext = 25) : P(p), TY(ty), N(p.N), at_(p.N) {
        next_.assign(TY.T, vector<vector<int>>(N + 1));
        rules_.resize(TY.T);
        for (int t = 0; t < TY.T; t++) {
            rules_[t] = TypeRules(P, TY, t);
            const int v = TY.rep[t];
            const int16_t zero[NITEMS] = {};
            int16_t q[NITEMS];
            auto ok = [&](int k) { return P.can(v, k) && rules_[t].take(zero, P.eq[k], q); };
            for (int k = 0; k <= N; k++) {   // k == N — старт бригады
                if (k < N && !ok(k)) continue;
                const int nk = k < N ? P.nd[k] : P.start[v];
                const double earliest = k < N ? P.a[k] + P.sv(k, v) : 0;
                vector<std::pair<double, int>> c;
                for (int j = 0; j < N; j++) {
                    if (j == k || !ok(j)) continue;
                    double arrive = max(earliest + P.t(v, nk, P.nd[j]), P.a[j]);
                    if (arrive > P.bv(j, v) + TOL) continue;
                    double wait = max(0.0, P.a[j] - (k < N ? P.b[k] + P.sv(k, v) : 0) - P.t(v, nk, P.nd[j]));
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
        const TypeRules& R = rules_[t];
        std::priority_queue<std::pair<double, int>, vector<std::pair<double, int>>, std::greater<>> queue;
        vector<std::pair<double, int>> negative;
        auto tryAdd = [&](Label X) {
            vector<int>& lst = at_[X.node];
            for (int id : lst) {
                const Label& Y = labels_[id];
                if (Y.alive && Y.t <= X.t + 1e-9 && Y.cost <= X.cost + 1e-9 && (Y.fed || !X.fed) && eqLeq(Y.q, X.q)) return;
            }
            size_t w = 0;
            for (size_t i = 0; i < lst.size(); i++) {
                Label& Y = labels_[lst[i]];
                if (!Y.alive) continue;
                if (X.t <= Y.t + 1e-9 && X.cost <= Y.cost + 1e-9 && (X.fed || !Y.fed) && eqLeq(X.q, Y.q)) {
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
        TypeRules::State next[2];
        const int16_t zero[NITEMS] = {};
        for (int j : next_[t][N]) {
            int nj = P.nd[j];
            Label X;
            if (!R.take(zero, P.eq[j], X.q)) continue;
            const int n = R.step(R.origin(), R.begin, P.t(v, P.start[v], nj), P.a[j], P.bv(j, v), P.sv(j, v), next);
            X.node = j;
            X.parent = -1;
            X.km = P.d(v, P.start[v], nj);
            X.cost = -u[j];
            X.visited.clear();
            X.visited.set(j);
            for (int i = 0; i < n; i++) {
                X.t = next[i].t;
                X.fed = next[i].fed;
                tryAdd(X);
            }
        }
        while (!queue.empty()) {
            int id = queue.top().second;
            queue.pop();
            if (!labels_[id].alive) continue;
            const Label X = labels_[id];
            double rc = fix + X.cost;
            if (rc < -1e-6 && R.closes({X.t, X.fed})) negative.push_back({rc, id});
            const int nk = P.nd[X.node];
            for (int j : next_[t][X.node]) {
                if (X.visited.has(j)) continue;
                int nj = P.nd[j];
                Label Y;
                if (!R.take(X.q, P.eq[j], Y.q)) continue;
                const int n = R.step({X.t, X.fed}, X.t, P.t(v, nk, nj), P.a[j], P.bv(j, v), P.sv(j, v), next);
                Y.node = j;
                Y.parent = id;
                Y.km = X.km + P.d(v, nk, nj);
                Y.cost = X.cost - u[j];
                Y.visited = X.visited;
                Y.visited.set(j);
                for (int i = 0; i < n; i++) {
                    Y.t = next[i].t;
                    Y.fed = next[i].fed;
                    tryAdd(Y);
                }
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
        int16_t q[NITEMS];
        bool fed, alive;
    };
    const Problem& P;
    const VehicleTypes& TY;
    const int N;
    vector<TypeRules> rules_;
    vector<vector<vector<int>>> next_;   // next_[t][k] — кандидаты на следующую заявку
    vector<Label> labels_;
    vector<vector<int>> at_;
};

// Точный поиск по ng-маршрутам: память метки — только заявки из ng-окрестности текущей (8 ближайших совместимых по
// окнам), поэтому ослабление даёт нижнюю оценку минимума rc. Оценка завершения F[k][время] — минимум стоимости
// продолжения из k по времени с шагом 2 мин (отдельно для «обед был» и «обед впереди»; оборудование в ней не
// учитывается — это только ослабление) — отсекает метки. При переполнении числа меток возвращается оценка
// завершения от старта (тоже допустимая).
class ExactPricer {
public:
    const Timer* clock = nullptr;
    double deadline = 1e18;

    ExactPricer(const Problem& p, const VehicleTypes& ty, int ngSize = 8) : P(p), TY(ty), N(p.N) {
        allowed_.resize(TY.T);
        rules_.resize(TY.T);
        for (int t = 0; t < TY.T; t++) {
            rules_[t] = TypeRules(P, TY, t);
            const int16_t zero[NITEMS] = {};
            int16_t q[NITEMS];
            for (int k = 0; k < N; k++)
                if (P.can(TY.rep[t], k) && P.a[k] <= P.bv(k, TY.rep[t]) + TOL && rules_[t].take(zero, P.eq[k], q))
                    allowed_[t].push_back(k);
        }
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
        FA_.resize(N);
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
        const TypeRules& R = rules_[t];
        labels_.clear();
        vector<vector<int>> at(N);
        std::priority_queue<std::pair<double, int>, vector<std::pair<double, int>>, std::greater<>> queue;
        double minRc = 1e18;
        auto tryAdd = [&](Label X) {
            double lb = fix_ + X.cost + completion(X.node, X.t, X.fed);
            if (lb >= -1e-9) {
                minRc = min(minRc, lb);
                return;
            }
            vector<int>& lst = at[X.node];
            for (int id : lst) {
                const Label& Y = labels_[id];
                if (Y.alive && Y.t <= X.t + 1e-9 && Y.cost <= X.cost + 1e-9 && (Y.fed || !X.fed) && eqLeq(Y.q, X.q) &&
                    Y.memory.subsetOf(X.memory))
                    return;
            }
            size_t w = 0;
            for (size_t i = 0; i < lst.size(); i++) {
                Label& Y = labels_[lst[i]];
                if (!Y.alive) continue;
                if (X.t <= Y.t + 1e-9 && X.cost <= Y.cost + 1e-9 && (X.fed || !Y.fed) && eqLeq(X.q, Y.q) &&
                    X.memory.subsetOf(Y.memory)) {
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
        TypeRules::State next[2];
        const int16_t zero[NITEMS] = {};
        for (int j : allowed_[t]) {
            Label X;
            R.take(zero, P.eq[j], X.q);
            const int n = R.step(R.origin(), R.begin, P.t(v, P.start[v], P.nd[j]), P.a[j], P.bv(j, v), P.sv(j, v), next);
            X.node = j;
            X.parent = -1;
            X.km = P.d(v, P.start[v], P.nd[j]);
            X.cost = -u_[j];
            X.memory.clear();
            X.memory.set(j);
            for (int i = 0; i < n; i++) {
                X.t = next[i].t;
                X.fed = next[i].fed;
                tryAdd(X);
            }
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
            if (R.closes({X.t, X.fed})) {
                double rc = fix_ + X.cost;
                minRc = min(minRc, rc);
                if (rc < -1e-6) negative.push_back({rc, id});
            }
            const int nk = P.nd[X.node];
            for (int j : allowed_[t]) {
                if (X.memory.has(j)) continue;
                Label Y;
                if (!R.take(X.q, P.eq[j], Y.q)) continue;
                const int n = R.step({X.t, X.fed}, X.t, P.t(v, nk, P.nd[j]), P.a[j], P.bv(j, v), P.sv(j, v), next);
                if (!n) continue;
                Y.node = j;
                Y.parent = id;
                Y.km = X.km + P.d(v, nk, P.nd[j]);
                Y.cost = X.cost - u_[j];
                for (int w = 0; w < OrderSet::WORDS; w++) Y.memory.w[w] = X.memory.w[w] & ng_[j].w[w];
                Y.memory.set(j);
                for (int i = 0; i < n; i++) {
                    Y.t = next[i].t;
                    Y.fed = next[i].fed;
                    tryAdd(Y);
                }
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

    // Все элементарные маршруты типа t с rc = fix − Σ u ≤ threshold (по одному на множество заявок) — в out.
    // Метки помнят все заявки маршрута; метка отбрасывается, только если её продолжения заведомо дороже порога
    // (оценка завершения — нижняя) или есть метка с тем же множеством заявок в той же заявке, раньше, с обедом не
    // позже и не большим оборудованием. complete = false, если упёрлись в labelCap или deadline.
    void enumerate(int t, const double* u, double fix, double threshold, size_t labelCap, bool& complete,
                   vector<vector<int>>& out) {
        u_ = u;
        fix_ = fix;
        complete = computeCompletion(t);
        if (!complete) return;
        const int v = TY.rep[t];
        const TypeRules& R = rules_[t];
        labels_.clear();
        vector<std::unordered_map<SetKey, vector<int>, SetHash>> at(N);
        std::priority_queue<std::pair<double, int>, vector<std::pair<double, int>>, std::greater<>> queue;
        auto tryAdd = [&](Label X) {
            if (fix_ + X.cost + completion(X.node, X.t, X.fed) > threshold + 1e-9) return;
            vector<int>& lst = at[X.node][SetKey{X.memory}];
            for (int id : lst) {
                const Label& Y = labels_[id];
                if (Y.alive && Y.t <= X.t + 1e-9 && (Y.fed || !X.fed) && eqLeq(Y.q, X.q)) return;
            }
            size_t w = 0;
            for (size_t i = 0; i < lst.size(); i++) {
                Label& Y = labels_[lst[i]];
                if (Y.alive && X.t <= Y.t + 1e-9 && (X.fed || !Y.fed) && eqLeq(X.q, Y.q)) {
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
        TypeRules::State next[2];
        const int16_t zero[NITEMS] = {};
        for (int j : allowed_[t]) {
            if (u_[j] < -1e8) continue;
            Label X;
            R.take(zero, P.eq[j], X.q);
            const int n = R.step(R.origin(), R.begin, P.t(v, P.start[v], P.nd[j]), P.a[j], P.bv(j, v), P.sv(j, v), next);
            X.node = j;
            X.parent = -1;
            X.km = 0;
            X.cost = -u_[j];
            X.memory.clear();
            X.memory.set(j);
            for (int i = 0; i < n; i++) {
                X.t = next[i].t;
                X.fed = next[i].fed;
                tryAdd(X);
            }
        }
        std::unordered_map<SetKey, int, SetHash> found;   // множество заявок → метка
        while (!queue.empty()) {
            int id = queue.top().second;
            queue.pop();
            if (!labels_[id].alive) continue;
            if (labels_.size() > labelCap || (clock && (labels_.size() & 255) == 0 && clock->sec() > deadline)) {
                complete = false;
                break;
            }
            const Label X = labels_[id];
            if (R.closes({X.t, X.fed}) && fix_ + X.cost <= threshold + 1e-9) found.emplace(SetKey{X.memory}, id);
            const int nk = P.nd[X.node];
            for (int j : allowed_[t]) {
                if (X.memory.has(j) || u_[j] < -1e8) continue;
                Label Y;
                if (!R.take(X.q, P.eq[j], Y.q)) continue;
                const int n = R.step({X.t, X.fed}, X.t, P.t(v, nk, P.nd[j]), P.a[j], P.bv(j, v), P.sv(j, v), next);
                if (!n) continue;
                Y.node = j;
                Y.parent = id;
                Y.km = 0;
                Y.cost = X.cost - u_[j];
                Y.memory = X.memory;
                Y.memory.set(j);
                for (int i = 0; i < n; i++) {
                    Y.t = next[i].t;
                    Y.fed = next[i].fed;
                    tryAdd(Y);
                }
            }
        }
        for (const auto& [key, id] : found) {
            vector<int> r;
            for (int x = id; x >= 0; x = labels_[x].parent) r.push_back(labels_[x].node);
            std::reverse(r.begin(), r.end());
            out.push_back(r);
        }
    }

private:
    struct SetKey {
        OrderSet s;
        bool operator==(const SetKey& o) const { return s == o.s; }
    };
    struct SetHash {
        size_t operator()(const SetKey& k) const {
            return std::hash<uint64_t>()(k.s.w[0] * 0x9E3779B97F4A7C15ULL ^ k.s.w[1] * 0xC2B2AE3D27D4EB4FULL ^ k.s.w[2]);
        }
    };
    static constexpr int BUCKET = 2;        // шаг оценки завершения, мин
    static constexpr double DEAD = 1e18;    // из состояния маршрут не закончить
    struct Label {
        int node, parent;
        double t, cost, km;
        OrderSet memory;
        int16_t q[NITEMS];
        bool fed, alive;
    };
    const Problem& P;
    const VehicleTypes& TY;
    const int N;
    vector<OrderSet> ng_;
    vector<vector<int>> allowed_;
    vector<TypeRules> rules_;
    vector<vector<double>> F_, FA_;   // F_[k][(время окончания − F0_[k]) / BUCKET]: обед был; FA_ — обед впереди
    vector<int> F0_;
    const double* u_ = nullptr;
    double fix_ = 0;
    vector<Label> labels_;

    // Оценка снизу стоимости продолжения из k после окончания в finish. Берётся ячейка с началом не позже finish:
    // из более раннего момента продолжений не меньше (все переходы монотонны), значит, оценка не выше.
    double completion(int k, double finish, bool fed) const {
        int i = ((int)floor(finish + 1e-9) - F0_[k]) / BUCKET;
        i = std::clamp(i, 0, (int)F_[k].size() - 1);
        return fed ? F_[k][i] : FA_[k][i];
    }
    // Лучшее продолжение из состояния s в k (k < 0 — старт): минимум −u[j] + оценка завершения из j.
    double bestNext(int t, int k, const TypeRules::State& s, double free) const {
        const int v = TY.rep[t];
        const TypeRules& R = rules_[t];
        const int nk = k < 0 ? P.start[v] : P.nd[k];
        double best = k < 0 || R.closes(s) ? 0 : DEAD;
        TypeRules::State next[2];
        for (int j : allowed_[t]) {
            if (j == k) continue;
            const int n = R.step(s, free, P.t(v, nk, P.nd[j]), P.a[j], P.bv(j, v), P.sv(j, v), next);
            for (int i = 0; i < n; i++) best = min(best, -u_[j] + completion(j, next[i].t, next[i].fed));   // позже: svc > 0
        }
        return best;
    }
    // Оценка завершения обходом времени от конца смены к началу; false — не успели до deadline. Ячейки заявки k —
    // от самого раннего окончания у бригады типа (a + длительность с её темпом): раньше работа кончиться не может.
    bool computeCompletion(int t) {
        const vector<int>& al = allowed_[t];
        const bool lunch = rules_[t].lunch;
        const int v = TY.rep[t];
        for (int k : al) {
            F0_[k] = (int)floor(P.a[k] + P.sv(k, v));
            int hi = (int)floor(P.b[k] + P.sv(k, v) + 1e-9);
            F_[k].assign(max(0, hi - F0_[k]) / BUCKET + 1, 0.0);   // окно может не влезать в смены: hi < F0
            FA_[k].assign(F_[k].size(), DEAD);
        }
        const int maxT = (int)SHIFT + 1;
        vector<vector<std::pair<int, int>>> byMinute(maxT + 1);   // (заявка, ячейка), начинающиеся в эту минуту
        for (int k : al)
            for (int i = 0; i < (int)F_[k].size(); i++) byMinute[min(F0_[k] + i * BUCKET, (int)SHIFT)].push_back({k, i});
        for (int m = maxT; m >= 0; m--) {
            if (clock && (m & 15) == 0 && clock->sec() > deadline) return false;
            for (auto [k, i] : byMinute[m]) {
                F_[k][i] = bestNext(t, k, {(double)m, true}, m);
                if (lunch) FA_[k][i] = bestNext(t, k, {(double)m, false}, m);
            }
        }
        return true;
    }
    double depotBound(int t) const {
        const TypeRules& R = rules_[t];
        return fix_ + bestNext(t, -1, R.origin(), R.begin);
    }
};

}  // namespace dispatch::bound
