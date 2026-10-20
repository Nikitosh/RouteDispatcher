// Локальный поиск с первым улучшением для полировки рекордов и финала.
//
// Решение хранит для каждой позиции состояние расписания (конец работы до и после обеда) и пороги хвоста, поэтому
// вставка, удаление и обмен проверяются за O(1). Окрестности: вставка невыполненных, перенос заявки, обмен заявками,
// 2-opt*, обмен маршрутами между бригадами, or-opt и обмен внутри маршрута. Критерий: штраф·1e6 + бригады·wveh + км +
// wwait·минуты ожидания аварий; ожидание пересчитывается проходом по хвосту, только если в нём есть авария. cap —
// максимум бригад.
#pragma once
#include "core/problem.hpp"

namespace dispatch::ls {

constexpr double INF = 1e18;
constexpr int MAX_BUF = 176;

struct Solution {
    const Instance* I = nullptr;
    vector<vector<int>> r;
    // По позициям — как в lns::Solution: dep/depB — конец работы на путях A (обед впереди) и B (обед был), lat/latF/latA —
    // пороги хвоста, wA/wB — минуты ожидания аварий до позиции включительно, nAcc — аварий на позициях ≥ i.
    vector<vector<double>> dep, depB, lat, latF, latA, wA, wB;
    vector<vector<int>> nAcc;
    vector<double> km, wt;    // пробег и минуты ожидания аварий маршрута
    vector<int> qTot;         // оборудование маршрута (упаковано)
    vector<int> rt;           // маршрут заявки или -1

    Solution() = default;
    explicit Solution(const Instance& in)
        : I(&in), r(in.V), dep(in.V), depB(in.V), lat(in.V), latF(in.V), latA(in.V), wA(in.V), wB(in.V), nAcc(in.V, vector<int>(1, 0)),
          km(in.V, 0), wt(in.V, 0), qTot(in.V, 0), rt(in.N, -1) {}
    Solution(const Instance& in, const Routes& R) : Solution(in) {
        for (int v = 0; v < in.V && v < (int)R.size(); v++) r[v] = R[v];
        for (int v = 0; v < in.V; v++) rebuild(v);
    }

