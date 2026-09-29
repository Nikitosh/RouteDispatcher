// Задача планирования и то, что нужно всем решателям: чтение задачи, проверка маршрута, цель, таймер, генератор
// случайных чисел, вывод ответа.
//
// Маршруты открытые (без возврата). У бригады своя стартовая точка, свой вид транспорта (своя матрица времени и
// расстояний) и маска навыков. Окно ограничивает начало работ, приехать раньше и ждать можно; работа должна
// закончиться до конца смены. Цель лексикографическая: штраф за невыполненные заявки → число бригад → км.
//
// Формат задачи (пишет dispatch/model/problem.py): имя; N V S; N строк «id длительность начало конец приоритет навык»;
// V строк «старт вид маска»; для каждого из 4 видов транспорта матрица времени и матрица расстояния (S+N)×(S+N).
#pragma once
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <iterator>
#include <numeric>
#include <string>
#include <vector>

namespace dispatch {

using std::max;
using std::min;
using std::string;
using std::vector;

constexpr double SHIFT = 720;      // смена 10:00–22:00, минуты
constexpr double EPS = 1e-6;       // допуск проверки, как в dispatch/planning/validate.py
constexpr double TOL = 1e-7;       // допуск внутри поиска: строже проверки
constexpr int FILE_MODES = 4;      // виды транспорта в файле: машина, общ. транспорт, велосипед, пешком
constexpr int MAX_MODES = 8;       // 4..7 — служебные матрицы (подсказки для дальних бригад)

// Веса цели: любой штраф (20..100) × 1e6 ≫ бригада 1e4 ≫ километры.
constexpr double W_UNSERVED = 1e6;
constexpr double W_VEHICLE = 1e4;

struct Order {
    string id;
    int svc;           // работа на месте, мин
    double a, b;       // окно начала работ
    int pri;           // 1 — авария, 2 — подключение, 3 — остальные
    int skill;         // номер навыка с учётом класса требования к транспорту (см. model/problem.py)
};

struct Vehicle {
    int start;         // номер стартовой точки
    int mode;          // вид транспорта (индекс матрицы)
    int mask;          // биты навыков
};

struct Instance {
    string name;
    int N = 0, V = 0, S = 0, M = 0;        // заявки, бригады, стартовые точки, всего точек
    vector<Order> ord;
    vector<Vehicle> veh;
    vector<double> T[MAX_MODES], D[MAX_MODES];   // M×M, строка — откуда

    int node(int k) const { return S + k; }
    double t(int v, int i, int j) const { return T[veh[v].mode][i * M + j]; }
    double d(int v, int i, int j) const { return D[veh[v].mode][i * M + j]; }
    bool can(int v, int k) const { return (veh[v].mask >> ord[k].skill) & 1; }
    double penalty(int k) const { return ord[k].pri == 1 ? 100 : ord[k].pri == 2 ? 50 : 20; }
    bool hasFarHomes() const {
        for (const Vehicle& v : veh) if (v.start != 0) return true;
        return false;
    }
};

inline Instance loadInstance(const string& path) {
    std::ifstream in(path);
    if (!in) {
        fprintf(stderr, "не открыть %s\n", path.c_str());
        exit(1);
    }
    Instance I;
    std::getline(in, I.name);
    in >> I.N >> I.V >> I.S;
    I.M = I.S + I.N;
    I.ord.resize(I.N);
    for (Order& o : I.ord) in >> o.id >> o.svc >> o.a >> o.b >> o.pri >> o.skill;
    I.veh.resize(I.V);
    for (Vehicle& v : I.veh) in >> v.start >> v.mode >> v.mask;
    for (int m = 0; m < FILE_MODES; m++) {
        I.T[m].resize(I.M * I.M);
        I.D[m].resize(I.M * I.M);
        for (double& x : I.T[m]) in >> x;
        for (double& x : I.D[m]) in >> x;
    }
    return I;
}

using Routes = vector<vector<int>>;   // routes[v] — индексы заявок бригады v по порядку

// Допустим ли маршрут бригады v; пробег — в km.
inline bool routeFeasible(const Instance& I, int v, const vector<int>& route, double* km = nullptr) {
    double t = 0, dist = 0;
    int prev = I.veh[v].start;
    for (int k : route) {
        if (!I.can(v, k)) return false;
        int n = I.node(k);
        double start = max(t + I.t(v, prev, n), I.ord[k].a);
        if (start > I.ord[k].b + EPS) return false;
        t = start + I.ord[k].svc;
        if (t > SHIFT + EPS) return false;
        dist += I.d(v, prev, n);
        prev = n;
    }
    if (km) *km = dist;
    return true;
}

struct Score {
    double unserved = 0;       // сумма штрафов невыполненных
    int nUnserved = 0;
    int used = 0;
    double km = 0;
    bool feasible = true;
    double scalar() const { return unserved * W_UNSERVED + used * W_VEHICLE + km; }
    long long fleetKey() const { return llround(unserved) * 100 + used; }   // сравнение по «штраф, бригады»
};

inline Score evaluate(const Instance& I, const Routes& R) {
    Score s;
    vector<int> seen(I.N, 0);
    for (int v = 0; v < I.V; v++) {
        double km;
        if (!routeFeasible(I, v, R[v], &km)) s.feasible = false;
        else s.km += km;
        if (!R[v].empty()) s.used++;
        for (int k : R[v]) seen[k]++;
    }
    for (int k = 0; k < I.N; k++) {
        if (seen[k] > 1) s.feasible = false;
        if (seen[k] == 0) {
            s.unserved += I.penalty(k);
            s.nUnserved++;
        }
    }
    return s;
}

// Секундомер от момента создания.
class Timer {
public:
    double sec() const { return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0_).count(); }

private:
    std::chrono::steady_clock::time_point t0_ = std::chrono::steady_clock::now();
};

