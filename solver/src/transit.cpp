// Время на общественном транспорте между точками по расписанию: алгоритм CSA (Connection Scan, Dibbelt и др., 2013).
//
// transit <conn.bin> <foot.bin> <query.txt>
//   conn.bin  — int32 × 5 на связь: откуда, куда, отправление, прибытие (с от полуночи), рейс; по отправлению;
//   foot.bin  — int32 × 3: остановка, остановка, секунды пешком (пересадки);
//   query.txt — «S P D MAX», D моментов отправления (с), затем P строк «k s1 t1 … sk tk»: остановки рядом с точкой и
//               время подхода к ним (с). Подход и уход с остановки считаются одинаково.
// Вывод: P строк, в строке i для каждой точки j через пробел «среднее h0 h1 … hH−1» — среднее по всем отправлениям и по
// отправлениям каждого часа (час — (отправление − первое) / 3600, H часов) время от i до j в минутах, ожидание
// включено; −1, если доехать не удаётся хотя бы в половине отправлений (всех или этого часа). Пешком напрямую не
// считается — это делает Python.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <thread>
#include <vector>

using namespace std;

struct Conn { int32_t from, to, dep, arr, trip; };
struct Foot { int32_t to, sec; };

static const int INF = 1 << 30;
static const int TRANSFER = 60;          // с на пересадку между рейсами (не успеть впритык)

template <class T> vector<T> readAll(const char* path) {
    ifstream f(path, ios::binary | ios::ate);
    size_t n = f.tellg();
    vector<T> v(n / sizeof(T));
    f.seekg(0);
    f.read(reinterpret_cast<char*>(v.data()), v.size() * sizeof(T));
    return v;
}

int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage: transit conn.bin foot.bin query.txt\n"); return 1; }
    vector<Conn> conn = readAll<Conn>(argv[1]);
    vector<int32_t> rawFoot = readAll<int32_t>(argv[2]);
    FILE* q = fopen(argv[3], "r");
    int S, P, D, MAX;
    if (fscanf(q, "%d %d %d %d", &S, &P, &D, &MAX) != 4) return 1;
    vector<int> deps(D);
    for (auto& d : deps) if (fscanf(q, "%d", &d) != 1) return 1;
    vector<vector<pair<int, int>>> acc(P);
    for (auto& a : acc) {
        int k; if (fscanf(q, "%d", &k) != 1) return 1;
        a.resize(k);
        for (auto& [s, t] : a) if (fscanf(q, "%d %d", &s, &t) != 2) return 1;
    }
    fclose(q);
    int trips = 0;
    for (auto& c : conn) trips = max(trips, c.trip + 1);
    vector<vector<Foot>> foot(S);
    for (size_t i = 0; i + 2 < rawFoot.size(); i += 3) foot[rawFoot[i]].push_back({rawFoot[i + 1], rawFoot[i + 2]});

    // sum[i][j] — сумма времени, cnt[i][j] — сколько отправлений доехали
    vector<vector<double>> sum(P, vector<double>(P, 0.0));
    vector<vector<int>> cnt(P, vector<int>(P, 0));
    const int H = (deps.back() - deps.front()) / 3600 + 1;
    vector<int> perHour(H, 0);
    for (int t : deps) perHour[(t - deps.front()) / 3600]++;
    vector<vector<double>> sumH(P, vector<double>((size_t)P * H, 0.0));    // по часам: [j·H + h]
    vector<vector<int>> cntH(P, vector<int>((size_t)P * H, 0));
    int threads = max(1u, thread::hardware_concurrency());
    vector<thread> pool;
    for (int w = 0; w < threads; w++) pool.emplace_back([&, w] {
        vector<int> T(S, INF);
        vector<char> ok(trips, 0);
        vector<int> touched, tripsOn;
        auto reach = [&](int s, int t) {
            if (t < T[s]) { if (T[s] == INF) touched.push_back(s); T[s] = t; }
        };
        for (int i = w; i < P; i += threads) for (int t0 : deps) {   // строки i считает один поток
            for (auto [s, t] : acc[i]) {
                reach(s, t0 + t);
                for (auto f : foot[s]) reach(f.to, t0 + t + f.sec);
            }
            auto it = lower_bound(conn.begin(), conn.end(), t0, [](const Conn& c, int t) { return c.dep < t; });
            for (; it != conn.end() && it->dep <= t0 + MAX; ++it) {
                const Conn& c = *it;
                if (!ok[c.trip]) {
                    if (T[c.from] == INF || T[c.from] + TRANSFER > c.dep) continue;
                    ok[c.trip] = 1; tripsOn.push_back(c.trip);
                }
                if (c.arr < T[c.to]) {
                    reach(c.to, c.arr);
                    for (auto f : foot[c.to]) reach(f.to, c.arr + f.sec);
                }
            }
            for (int j = 0; j < P; j++) {
                if (j == i) continue;
                int best = INF;
                for (auto [s, t] : acc[j]) if (T[s] < INF) best = min(best, T[s] + t);
                if (best < INF && best - t0 <= MAX) {
                    int h = (t0 - deps.front()) / 3600;
                    cnt[i][j]++; sum[i][j] += (best - t0) / 60.0;
                    cntH[i][(size_t)j * H + h]++; sumH[i][(size_t)j * H + h] += (best - t0) / 60.0;
                }
            }
            for (int s : touched) T[s] = INF;
            for (int t : tripsOn) ok[t] = 0;
            touched.clear(); tripsOn.clear();
        }
    });
    for (auto& t : pool) t.join();
    for (int i = 0; i < P; i++) {
        for (int j = 0; j < P; j++) {
            double v = (i == j) ? 0.0 : (cnt[i][j] * 2 >= D ? sum[i][j] / cnt[i][j] : -1.0);
            printf(j ? " %.2f" : "%.2f", v);
            for (int h = 0; h < H; h++) {
                int c = cntH[i][(size_t)j * H + h];
                printf(" %.2f", i == j ? 0.0 : (c * 2 >= perHour[h] && c ? sumH[i][(size_t)j * H + h] / c : -1.0));
            }
        }
        printf("\n");
    }
    return 0;
}