    int used() const {
        int u = 0;
        for (const vector<int>& x : r) u += !x.empty();
        return u;
    }
    double cost(int v) const { return km[v] + I->wWait * wt[v]; }
    double last(int v, int k) const { return I->last(k, v); }
    // Пересчитать расписание маршрута v; недопустимые заявки выпадают (rt = -1, в out). Если обеду нет места, выпадает
    // первая работа, которая кончается позже начала окна обеда.
    void rebuild(int v, vector<int>* out = nullptr) {
        const Instance& In = *I;
        const Vehicle& x = In.veh[v];
        vector<int>& R = r[v];
        int L;
        for (;;) {
            L = (int)R.size();
            for (auto* a : {&dep[v], &depB[v], &wA[v], &wB[v]}) a->resize(L);
            Walk w = startWalk(x.begin);
            double dist = 0;
            int prev = x.start, n = 0, q = 0;
            for (int i = 0; i < L; i++) {
                int k = R[i], nd = In.node(k);
                const Order& o = In.ord[k];
                Walk y = w;
                if (!In.can(v, k) || (In.equip && !x.fits(q + o.eq)) ||
                    !advance(y, x.lunch, In.t(v, prev, nd), o.a, last(v, k), In.svc(k, v), In.acc(k), TOL)) {
                    rt[k] = -1;
                    if (out) out->push_back(k);
                    continue;
                }
                w = y;
                q += o.eq;
                dist += In.d(v, prev, nd);
                prev = nd;
                R[n] = k;
                dep[v][n] = w.t;
                depB[v][n] = w.tB;
                wA[v][n] = w.wA;
                wB[v][n] = w.wB;
                rt[k] = v;
                n++;
            }
            R.resize(n);
            double wait;
            if (settle(w, x.lunch, x.end, n == 0, TOL, wait)) {
                km[v] = dist;
                wt[v] = wait;
                qTot[v] = q;
                L = n;
                break;
            }
            int i = 0;
            while (i + 1 < n && dep[v][i] <= x.lunch.la) i++;
            rt[R[i]] = -1;
            if (out) out->push_back(R[i]);
            R.erase(R.begin() + i);
        }
        for (auto* a : {&dep[v], &depB[v], &wA[v], &wB[v], &lat[v], &latF[v], &latA[v]}) a->resize(L);
        nAcc[v].assign(L + 1, 0);
        for (int i = L - 1; i >= 0; i--) {
            const int k = R[i];
            nAcc[v][i] = nAcc[v][i + 1] + In.acc(k);
            if (i + 1 < L) {
                int nx = R[i + 1];
                tailLatest(x.lunch, last(v, k), In.svc(k, v), x.end, false, In.t(v, In.node(k), In.node(nx)), In.ord[nx].a, lat[v][i + 1],
                           latF[v][i + 1], latA[v][i + 1], lat[v][i], latF[v][i], latA[v][i]);
            } else {
                tailLatest(x.lunch, last(v, k), In.svc(k, v), x.end, true, 0, 0, 0, 0, 0, lat[v][i], latF[v][i], latA[v][i]);
            }
        }
    }
    // Состояние расписания перед позицией p и точка, где бригада.
    Walk before(int v, int p, int& prev) const {
        if (p == 0) {
            prev = I->veh[v].start;
            return startWalk(I->veh[v].begin);
        }
        Walk w;
        w.t = w.free = dep[v][p - 1];
        w.tB = depB[v][p - 1];
        w.wA = wA[v][p - 1];
        w.wB = wB[v][p - 1];
        prev = I->node(r[v][p - 1]);
        return w;
    }
    // Работа k бригадой v после состояния w в точке prev; false — нельзя.
    bool step(int v, Walk& w, int& prev, int k) const {
        const Order& o = I->ord[k];
        int n = I->node(k);
        bool ok = advance(w, I->veh[v].lunch, I->t(v, prev, n), o.a, last(v, k), I->svc(k, v), I->acc(k), TOL);
        prev = n;
        return ok;
    }
    // Можно ли после состояния w в точке prev продолжить хвостом маршрута v с позиции j (или закончить маршрут).
    bool tailOK(int v, const Walk& w, int prev, int j) const {
        const Vehicle& x = I->veh[v];
        double wait;
        if (j >= (int)r[v].size()) return settle(w, x.lunch, x.end, false, TOL, wait);
        int k = r[v][j];
        return ::dispatch::tailOK(w, x.lunch, I->t(v, prev, I->node(k)), I->ord[k].a, latA[v][j], lat[v][j], TOL);
    }
    // Минуты ожидания аварий маршрута v, если после состояния w в точке prev идёт хвост маршрута с позиции j. Проход
    // идёт, пока в хвосте есть аварии; без обеда он кончается и там, где расписание сошлось с прежним.
    double waitFrom(int v, int j, Walk w, int prev) const {
        const Vehicle& x = I->veh[v];
        const int L = (int)r[v].size();
        if (x.lunch.ld <= 0) {
            double t = w.t, wa = w.wA;
            for (int i = j; i < L && nAcc[v][i] > 0; i++) {
                const int k = r[v][i], n = I->node(k);
                const Order& o = I->ord[k];
                double s = max(t + I->t(v, prev, n), o.a);
                if (s + I->svc(k, v) == dep[v][i]) return wa + wA[v][L - 1] - (i ? wA[v][i - 1] : 0.0);
                if (I->acc(k)) wa += s - o.a;
                t = s + I->svc(k, v);
                prev = n;
            }
            return wa;
        }
        double wait;
        for (int i = j; i < L; i++) {
            int k = r[v][i];
            if (nAcc[v][i] == 0) {
                wait = quietTailWait(w, takesPathA(w, x.lunch, I->t(v, prev, I->node(k)), I->ord[k].a, latF[v][i], TOL));
                if (wait >= 0) return wait;
            }
            step(v, w, prev, k);
        }
        settle(w, x.lunch, x.end, false, TOL, wait);
        return wait;
    }
    // Изменение маршрута v с позиции i может поменять ожидание аварий: авария есть дальше по маршруту, а при обеде —
    // где угодно в маршруте (обед может переехать в более ранний промежуток).
    bool waitAt(int v, int i) const {
        return I->wWait > 0 && nAcc[v][I->veh[v].lunch.ld > 0 ? 0 : min(i, (int)r[v].size())] > 0;
    }
    bool fits(int v, int q) const { return !I->equip || I->veh[v].fits(q); }
    // Прирост цели ниже бригад при вставке k в маршрут v на позицию p; INF, если нельзя.
    double insDelta(int v, int p, int k) const {
        if (!I->can(v, k) || !fits(v, qTot[v] + I->ord[k].eq)) return INF;
        int prev;
        Walk w = before(v, p, prev);
        const int from = prev;
        if (!step(v, w, prev, k) || !tailOK(v, w, prev, p)) return INF;
        int nk = I->node(k);
        double dd = I->d(v, from, nk);
        if (p < (int)r[v].size()) {
            int nx = I->node(r[v][p]);
            dd += I->d(v, nk, nx) - I->d(v, from, nx);
        }
        if (I->wWait > 0 && (I->acc(k) || waitAt(v, p))) dd += I->wWait * (waitFrom(v, p, w, prev) - wt[v]);
        return dd;
    }
    // Выигрыш км при удалении позиции i (без проверки допустимости).
    double remGain(int v, int i) const {
        int prev = i ? I->node(r[v][i - 1]) : I->veh[v].start;
        int n = I->node(r[v][i]);
        double g = I->d(v, prev, n);
        if (i + 1 < (int)r[v].size()) {
            int nx = I->node(r[v][i + 1]);
            g += I->d(v, n, nx) - I->d(v, prev, nx);
        }
        return g;
    }
    // Выигрыш цели ниже бригад при удалении позиции i (маршрут после удаления допустим — canRemove).
    double remDelta(int v, int i) const {
        double g = remGain(v, i);
        if (!waitAt(v, i)) return g;
        int prev;
        Walk w = before(v, i, prev);
        return g + I->wWait * (wt[v] - (r[v].size() == 1 ? 0.0 : waitFrom(v, i + 1, w, prev)));
    }
    bool canRemove(int v, int i) const {
        if (r[v].size() == 1) return true;
        int prev;
        Walk w = before(v, i, prev);
        return tailOK(v, w, prev, i + 1);
    }
    void insertAt(int v, int p, int k) {
        r[v].insert(r[v].begin() + p, k);
        rt[k] = v;
        rebuild(v);
    }
};

// Жадная вставка невыполненных по возрастанию номера, каждая на место с минимальным приростом цели (+ цена бригады за
// новую, пока бригад меньше cap). Возвращает число вставленных.
inline int insertAbsent(Solution& s, int cap) {
    const Instance& I = *s.I;
    int used = s.used(), inserted = 0;
    cap = min(cap, I.maxUsed);
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
                if (empty) d += I.wVeh;
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
    explicit LocalSearch(const Instance& in) : I(in), V(in.V), wait(in.waits()), pk(in.V) {
        for (int m = 0; m < MAX_MODES; m++) suf[m].assign(in.V, {});
    }

    // До локального минимума или до момента tEnd по часам clock.
    void run(Solution& s, int cap, const Timer& clock, double tEnd) {
        cap = min(cap, I.maxUsed);
        for (int v = 0; v < V; v++) cache(s, v);
        for (int it = 1;; it++) {
            if ((it & 3) == 0 && clock.sec() > tEnd) break;
            if (tryInsertAbsent(s, cap)) continue;
            if (tryRelocate(s, cap)) continue;
            if (trySwap(s)) continue;
            if (tryTwoOptStar(s, cap)) continue;
            if (tryRouteSwap(s)) continue;
            if (tryIntra(s)) continue;
            break;
        }
    }

private:
    const Instance& I;
    int V;
    bool wait;                               // есть аварии и цена их ожидания: км-отсечки верны не везде
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
    // Допустима ли последовательность buf[0..n) для бригады v; цель ниже бригад (км + ожидание аварий) — в costOut.
    bool feasBuf(int v, int n, double& costOut) const {
        const Vehicle& x = I.veh[v];
        Walk w = startWalk(x.begin);
        double dist = 0;
        int prev = x.start, q = 0;
        for (int i = 0; i < n; i++) {
            int k = buf[i];
            if (!I.can(v, k)) return false;
            const Order& o = I.ord[k];
            int nd = I.node(k);
            if (!advance(w, x.lunch, I.t(v, prev, nd), o.a, I.last(k, v), I.svc(k, v), I.acc(k), TOL)) return false;
            q += o.eq;
            dist += I.d(v, prev, nd);
            prev = nd;
        }
        double wt;
        if (!settle(w, x.lunch, x.end, n == 0, TOL, wt) || (I.equip && !x.fits(q))) return false;
        costOut = dist + I.wWait * wt;
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
                if (!s.canRemove(a, i)) continue;
                double g = s.remDelta(a, i) + (La == 1 ? I.wVeh : 0);
                for (int b = 0; b < V; b++) {
                    if (b == a || !I.can(b, k)) continue;
                    bool empty = s.r[b].empty();
                    if (empty && La != 1 && used >= cap) continue;
                    double pen = empty ? I.wVeh : 0;
                    if (g - pen < 1e-7) continue;   // вставка не уменьшает ни км, ни ожидание
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
                    int pa;
                    Walk wa0 = s.before(a, i, pa);
                    int na = i + 1 < La ? I.node(s.r[a][i + 1]) : -1;
                    double baseA = I.d(a, pa, I.node(u)) + (na >= 0 ? I.d(a, I.node(u), na) : 0);
                    for (int j = 0; j < Lb; j++) {
                        int w = s.r[b][j];
                        if (!I.can(a, w)) continue;
                        int pb;
                        Walk wb0 = s.before(b, j, pb);
                        int nb = j + 1 < Lb ? I.node(s.r[b][j + 1]) : -1;
                        double baseB = I.d(b, pb, I.node(w)) + (nb >= 0 ? I.d(b, I.node(w), nb) : 0);
                        double newA = I.d(a, pa, I.node(w)) + (na >= 0 ? I.d(a, I.node(w), na) : 0);
                        double newB = I.d(b, pb, I.node(u)) + (nb >= 0 ? I.d(b, I.node(u), nb) : 0);
                        const bool exact = wait && (s.waitAt(a, i) || s.waitAt(b, j));
                        if (!exact && newA + newB >= baseA + baseB - 1e-7) continue;
                        if (I.equip && (!s.fits(a, s.qTot[a] - I.ord[u].eq + I.ord[w].eq) || !s.fits(b, s.qTot[b] - I.ord[w].eq + I.ord[u].eq)))
                            continue;
                        Walk wa = wa0, wb = wb0;
                        int qa = pa, qb = pb;
                        if (!s.step(a, wa, qa, w) || !s.tailOK(a, wa, qa, i + 1)) continue;
                        if (!s.step(b, wb, qb, u) || !s.tailOK(b, wb, qb, j + 1)) continue;
                        if (exact) {
                            double ca = s.km[a] - baseA + newA + I.wWait * s.waitFrom(a, i + 1, wa, qa);
                            double cb = s.km[b] - baseB + newB + I.wWait * s.waitFrom(b, j + 1, wb, qb);
                            if (ca + cb >= s.cost(a) + s.cost(b) - 1e-7) continue;
                        }
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
    bool tryTwoOptStar(Solution& s, int cap) {
        const int used = s.used();
        for (int a = 0; a < V; a++)
            for (int b = a + 1; b < V; b++) {
                int La = (int)s.r[a].size(), Lb = (int)s.r[b].size();
                if (!La && !Lb) continue;
                int ma = I.veh[a].mode, mb = I.veh[b].mode;
                const bool exact = wait && (s.waitAt(a, 0) || s.waitAt(b, 0));
                double old = s.cost(a) + s.cost(b) + (La ? I.wVeh : 0) + (Lb ? I.wVeh : 0);
                for (int i = 0; i <= La; i++)
                    for (int j = 0; j <= Lb; j++) {
                        if ((i == La && j == Lb) || (i == 0 && j == 0)) continue;
                        int nA = i + (Lb - j), nB = j + (La - i);
                        if (used - (La > 0) - (Lb > 0) + (nA > 0) + (nB > 0) > cap) continue;
                        double veh = (nA ? I.wVeh : 0) + (nB ? I.wVeh : 0);
                        if (!exact) {
                            int lastA = i ? I.node(s.r[a][i - 1]) : I.veh[a].start;
                            int lastB = j ? I.node(s.r[b][j - 1]) : I.veh[b].start;
                            double ka = pk[a][i] + (j < Lb ? I.d(a, lastA, I.node(s.r[b][j])) + suf[ma][b][j] : 0);
                            double kb = pk[b][j] + (i < La ? I.d(b, lastB, I.node(s.r[a][i])) + suf[mb][a][i] : 0);
                            if (ka + kb + veh >= old - 1e-7) continue;
                        }
                        int n = 0;
                        for (int x = 0; x < i; x++) buf[n++] = s.r[a][x];
                        for (int x = j; x < Lb; x++) buf[n++] = s.r[b][x];
                        double k1, k2;
                        if (!feasBuf(a, n, k1)) continue;
                        vector<int> ra(buf, buf + n);
                        n = 0;
                        for (int x = 0; x < j; x++) buf[n++] = s.r[b][x];
                        for (int x = i; x < La; x++) buf[n++] = s.r[a][x];
                        if (!feasBuf(b, n, k2) || k1 + k2 + veh >= old - 1e-7) continue;
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
                if (ka + kb >= s.cost(a) + s.cost(b) - 1e-7) continue;
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
            const double old = s.cost(v);
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
// нарушающая заявка (не успевает, нет навыка или оборудования); если не помещается обед — первая работа, которая
// кончается позже начала его окна.
inline Routes finalize(const Instance& I, Routes R) {
    R.resize(I.V);
    for (int v = 0; v < I.V; v++)
        while (!R[v].empty() && !routeFeasible(I, v, R[v])) {
            const Vehicle& x = I.veh[v];
            Walk w = startWalk(x.begin);
            int prev = x.start, q = 0, bad = -1, lunchCut = -1;
            for (int i = 0; i < (int)R[v].size(); i++) {
                int k = R[v][i], n = I.node(k);
                const Order& o = I.ord[k];
                if (!I.can(v, k) || (I.equip && !x.fits(q + o.eq)) || !advance(w, x.lunch, I.t(v, prev, n), o.a, I.last(k, v), I.svc(k, v), I.acc(k), EPS)) {
                    bad = i;
                    break;
                }
                if (lunchCut < 0 && w.t > x.lunch.la) lunchCut = i;
                q += o.eq;
                prev = n;
            }
            if (bad < 0) bad = lunchCut >= 0 ? lunchCut : (int)R[v].size() - 1;
            R[v].erase(R[v].begin() + bad);
        }
    return R;
}

}  // namespace dispatch::ls