// xorshift: быстрый и воспроизводимый для заданного сида.
class Rng {
public:
    explicit Rng(uint64_t seed) : s_(seed * 0x9E3779B97F4A7C15ULL + 1) {
        if (!s_) s_ = 0x2545F4914F6CDD1DULL;
    }
    uint64_t next() {
        s_ ^= s_ << 7;
        s_ ^= s_ >> 9;
        return s_;
    }
    int randint(int n) { return (int)(next() % (uint64_t)n); }           // [0, n)
    double uni() { return (next() >> 11) * (1.0 / 9007199254740992.0); }  // [0, 1)
    template <class It> void shuffle(It first, It last) {
        for (auto n = last - first - 1; n > 0; n--) std::swap(first[n], first[randint((int)n + 1)]);
    }

private:
    uint64_t s_;
};

// Ответ решателя — одна строка JSON в stdout (схема — docs/SOLVER_IO.md):
// {"solver": ..., "routes": [[k, ...], ...], "feasible": ..., "unserved": ..., "used": ..., "km": ..., "ms": ...}
inline void printAnswer(const Instance& I, Routes R, const char* solver, double seconds) {
    R.resize(I.V);
    Score s = evaluate(I, R);
    printf("{\"solver\": \"%s\", \"routes\": [", solver);
    for (int v = 0; v < I.V; v++) {
        printf(v ? ", [" : "[");
        for (size_t i = 0; i < R[v].size(); i++) printf(i ? ", %d" : "%d", R[v][i]);
        printf("]");
    }
    printf("], \"feasible\": %s, \"unserved\": %d, \"used\": %d, \"km\": %.3f, \"ms\": %.1f}\n", s.feasible ? "true" : "false",
           s.nUnserved, s.used, s.km, seconds * 1000);
}

// Маршруты из ответа printAnswer: поле "routes" — массив массивов целых. Разбирается только это поле.
inline Routes readRoutes(const string& path, int V) {
    auto fail = [&](const char* what) {
        fprintf(stderr, "%s: %s\n", path.c_str(), what);
        exit(1);
    };
    std::ifstream in(path);
    if (!in) fail("не открыть");
    string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    size_t p = text.find("\"routes\"");
    if (p == string::npos || (p = text.find('[', p)) == string::npos) fail("нет поля routes");
    const char* c = text.c_str() + p + 1;
    auto skip = [&] {
        while (*c == ' ' || *c == ',' || *c == '\n' || *c == '\t' || *c == '\r') c++;
    };
    Routes R;
    for (skip(); *c != ']'; skip()) {
        if (*c != '[') fail("маршрут должен быть массивом");
        c++;
        vector<int> route;
        for (skip(); *c != ']'; skip()) {
            char* end;
            long k = strtol(c, &end, 10);
            if (end == c) fail("номер заявки должен быть целым");
            route.push_back((int)k);
            c = end;
        }
        c++;
        R.push_back(route);
    }
    if ((int)R.size() > V) fail("маршрутов больше, чем бригад");
    R.resize(V);
    return R;
}

// Аргументы командной строки решателя: <задача> [секунды=1] [сид=1].
struct Args {
    Instance instance;
    double seconds = 1.0;
    uint64_t seed = 1;
};

inline Args parseArgs(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "использование: %s <задача> [секунды] [сид]\n", argv[0]);
        exit(1);
    }
    Args a;
    a.instance = loadInstance(argv[1]);
    if (argc > 2) a.seconds = atof(argv[2]);
    if (argc > 3) a.seed = strtoull(argv[3], nullptr, 10);
    return a;
}

}  // namespace dispatch
