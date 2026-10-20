// Доказательство «на K бригадах нельзя» для заявок, которые обслуживает план: генерация столбцов до сходимости,
// затем перебор всех маршрутов, которые могут войти в план на K бригад, и точное покрытие (research/fleet_proof2.py,
// CP-SAT). Использование: fleet_proof <задача> <ответ plan> <K> <секунды> <выход.json> [--common].
//
// Почему перебора хватает. Для цен u ≥ 0 заявок и π ≤ 0 строки «бригад не больше V» любой план из маршрутов x
// стоит z = Σ rc_j + Σu + π·(число маршрутов) ≥ Σ rc_j + Σu + π·V, где rc_j = 1 − π − Σ_{k∈j} u_k. Если у каждого
// типа бригад rc ≥ m_t, сумма rc остальных маршрутов не меньше Σ_t q_t·min(0, m_t) — отсюда лагранжева оценка L
// (bound/fleet_cg.hpp). Значит, в плане с z ≤ K каждый маршрут имеет rc ≤ K − L. Перебор выдаёт все такие
// маршруты (с правилами окон, смены, обеда и оборудования, как в core/problem.hpp); если из них нельзя собрать
// точное покрытие обслуживаемых заявок K маршрутами с не больше чем cnt_t бригадами каждого типа — K бригад мало.
// --common — то же на ослабленной задаче с общим стартом (fleet_bound.cpp): её минимум не больше исходного.
#include "bound/fleet_cg.hpp"

using namespace dispatch;

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

// Оценка для узла: у типа t не больше (exact — ровно) cap[t] бригад; маршруты плана бригад сверх cap не берутся
// в начальный базис.
struct Node {
    double L = -1e18, lag = -1e18, fix = 0;
    bool converged = false;
    vector<double> u, minRc;
};

static int NG = 8;   // память ng-маршрутов в точном поиске: больше — оценка ближе к элементарной ЛП, но дольше

static Node solveLP(const lns::Problem& P, const lns::Solution& plan, const bound::VehicleTypes& types,
                    const vector<int>& cap, bool exactTypes, double stopAt, double seconds) {
    Timer clock;
    bound::VehicleTypes tb = types;
    tb.cnt = cap;
    vector<char> skip(P.V, 0);
    for (int t = 0; t < types.T; t++) {
        int keep = cap[t];
        for (int v : types.vehicles[t])
            if (plan.len[v]) {
                if (keep > 0) keep--;
                else skip[v] = 1;
            }
    }
    bound::ColumnPool pool;
    for (int v = 0; v < P.V; v++)
        if (plan.len[v] && !skip[v]) pool.add(tb.typeOf[v], plan.r[v], plan.len[v], plan.km[v]);
    bound::HeuristicPricer heuristic(P, tb);
    bound::ExactPricer exact(P, tb, NG);
    bound::FleetBound cg(P, tb, pool, heuristic, exact);
    cg.skipStart = skip;
    cg.exactTypes = exactTypes;
    Node n;
    n.L = cg.run(plan, stopAt, clock, seconds);
    n.lag = cg.bestLag;
    n.fix = cg.fixLag;
    n.converged = cg.converged;
    n.u = cg.uLag;
    n.minRc = cg.minRcLag;
    return n;
}

struct Search {
    const lns::Problem& P;
    const lns::Solution& plan;
    const bound::VehicleTypes& types;
    int K;
    double seconds;
    string prefix;
    FILE* log;
    int nodes = 0, leaves = 0;
    bool ok = true;

