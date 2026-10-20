// Задача планирования и то, что нужно всем решателям: чтение задачи, расписание маршрута с обедом, проверка маршрута,
// цель, таймер, генератор случайных чисел, вывод ответа.
//
// Маршруты открытые (без возврата). У бригады своя стартовая точка, свой вид транспорта (своя матрица времени и
// расстояний), маска навыков, обед и запас оборудования. Окно ограничивает начало работ, приехать раньше и ждать можно;
// работа должна закончиться до конца смены. Цель: штраф за невыполненные заявки·1e6 + бригады·wveh + км +
// wwait·минуты ожидания аварий (от начала окна до начала работы).
//
// Формат задачи (пишет dispatch/model/problem.py, схема — docs/SOLVER_IO.md): имя; N V S; N строк
// «id длительность начало конец приоритет навык [роутеры приставки]»; V строк «старт вид маска [конец смены [начало
// смены обед_с обед_до обед_мин [роутеры приставки всего [темп]]]]»; для каждого из 4 видов транспорта матрица времени и
// матрица расстояния (S+N)×(S+N).
//
// Темп бригады — множитель длительности работ: заявку k бригада v делает за svc(k, v) = ⌊длительность·темп + 0,5⌋ минут
// (так же в model/problem.py: Problem.service). Все проверки расписания берут длительность для своей бригады.
#pragma once
#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
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
constexpr double LUNCH_TOL = 1e-6; // допуск границы окна обеда — тот же в model/problem.py и validate.py
constexpr double INF_TIME = 1e18;
constexpr int FILE_MODES = 4;      // виды транспорта в файле: машина, общ. транспорт, велосипед, пешком
constexpr int MAX_MODES = 8;       // 4..7 — служебные матрицы (подсказки для дальних бригад)

// Веса цели по умолчанию: любой штраф (20..100) × 1e6 ≫ бригада 1e4 ≫ км; минута ожидания аварии — 0,1 км.
constexpr double W_UNSERVED = 1e6;
constexpr double W_VEHICLE = 1e4;
constexpr double W_WAIT = 0.1;

// Оборудование: роутеры и ТВ-приставки. Количества упакованы в одно целое по EQ_BITS бит на вид, поэтому сумма
// упакованных — упакованная сумма.
constexpr int NITEMS = 2, EQ_BITS = 10, EQ_MASK = (1 << EQ_BITS) - 1;
constexpr int UNLIMITED = 1 << 20;
inline int eqItem(int q, int i) { return (q >> (i * EQ_BITS)) & EQ_MASK; }

// Обед: начать в [la, lb], длится ld минут; ld = 0 — бригаде обед не нужен.
struct Lunch {
    double la = 0, lb = -1, ld = 0;
    bool operator==(const Lunch& o) const { return la == o.la && lb == o.lb && ld == o.ld; }
};

struct Order {
    string id;
    int svc;           // работа на месте по нормативу и сложности, мин (у бригады — svc(k, v) с её темпом)
    double a, b;       // окно начала работ
    int pri;           // 1 — авария, 2 — подключение, 3 — остальные
    int skill;         // номер навыка с учётом класса требования к транспорту (см. model/problem.py)
    int eq = 0;        // нужное оборудование, упаковано (eqItem)
};

struct Vehicle {
    int start;         // номер стартовой точки
    int mode;          // вид транспорта (индекс матрицы)
    int mask;          // биты навыков
    double end = SHIFT;   // конец смены бригады (график 5/2: 10–19); начало смены — в матрице из её стартовой точки
    double begin = 0;     // с какого момента бригада свободна в стартовой точке (нужно только для обеда до выезда)
    Lunch lunch;
    int cap[NITEMS] = {UNLIMITED, UNLIMITED}, capTot = UNLIMITED;   // запас по видам и всего (сколько унесёт)
    double speed = 1;     // темп: множитель длительности работ
    bool fits(int q) const {
        int tot = 0;
        for (int i = 0; i < NITEMS; i++) {
            int x = eqItem(q, i);
            if (x > cap[i]) return false;
            tot += x;
        }
        return tot <= capTot;
    }
};

