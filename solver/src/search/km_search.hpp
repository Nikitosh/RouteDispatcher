// Км-поиск при фиксированном парке: отжиг на разрушении-восстановлении (в основном SISR), каждый рекорд
// полируется локальным поиском. Опрос poll даёт внешнему коду оборвать поиск или подменить решение.
#pragma once
#include "search/lns.hpp"
#include "search/local_search.hpp"

namespace dispatch::lns {

enum class Poll { Continue, Stop, Replaced };   // Replaced — best и cur заменены вызывающим
using PollFn = std::function<Poll(Solution& best, Solution& cur)>;

class KmSearch {
public:
    bool newBest = false;   // появился рекорд с прошлого сброса флага

    KmSearch(const Instance& in, const Problem& p, Rng& r, const Timer& clock) : I(in), P(p), rng(r), clock_(clock), ls_(in), ruins_(p) {
        pool_.reserve(in.N);
    }

    // Локальный поиск до tEnd; результат принимается, если не хуже по (штраф, бригады, км).
    void polish(Solution& s, int cap, double tEnd) {
        ls::Solution x(I, s.routes());
        ls_.run(x, cap, clock_, tEnd);
        Solution y(P, x.r);
        if (y.better(s, P) || (y.penSum(P) == s.penSum(P) && y.used() == s.used() && y.kmTot() <= s.kmTot())) s = y;
    }

    // Отжиг от best до tEnd; best — вход и выход. Температура в единицах среднего ребра, геометрически 5 → 0,05.
    // Возвращает true, если оборван опросом.
    bool run(Solution& best, double tEnd, const PollFn& poll) {
        const int N = P.N;
        int cap = best.nUn() > 0 ? P.V : best.used();   // не всё обслужено — можно открывать бригады
        Solution cur = best;
        const int qmin = min(N, 4), qmax = max(qmin, min((int)(0.3 * N), 30));
        const double edge = max(1.0, best.kmTot()) / max(1, N - best.nUn());
        const double T0 = T_START * edge, Tf = T_END * edge;
        const double t1 = clock_.sec(), span = max(1e-9, tEnd - t1);
        double T = T0, curCost = cur.cost(P), bestCost = best.cost(P);
        for (long it = 0;; it++) {
            if ((it & 15) == 0) {
                double now = clock_.sec();
                if (now > tEnd) break;
                T = T0 * pow(Tf / T0, min(1.0, max(0.0, (now - t1) / span)));
                if (poll && it % POLL_EVERY == 0 && it) {
                    Poll r = poll(best, cur);
                    if (r == Poll::Stop) return true;
                    if (r == Poll::Replaced) {
                        curCost = cur.cost(P);
                        bestCost = best.cost(P);
                    }
                }
            }
            undo_.begin(cur);
            pool_.clear();
            collectAbsent(cur, pool_);
            ruins_.clear();
            bool big = false;
            double u = rng.uni();
            if (u < P_ROUTE) ruins_.route(cur, rng, qmin + rng.randint(qmax - qmin + 1));
            else if (u < P_ROUTE + P_PAIR) {
                ruins_.routePair(cur, rng);
                big = true;
            } else ruins_.sisr(cur, rng);
            removeSet(P, cur, ruins_.rem.data(), (int)ruins_.rem.size(), pool_, &undo_);
            if (big && rng.uni() < 0.5) {
                rng.shuffle(pool_.begin(), pool_.end());
                regret_.run(P, cur, pool_, rng, 2, cap, W_VEHICLE, &undo_);
            } else {
                sisrSort(P, pool_, rng);
                greedyInsert(P, cur, pool_, rng, BLINK, cap, W_VEHICLE, &undo_);
            }
            double c = cur.cost(P);
            if (c >= curCost - T * log(rng.uni() + 1e-300)) {
                undo_.restore(P, cur);
                continue;
            }
            curCost = c;
            if (c < bestCost - 1e-9) {
                best = cur;
                bestCost = c;
                polish(best, cap, tEnd);
                double c2 = best.cost(P);
                if (c2 < bestCost - 1e-9) {
                    bestCost = c2;
                    cur = best;
                    curCost = c2;
                }
                newBest = true;
            }
        }
        return false;
    }

private:
    static constexpr double T_START = 5, T_END = 0.05;
    static constexpr double P_ROUTE = 0.05, P_PAIR = 0.05;   // разрушение маршрута целиком и пары маршрутов
    static constexpr double BLINK = 0.01;
    static constexpr int POLL_EVERY = 32;

    const Instance& I;
    const Problem& P;
    Rng& rng;
    const Timer& clock_;
    ls::LocalSearch ls_;
    Ruins ruins_;
    Regret regret_;
    Undo undo_;
    vector<int> pool_;
};

}  // namespace dispatch::lns
