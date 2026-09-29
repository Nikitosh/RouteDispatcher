// Кооперативный поиск: потоки с разными ролями делят одно лучшее решение (SharedBest).
//
//  A — отжиг (починка и сокращение бригад), затем сокращение разрушением-восстановлением до 0,6·T; дальше км отжигом.
//  R — построение (regret или жадное), затем сокращение разрушением-восстановлением до 0,5·T; дальше км SISR.
//  K — км SISR от общего лучшего.
//  Z — отжиг целиком (оценка снизу, переназначение бригад, разбиение + удаление); дальше км SISR.
//  H — то же на копии задачи, где дальним бригадам дороже км вне своего кластера; дальше км SISR.
//  W — обмен «голов» маршрутов у пар бригад (хотя бы одна дальняя) по разрезу времени; дальше км SISR.
// Без дальних бригад H и W работают как Z. Км-потоки обрываются, когда другой поток сократил парк, и начинают заново
// от нового лучшего; отстав от общего лучшего, забирают его. Когда парк дошёл до оценки снизу по нагрузке, потоки
// сокращения переходят к км.
#pragma once
#include <map>
#include <memory>
#include <thread>
#include "coop/shared_best.hpp"
#include "search/annealer.hpp"
#include "search/ejection.hpp"
#include "search/fleet_reduction.hpp"

namespace dispatch::coop {

using lns::Poll;

// Копия задачи, где км между кластерами дороже в penalty раз (кластер заявки — ближайшая стартовая точка по
// времени на машине). Выезды из офиса не дорожают.
inline Instance clusterPenalized(const Instance& I, double penalty) {
    Instance G = I;
    const int S = I.S, M = I.M;
    vector<int> cls(M);
    for (int i = 0; i < S; i++) cls[i] = i;
    for (int k = 0; k < I.N; k++) {
        int b = 0;
        for (int s = 1; s < S; s++)
            if (I.T[0][s * M + I.node(k)] < I.T[0][b * M + I.node(k)]) b = s;
        cls[I.node(k)] = b;
    }
    for (int m = 0; m < FILE_MODES; m++)
        for (int i = 0; i < M; i++)
            for (int j = S; j < M; j++)
                if (cls[i] != cls[j] && i != 0) G.D[m][i * M + j] *= penalty;
    return G;
}

// Копия задачи, где у каждой дальней бригады своя служебная матрица: км до заявок вне её домашнего кластера
// дороже в penalty раз (время не меняется). Стартовые точки ближе 3 минут друг к другу — один кластер.
inline Instance homePenalized(const Instance& I, double penalty) {
    Instance H = I;
    const int S = I.S, M = I.M;
    vector<int> rep(S);
    for (int s = 0; s < S; s++) {
        rep[s] = s;
        for (int q = 0; q < s; q++)
            if (I.T[0][q * M + s] < 3 && I.T[0][s * M + q] < 3) {
                rep[s] = rep[q];
                break;
            }
    }
    vector<int> cluster(I.N);
    for (int k = 0; k < I.N; k++) {
        int b = 0;
        for (int s = 1; s < S; s++)
            if (I.T[0][s * M + I.node(k)] < I.T[0][b * M + I.node(k)]) b = s;
        cluster[k] = rep[b];
    }
    const int office = rep[0];
    int slot = FILE_MODES;
    std::map<std::pair<int, int>, int> made;   // (дом, исходный вид транспорта) → служебная матрица
    for (int v = 0; v < I.V; v++) {
        int home = rep[I.veh[v].start];
        if (home == office) continue;
        auto key = std::make_pair(home, I.veh[v].mode);
        if (!made.count(key)) {
            if (slot >= MAX_MODES) continue;
            int m0 = I.veh[v].mode;
            H.T[slot] = I.T[m0];
            H.D[slot] = I.D[m0];
            for (int i = 0; i < M; i++)
                for (int k = 0; k < I.N; k++)
                    if (cluster[k] != home) H.D[slot][i * M + I.node(k)] *= penalty;
            made[key] = slot++;
        }
        H.veh[v].mode = made[key];
    }
    return H;
}

class Cooperative {
public:
    Cooperative* forwardTo = nullptr;   // куда ещё отдавать свои решения (ведомый → основной)

