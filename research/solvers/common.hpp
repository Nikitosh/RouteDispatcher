// Общий каркас для всех решателей.
// Задача: открытые маршруты (без возврата), у каждой бригады своя стартовая точка, свой вид транспорта
// (своя матрица времени и расстояний) и набор навыков. Окна жёсткие на НАЧАЛО работ, ожидание разрешено.
// Цель лексикографическая: 1) штраф за невыполненные заявки (по приоритету), 2) число бригад, 3) км.
#pragma once
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <numeric>
#include <queue>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <vector>
using namespace std;

struct Order { string id; int svc; double a, b; int pri, skill; };
struct Vehicle { int start, mode, mask; };

struct Instance {
    string name; int N = 0, V = 0, S = 0, M = 0;
    vector<Order> ord; vector<Vehicle> veh;
    // Виды транспорта: 0..3 читаются из файла; 4..7 — служебные (например, личная матрица бригады для подсказок).
    vector<double> T[8], D[8];                       // M*M, строка i столбец j
    int node(int k) const { return S + k; }           // узел заявки k
    double t(int v, int i, int j) const { return T[veh[v].mode][i * M + j]; }
    double d(int v, int i, int j) const { return D[veh[v].mode][i * M + j]; }
    bool can(int v, int k) const { return (veh[v].mask >> ord[k].skill) & 1; }
    double penalty(int k) const { return ord[k].pri == 1 ? 100 : ord[k].pri == 2 ? 50 : 20; }
};

inline Instance loadInstance(const string& path) {
    ifstream in(path); Instance I; if (!in) { fprintf(stderr, "cannot open %s\n", path.c_str()); exit(1); }
    getline(in, I.name); in >> I.N >> I.V >> I.S; I.M = I.S + I.N;
    I.ord.resize(I.N);
    for (auto& o : I.ord) in >> o.id >> o.svc >> o.a >> o.b >> o.pri >> o.skill;
    I.veh.resize(I.V);
    for (auto& v : I.veh) in >> v.start >> v.mode >> v.mask;
    for (int m = 0; m < 4; m++) {
        I.T[m].resize(I.M * I.M); I.D[m].resize(I.M * I.M);
        for (auto& x : I.T[m]) in >> x;
        for (auto& x : I.D[m]) in >> x;
    }
    return I;
}

using Routes = vector<vector<int>>;   // routes[v] = список индексов заявок по порядку
constexpr double EPS = 1e-6;
constexpr double SHIFT = 720;

// ---------- маршрут ----------
// Проверка допустимости маршрута бригады v и его пробег. Время старта 0 в стартовой точке.
inline bool routeFeasible(const Instance& I, int v, const vector<int>& r, double* km = nullptr, double* endT = nullptr) {
    double t = 0, dist = 0; int prev = I.veh[v].start;
    for (int k : r) {
        if (!I.can(v, k)) return false;
        int n = I.node(k);
        double beg = max(t + I.t(v, prev, n), I.ord[k].a);
        if (beg > I.ord[k].b + EPS) return false;
        t = beg + I.ord[k].svc;
        if (t > SHIFT + EPS) return false;
        dist += I.d(v, prev, n); prev = n;
    }
    if (km) *km = dist; if (endT) *endT = t;
    return true;
}
inline double routeKm(const Instance& I, int v, const vector<int>& r) {
    double dist = 0; int prev = I.veh[v].start;
    for (int k : r) { dist += I.d(v, prev, I.node(k)); prev = I.node(k); }
    return dist;
}
// Плановые времена начала работ по маршруту (для вывода и объяснений).
inline vector<double> routeStarts(const Instance& I, int v, const vector<int>& r) {
    vector<double> s; double t = 0; int prev = I.veh[v].start;
    for (int k : r) { int n = I.node(k); double beg = max(t + I.t(v, prev, n), I.ord[k].a); s.push_back(beg); t = beg + I.ord[k].svc; prev = n; }
    return s;
}

// ---------- целевая функция ----------
struct Score {
    double unserved = 0; int nUnserved = 0; int used = 0; double km = 0; bool feasible = true;
    double scalar() const { return unserved * 1e6 + used * 1e4 + km; }
};
constexpr double W_UNSERVED = 1e6, W_VEHICLE = 1e4;   // штраф 20..100 * 1e6 >> бригада 1e4 >> км
inline Score evaluate(const Instance& I, const Routes& R) {
    Score s; vector<int> seen(I.N, 0);
    for (int v = 0; v < I.V; v++) {
        double km;
        if (!routeFeasible(I, v, R[v], &km)) s.feasible = false; else s.km += km;
        if (!R[v].empty()) s.used++;
        for (int k : R[v]) seen[k]++;
    }
    for (int k = 0; k < I.N; k++) {
        if (seen[k] > 1) s.feasible = false;
        if (seen[k] == 0) { s.unserved += I.penalty(k); s.nUnserved++; }
    }
    return s;
}

