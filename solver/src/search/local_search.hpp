// Локальный поиск с первым улучшением для полировки рекордов и финала.
//
// Решение хранит для каждой позиции время окончания работы (dep) и самое позднее допустимое начало с учётом хвоста
// (lat), поэтому вставка, удаление и обмен проверяются за O(1). Окрестности: вставка невыполненных, перенос заявки,
// обмен заявками, 2-opt*, обмен маршрутами между бригадами, or-opt и обмен внутри маршрута. Критерий:
// штраф·1e6 + бригады·1e4 + км; cap — максимум бригад.
#pragma once
#include "core/problem.hpp"

namespace dispatch::ls {

constexpr double INF = 1e18;
constexpr int MAX_BUF = 176;

struct Solution {
    const Instance* I = nullptr;
    vector<vector<int>> r;
    vector<vector<double>> dep, lat;
    vector<double> km;
    vector<int> rt;    // маршрут заявки или -1

    Solution() = default;
    explicit Solution(const Instance& in) : I(&in), r(in.V), dep(in.V), lat(in.V), km(in.V, 0), rt(in.N, -1) {}
    Solution(const Instance& in, const Routes& R) : Solution(in) {
        for (int v = 0; v < in.V && v < (int)R.size(); v++) r[v] = R[v];
        for (int v = 0; v < in.V; v++) rebuild(v);
    }

    int used() const {
        int u = 0;
        for (const vector<int>& x : r) u += !x.empty();
        return u;
    }
    // Пересчитать расписание маршрута v; недопустимые заявки выпадают (rt = -1, в out).
    void rebuild(int v, vector<int>* out = nullptr) {
        const Instance& In = *I;
        vector<int>& R = r[v];
        vector<double>& D = dep[v];
        vector<double>& Lt = lat[v];
        const int L = (int)R.size();
        D.resize(L);
        double t = 0, dist = 0;
        int prev = In.veh[v].start, w = 0;
        for (int i = 0; i < L; i++) {
            int k = R[i], n = In.node(k);
            const Order& o = In.ord[k];
            double start = max(t + In.t(v, prev, n), o.a);
            if (!In.can(v, k) || start > o.b + TOL || start + o.svc > SHIFT + TOL) {
                rt[k] = -1;
                if (out) out->push_back(k);
                continue;
            }
            t = start + o.svc;
            dist += In.d(v, prev, n);
            prev = n;
            R[w] = k;
            D[w] = t;
            rt[k] = v;
            w++;
        }
        R.resize(w);
        D.resize(w);
        Lt.resize(w);
        km[v] = dist;
        for (int i = w - 1; i >= 0; i--) {
            const Order& o = In.ord[R[i]];
            double latest = min(o.b, SHIFT - o.svc);
            if (i + 1 < w) latest = min(latest, Lt[i + 1] - o.svc - In.t(v, In.node(R[i]), In.node(R[i + 1])));
            Lt[i] = latest;
        }
    }
    // Состояние перед позицией p: время окончания предыдущей работы и её точка.
    void before(int v, int p, double& t, int& prev) const {
        if (p == 0) {
            t = 0;
            prev = I->veh[v].start;
        } else {
            t = dep[v][p - 1];
            prev = I->node(r[v][p - 1]);
        }
    }
    // Время окончания работы k бригадой v после (t, prev); -1, если нельзя.
    double step(int v, double t, int prev, int k) const {
        const Order& o = I->ord[k];
        double start = max(t + I->t(v, prev, I->node(k)), o.a);
        if (start > o.b + TOL) return -1;
        double end = start + o.svc;
        return end > SHIFT + TOL ? -1 : end;
    }
    // Можно ли от (t, prev) продолжить хвостом маршрута v с позиции j.
    bool tailOK(int v, double t, int prev, int j) const {
        if (j >= (int)r[v].size()) return true;
        return t + I->t(v, prev, I->node(r[v][j])) <= lat[v][j] + TOL;
    }
    // Прирост км при вставке k в маршрут v на позицию p; INF, если нельзя.
    double insDelta(int v, int p, int k) const {
        if (!I->can(v, k)) return INF;
        double t;
        int prev;
        before(v, p, t, prev);
        double end = step(v, t, prev, k);
        if (end < 0) return INF;
        int nk = I->node(k);
        double dd = I->d(v, prev, nk);
        if (p < (int)r[v].size()) {
            int nx = I->node(r[v][p]);
            if (end + I->t(v, nk, nx) > lat[v][p] + TOL) return INF;
            dd += I->d(v, nk, nx) - I->d(v, prev, nx);
        }
        return dd;
    }
    // Выигрыш км при удалении позиции i (без проверки допустимости).
    double remGain(int v, int i) const {
        double t;
        int prev;
        before(v, i, t, prev);
        int n = I->node(r[v][i]);
        double g = I->d(v, prev, n);
        if (i + 1 < (int)r[v].size()) {
            int nx = I->node(r[v][i + 1]);
            g += I->d(v, n, nx) - I->d(v, prev, nx);
        }
        return g;
    }
    bool canRemove(int v, int i) const {
        double t;
        int prev;
        before(v, i, t, prev);
        return tailOK(v, t, prev, i + 1);
    }
    void insertAt(int v, int p, int k) {
        r[v].insert(r[v].begin() + p, k);
        rt[k] = v;
        rebuild(v);
    }
};

// Жадная вставка невыполненных по возрастанию номера, каждая на место с минимальным Δкм (+ W_VEHICLE за новую
// бригаду, пока бригад меньше cap). Возвращает число вставленных.
inline int insertAbsent(Solution& s, int cap) {
    const Instance& I = *s.I;
    int used = s.used(), inserted = 0;
    for (int k = 0; k < I.N; k++) {
        if (s.rt[k] >= 0) continue;
        double best = INF;
        int bv = -1, bp = -1;
        for (int v = 0; v < I.V; v++) {
            if (!I.can(v, k)) continue;
            bool empty = s.r[v].empty();
            if (empty && used >= cap) continue;
            for (int p = 0; p <= (int)s.r[v].size(); p++) {
                double d = s.insDelta(v, p, k);
                if (d >= INF) continue;
                if (empty) d += W_VEHICLE;
                if (d < best) {
                    best = d;
                    bv = v;
                    bp = p;
                }
            }
        }
        if (bv < 0) continue;
        if (s.r[bv].empty()) used++;
        s.insertAt(bv, bp, k);
        inserted++;
    }
    return inserted;
}

class LocalSearch {
public:
    explicit LocalSearch(const Instance& in) : I(in), V(in.V), pk(in.V) {
        for (int m = 0; m < MAX_MODES; m++) suf[m].assign(in.V, {});
    }

