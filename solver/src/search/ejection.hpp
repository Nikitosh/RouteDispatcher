// Ремонт вставкой с выталкиванием (Nagata, Bräysy 2009): невыполненные заявки лежат в стеке; заявка, которую нельзя
// вставить без нарушений, вставляется с выталкиванием до 3 заявок одного маршрута — набор с наименьшей суммой
// счётчиков неудач. Выбор набора — перебор «оставить/выкинуть» по позициям маршрута с отсечением по этой сумме;
// после вставленной заявки остаток без выкидываний проверяется за O(1) по порогам хвоста исходного маршрута (с
// обедом) и сумме оборудования.
#pragma once
#include "search/lns.hpp"

namespace dispatch::lns {

class EjectionRepair {
public:
    EjectionRepair(const Problem& p, Rng& r, const Timer& clock) : P(p), rng(r), clock_(clock), fails_(p.N, 1) {}

    // Вставить все невыполненные, не превышая cap бригад. true — вставлены все.
    bool repair(Solution& s, int cap, double tEnd, long maxIter) {
        stack_.clear();
        for (int k = 0; k < P.N; k++)
            if (s.rt[k] < 0) stack_.push_back(k);
        std::fill(fails_.begin(), fails_.end(), 1);
        for (long it = 0; !stack_.empty(); it++) {
            if ((it & 7) == 0 && clock_.sec() > tEnd) return false;
            if (maxIter > 0 && it > maxIter) return false;
            int k = stack_.back();
            stack_.pop_back();
            if (insertFeasible(s, k, cap)) continue;
            fails_[k]++;
            if (!insertEject(s, k)) stack_.insert(stack_.begin(), k);
        }
        return true;
    }

private:
    static constexpr int MAX_EJECT = 3;
    static constexpr long NODE_CAP = 200000;   // предел узлов перебора на одну вставку

    const Problem& P;
    Rng& rng;
    const Timer& clock_;
    vector<int> fails_, stack_;
    // рабочие массивы перебора
    int seq_[MAX_LEN + 2], len_ = 0, posK_ = 0, kIns_ = -1, M_ = 0;
    double latA_[MAX_LEN + 2], latB_[MAX_LEN + 2];   // пороги хвоста исходного маршрута для позиций после вставленной
    int sufQ_[MAX_LEN + 3];                           // оборудование позиций ≥ i
    const double* Tv_ = nullptr;
    int vCur_ = 0;                 // бригада, для которой идёт перебор (конец её смены, обед, запас)
    int curE_[MAX_EJECT], bestE_[MAX_EJECT], nBestE_ = 0, bestV_ = -1, bestP_ = -1;
    double bestSum_ = INF;
    long nodes_ = 0;