    Cooperative(const Instance& in, double seconds, uint64_t seed, const Timer& clock, string roles)
        : I(in), tl_(seconds), seed_(seed), clock_(clock), roles_(std::move(roles)), F_(in), P_(in), shared_(in),
          lowerBound_(fast::workLowerBound(F_)) {}

    const lns::Problem& problem() const { return P_; }
    bool best(Routes& R) { return shared_.get(R); }

    bool offer(const Routes& R) {
        if (forwardTo) forwardTo->offer(R);
        bool better = shared_.offer(R);
        if (better && shared_.fleetKey() <= lowerBound_) fleetDone_ = true;   // штраф 0 и бригад не больше оценки
        return better;
    }

    void launch(ThreadPool& pool) {
        for (int id = 0; id < (int)roles_.size(); id++) {
            char role = roles_[id];
            pool.spawn([this, id, role] {
                Rng rng(seed_ * 1000003 + id * 7777);
                switch (role) {
                    case 'A':
                        annealFleet(id, FLEET_END_A * tl_);
                        kmAnneal(seed_ * 31 + id * 1009, tl_);
                        break;
                    case 'R':
                        constructFleet(id, FLEET_END_R * tl_);
                        kmSisr(rng, tl_);
                        break;
                    case 'K': kmSisr(rng, tl_); break;
                    case 'Z':
                        annealWhole(id);
                        kmSisr(rng, tl_);
                        break;
                    case 'H':
                        annealHomeGuided(id);
                        kmSisr(rng, tl_);
                        break;
                    case 'W':
                        swapHeads(id);
                        kmSisr(rng, tl_);
                        break;
                    default: fprintf(stderr, "неизвестная роль %c\n", role); exit(1);
                }
            });
        }
    }

private:
    static constexpr double FLEET_END_A = 0.6, FLEET_END_R = 0.5;
    static constexpr double MIGRATE_EVERY = 0.05;   // доля T между проверками миграции
    static constexpr double MIGRATE_GAP = 0.003;    // забрать общий лучший, если он лучше на эту долю км
    static constexpr double PUBLISH_EVERY = 0.004;  // доля T между публикациями км-улучшений отжига
    static constexpr auto IDLE = std::chrono::microseconds(200), IDLE_W = std::chrono::microseconds(500);

    const Instance& I;
    const double tl_;
    const uint64_t seed_;
    const Timer& clock_;
    const string roles_;
    const fast::Data F_;
    const lns::Problem P_;
    SharedBest shared_;
    const int lowerBound_;
    std::atomic<bool> fleetDone_{false};

    long long keyOf(const Routes& R) const { return evaluate(I, R).fleetKey(); }
    static long long keyOf(double scalar) { return (long long)floor(scalar / W_VEHICLE); }
    // У общего лучшего парк меньше, чем у решения с целью scalar.
    bool sharedFleetBetter(double scalar) const { return shared_.fleetKey() < keyOf(scalar); }

    // ---- км ----

