// Нижняя граница числа бригад генерацией столбцов.
//
// Мастер-задача: каждая заявка, которую обслуживает найденный план, покрыта ровно одним маршрутом; маршрутов типа t
// не больше числа таких бригад, всего не больше V; стоимость маршрута 1. Двойственные цены для эвристического поиска
// сглаживаются (центр стабилизации, α = 0,5). Когда пул и эвристика больше не дают отрицательных столбцов, точный
// ng-поиск при текущих ценах даёт допустимую лагранжеву оценку L = Σu + π_V·V + Σ_t q_t·min rc_t (q_t — сколько
// бригад можно отдать типу t, начиная с самых отрицательных) и оценку Фарли Σu / max_t(1 − min rc_t).
#pragma once
#include "bound/pricing.hpp"
#include "bound/simplex.hpp"

namespace dispatch::bound {

class FleetBound {
public:
    bool converged = false;   // точный поиск не нашёл отрицательных столбцов: оценка равна оптимуму ЛП
    // Лучшая лагранжева оценка (без оценки Фарли) и цены, при которых она получена: для отсечения по приведённой
    // стоимости в доказательстве перебором (fleet_proof.cpp). fixLag — постоянная часть rc, одна для всех типов.
    double bestLag = -1e18, fixLag = 0;
    vector<double> uLag;
    vector<char> skipStart;   // маршруты этих бригад плана не берём в начальный базис (ветка без бригады)
    // Состав задан точно: маршрутов типа t ровно cnt_t (строки типов — равенства). Тогда число маршрутов n = Σcnt
    // известно, и L = Σu + π_V·n + Σ_t cnt_t·min rc_t — с любыми знаками цен и rc; L > n — такого состава нет.
    bool exactTypes = false;
    vector<double> minRcLag;  // min rc по типам при ценах лучшей оценки

    FleetBound(const Problem& p, const VehicleTypes& ty, ColumnPool& pool, HeuristicPricer& hp, ExactPricer& xp)
        : P(p), TY(ty), pool_(pool), hp_(hp), xp_(xp), N(p.N), T(ty.T), rowIdx_(p.N, -1) {}

    // Оценка для решения best до tEnd; остановка, как только оценка превысила stopAt. -1e18, если оценки нет.
    double run(const lns::Solution& best, double stopAt, const Timer& clock, double tEnd) {
        setup(best);
        vector<int> basis(m_);
        std::iota(basis.begin(), basis.end(), 0);
        for (int v = 0; v < P.V; v++)
            if (best.len[v] && !(v < (int)skipStart.size() && skipStart[v]))
                basis[rowIdx_[best.r[v][0]]] = toLP(pool_.add(TY.typeOf[v], best.r[v], best.len[v], best.km[v]));
        lp_.setBasis(basis);
        loop(stopAt, clock, tEnd);
        return bestL_;
    }

    // Цены последней ЛП со строками типов (π_t ≤ 0, π_V ≤ 0 — иначе обрезаются до 0) и проверка точным поиском:
    // L = Σu + Σ_t π_t·cnt_t + π_V·V + Σ_t cnt_t·min(0, min rc_t) — нижняя граница числа маршрутов, а в плане с z ≤ K
    // у маршрута типа t приведённая стоимость fixT[t] − Σu не больше K − L. false — точный поиск не завершился.
    bool fullDuals(vector<double>& u, vector<double>& fixT, double& L) {
        const vector<double>& pi = lp_.pi;
        u.assign(N, -1e9);
        fixT.assign(T, 0);
        const double piV = min(0.0, pi[vehicleRow()]);
        L = piV * P.V;
        for (int k = 0; k < N; k++)
            if (rowIdx_[k] >= 0) {
                u[k] = pi[rowIdx_[k]];
                L += u[k];
            }
        for (int t = 0; t < T; t++) {
            const double piT = min(0.0, pi[R_ + t]);
            fixT[t] = ROUTE_COST - piT - piV;
            L += piT * TY.cnt[t];
        }
        for (int t = 0; t < T; t++) {
            if (!TY.cnt[t]) continue;
            bool complete;
            vector<std::pair<double, vector<int>>> cols;
            const double m = xp_.minReducedCost(t, u.data(), fixT[t], 4 * LABEL_CAP, complete, cols, 0);
            if (!complete) return false;
            L += TY.cnt[t] * min(0.0, m);
        }
        return true;
    }

private:
    static constexpr double ROUTE_COST = 1, BIG_M = 1.5;
    static constexpr double ALPHA = 0.5;               // вес центра при сглаживании цен
    static constexpr int LABELS = 6, MAX_COLS = 20;    // эвристика: меток на заявку, столбцов на тип
    static constexpr int SIFT = 300;                   // столбцов из пула за раунд
    static constexpr size_t LABEL_CAP = 200000;        // предел меток точного поиска