    // До локального минимума или до момента tEnd по часам clock.
    void run(Solution& s, int cap, const Timer& clock, double tEnd) {
        for (int v = 0; v < V; v++) cache(s, v);
        for (int it = 1;; it++) {
            if ((it & 3) == 0 && clock.sec() > tEnd) break;
            if (tryInsertAbsent(s, cap)) continue;
            if (tryRelocate(s, cap)) continue;
            if (trySwap(s)) continue;
            if (tryTwoOptStar(s)) continue;
            if (tryRouteSwap(s)) continue;
            if (tryIntra(s)) continue;
            break;
        }
    }

private:
    const Instance& I;
    int V;
    vector<vector<double>> pk;               // pk[v][i] — км первых i посещений
    vector<vector<double>> suf[MAX_MODES];   // suf[m][v][j] — км хвоста r[v][j..] в матрице m (без заезда в j)
    int buf[MAX_BUF];

    double dm(int m, int a, int b) const { return I.D[m][a * I.M + b]; }
    void cache(const Solution& s, int v) {
        const vector<int>& R = s.r[v];
        const int L = (int)R.size();
        pk[v].assign(L + 1, 0);
        int prev = I.veh[v].start;
        for (int i = 0; i < L; i++) {
            pk[v][i + 1] = pk[v][i] + I.d(v, prev, I.node(R[i]));
            prev = I.node(R[i]);
        }
        for (int m = 0; m < MAX_MODES; m++) {
            if (I.D[m].empty()) continue;
            vector<double>& S = suf[m][v];
            S.assign(L + 1, 0);
            for (int j = L - 2; j >= 0; j--) S[j] = S[j + 1] + dm(m, I.node(R[j]), I.node(R[j + 1]));
        }
    }
    void touch(Solution& s, int v) {
        s.rebuild(v);
        cache(s, v);
    }
    // Допустима ли последовательность buf[0..n) для бригады v; пробег — в kmOut.
    bool feasBuf(int v, int n, double& kmOut) const {
        double t = 0, dist = 0;
        int prev = I.veh[v].start;
        for (int i = 0; i < n; i++) {
            int k = buf[i];
            if (!I.can(v, k)) return false;
            const Order& o = I.ord[k];
            int nd = I.node(k);
            double start = max(t + I.t(v, prev, nd), o.a);
            if (start > o.b + TOL) return false;
            t = start + o.svc;
            if (t > SHIFT + TOL) return false;
            dist += I.d(v, prev, nd);
            prev = nd;
        }
        kmOut = dist;
        return true;
    }