    // Допустимая вставка с минимальным приростом цели; пустую бригаду можно открыть, пока бригад меньше cap.
    bool insertFeasible(Solution& s, int k, int cap) {
        int used = s.used(), bv = -1, bp = -1;
        double best = INF;
        cap = min(cap, P.cap);
        for (int v = 0; v < P.V; v++) {
            if (!P.can(v, k)) continue;
            const int L = s.len[v];
            if (L == 0 && used >= cap) continue;
            for (int p = 0; p <= L; p++) {
                if (p > 0 && s.dep[v][p - 1] > P.b[k]) break;
                double d = s.insDelta(P, v, p, k);
                if (d >= INF) continue;
                d += (L == 0 ? P.wVeh : 0) + 1e-6 * rng.uni();
                if (d < best) {
                    best = d;
                    bv = v;
                    bp = p;
                }
            }
        }
        if (bv < 0) return false;
        s.insertAt(P, bv, bp, k);
        return true;
    }
    // Перебор по позиции i последовательности seq_: состояние расписания w в точке prev, оборудование оставленных q,
    // сумма счётчиков выкинутых sum, выкинуто cnt.
    void dfs(int i, const Walk& w, int prev, int q, int cnt, double sum) {
        if (++nodes_ > NODE_CAP) return;
        const Lunch& L = P.lun[vCur_];
        double wt;
        if (i == len_) {
            if (sum < bestSum_ && P.fits(vCur_, q) && settle(w, L, P.end[vCur_], false, TOL, wt)) record(cnt, sum);
            return;
        }
        int e = seq_[i], ne = P.nd[e];
        const double tr = Tv_[prev * M_ + ne];
        if (i > posK_ && P.fits(vCur_, q + sufQ_[i]) && tailOK(w, L, tr, P.a[e], latA_[i], latB_[i], TOL)) {
            if (sum < bestSum_) record(cnt, sum);   // остаток без выкидываний допустим, дальше только дороже
            return;
        }
        Walk x = w;
        if (P.fits(vCur_, q + P.eq[e]) && advance(x, L, tr, P.a[e], P.bv(e, vCur_), P.sv(e, vCur_), P.acc[e], TOL)) {
            dfs(i + 1, x, ne, q + P.eq[e], cnt, sum);
        } else if (e == kIns_) {
            return;   // вставляемую не выкинуть, а раньше неё время только растёт
        }
        if (e != kIns_ && cnt < MAX_EJECT && sum + fails_[e] < bestSum_) {
            curE_[cnt] = e;
            dfs(i + 1, w, prev, q, cnt + 1, sum + fails_[e]);
        }
    }
    void record(int cnt, double sum) {
        bestSum_ = sum;
        nBestE_ = cnt;
        memcpy(bestE_, curE_, cnt * sizeof(int));
    }
    // Лучшая вставка k с выталкиванием по всем непустым маршрутам и позициям; выкинутые — в стек.
    bool insertEject(Solution& s, int k) {
        bestSum_ = INF;
        bestV_ = -1;
        M_ = P.M;
        nodes_ = 0;
        int order[MAX_VEHICLES], nv = 0;
        for (int v = 0; v < P.V; v++)
            if (s.len[v] && P.can(v, k)) order[nv++] = v;
        rng.shuffle(order, order + nv);
        for (int q = 0; q < nv; q++) {
            const int v = order[q], L = s.len[v];
            Tv_ = P.T[v];
            vCur_ = v;
            for (int p = 0; p <= L; p++) {
                len_ = 0;
                for (int j = 0; j < p; j++) seq_[len_++] = s.r[v][j];
                posK_ = len_;
                seq_[len_++] = k;
                for (int j = p; j < L; j++) seq_[len_++] = s.r[v][j];
                for (int j = p; j < L; j++) {
                    latA_[posK_ + 1 + (j - p)] = s.latA[v][j];
                    latB_[posK_ + 1 + (j - p)] = s.lat[v][j];
                }
                sufQ_[len_] = 0;
                for (int j = len_ - 1; j >= 0; j--) sufQ_[j] = sufQ_[j + 1] + P.eq[seq_[j]];
                kIns_ = k;
                double before = bestSum_;
                dfs(0, startWalk(P.begin[v]), P.start[v], 0, 0, 0);
                if (bestSum_ < before) {
                    bestV_ = v;
                    bestP_ = p;
                }
            }
        }
        if (bestV_ < 0) return false;
        const int v = bestV_, L = s.len[v];
        auto ejected = [&](int x) { return std::find(bestE_, bestE_ + nBestE_, x) != bestE_ + nBestE_; };
        int tmp[MAX_LEN + 2], nl = 0;
        for (int j = 0; j <= L; j++) {
            if (j == bestP_) tmp[nl++] = k;
            if (j < L && !ejected(s.r[v][j])) tmp[nl++] = s.r[v][j];
        }
        for (int j = 0; j < nBestE_; j++) {
            s.rt[bestE_[j]] = -1;
            stack_.push_back(bestE_[j]);
        }
        s.len[v] = nl;
        memcpy(s.r[v], tmp, nl * sizeof(int));
        s.rt[k] = v;
        vector<int> dropped;
        s.rebuild(P, v, &dropped);
        for (int x : dropped) stack_.push_back(x);
        return true;
    }
};

}  // namespace dispatch::lns