    const Problem& P;
    const VehicleTypes& TY;
    ColumnPool& pool_;
    HeuristicPricer& hp_;
    ExactPricer& xp_;
    const int N, T;
    int R_ = 0, m_ = 0;
    vector<int> rowIdx_, lpOf_;
    Simplex lp_;
    double bestL_ = -1e18;

    int vehicleRow() const { return R_ + T; }
    void setup(const lns::Solution& best) {
        R_ = 0;
        for (int k = 0; k < N; k++) rowIdx_[k] = best.rt[k] >= 0 ? R_++ : -1;
        m_ = R_ + T + 1;
        vector<double> rhs(m_, 1);
        vector<char> eq(m_, 1);
        for (int t = 0; t < T; t++) {
            rhs[R_ + t] = TY.cnt[t];
            eq[R_ + t] = exactTypes ? 1 : 0;
        }
        rhs[vehicleRow()] = P.V;
        eq[vehicleRow()] = 0;
        lp_.init(m_, rhs, eq, BIG_M * ROUTE_COST);
        lpOf_.assign(pool_.cols.size(), -1);
    }
    int toLP(int c) {
        if (c < (int)lpOf_.size() && lpOf_[c] >= 0) return lpOf_[c];
        if ((int)lpOf_.size() <= c) lpOf_.resize(pool_.cols.size(), -1);
        const Column& col = pool_.cols[c];
        const int* s = pool_.seq(col);
        vector<int> rows;
        for (int i = 0; i < col.len; i++) rows.push_back(rowIdx_[s[i]]);
        rows.push_back(R_ + col.t);
        rows.push_back(vehicleRow());
        return lpOf_[c] = lp_.addCol(ROUTE_COST, rows);
    }
    // Приведённая стоимость столбца пула при текущих ценах; 1e18, если он заходит в заявку вне строк.
    double rcPool(int c) const {
        const Column& col = pool_.cols[c];
        const int* s = pool_.seq(col);
        double r = ROUTE_COST - lp_.pi[R_ + col.t] - lp_.pi[vehicleRow()];
        for (int i = 0; i < col.len; i++) {
            int q = rowIdx_[s[i]];
            if (q < 0) return 1e18;
            r -= lp_.pi[q];
        }
        return r;
    }
    // До maxAdd самых отрицательных столбцов пула, ещё не бывших в мастер-задаче.
    int addNegativeFromPool(int maxAdd) {
        vector<std::pair<double, int>> v;
        for (int c = 0; c < (int)pool_.cols.size(); c++) {
            if (c < (int)lpOf_.size() && lpOf_[c] >= 0) continue;
            double r = rcPool(c);
            if (r < -1e-7) v.push_back({r, c});
        }
        if ((int)v.size() > maxAdd) {
            std::nth_element(v.begin(), v.begin() + maxAdd, v.end());
            v.resize(maxAdd);
        }
        for (auto& x : v) toLP(x.second);
        return (int)v.size();
    }
    // Цены заявок (вне строк — «запрещено») и постоянная часть rc по типам.
    void duals(const vector<double>& pi, vector<double>& u, vector<double>& fix) const {
        u.assign(N, -1e9);
        fix.assign(T, 0);
        for (int k = 0; k < N; k++)
            if (rowIdx_[k] >= 0) u[k] = pi[rowIdx_[k]];
        for (int t = 0; t < T; t++) fix[t] = ROUTE_COST - pi[R_ + t] - pi[vehicleRow()];
    }
    double routeRc(const vector<double>& u, double fix, const vector<int>& r) const {
        double rc = fix;
        for (int k : r) rc -= u[k];
        return rc;
    }

