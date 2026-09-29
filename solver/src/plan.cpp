// План на день: кооперативный параллельный поиск (coop/cooperative.hpp), затем локальный поиск и страховка.
//
// Роли потоков зависят от числа ядер: ≥ 8 — ARKZARWH, 4–7 — ARKH, иначе ZR. Если есть дальние бригады и ядер ≥ 8,
// работают два кооператива со своими хранилищами: основной ARKZW на настоящей задаче и ведомый ZAK на копии, где км
// между кластерами втрое дороже. Ведомый не затягивается в яму основного, а свои решения отдаёт и основному
// (там они оцениваются по настоящим км и полируются его км-потоками).
// Использование: plan <задача> [секунды] [сид].
#include "coop/cooperative.hpp"

using namespace dispatch;

static constexpr double SEARCH_SHARE = 0.97, POLISH_SHARE = 0.99;   // доли времени на поиск и на поиск + полировку
static constexpr double GUIDE_PENALTY = 3;

static Routes solve(const Instance& I, double seconds, uint64_t seed) {
    Timer clock;
    const double T = seconds * SEARCH_SHARE;
    const unsigned cores = std::thread::hardware_concurrency();
    const bool guided = I.hasFarHomes() && cores >= 8;
    const string roles = guided ? "ARKZW" : cores >= 8 ? "ARKZARWH" : cores >= 4 ? "ARKH" : "ZR";
    coop::Cooperative main(I, T, seed, clock, roles);
    const Instance penalized = guided ? coop::clusterPenalized(I, GUIDE_PENALTY) : Instance{};
    std::unique_ptr<coop::Cooperative> follower;
    coop::ThreadPool pool;
    if (guided) {
        follower = std::make_unique<coop::Cooperative>(penalized, T, seed * 31 + 7, clock, "ZAK");
        follower->forwardTo = &main;
        follower->launch(pool);
    }
    main.launch(pool);
    pool.join();
    Routes R;
    if (follower && follower->best(R)) main.offer(R);
    main.best(R);

    const lns::Problem& P = main.problem();
    lns::Solution best(P, R);
    ls::Solution x(I, best.routes());
    ls::LocalSearch(I).run(x, best.used(), clock, seconds * POLISH_SHARE);
    lns::Solution y(P, x.r);
    if (y.better(best, P)) best = y;
    return ls::finalize(I, best.routes());
}

int main(int argc, char** argv) {
    Args a = parseArgs(argc, argv);
    Timer timer;
    Routes R = solve(a.instance, a.seconds, a.seed);
    printAnswer(a.instance, R, "plan", timer.sec());
    return 0;
}
