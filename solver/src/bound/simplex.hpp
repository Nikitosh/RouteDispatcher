// Прямой симплекс для ограниченной мастер-задачи: min c·x, A x (= или ≤) b, x ≥ 0, A из нулей и единиц, строк до
// ~200 — плотная обратная матрица базиса. Первые m столбцов — единичные: искусственные со стоимостью bigM для строк
// «=», слабые с нулевой стоимостью для «≤». Правые части слегка возмущены против зацикливания.
#pragma once
#include "core/problem.hpp"

namespace dispatch::bound {

class Simplex {
public:
    vector<double> b, pi;   // правые части (возмущённые) и двойственные цены

    void init(int rows, const vector<double>& rhs, const vector<char>& isEq, double bigM) {
        m_ = rows;
        b = rhs;
        cost_.clear();
        A_.clear();
        pos_.clear();
        for (int r = 0; r < m_; r++) {
            b[r] += (1 + rng_.uni()) * PERTURB * (isEq[r] ? 1.0 : 100.0 * max(1.0, fabs(b[r])));
            addCol(isEq[r] ? bigM : 0, {r});
        }
        basis_.resize(m_);
        for (int r = 0; r < m_; r++) {
            basis_[r] = r;
            pos_[r] = r;
        }
        Binv_.assign(m_ * m_, 0);
        for (int r = 0; r < m_; r++) Binv_[r * m_ + r] = 1;
        xB_ = b;
        pi.assign(m_, 0);
    }
    int addCol(double c, vector<int> rows) {
        cost_.push_back(c);
        A_.push_back(std::move(rows));
        pos_.push_back(-1);
        return (int)cost_.size() - 1;
    }
    int ncols() const { return (int)cost_.size(); }
    double obj() const {
        double o = 0;
        for (int i = 0; i < m_; i++) o += cost_[basis_[i]] * xB_[i];
        return o;
    }
    // Стартовый базис: номер столбца на каждую позицию.
    void setBasis(const vector<int>& bs) {
        for (int j : basis_) pos_[j] = -1;
        basis_ = bs;
        for (int i = 0; i < m_; i++) pos_[basis_[i]] = i;
        refactor();
    }
    // До maxIt итераций или до tEnd. 0 — оптимум, 1 — предел итераций или времени, 2 — задача неограничена.
    int solve(int maxIt, const Timer& clock, double tEnd) {
        vector<double> d(m_);
        for (int it = 0; it < maxIt; it++) {
            if (it % 60 == 59) refactor();
            if ((it & 15) == 15 && clock.sec() > tEnd) return 1;
            computePi();
            // частичный выбор: циклические отрезки по SEGMENT столбцов до первого отрезка с отрицательной rc
            int q = -1, scanned = 0;
            double best = -1e-9;
            const int nc = ncols();
            while (scanned < nc && q < 0) {
                int lim = min(nc - scanned, SEGMENT);
                for (int s = 0; s < lim; s++) {
                    int j = scanPos_;
                    if (++scanPos_ >= nc) scanPos_ = 0;
                    if (pos_[j] >= 0) continue;
                    double r = rcOf(j);
                    if (r < best) {
                        best = r;
                        q = j;
                    }
                }
                scanned += lim;
            }
            if (q < 0) return 0;
            for (int i = 0; i < m_; i++) {
                double x = 0;
                const double* row = &Binv_[i * m_];
                for (int r : A_[q]) x += row[r];
                d[i] = x;
            }
            // тест отношений Харриса
            const double tol = 1e-9, tp = 1e-9;
            double tmax = 1e30;
            for (int i = 0; i < m_; i++)
                if (d[i] > tol) tmax = min(tmax, (xB_[i] + tp) / d[i]);
            if (tmax >= 1e29) return 2;
            int p = -1;
            double bd = 0;
            for (int i = 0; i < m_; i++)
                if (d[i] > tol && xB_[i] / d[i] <= tmax && d[i] > bd) {
                    bd = d[i];
                    p = i;
                }
            double th = max(0.0, xB_[p] / d[p]);
            for (int i = 0; i < m_; i++) xB_[i] -= th * d[i];
            xB_[p] = th;
            double inv = 1.0 / d[p];
            double* rp = &Binv_[p * m_];
            for (int r = 0; r < m_; r++) rp[r] *= inv;
            for (int i = 0; i < m_; i++) {
                if (i == p || d[i] == 0) continue;
                double f = d[i];
                double* ri = &Binv_[i * m_];
                for (int r = 0; r < m_; r++) ri[r] -= f * rp[r];
            }
            pos_[basis_[p]] = -1;
            basis_[p] = q;
            pos_[q] = p;
        }
        return 1;
    }

private:
    static constexpr int SEGMENT = 400;
    static constexpr double PERTURB = 1e-6;