// ---------- вставка ----------
struct InsertPos { int v = -1, pos = -1; double delta = numeric_limits<double>::infinity(); };
// Стоимость вставки заявки k в маршрут v на позицию pos (км + штраф за открытие новой бригады). inf если нельзя.
inline double insertDelta(const Instance& I, const Routes& R, int v, int pos, int k, double newRoutePenalty = W_VEHICLE) {
    const auto& r = R[v]; if (!I.can(v, k)) return numeric_limits<double>::infinity();
    vector<int> tmp; tmp.reserve(r.size() + 1);
    tmp.insert(tmp.end(), r.begin(), r.begin() + pos); tmp.push_back(k); tmp.insert(tmp.end(), r.begin() + pos, r.end());
    double km; if (!routeFeasible(I, v, tmp, &km)) return numeric_limits<double>::infinity();
    return km - routeKm(I, v, r) + (r.empty() ? newRoutePenalty : 0);
}
inline InsertPos bestInsert(const Instance& I, const Routes& R, int k, double newRoutePenalty = W_VEHICLE) {
    InsertPos best;
    for (int v = 0; v < I.V; v++) {
        if (!I.can(v, k)) continue;
        for (int p = 0; p <= (int)R[v].size(); p++) {
            double dl = insertDelta(I, R, v, p, k, newRoutePenalty);
            if (dl < best.delta) best = {v, p, dl};
        }
    }
    return best;
}
// Вставить всех невставленных жадно (по приоритету, затем по ширине окна). Возвращает число невставленных.
inline int insertUnserved(const Instance& I, Routes& R, double newRoutePenalty = W_VEHICLE) {
    vector<int> in(I.N, 0); for (auto& r : R) for (int k : r) in[k] = 1;
    vector<int> rest; for (int k = 0; k < I.N; k++) if (!in[k]) rest.push_back(k);
    sort(rest.begin(), rest.end(), [&](int x, int y) {
        if (I.ord[x].pri != I.ord[y].pri) return I.ord[x].pri < I.ord[y].pri;
        return I.ord[x].b - I.ord[x].a < I.ord[y].b - I.ord[y].a; });
    int left = 0;
    for (int k : rest) { auto p = bestInsert(I, R, k, newRoutePenalty); if (p.v < 0) { left++; continue; } R[p.v].insert(R[p.v].begin() + p.pos, k); }
    return left;
}