    void loop(double stopAt, const Timer& clock, double tEnd) {
        xp_.clock = &clock;
        xp_.deadline = tEnd;
        vector<double> center;
        bool needSolve = true;
        const double t0 = clock.sec(), slice = max(0.5, 0.3 * (tEnd - t0));
        double lastExact = t0;
        while (clock.sec() < tEnd) {
            if (needSolve) lp_.solve(100000, clock, tEnd);
            needSolve = true;
            const int added = addNegativeFromPool(SIFT);
            const vector<double>& pi = lp_.pi;
            if (center.empty()) center = pi;
            vector<double> smooth(m_);
            for (int i = 0; i < m_; i++) smooth[i] = ALPHA * center[i] + (1 - ALPHA) * pi[i];
            vector<double> u, fix, uLP, fixLP;
            duals(smooth, u, fix);
            duals(pi, uLP, fixLP);
            vector<PricedRoute> out;
            for (int t = 0; t < T; t++) hp_.price(t, u.data(), fix[t], LABELS, MAX_COLS, out);
            int negLP = 0;
            for (const PricedRoute& o : out) {
                toLP(pool_.add(o.t, o.r.data(), (int)o.r.size(), o.km));
                if (routeRc(uLP, fixLP[o.t], o.r) < -1e-7) negLP++;
            }
            if (negLP == 0 && !added) {   // промах сглаживания: центр — в цены ЛП, поиск заново
                bool same = true;
                for (int i = 0; i < m_ && same; i++) same = fabs(center[i] - pi[i]) <= 1e-9;
                center = pi;
                if (!same) {
                    needSolve = !out.empty();
                    continue;
                }
            } else {
                center = smooth;
            }
            // эвристика ещё находит столбцы — продолжаем, но не дольше slice без точного шага: при многих типах бригад
            // (у каждой свой дом) она может не иссякнуть до конца времени, а оценка Лагранжа верна при любых ценах
            if ((added || negLP) && clock.sec() - lastExact < slice) {
                if (!added && out.empty()) break;
                continue;
            }
            lastExact = clock.sec();
            // точный поиск при ценах ЛП: новые столбцы и допустимая оценка
            vector<double> uB = uLP;
            int nTot = 0;
            for (int t = 0; t < T; t++) nTot += TY.cnt[t];
            if (!exactTypes)
                for (int k = 0; k < N; k++)
                    if (rowIdx_[k] >= 0) uB[k] = max(0.0, uB[k]);
            double L = pi[vehicleRow()] * (exactTypes ? nTot : P.V), sumU = 0;
            for (int k = 0; k < N; k++)
                if (rowIdx_[k] >= 0) {
                    L += uB[k];
                    sumU += uB[k];
                }
            bool allExact = true;
            int nNew = 0;
            double theta = 0;
            const double fixB = ROUTE_COST - pi[vehicleRow()];
            vector<double> minRc(T, 0);
            for (int t = 0; t < T; t++) {
                if (!TY.cnt[t]) continue;
                vector<std::pair<double, vector<int>>> cols;
                bool complete;
                minRc[t] = xp_.minReducedCost(t, uB.data(), fixB, LABEL_CAP, complete, cols, MAX_COLS);
                allExact &= complete;
                theta = max(theta, fixB - minRc[t]);
                for (const auto& [km, r] : cols)
                    if (routeRc(uLP, fixLP[t], r) < -1e-7) {
                        toLP(pool_.add(t, r.data(), (int)r.size(), km));
                        nNew++;
                    }
            }
            vector<int> order(T);
            std::iota(order.begin(), order.end(), 0);
            std::sort(order.begin(), order.end(), [&](int a, int b) { return minRc[a] < minRc[b]; });
            if (exactTypes) {
                for (int t = 0; t < T; t++) L += TY.cnt[t] * minRc[t];
            } else {
                int left = P.V;
                for (int t : order) {
                    if (left <= 0 || minRc[t] >= 0) break;
                    int q = min(left, TY.cnt[t]);
                    L += q * minRc[t];
                    left -= q;
                }
            }
            if (L > bestLag) {
                bestLag = L;
                uLag = uB;
                fixLag = fixB;
                minRcLag = minRc;
            }
            if (theta > 1e-9 && !exactTypes) L = max(L, ROUTE_COST * sumU / theta);   // Фарли
            bestL_ = max(bestL_, L);
            if (bestL_ > stopAt) break;
            if (nNew == 0) {
                converged = allExact;
                break;
            }
        }
    }
};

}  // namespace dispatch::bound
