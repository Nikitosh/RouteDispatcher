// Столбцы (маршруты) для генерации столбцов: множество заявок, типы бригад, пул маршрутов без повторов.
#pragma once
#include <unordered_map>
#include "search/lns.hpp"

namespace dispatch::bound {

using lns::Problem;

// Множество заявок битами (до 192).
struct OrderSet {
    static constexpr int WORDS = 3;
    uint64_t w[WORDS];

    void clear() { w[0] = w[1] = w[2] = 0; }
    bool has(int k) const { return (w[k >> 6] >> (k & 63)) & 1; }
    void set(int k) { w[k >> 6] |= 1ULL << (k & 63); }
    bool subsetOf(const OrderSet& o) const { return !(w[0] & ~o.w[0]) && !(w[1] & ~o.w[1]) && !(w[2] & ~o.w[2]); }
    bool operator==(const OrderSet& o) const { return w[0] == o.w[0] && w[1] == o.w[1] && w[2] == o.w[2]; }
};

// Бригады с одинаковыми (старт, вид транспорта, навыки) — один тип; rep — представитель, cnt — сколько их.
struct VehicleTypes {
    int T = 0;
    vector<int> start, mode, mask, cnt, rep, typeOf;
    vector<vector<int>> vehicles;

    explicit VehicleTypes(const Instance& I) : typeOf(I.V) {
        for (int v = 0; v < I.V; v++) {
            const Vehicle& x = I.veh[v];
            int f = -1;
            for (int t = 0; t < T && f < 0; t++)
                if (start[t] == x.start && mode[t] == x.mode && mask[t] == x.mask) f = t;
            if (f < 0) {
                f = T++;
                start.push_back(x.start);
                mode.push_back(x.mode);
                mask.push_back(x.mask);
                cnt.push_back(0);
                rep.push_back(v);
                vehicles.push_back({});
            }
            cnt[f]++;
            vehicles[f].push_back(v);
            typeOf[v] = f;
        }
    }
};

struct Column {
    int t, off, len;   // тип бригады, начало последовательности в ColumnPool::data, длина
    double km;
    OrderSet set;
};

// Маршруты по (тип, множество заявок); для повторного множества хранится порядок с меньшим км.
class ColumnPool {
public:
    vector<Column> cols;

    ColumnPool() {
        cols.reserve(1 << 16);
        data_.reserve(1 << 20);
        index_.reserve(1 << 16);
    }
    const int* seq(const Column& c) const { return data_.data() + c.off; }
    // Добавить допустимый маршрут; возвращает номер столбца (существующего, если множество уже было).
    int add(int t, const int* s, int len, double km) {
        if (len <= 0) return -1;
        OrderSet b;
        b.clear();
        for (int i = 0; i < len; i++) b.set(s[i]);
        auto key = std::make_pair(b, t);
        auto it = index_.find(key);
        if (it != index_.end()) {
            Column& c = cols[it->second];
            if (km < c.km - 1e-9) {
                c.off = (int)data_.size();
                data_.insert(data_.end(), s, s + len);
                c.km = km;
            }
            return it->second;
        }
        int id = (int)cols.size();
        cols.push_back({t, (int)data_.size(), len, km, b});
        data_.insert(data_.end(), s, s + len);
        index_.emplace(key, id);
        return id;
    }

private:
    struct KeyHash {
        size_t operator()(const std::pair<OrderSet, int>& k) const {
            uint64_t h = 1469598103934665603ULL ^ (uint64_t)k.second;
            for (int i = 0; i < OrderSet::WORDS; i++) {
                h ^= k.first.w[i];
                h *= 1099511628211ULL;
                h ^= h >> 29;
            }
            return h;
        }
    };
    vector<int> data_;
    std::unordered_map<std::pair<OrderSet, int>, int, KeyHash> index_;
};

}  // namespace dispatch::bound