    int m_ = 0, scanPos_ = 0;
    vector<double> cost_, Binv_, xB_;
    vector<vector<int>> A_;
    vector<int> basis_, pos_;
    Rng rng_{12345};

    double rcOf(int j) const {
        double r = cost_[j];
        for (int q : A_[j]) r -= pi[q];
        return r;
    }
    void computePi() {
        std::fill(pi.begin(), pi.end(), 0.0);
        for (int i = 0; i < m_; i++) {
            double c = cost_[basis_[i]];
            if (c == 0) continue;
            const double* row = &Binv_[i * m_];
            for (int r = 0; r < m_; r++) pi[r] += c * row[r];
        }
    }
    // Обращение базиса Гауссом–Жорданом; вырожденные столбцы заменяются единичными свободных строк.
    void refactor() {
        vector<double> Bm(m_ * m_, 0), Iv(m_ * m_, 0);
        for (int c = 0; c < m_; c++)
            for (int r : A_[basis_[c]]) Bm[r * m_ + c] = 1;
        for (int i = 0; i < m_; i++) Iv[i * m_ + i] = 1;
        vector<int> rowOfCol(m_, -1), rowDone(m_, 0);
        for (int c = 0; c < m_; c++) {
            int pr = -1;
            double bv = 1e-9;
            for (int r = 0; r < m_; r++)
                if (!rowDone[r] && fabs(Bm[r * m_ + c]) > bv) {
                    bv = fabs(Bm[r * m_ + c]);
                    pr = r;
                }
            if (pr < 0) continue;
            rowDone[pr] = 1;
            rowOfCol[c] = pr;
            double inv = 1.0 / Bm[pr * m_ + c];
            for (int j = 0; j < m_; j++) {
                Bm[pr * m_ + j] *= inv;
                Iv[pr * m_ + j] *= inv;
            }
            for (int r = 0; r < m_; r++) {
                if (r == pr) continue;
                double f = Bm[r * m_ + c];
                if (f == 0) continue;
                for (int j = 0; j < m_; j++) {
                    Bm[r * m_ + j] -= f * Bm[pr * m_ + j];
                    Iv[r * m_ + j] -= f * Iv[pr * m_ + j];
                }
            }
        }
        if (std::find(rowOfCol.begin(), rowOfCol.end(), -1) != rowOfCol.end()) {
            vector<int> freeRows;
            for (int r = 0; r < m_; r++)
                if (!rowDone[r]) freeRows.push_back(r);
            int fi = 0;
            for (int c = 0; c < m_; c++)
                if (rowOfCol[c] < 0) {
                    pos_[basis_[c]] = -1;
                    basis_[c] = freeRows[fi++];
                    pos_[basis_[c]] = c;
                }
            refactor();
            return;
        }
        for (int c = 0; c < m_; c++) memcpy(&Binv_[c * m_], &Iv[rowOfCol[c] * m_], m_ * sizeof(double));
        for (int i = 0; i < m_; i++) {
            double x = 0;
            for (int r = 0; r < m_; r++) x += Binv_[i * m_ + r] * b[r];
            xB_[i] = x;
        }
    }
};

}  // namespace dispatch::bound