    // Км SISR от общего лучшего до tEnd; при чужом улучшении парка — заново от нового лучшего.
    void kmSisr(Rng& rng, double tEnd) {
        lns::KmSearch km(I, P_, rng, clock_);
        while (clock_.sec() < tEnd) {
            Routes R;
            if (!shared_.get(R)) {
                std::this_thread::sleep_for(IDLE);
                continue;
            }
            const long long myKey = keyOf(R);
            lns::Solution s(P_, R);
            double lastMigration = clock_.sec();
            auto poll = [&](lns::Solution& best, lns::Solution& cur) {
                if (km.newBest) {
                    km.newBest = false;
                    offer(best.routes());
                }
                if (shared_.fleetKey() < myKey) return Poll::Stop;
                if (clock_.sec() - lastMigration > MIGRATE_EVERY * tl_) {
                    lastMigration = clock_.sec();
                    Routes G;
                    if (shared_.bestScalar() < best.cost(P_) - MIGRATE_GAP * best.kmTot() && shared_.get(G) && keyOf(G) == myKey) {
                        best.load(P_, G);
                        cur = best;
                        return Poll::Replaced;
                    }
                }
                return Poll::Continue;
            };
            km.run(s, tEnd, poll);
            km.newBest = false;
            offer(s.routes());
        }
    }
    // Км ходами отжига от общего лучшего, с теми же перезапусками и миграцией.
    void kmAnneal(uint64_t seed, double tEnd) {
        auto sa = std::make_unique<fast::Annealer>(F_, seed);
        while (clock_.sec() < tEnd) {
            Routes R;
            if (!shared_.get(R)) {
                std::this_thread::sleep_for(IDLE);
                continue;
            }
            const long long myKey = keyOf(R);
            double lastPub = 1e300, lastPubT = clock_.sec(), lastMigration = clock_.sec();
            sa->aborted = false;
            fast::Annealer& a = *sa;
            a.poll = [&] {
                const double now = clock_.sec();
                if (a.bestScore < lastPub - 1e-9 && now - lastPubT > PUBLISH_EVERY * tl_) {
                    lastPub = a.bestScore;
                    lastPubT = now;
                    offer(a.bestRoutes());
                }
                if (shared_.fleetKey() < myKey) return true;
                if (now - lastMigration > MIGRATE_EVERY * tl_) {
                    lastMigration = now;
                    Routes G;
                    if (shared_.bestScalar() < a.bestScore - MIGRATE_GAP * fmod(a.bestScore, W_VEHICLE) && shared_.get(G) &&
                        keyOf(G) == myKey)
                        a.loadBest(G);
                }
                return false;
            };
            Routes out = a.kmOnly(R, tEnd, clock_);
            a.poll = nullptr;
            offer(out);
        }
    }

    // ---- сокращение парка ----