struct Instance {
    string name;
    int N = 0, V = 0, S = 0, M = 0;        // заявки, бригады, стартовые точки, всего точек
    vector<Order> ord;
    vector<Vehicle> veh;
    vector<double> T[MAX_MODES], D[MAX_MODES];   // M×M, строка — откуда
    double wVeh = W_VEHICLE, wWait = W_WAIT;     // цена бригады в км, минуты ожидания аварии в км
    int maxUsed = INT_MAX;                       // жёсткий предел числа выехавших бригад
    bool lunch = false, equip = false;           // у кого-то есть обед; у кого-то нужно или ограничено оборудование
    vector<int> svcV;                            // svcV[v·N + k] — длительность заявки k у бригады v (с темпом)

    int node(int k) const { return S + k; }
    double t(int v, int i, int j) const { return T[veh[v].mode][i * M + j]; }
    double d(int v, int i, int j) const { return D[veh[v].mode][i * M + j]; }
    bool can(int v, int k) const { return (veh[v].mask >> ord[k].skill) & 1; }
    double penalty(int k) const { return ord[k].pri == 1 ? 100 : ord[k].pri == 2 ? 50 : 20; }
    bool acc(int k) const { return ord[k].pri == 1; }
    bool waits() const { return wWait > 0 && std::any_of(ord.begin(), ord.end(), [](const Order& o) { return o.pri == 1; }); }
    int svc(int k, int v) const { return svcV[v * N + k]; }
    double last(int k, int v) const { return min(ord[k].b, veh[v].end - svc(k, v)); }   // самое позднее начало
    // Длительности по бригадам: ⌊длительность·темп + 0,5⌋ (как Problem.service в model/problem.py).
    void computeService() {
        svcV.assign((size_t)V * N, 0);
        for (int v = 0; v < V; v++)
            for (int k = 0; k < N; k++)
                svcV[v * N + k] = veh[v].speed == 1 ? ord[k].svc : (int)floor(ord[k].svc * veh[v].speed + 0.5);
    }
    bool hasFarHomes() const {
        for (const Vehicle& v : veh) if (v.start != 0) return true;
        return false;
    }
};

// ---- расписание с обедом
//
// Правило обеда (то же в model/problem.py и planning/validate.py). Обед берётся между работами: в промежутке перед
// очередной работой — на её месте (по приезде, в том числе пока ждёт начала окна) или, если там начать не позже lb
// уже нельзя, перед выездом; либо после последней работы. Начало — как можно раньше, но не раньше la. Из всех
// мест выбирается то, при котором все работы начинаются как можно раньше (при равенстве — более раннее). Это прямой
// проход с двумя состояниями: A — обед впереди, B — обед был (лучший из промежутков). Обед после последней работы
// никого не задерживает, поэтому, если он возможен, времена работ — как на пути A (model/problem.py при этом
// показывает обед в более раннем промежутке, если и он никого не сдвигает: времена те же). Пустому маршруту обед не
// нужен.

// Состояние расписания после префикса маршрута.
struct Walk {
    double t = 0;          // путь A (обед впереди): конец последней работы; на старте 0 — выезд уже в матрице
    double free = 0;       // путь A: с какого момента бригада свободна (на старте — начало смены)
    double tB = INF_TIME;  // путь B (обед был): конец последней работы
    double wA = 0, wB = 0; // минуты ожидания аварий на путях A и B
};

inline Walk startWalk(double begin) {
    Walk w;
    w.free = begin;
    return w;
}

// Начало работы, если обед — в промежутке перед ней: бригада свободна с free, без обеда приехала бы в arr, окно
// начинается в a. INF_TIME — обед в этот промежуток не помещается.
inline double lunchGap(double free, double arr, double a, const Lunch& L) {
    double s = max(arr, L.la);
    if (s <= L.lb + LUNCH_TOL) return max(s + L.ld, a);                    // на месте, по приезде
    s = max(free, L.la);
    if (s <= L.lb + LUNCH_TOL) return max(arr + (s + L.ld - free), a);     // перед выездом
    return INF_TIME;
}

