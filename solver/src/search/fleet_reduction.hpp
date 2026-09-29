// Сокращение парка разрушением-восстановлением с учётом «неудач» заявок (absence).
//
// Попытка: снять маршрут и вернуть его заявки в оставшиеся (бригад на одну меньше). Итерация разрушает часть решения
// одним из пяти операторов и восстанавливает regret-вставкой (K = 1..3, с шумом или без); операторы выбираются
// адаптивно. Каждая заявка, оставшаяся невставленной, копит absence; принимается решение, которое лучше по
// штрафу или по сумме absence невставленных (Christiaens, Vanden Berghe). Пока не всё обслужено (до tServe), парк
// не сокращается, а добирается штраф.
#pragma once
#include "search/km_search.hpp"

namespace dispatch::lns {

class FleetReducer {
public:
    FleetReducer(const Problem& p, Rng& r, const Timer& clock, KmSearch& km)
        : P(p), rng(r), clock_(clock), km_(km), ruins_(p), absence_(p.N, 0) {
        pool_.reserve(p.N);
    }

    // best — вход и выход. poll: Replaced — best заменён извне (новая попытка), Stop — выход. onImprove вызывается
    // на каждом рекорде; рекорды полируются до tPolish.
    void run(Solution& best, double tServe, double tEnd, double tPolish, const PollFn& poll,
             const std::function<void(const Solution&)>& onImprove) {
        const int N = P.N;
        const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30));
        Solution cur = best;
        int cap = P.V;
        bool elim = best.nUn() == 0;
        auto startAttempt = [&] {
            cur = best;
            cap = best.used() - 1;
            int v = pickRouteToRemove(P, cur, rng, absence_);
            if (nAttempt_++ > 0 && rng.uni() < RANDOM_ROUTE) {   // случайный маршрут с весом 1/длина²
                double w[MAX_VEHICLES], wsum = 0;
                for (int u = 0; u < P.V; u++) {
                    w[u] = cur.len[u] ? 1.0 / (cur.len[u] * cur.len[u]) : 0;
                    wsum += w[u];
                }
                double x = rng.uni() * wsum;
                for (int u = 0; u < P.V; u++) {
                    x -= w[u];
                    if (w[u] > 0 && x <= 0) {
                        v = u;
                        break;
                    }
                }
            }
            vector<int> removed;
            removeSet(P, cur, cur.r[v], cur.len[v], removed, nullptr);
        };
        if (elim && best.used() > 1) startAttempt();
        else if (elim) cap = 0;
        double curPen = cur.penSum(P), curAbs = sumAbsence(cur);
        while (cap > 0 && clock_.sec() < tEnd) {
            if (poll && iters_ % POLL_EVERY == 0) {
                Poll r = poll(best, cur);
                if (r == Poll::Stop) return;
                if (r == Poll::Replaced) {
                    elim = best.nUn() == 0;
                    if (elim) {
                        if (best.used() <= 1) return;
                        startAttempt();
                    } else {
                        cur = best;
                        cap = P.V;
                    }
                    curPen = cur.penSum(P);
                    curAbs = sumAbsence(cur);
                }
            }
            if (!elim && clock_.sec() > tServe) {
                elim = true;
                startAttempt();
                curPen = cur.penSum(P);
                curAbs = sumAbsence(cur);
            }
            iters_++;
            int ruinOp = ruinChoice_.pick(rng), noisy = noiseChoice_.pick(rng), regretOp = regretChoice_.pick(rng);
            undo_.begin(cur);
            pool_.clear();
            collectAbsent(cur, pool_);
            prevAbsent_.assign(pool_.begin(), pool_.end());
            ruin(cur, ruinOp, qmin + rng.randint(qmax - qmin + 1));
            rng.shuffle(pool_.begin(), pool_.end());
            regret_.run(P, cur, pool_, rng, regretOp + 1, cap, W_VEHICLE, &undo_, noisy ? NOISE * P.maxD : 0);
            for (int k : pool_) absence_[k] += 1;
            curAbs = 0;
            for (int k : prevAbsent_) curAbs += absence_[k];
            double p = cur.penSum(P), a = sumAbsence(cur), score = 0;
            if (cur.better(best, P)) {
                score = SCORE_BEST;
                best = cur;
                km_.polish(best, cap, tPolish);
                if (onImprove) onImprove(best);
                if (best.nUn() == 0 || elim) {
                    elim = true;
                    if (best.used() <= 1) break;
                    startAttempt();
                } else {
                    cur = best;
                }
                curPen = cur.penSum(P);
                curAbs = sumAbsence(cur);
            } else if (p < curPen - 1e-9) {
                score = SCORE_PENALTY;
                curPen = p;
                curAbs = a;
            } else if (a < curAbs) {
                score = SCORE_ABSENCE;
                curPen = p;
                curAbs = a;
            } else {
                undo_.restore(P, cur);
            }
            ruinChoice_.add(ruinOp, score);
            regretChoice_.add(regretOp, score);
            noiseChoice_.add(noisy, score);
            if (iters_ % 100 == 0) {
                ruinChoice_.update(0.1);
                regretChoice_.update(0.1);
                noiseChoice_.update(0.1);
            }
        }
    }

private:
    static constexpr double RANDOM_ROUTE = 0.5;   // доля попыток, где маршрут выбирается случайно, а не по absence
    static constexpr double NOISE = 0.025;        // шум вставки, доля наибольшего расстояния между заявками
    static constexpr double SCORE_BEST = 33, SCORE_PENALTY = 9, SCORE_ABSENCE = 13;
    static constexpr int POLL_EVERY = 32;

    const Problem& P;
    Rng& rng;
    const Timer& clock_;
    KmSearch& km_;
    Ruins ruins_;
    Regret regret_;
    Undo undo_;
    vector<int> pool_, prevAbsent_;
    vector<double> absence_;
    Adaptive ruinChoice_{5}, noiseChoice_{2}, regretChoice_{3};
    long iters_ = 0;
    int nAttempt_ = 0;

    double sumAbsence(const Solution& s) const {
        double x = 0;
        for (int k = 0; k < P.N; k++)
            if (s.rt[k] < 0) x += absence_[k];
        return x;
    }
    void ruin(Solution& s, int op, int q) {
        ruins_.clear();
        switch (op) {
            case 0: ruins_.random(s, rng, q); break;
            case 1: ruins_.worst(s, rng, q); break;
            case 2: ruins_.shaw(s, rng, q); break;
            case 3: ruins_.route(s, rng, q); break;
            default: ruins_.slot(s, rng, q);
        }
        removeSet(P, s, ruins_.rem.data(), (int)ruins_.rem.size(), pool_, &undo_);
    }
};

// Стартовое решение: лучшее из regret-2 по всем заявкам и жадной вставки по возрастанию ширины окна.
inline Solution construct(const Problem& P, Rng& rng) {
    Regret regret;
    vector<int> pool(P.N);
    Solution a;
    a.init(P);
    std::iota(pool.begin(), pool.end(), 0);
    regret.run(P, a, pool, rng, 2, P.V, W_VEHICLE, nullptr);
    Solution b;
    b.init(P);
    pool.resize(P.N);
    std::iota(pool.begin(), pool.end(), 0);
    std::stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return P.b[x] - P.a[x] < P.b[y] - P.a[y]; });
    greedyInsert(P, b, pool, rng, 0, P.V, W_VEHICLE, nullptr);
    return a.better(b, P) ? a : b;
}

}  // namespace dispatch::lns