    // Сокращение разрушением-восстановлением от start (или общего лучшего) до tEnd; чужой меньший парк подхватывается.
    // Пока не всё обслужено — первые serveShare оставшегося времени добирается штраф.
    void reduceFleet(Rng& rng, double tEnd, double serveShare, const lns::Solution* start = nullptr) {
        if (clock_.sec() >= tEnd || fleetDone_) return;
        lns::KmSearch km(I, P_, rng, clock_);
        lns::FleetReducer reducer(P_, rng, clock_, km);
        lns::Solution best;
        if (start) {
            best = *start;
        } else {
            Routes G;
            if (!shared_.get(G)) return;
            best.load(P_, G);
        }
        long long myKey = keyOf(best.routes());
        auto poll = [&](lns::Solution& b, lns::Solution&) {
            if (fleetDone_) return Poll::Stop;
            Routes G;
            if (shared_.fleetKey() < myKey && shared_.get(G)) {
                long long k = keyOf(G);
                if (k < myKey) {
                    b.load(P_, G);
                    myKey = k;
                    return Poll::Replaced;
                }
            }
            return Poll::Continue;
        };
        auto onImprove = [&](const lns::Solution& b) {
            offer(b.routes());
            myKey = min(myKey, keyOf(b.routes()));
        };
        double tServe = best.nUn() ? clock_.sec() + serveShare * (tEnd - clock_.sec()) : 0;
        reducer.run(best, tServe, tEnd, tEnd, poll, onImprove);
        offer(best.routes());
    }
    // A: отжиг с жадного старта до сокращения включительно, затем reduceFleet.
    void annealFleet(int id, double tFleet) {
        Rng rng(seed_ * 7919 + id);
        {
            auto sa = std::make_unique<fast::Annealer>(F_, seed_ * 7 + 3 + id * 101, fast::AnnealerOptions{0.75, true});
            fast::Annealer& a = *sa;
            auto greedy = std::make_unique<fast::Solution>();
            Routes init = fast::multiGreedy(F_, rng, tFleet * 0.08, *greedy);
            offer(init);
            double lastPub = 1e300;
            a.poll = [&] {
                if (fleetDone_) return true;
                if (a.bestScore < lastPub - W_VEHICLE + 1) {   // публикуется только уменьшение штрафа или парка
                    lastPub = a.bestScore;
                    offer(a.bestRoutes());
                }
                return false;
            };
            a.onAttempt = [&] {
                if (a.bestScore < lastPub - 1e-9) {
                    lastPub = a.bestScore;
                    offer(a.bestRoutes());
                }
                Routes G;
                if (sharedFleetBetter(a.bestScore) && shared_.get(G) && evaluate(I, G).scalar() < a.bestScore) a.loadBest(G);
            };
            offer(a.run(init, tFleet, clock_));
        }
        reduceFleet(rng, tFleet, 0.5);
    }
    // R: построение и полировка, затем reduceFleet от своего решения.
    void constructFleet(int id, double tFleet) {
        Rng rng(seed_ * 104729 + id);
        lns::Solution b = lns::construct(P_, rng);
        lns::KmSearch(I, P_, rng, clock_).polish(b, P_.V, tFleet);
        offer(b.routes());
        reduceFleet(rng, tFleet, 0.3, &b);
    }

    // ---- отжиг целиком ----

    static fast::AnnealerOptions wholeOptions() {
        fast::AnnealerOptions o;
        o.workBoundStop = true;
        o.reassign = true;
        o.splitShare = 0.5;
        return o;
    }
    // Отжиг на данных data (задача этого кооператива или её копия); follow — подхватывать чужой меньший парк.
    void anneal(const fast::Data& data, Rng& rng, uint64_t seed, bool follow) {
        auto greedy = std::make_unique<fast::Solution>();
        Routes init = fast::multiGreedy(data, rng, (tl_ - clock_.sec()) * 0.05, *greedy);
        offer(init);
        auto sa = std::make_unique<fast::Annealer>(data, seed, wholeOptions());
        fast::Annealer& a = *sa;
        double lastPub = 1e300, lastPubT = 0;
        a.poll = [&] {
            const double now = clock_.sec();
            if (a.bestScore < lastPub - 1e-9 && (a.bestScore < lastPub - 5e3 || now - lastPubT > PUBLISH_EVERY * tl_)) {
                lastPub = a.bestScore;
                lastPubT = now;
                offer(a.bestRoutes());
            }
            return follow && a.inKmPhase() && sharedFleetBetter(a.bestScore);   // км ищут у худшего парка
        };
        a.onAttempt = [&] {
            if (a.bestScore < lastPub - 1e-9) {
                lastPub = a.bestScore;
                offer(a.bestRoutes());
            }
            Routes G;
            if (follow && sharedFleetBetter(a.bestScore) && shared_.get(G) && evaluate(I, G).scalar() < a.bestScore) a.loadBest(G);
        };
        offer(a.run(init, tl_, clock_));
    }
    void annealWhole(int id) {
        Rng rng(seed_ * 15485863 + id);
        anneal(F_, rng, seed_ * 7 + 3 + id * 131, true);
    }
    // H: если жадное решение обслуживает всё — отжиг на копии с «домашним» штрафом (2 или 5 по чётности номера),
    // иначе (тесная задача) — как Z.
    void annealHomeGuided(int id) {
        if (!I.hasFarHomes()) return annealWhole(id);
        {
            Rng rng(seed_ * 7 + id);
            auto greedy = std::make_unique<fast::Solution>();
            if (evaluate(I, fast::multiGreedy(F_, rng, tl_ * 0.05, *greedy)).nUnserved > 0) return annealWhole(id);
        }
        const Instance guided = homePenalized(I, id % 2 ? 5.0 : 2.0);
        const fast::Data data(guided);
        Rng rng(seed_ * 15485869 + id);
        anneal(data, rng, seed_ * 7 + 9 + id * 131, false);
    }