// Переезд tr и работа (окно a..last, last — с концом смены; svc — длительность; acc — авария). false — путь A
// не успевает (тогда не успевает и B); состояние обновляется в любом случае.
inline bool advance(Walk& w, const Lunch& L, double tr, double a, double last, double svc, bool acc, double tol) {
    const double arr = w.t + tr, s = max(arr, a);
    if (L.ld > 0) {
        double sB = w.tB < INF_TIME ? max(w.tB + tr, a) : INF_TIME;
        const double sL = lunchGap(w.free, arr, a, L);
        if (sL <= sB) {   // при равенстве — обед позже: работы между промежутками начинаются раньше
            sB = sL;
            w.wB = w.wA;
        }
        if (sB <= last + tol) {
            w.tB = sB + svc;
            if (acc) w.wB += sB - a;
        } else {
            w.tB = INF_TIME;
        }
    }
    if (acc) w.wA += s - a;
    w.t = w.free = s + svc;
    return s <= last + tol;
}

// Конец маршрута: обед после последней работы или он уже был. wait — минуты ожидания аварий выбранного пути.
inline bool settle(const Walk& w, const Lunch& L, double end, bool empty, double tol, double& wait) {
    wait = w.wA;
    if (L.ld <= 0 || empty) return true;
    const double s = max(w.free, L.la);
    if (s <= L.lb + LUNCH_TOL && s + L.ld <= end + tol) return true;
    wait = w.wB;
    return w.tB < INF_TIME;
}

// Расписание пойдёт по пути A, если хвост с работы j (окно с a, переезд tr) успевает при обеде после последней
// работы: latF — самое позднее начало j для этого.
inline bool takesPathA(const Walk& w, const Lunch& L, double tr, double a, double latF, double tol) {
    return L.ld <= 0 || max(w.t + tr, a) <= latF + tol;
}

// Минуты ожидания аварий маршрута, если после состояния w в хвосте аварий нет: на пути A — wA; на пути B — wB, если
// обед уже был и на пути B ожидание не больше, чем на A, а если обеда ещё не было — он будет дальше, и ожидание то же,
// что на A. −1 — без прохода по хвосту не сказать (обед был, но может переехать в промежуток дальше).
inline double quietTailWait(const Walk& w, bool pathA) {
    if (pathA || w.tB >= INF_TIME || w.wB == w.wA) return w.wA;
    return -1;
}

// Хвост с работы j допустим после состояния w: переезд tr, окно с a; latA — самое позднее начало j, если обед ещё
// впереди, latB — если обед уже был.
inline bool tailOK(const Walk& w, const Lunch& L, double tr, double a, double latA, double latB, double tol) {
    const double arr = w.t + tr;
    if (L.ld <= 0) return max(arr, a) <= latB + tol;
    if (max(arr, a) <= latA + tol) return true;
    if (w.tB < INF_TIME && max(w.tB + tr, a) <= latB + tol) return true;
    return lunchGap(w.free, arr, a, L) <= latB + tol;
}

// Самое позднее окончание работы, после которого обед в следующем промежутке (переезд tr, окно следующей с a) ещё
// даёт начать следующую не позже latB.
inline double lunchGapLatest(double tr, double a, double latB, const Lunch& L) {
    if (a > latB + TOL) return -INF_TIME;
    double x = -INF_TIME;
    if (L.la + L.ld <= latB) x = min(L.lb, latB - L.ld) - tr;                 // на месте следующей работы
    if (L.la + L.ld + tr <= latB) x = max(x, min(L.lb, latB - L.ld - tr));   // перед выездом
    return x;
}

// Самое позднее окончание последней работы, после которого обед ещё помещается в окно и в смену.
inline double lunchAfterLatest(const Lunch& L, double end) { return L.la + L.ld <= end ? min(L.lb, end - L.ld) : -INF_TIME; }