    string capStr(const vector<int>& cap) const {
        string s;
        for (int t = 0; t < (int)cap.size(); t++) s += (t ? "," : "") + std::to_string(cap[t]);
        return s;
    }
    // Узел cap: план на ≤ K маршрутов с бригадами не больше cap. Типы уменьшаем по неубыванию номера (from).
    void visit(vector<int>& cap, int from) {
        nodes++;
        int sum = 0;
        for (int c : cap) sum += c;
        const Node le = solveLP(P, plan, types, cap, false, K + 1e-6, seconds);
        if (le.L > K + 1e-6) {
            fprintf(log, "{\"cap\": [%s], \"mode\": \"le\", \"L\": %.6f, \"closed\": true}\n", capStr(cap).c_str(), le.L);
            return;
        }
        if (sum <= K) {   // все бригады cap выходят: ровно sum маршрутов
            const Node ex = solveLP(P, plan, types, cap, true, sum + 1e-6, seconds);
            const bool closed = ex.L > sum + 1e-6;
            fprintf(log, "{\"cap\": [%s], \"mode\": \"exact\", \"L\": %.6f, \"converged\": %s, \"closed\": %s", capStr(cap).c_str(),
                    ex.L, ex.converged ? "true" : "false", closed ? "true" : "false");
            if (!closed) {
                const string file = prefix + "_leaf" + std::to_string(leaves++) + ".json";
                const bool complete = enumerateLeaf(cap, ex, sum, file);
                fprintf(log, ", \"file\": \"%s\", \"complete\": %s", file.c_str(), complete ? "true" : "false");
                ok &= complete;
            }
            fprintf(log, "}\n");
        }
        fflush(log);
        for (int t = from; t < types.T; t++)
            if (cap[t] > 0) {
                cap[t]--;
                visit(cap, t);
                cap[t]++;
            }
    }
    // Все маршруты, которые могут войти в план ровно с cap[t] бригадами типов: rc_t ≤ min rc_t + (n − L).
    bool enumerateLeaf(const vector<int>& cap, const Node& ex, int n, const string& file) {
        bound::VehicleTypes tb = types;
        tb.cnt = cap;
        bound::ExactPricer exact(P, tb);
        FILE* f = fopen(file.c_str(), "w");
        fprintf(f, "{\"cap\": [%s], \"n\": %d, \"L\": %.6f, \"cols\": [", capStr(cap).c_str(), n, ex.lag);
        bool first = true, complete = true;
        size_t total = 0;
        for (int t = 0; t < tb.T && complete; t++) {
            if (!cap[t]) continue;
            vector<vector<int>> out;
            bool c;
            exact.enumerate(t, ex.u.data(), ex.fix, ex.minRc[t] + (n - ex.lag), 30000000, c, out);
            complete &= c;
            total += out.size();
            if (total > 3000000) complete = false;
            for (const auto& r : out) {
                fprintf(f, "%s[%d, [", first ? "" : ", ", t);
                for (size_t i = 0; i < r.size(); i++) fprintf(f, "%s%d", i ? ", " : "", r[i]);
                fprintf(f, "]]");
                first = false;
            }
        }
        fprintf(f, "], \"complete\": %s}\n", complete ? "true" : "false");
        fclose(f);
        fprintf(stderr, "лист [%s]: ЛП %.4f при %d маршрутах, перебор %zu%s\n", capStr(cap).c_str(), ex.lag, n, total,
                complete ? "" : " (неполный)");
        return complete;
    }
};

int main(int argc, char** argv) {
    if (argc < 6) {
        fprintf(stderr, "использование: %s <задача> <ответ> <K> <секунды на ЛП> <префикс выхода> [--common] [--ng=8]\n", argv[0]);
        return 1;
    }
    Timer clock;
    const Instance I0 = loadInstance(argv[1]);
    bool common = false;
    for (int i = 6; i < argc; i++) {
        common |= string(argv[i]) == "--common";
        if (string(argv[i]).rfind("--ng=", 0) == 0) NG = atoi(argv[i] + 5);
    }
    const Instance I = common ? commonStart(I0) : I0;
    const int K = atoi(argv[3]);
    const Routes routes = readRoutes(argv[2], I.V);
    const lns::Problem P(I);
    const lns::Solution plan(P, routes);
    const bound::VehicleTypes types(I);
    const string prefix = argv[5];
    FILE* log = fopen((prefix + "_tree.jsonl").c_str(), "w");
    fprintf(log, "{\"K\": %d, \"served\": [", K);
    bool first = true;
    for (int k = 0; k < P.N; k++)
        if (plan.rt[k] >= 0) {
            fprintf(log, "%s%d", first ? "" : ", ", k);
            first = false;
        }
    fprintf(log, "], \"cnt\": [");
    for (int t = 0; t < types.T; t++) fprintf(log, "%s%d", t ? ", " : "", types.cnt[t]);
    fprintf(log, "], \"types\": [");
    for (int t = 0; t < types.T; t++) {
        fprintf(log, "%s[", t ? ", " : "");
        for (size_t i = 0; i < types.vehicles[t].size(); i++) fprintf(log, "%s%d", i ? ", " : "", types.vehicles[t][i]);
        fprintf(log, "]");
    }
    fprintf(log, "]}\n");
    Search S{P, plan, types, K, atof(argv[4]), prefix, log};
    vector<int> cap = types.cnt;
    S.visit(cap, 0);
    fclose(log);
    fprintf(stderr, "узлов %d, листов на перебор %d, %s, %.1f с\n", S.nodes, S.leaves,
            S.ok ? "перебор везде полный" : "перебор где-то неполный", clock.sec());
    return 0;
}