    // ---- W: обмен голов ----

    // Для пар бригад (сначала обе дальние) и разреза по времени t: бригада u берёт заявки v, начатые до t, и свои
    // после, v — наоборот. Недопустимое выпадает и вставляется regret-3 и ремонтом выталкиванием, затем короткий
    // км-поиск. Работает от общего лучшего с 0,15·T до 0,85·T.
    void swapHeads(int id) {
        if (!I.hasFarHomes()) return annealWhole(id);
        Rng rng(seed_ * 2246822519ULL + id);
        const double tBegin = 0.15 * tl_, tEnd = 0.85 * tl_, tPolish = 0.01 * tl_;
        const long repairIters = 300;
        static const double cuts[] = {120, 180, 240, 300, 360};
        while (clock_.sec() < tBegin) std::this_thread::sleep_for(IDLE_W);
        lns::Regret regret;
        vector<int> pool;
        while (clock_.sec() < tEnd) {
            Routes G;
            if (!shared_.get(G) || evaluate(I, G).nUnserved) {
                std::this_thread::sleep_for(IDLE_W);
                continue;
            }
            lns::Solution base(P_, G);
            const int K = base.used();
            const double baseCost = base.cost(P_);
            vector<std::tuple<int, int, int, int>> cand;   // (класс пары, u, v, разрез)
            for (int u = 0; u < P_.V; u++)
                for (int v = u + 1; v < P_.V; v++) {
                    if (!base.len[u] || !base.len[v]) continue;
                    int nFar = (P_.start[u] != 0) + (P_.start[v] != 0);
                    if (!nFar) continue;
                    for (int c = 0; c < 5; c++) cand.push_back({2 - nFar, u, v, c});
                }
            rng.shuffle(cand.begin(), cand.end());
            std::stable_sort(cand.begin(), cand.end(), [](const auto& a, const auto& b) { return std::get<0>(a) < std::get<0>(b); });
            const long version0 = shared_.version();
            for (const auto& [cls, u, v, ci] : cand) {
                if (clock_.sec() > tEnd) break;
                if (shared_.version() != version0 && shared_.bestScalar() < baseCost - 1e-6) break;   // лучший сменился
                const double tc = cuts[ci];
                auto cut = [&](int w) {
                    int i = 0;
                    while (i < base.len[w] && base.dep[w][i] - P_.svc[base.r[w][i]] < tc) i++;
                    return i;
                };
                int i = cut(u), j = cut(v);
                Routes R = base.routes();
                vector<int> nu(R[v].begin(), R[v].begin() + j), nv(R[u].begin(), R[u].begin() + i);
                nu.insert(nu.end(), R[u].begin() + i, R[u].end());
                nv.insert(nv.end(), R[v].begin() + j, R[v].end());
                R[u] = nu;
                R[v] = nv;
                lns::Solution c(P_, R);
                pool.clear();
                lns::collectAbsent(c, pool);
                rng.shuffle(pool.begin(), pool.end());
                regret.run(P_, c, pool, rng, 3, K, W_VEHICLE, nullptr);
                if (c.nUn()) lns::EjectionRepair(P_, rng, clock_).repair(c, K, tEnd, repairIters);
                if (c.nUn() || c.used() > K) continue;
                lns::KmSearch(I, P_, rng, clock_).run(c, clock_.sec() + tPolish, nullptr);
                offer(c.routes());
            }
        }
    }
};

}  // namespace dispatch::coop