// Пороги хвоста для позиции i по порогам позиции i + 1 (next < 0 — i последняя): lat — обед был, latF — обед после
// последней работы, latA — обед где-то в хвосте. own — самое позднее начало самой работы i.
inline void tailLatest(const Lunch& L, double own, double svc, double endShift, bool last, double tr, double aNext, double latNext,
                       double latFNext, double latANext, double& lat, double& latF, double& latA) {
    if (last) {
        lat = own;
        latF = latA = L.ld > 0 ? min(own, lunchAfterLatest(L, endShift) - svc) : own;
        return;
    }
    lat = min(own, latNext - svc - tr);
    if (L.ld <= 0) {
        latF = latA = lat;
        return;
    }
    latF = latFNext + TOL >= aNext ? min(own, latFNext - svc - tr) : -INF_TIME;
    double carry = latANext + TOL >= aNext ? latANext - tr : -INF_TIME;
    latA = min(own, max(carry, lunchGapLatest(tr, aNext, latNext, L)) - svc);
}

// ---- чтение задачи

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
    string line;
    std::getline(in, line);
    I.ord.resize(I.N);
    for (Order& o : I.ord) {
        std::getline(in, line);
        std::istringstream s(line);
        if (!(s >> o.id >> o.svc >> o.a >> o.b >> o.pri >> o.skill)) {
            fprintf(stderr, "строка заявки не разобрана (нужно: номер, работа, начало, конец окна, приоритет, навык): %s\n",
                    line.c_str());
            exit(1);
        }
        int q[NITEMS];
        if (s >> q[0] >> q[1]) {           // необязательно: нужно роутеров и приставок
            o.eq = (q[0] & EQ_MASK) | ((q[1] & EQ_MASK) << EQ_BITS);
            if (o.eq) I.equip = true;
        }
    }
    I.veh.resize(I.V);
    for (Vehicle& v : I.veh) {
        std::getline(in, line);
        std::istringstream s(line);
        if (!(s >> v.start >> v.mode >> v.mask)) {
            fprintf(stderr, "строка бригады не разобрана (нужно: старт, транспорт, навыки): %s\n", line.c_str());
            exit(1);
        }
        double end;
        if (!(s >> end)) continue;         // необязательно: конец смены
        v.end = end;
        double begin, la, lb, ld;
        if (!(s >> begin >> la >> lb >> ld)) continue;   // необязательно: начало смены и обед
        v.begin = begin;
        if (ld > 0) {
            v.lunch = {la, lb, ld};
            I.lunch = true;
        }
        int c0, c1, ct;
        if (!(s >> c0 >> c1 >> ct)) continue;            // необязательно: запас оборудования, −1 — без предела
        v.cap[0] = c0 < 0 ? UNLIMITED : c0;
        v.cap[1] = c1 < 0 ? UNLIMITED : c1;
        v.capTot = ct < 0 ? UNLIMITED : ct;
        if (c0 >= 0 || c1 >= 0 || ct >= 0) I.equip = true;
        double speed;
        if (!(s >> speed)) continue;                     // необязательно: темп работы
        if (!(speed > 0)) {
            fprintf(stderr, "темп бригады должен быть больше 0: %s\n", line.c_str());
            exit(1);
        }
        v.speed = speed;
    }
    for (int m = 0; m < FILE_MODES; m++) {
        I.T[m].resize(I.M * I.M);
        I.D[m].resize(I.M * I.M);
        for (double& x : I.T[m]) in >> x;
        for (double& x : I.D[m]) in >> x;
    }
    if (!in) {
        fprintf(stderr, "матрицы времени и расстояний неполные: нужно %d матриц %dx%d\n", 2 * FILE_MODES, I.M, I.M);
        exit(1);
    }
    I.computeService();
    return I;
}

using Routes = vector<vector<int>>;   // routes[v] — индексы заявок бригады v по порядку