    bool tryInsertAbsent(Solution& s, int cap) {
        if (insertAbsent(s, cap) == 0) return false;
        for (int v = 0; v < V; v++) cache(s, v);
        return true;
    }
    bool tryRelocate(Solution& s, int cap) {
        int used = s.used();
        for (int a = 0; a < V; a++)
            for (int i = 0; i < (int)s.r[a].size(); i++) {
                int k = s.r[a][i], La = (int)s.r[a].size();
                double g = s.remGain(a, i) + (La == 1 ? W_VEHICLE : 0);
                if (!s.canRemove(a, i)) continue;
                for (int b = 0; b < V; b++) {
                    if (b == a || !I.can(b, k)) continue;
                    bool empty = s.r[b].empty();
                    if (empty && La != 1 && used >= cap) continue;
                    double pen = empty ? W_VEHICLE : 0;
                    if (g - pen < 1e-7) continue;
                    for (int p = 0; p <= (int)s.r[b].size(); p++) {
                        if (s.insDelta(b, p, k) + pen >= g - 1e-7) continue;
                        s.r[a].erase(s.r[a].begin() + i);
                        s.r[b].insert(s.r[b].begin() + p, k);
                        touch(s, a);
                        touch(s, b);
                        return true;
                    }
                }
            }
        return false;
    }
    bool trySwap(Solution& s) {
        for (int a = 0; a < V; a++)
            for (int b = a + 1; b < V; b++) {
                int La = (int)s.r[a].size(), Lb = (int)s.r[b].size();
                if (!La || !Lb) continue;
                for (int i = 0; i < La; i++) {
                    int u = s.r[a][i];
                    if (!I.can(b, u)) continue;
                    double ta;
                    int pa;
                    s.before(a, i, ta, pa);
                    int na = i + 1 < La ? I.node(s.r[a][i + 1]) : -1;
                    double baseA = I.d(a, pa, I.node(u)) + (na >= 0 ? I.d(a, I.node(u), na) : 0);
                    for (int j = 0; j < Lb; j++) {
                        int w = s.r[b][j];
                        if (!I.can(a, w)) continue;
                        double tb;
                        int pb;
                        s.before(b, j, tb, pb);
                        int nb = j + 1 < Lb ? I.node(s.r[b][j + 1]) : -1;
                        double baseB = I.d(b, pb, I.node(w)) + (nb >= 0 ? I.d(b, I.node(w), nb) : 0);
                        double newA = I.d(a, pa, I.node(w)) + (na >= 0 ? I.d(a, I.node(w), na) : 0);
                        double newB = I.d(b, pb, I.node(u)) + (nb >= 0 ? I.d(b, I.node(u), nb) : 0);
                        if (newA + newB >= baseA + baseB - 1e-7) continue;
                        double e = s.step(a, ta, pa, w);
                        if (e < 0 || !s.tailOK(a, e, I.node(w), i + 1)) continue;
                        e = s.step(b, tb, pb, u);
                        if (e < 0 || !s.tailOK(b, e, I.node(u), j + 1)) continue;
                        std::swap(s.r[a][i], s.r[b][j]);
                        touch(s, a);
                        touch(s, b);
                        return true;
                    }
                }
            }
        return false;
    }
    // 2-opt*: A' = A[0..i) + B[j..], B' = B[0..j) + A[i..].
    bool tryTwoOptStar(Solution& s) {
        for (int a = 0; a < V; a++)
            for (int b = a + 1; b < V; b++) {
                int La = (int)s.r[a].size(), Lb = (int)s.r[b].size();
                if (!La && !Lb) continue;
                int ma = I.veh[a].mode, mb = I.veh[b].mode;
                double old = s.km[a] + s.km[b] + (La ? W_VEHICLE : 0) + (Lb ? W_VEHICLE : 0);
                for (int i = 0; i <= La; i++)
                    for (int j = 0; j <= Lb; j++) {
                        if ((i == La && j == Lb) || (i == 0 && j == 0)) continue;
                        int nA = i + (Lb - j), nB = j + (La - i);
                        int lastA = i ? I.node(s.r[a][i - 1]) : I.veh[a].start;
                        int lastB = j ? I.node(s.r[b][j - 1]) : I.veh[b].start;
                        double ka = pk[a][i] + (j < Lb ? I.d(a, lastA, I.node(s.r[b][j])) + suf[ma][b][j] : 0);
                        double kb = pk[b][j] + (i < La ? I.d(b, lastB, I.node(s.r[a][i])) + suf[mb][a][i] : 0);
                        if (ka + kb + (nA ? W_VEHICLE : 0) + (nB ? W_VEHICLE : 0) >= old - 1e-7) continue;
                        int n = 0;
                        for (int x = 0; x < i; x++) buf[n++] = s.r[a][x];
                        for (int x = j; x < Lb; x++) buf[n++] = s.r[b][x];
                        double k1, k2;
                        if (!feasBuf(a, n, k1)) continue;
                        vector<int> ra(buf, buf + n);
                        n = 0;
                        for (int x = 0; x < j; x++) buf[n++] = s.r[b][x];
                        for (int x = i; x < La; x++) buf[n++] = s.r[a][x];
                        if (!feasBuf(b, n, k2)) continue;
                        s.r[b].assign(buf, buf + n);
                        s.r[a] = ra;
                        touch(s, a);
                        touch(s, b);
                        return true;
                    }
            }
        return false;
    }
    // Перенос целого маршрута на другую бригаду (в том числе пустую) или обмен маршрутами.
    bool tryRouteSwap(Solution& s) {
        for (int a = 0; a < V; a++)
            for (int b = a + 1; b < V; b++) {
                if (s.r[a].empty() && s.r[b].empty()) continue;
                int n = 0;
                for (int k : s.r[b]) buf[n++] = k;
                double ka, kb;
                if (!feasBuf(a, n, ka)) continue;
                n = 0;
                for (int k : s.r[a]) buf[n++] = k;
                if (!feasBuf(b, n, kb)) continue;
                if (ka + kb >= s.km[a] + s.km[b] - 1e-7) continue;
                std::swap(s.r[a], s.r[b]);
                touch(s, a);
                touch(s, b);
                return true;
            }
        return false;
    }
    // Внутри маршрута: перенос отрезка длины 1..3 и обмен двух заявок.
    bool tryIntra(Solution& s) {
        for (int v = 0; v < V; v++) {
            const int L = (int)s.r[v].size();
            if (L < 2) continue;
            const vector<int>& R = s.r[v];
            const double old = s.km[v];
            for (int len = 1; len <= 3 && len < L; len++)
                for (int i = 0; i + len <= L; i++)
                    for (int p = 0; p <= L - len; p++) {
                        if (p == i) continue;
                        auto rest = [&](int x) { return x < i ? R[x] : R[x + len]; };   // маршрут без отрезка
                        int n = 0;
                        for (int x = 0; x < p; x++) buf[n++] = rest(x);
                        for (int x = i; x < i + len; x++) buf[n++] = R[x];
                        for (int x = p; x < L - len; x++) buf[n++] = rest(x);
                        double k;
                        if (!feasBuf(v, n, k) || k >= old - 1e-7) continue;
                        s.r[v].assign(buf, buf + n);
                        touch(s, v);
                        return true;
                    }
            for (int i = 0; i < L; i++)
                for (int j = i + 1; j < L; j++) {
                    int n = 0;
                    for (int x = 0; x < L; x++) buf[n++] = R[x];
                    std::swap(buf[i], buf[j]);
                    double k;
                    if (!feasBuf(v, n, k) || k >= old - 1e-7) continue;
                    s.r[v].assign(buf, buf + n);
                    touch(s, v);
                    return true;
                }
        }
        return false;
    }
};

// Страховка перед выводом: пока маршрут не проходит проверку с допуском EPS, из него выбрасывается первая
// нарушающая заявка.
inline Routes finalize(const Instance& I, Routes R) {
    R.resize(I.V);
    for (int v = 0; v < I.V; v++)
        while (!R[v].empty() && !routeFeasible(I, v, R[v])) {
            double t = 0;
            int prev = I.veh[v].start;
            for (int i = 0; i < (int)R[v].size(); i++) {
                int k = R[v][i], n = I.node(k);
                double start = max(t + I.t(v, prev, n), I.ord[k].a);
                if (!I.can(v, k) || start > I.ord[k].b + EPS || start + I.ord[k].svc > SHIFT + EPS) {
                    R[v].erase(R[v].begin() + i);
                    break;
                }
                t = start + I.ord[k].svc;
                prev = n;
            }
        }
    return R;
}

}  // namespace dispatch::ls