// ---------- базовый локальный поиск (relocate, swap, 2-opt*) с первым улучшением ----------
// Критерий: скаляр evaluate(). Удобен как «полировка» после любого построения.
inline void localSearch(const Instance& I, Routes& R, double timeLimitSec = 1e9) {
    auto t0 = chrono::steady_clock::now();
    auto elapsed = [&] { return chrono::duration<double>(chrono::steady_clock::now() - t0).count(); };
    vector<double> km(I.V); for (int v = 0; v < I.V; v++) km[v] = routeKm(I, v, R[v]);
    auto rc = [&](int v, const vector<int>& r, double& out) { double x; if (!routeFeasible(I, v, r, &x)) return false; out = x + (r.empty() ? 0 : W_VEHICLE); return true; };
    bool improved = true;
    while (improved && elapsed() < timeLimitSec) {
        improved = false;
        for (int a = 0; a < I.V && !improved && elapsed() < timeLimitSec; a++) for (int b = 0; b < I.V && !improved; b++) {
            double oa = km[a] + (R[a].empty() ? 0 : W_VEHICLE), ob = km[b] + (R[b].empty() ? 0 : W_VEHICLE);
            // relocate a[i] -> b[j]
            for (int i = 0; i < (int)R[a].size() && !improved; i++) {
                int k = R[a][i]; if (!I.can(b, k)) continue;
                vector<int> ra = R[a]; ra.erase(ra.begin() + i);
                for (int j = 0; j <= (int)(a == b ? ra.size() : R[b].size()) && !improved; j++) {
                    if (a == b) {
                        vector<int> rr = ra; rr.insert(rr.begin() + j, k); double c;
                        if (rc(a, rr, c) && c < oa - 1e-7) { R[a] = rr; km[a] = routeKm(I, a, rr); improved = true; }
                    } else {
                        vector<int> rb = R[b]; rb.insert(rb.begin() + j, k); double ca, cb;
                        if (rc(a, ra, ca) && rc(b, rb, cb) && ca + cb < oa + ob - 1e-7) { R[a] = ra; R[b] = rb; km[a] = routeKm(I, a, ra); km[b] = routeKm(I, b, rb); improved = true; }
                    }
                }
            }
            if (a >= b) continue;
            // swap a[i] <-> b[j]
            for (int i = 0; i < (int)R[a].size() && !improved; i++) for (int j = 0; j < (int)R[b].size() && !improved; j++) {
                vector<int> ra = R[a], rb = R[b]; swap(ra[i], rb[j]); double ca, cb;
                if (rc(a, ra, ca) && rc(b, rb, cb) && ca + cb < oa + ob - 1e-7) { R[a] = ra; R[b] = rb; km[a] = routeKm(I, a, ra); km[b] = routeKm(I, b, rb); improved = true; }
            }
            // 2-opt*: обмен хвостами
            for (int i = 0; i <= (int)R[a].size() && !improved; i++) for (int j = 0; j <= (int)R[b].size() && !improved; j++) {
                vector<int> ra(R[a].begin(), R[a].begin() + i), rb(R[b].begin(), R[b].begin() + j);
                ra.insert(ra.end(), R[b].begin() + j, R[b].end()); rb.insert(rb.end(), R[a].begin() + i, R[a].end());
                double ca, cb;
                if (rc(a, ra, ca) && rc(b, rb, cb) && ca + cb < oa + ob - 1e-7) { R[a] = ra; R[b] = rb; km[a] = routeKm(I, a, ra); km[b] = routeKm(I, b, rb); improved = true; }
            }
        }
        if (!improved) { // попытка вставить невыполненные
            Routes R2 = R; if (insertUnserved(I, R2) < evaluate(I, R).nUnserved) { R = R2; for (int v = 0; v < I.V; v++) km[v] = routeKm(I, v, R[v]); improved = true; }
        }
    }
}

// ---------- утилиты ----------
// Таймер. При CPU_TIME=1 считается процессорное время процесса (честные замеры на загруженной машине).
struct Timer {
    chrono::steady_clock::time_point t0 = chrono::steady_clock::now();
    bool cpu = getenv("CPU_TIME") != nullptr; clock_t c0 = clock();
    double sec() const { return cpu ? double(clock() - c0) / CLOCKS_PER_SEC : chrono::duration<double>(chrono::steady_clock::now() - t0).count(); }
};
struct Rng {
    uint64_t s; explicit Rng(uint64_t seed) : s(seed * 0x9E3779B97F4A7C15ULL + 1) { if (!s) s = 0x2545F4914F6CDD1DULL; }
    uint64_t next() { s ^= s << 7; s ^= s >> 9; return s; }
    int randint(int n) { return (int)(next() % (uint64_t)n); }          // [0, n)
    double uni() { return (next() >> 11) * (1.0 / 9007199254740992.0); } // [0, 1)
};

// ---------- запуск ----------
// Использование: ./bin/<solver> <instance> [time_limit_sec=1] [seed=1]
// Вывод: строки ROUTE v k1 k2 ... и итог RESULT.
using SolverFn = function<Routes(const Instance&, double, uint64_t)>;
inline int runMain(int argc, char** argv, SolverFn solve, const char* name) {
    if (argc < 2) { fprintf(stderr, "usage: %s instance [tl] [seed]\n", argv[0]); return 1; }
    Instance I = loadInstance(argv[1]);
    double tl = argc > 2 ? atof(argv[2]) : 1.0; uint64_t seed = argc > 3 ? atoll(argv[3]) : 1;
    Timer tm; Routes R = solve(I, tl, seed); double ms = tm.sec() * 1000;
    R.resize(I.V);
    Score s = evaluate(I, R);
    printf("SOLVER %s\n", name);
    for (int v = 0; v < I.V; v++) { printf("ROUTE %d", v); for (int k : R[v]) printf(" %d", k); printf("\n"); }
    printf("RESULT feasible=%d unserved=%d used=%d km=%.3f ms=%.1f\n", s.feasible, s.nUnserved, s.used, s.km, ms);
    return 0;
}