// Допустим ли маршрут бригады v (допуск tol); пробег — в km, минуты ожидания аварий — в wait.
inline bool routeFeasible(const Instance& I, int v, const vector<int>& route, double* km = nullptr, double* wait = nullptr,
                          double tol = EPS) {
    const Vehicle& x = I.veh[v];
    Walk w = startWalk(x.begin);
    double dist = 0;
    int prev = x.start, q = 0;
    for (int k : route) {
        if (!I.can(v, k)) return false;
        const Order& o = I.ord[k];
        int n = I.node(k);
        if (!advance(w, x.lunch, I.t(v, prev, n), o.a, I.last(k, v), I.svc(k, v), I.acc(k), tol)) return false;
        q += o.eq;
        dist += I.d(v, prev, n);
        prev = n;
    }
    double wt;
    if (!settle(w, x.lunch, x.end, route.empty(), tol, wt)) return false;
    if (I.equip && !x.fits(q)) return false;
    if (km) *km = dist;
    if (wait) *wait = wt;
    return true;
}

struct Score {
    double unserved = 0;       // сумма штрафов невыполненных
    int nUnserved = 0;
    int used = 0;
    double km = 0, wait = 0;   // пробег и минуты ожидания аварий
    double obj = 0;            // цель
    bool feasible = true;
    double scalar() const { return obj; }
    long long fleetKey() const { return llround(unserved) * 100 + used; }   // сравнение по «штраф, бригады»
};

inline Score evaluate(const Instance& I, const Routes& R) {
    Score s;
    vector<int> seen(I.N, 0);
    for (int v = 0; v < I.V; v++) {
        double km, wt;
        if (!routeFeasible(I, v, R[v], &km, &wt)) s.feasible = false;
        else {
            s.km += km;
            s.wait += wt;
        }
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
    if (s.used > I.maxUsed) s.feasible = false;
    s.obj = s.unserved * W_UNSERVED + s.used * I.wVeh + s.km + I.wWait * s.wait;
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
// {"solver": ..., "routes": [[k, ...], ...], "feasible": ..., "unserved": ..., "used": ..., "km": ..., "wait": ..., "ms": ...}
inline void printAnswer(const Instance& I, Routes R, const char* solver, double seconds) {
    R.resize(I.V);
    Score s = evaluate(I, R);
    printf("{\"solver\": \"%s\", \"routes\": [", solver);
    for (int v = 0; v < I.V; v++) {
        printf(v ? ", [" : "[");
        for (size_t i = 0; i < R[v].size(); i++) printf(i ? ", %d" : "%d", R[v][i]);
        printf("]");
    }
    printf("], \"feasible\": %s, \"unserved\": %d, \"used\": %d, \"km\": %.3f, \"wait\": %.1f, \"ms\": %.1f}\n",
           s.feasible ? "true" : "false", s.nUnserved, s.used, s.km, s.wait, seconds * 1000);
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

// Аргументы командной строки решателя: <задача> [секунды=1] [сид=1] [maxv=K] [wveh=X] [wwait=Y].
struct Args {
    Instance instance;
    double seconds = 1.0;
    uint64_t seed = 1;
};

inline Args parseArgs(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "использование: %s <задача> [секунды] [сид] [maxv=K] [wveh=X] [wwait=Y]\n", argv[0]);
        exit(1);
    }
    Args a;
    a.instance = loadInstance(argv[1]);
    Instance& I = a.instance;
    int pos = 0;
    for (int i = 2; i < argc; i++) {
        string s = argv[i];
        size_t eq = s.find('=');
        if (eq == string::npos) {
            if (pos == 0) a.seconds = atof(argv[i]);
            else if (pos == 1) a.seed = strtoull(argv[i], nullptr, 10);
            pos++;
            continue;
        }
        string key = s.substr(0, eq);
        double val = atof(s.c_str() + eq + 1);
        if (key == "maxv") I.maxUsed = max(0, (int)val);
        else if (key == "wveh") I.wVeh = val;
        else if (key == "wwait") I.wWait = val;
        else {
            fprintf(stderr, "неизвестный параметр %s (maxv, wveh, wwait)\n", argv[i]);
            exit(1);
        }
    }
    return a;
}

}  // namespace dispatch
