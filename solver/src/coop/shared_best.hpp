// Общее лучшее решение потоков кооперативного поиска и пул потоков с большим стеком.
#pragma once
#include <atomic>
#include <mutex>
#include <pthread.h>
#include "core/problem.hpp"

namespace dispatch::coop {

// Решение под мьютексом; цель и «ключ парка» (штраф·100 + бригады) дублируются в атомиках, чтобы потоки
// могли часто сверяться без блокировки.
class SharedBest {
public:
    explicit SharedBest(const Instance& in) : I(in) {}

    // Предложить решение; true — оно допустимо и стало лучшим.
    bool offer(const Routes& R) {
        Score s = evaluate(I, R);
        if (!s.feasible) return false;
        const double sc = s.scalar();
        if (sc >= bestScalar_.load(std::memory_order_relaxed) - 1e-9) return false;
        std::lock_guard<std::mutex> lock(m_);
        if (have_ && sc >= bestScore_.scalar() - 1e-9) return false;
        best_ = R;
        bestScore_ = s;
        have_ = true;
        fleetKey_.store(s.fleetKey());
        bestScalar_.store(sc);
        version_.fetch_add(1);
        return true;
    }
    bool get(Routes& R) {
        std::lock_guard<std::mutex> lock(m_);
        if (!have_) return false;
        R = best_;
        return true;
    }
    long long fleetKey() const { return fleetKey_.load(); }
    double bestScalar() const { return bestScalar_.load(); }
    long version() const { return version_.load(); }   // растёт при каждой смене лучшего

private:
    const Instance& I;
    std::mutex m_;
    Routes best_;
    Score bestScore_;
    bool have_ = false;
    std::atomic<long long> fleetKey_{1LL << 60};
    std::atomic<double> bestScalar_{1e300};
    std::atomic<long> version_{0};
};

// Потоки со стеком 64 МБ: решения на фиксированных массивах занимают сотни килобайт.
class ThreadPool {
public:
    void spawn(std::function<void()> f) {
        auto* fn = new std::function<void()>(std::move(f));
        pthread_attr_t attr;
        pthread_attr_init(&attr);
        pthread_attr_setstacksize(&attr, STACK);
        pthread_t t;
        pthread_create(&t, &attr, trampoline, fn);
        pthread_attr_destroy(&attr);
        threads_.push_back(t);
    }
    void join() {
        for (pthread_t t : threads_) pthread_join(t, nullptr);
        threads_.clear();
    }

private:
    static constexpr size_t STACK = 64u << 20;
    vector<pthread_t> threads_;

    static void* trampoline(void* p) {
        auto* fn = static_cast<std::function<void()>*>(p);
        (*fn)();
        delete fn;
        return nullptr;
    }
};

}  // namespace dispatch::coop
