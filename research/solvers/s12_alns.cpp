// 12. ALNS (Ropke & Pisinger 2006). Разрушение: случайное, худшее (вклад в км), Шоу (расстояние+время+навык),
// удаление маршрута, удаление временного слота. Восстановление: жадная (глобально дешёвая), regret-2, regret-3;
// шум выбирается адаптивно отдельно. Веса операторов по сегментам (σ1=33, σ2=9, σ3=13, r=0.1).
// Фаза 1 — сокращение парка по схеме Pisinger–Ropke: удаляем маршрут, заявки в «банк», ALNS при ограничении
// числа бригад. Приёмка (суррогат, ACC=1): меньше штраф невыполненных ИЛИ меньше Σabsence[c] (как в SISR) —
// заметно лучше отжига по «штраф банка + км» (ACC=0). Фаза 2 — отжиг по скаляру (км) при фиксированном парке,
// кандидаты в пределах 30% от лучшего полируются быстрым ЛП. Параметры можно переопределить переменными среды.
#include "lns_util.hpp"
#include <unordered_set>
using namespace lu;
static double P(const char* n, double d) { const char* e = getenv(n); return e ? atof(e) : d; }

struct Adaptive {
    vector<double> w, sc; vector<int> cnt;
    explicit Adaptive(int n) : w(n, 1), sc(n, 0), cnt(n, 0) {}
    int pick(Rng& rng) const {
        double s = 0; for (double x : w) s += x; double u = rng.uni() * s;
        for (int i = 0; i < (int)w.size(); i++) { u -= w[i]; if (u <= 0) return i; }
        return w.size() - 1;
    }
    void add(int i, double x) { sc[i] += x; cnt[i]++; }
    void update(double r) {
        for (int i = 0; i < (int)w.size(); i++) {
            if (cnt[i]) w[i] = w[i] * (1 - r) + r * sc[i] / cnt[i];
            w[i] = max(w[i], 0.05); sc[i] = 0; cnt[i] = 0;
        }
    }
};

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; Ctx C(I); Rng rng(seed); const int N = I.N, V = I.V;
    LocalSearch LS(I);
    // --- построение: regret-2 и жадная по ширине окна, лучшая + ЛП ---
    Sol best(I);
    {
        Sol a(I); vector<int> pool(N); iota(pool.begin(), pool.end(), 0);
        regretInsert(a, pool, rng, 2, V, W_VEHICLE);
        Sol b(I); pool.assign(N, 0); iota(pool.begin(), pool.end(), 0);
        stable_sort(pool.begin(), pool.end(), [&](int x, int y) { return I.ord[x].b - I.ord[x].a < I.ord[y].b - I.ord[y].a; });
        greedyInsert(b, pool, rng, 0, V, W_VEHICLE);
        best = a.better(b) ? a : b;
    }
    LS.run(best, rng, V, &tm, tl * 0.9);

    const double noiseAmp = P("NOISE", 0.025) * C.maxD;
    const int qmin = min(N, 4), qmax = max(qmin, min((int)(P("XI", 0.3) * N), 30));
    enum { I_GREEDY, I_REG2, I_REG3, NI };
    Adaptive AR(NR), AI(NI), AN(2);
    const double sig1 = 33, sig2 = 9, sig3 = 13, react = 0.1; const int segLen = 100;
    unordered_set<uint64_t> seen;
    auto hashSol = [&](const Sol& s) {
        uint64_t h = 1469598103934665603ULL;
        for (int v = 0; v < V; v++) { h = (h ^ (uint64_t)(1000 + v)) * 1099511628211ULL; for (int k : s.r[v]) h = (h ^ (uint64_t)k) * 1099511628211ULL; }
        return h;
    };
    vector<double> absence(N, 0);
    vector<int> pool;

    Destroyer DS(C);
    auto destroy = [&](Sol& s, int op, int q) { DS.apply(s, rng, op, q, pool); };
    auto repair = [&](Sol& s, int op, bool noise, int cap) {
        double nz = noise ? noiseAmp : 0;
        for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
        if (op == I_GREEDY) regretInsert(s, pool, rng, 1, cap, W_VEHICLE, nz);
        else regretInsert(s, pool, rng, op == I_REG2 ? 2 : 3, cap, W_VEHICLE, nz);
    };

    // --- фаза 1: сокращение парка ---
    // суррогат: Σ_{отсутствующие} penalty*(wb + absence) + км
    const double wb = P("WB", 1.0), wa = P("WA", 0.02);
    auto f1 = [&](const Sol& s) { double x = s.kmTot(); for (int k = 0; k < N; k++) if (s.rt[k] < 0) x += I.penalty(k) * (wb + wa * absence[k]); return x; };
    const double tFleet = tl * P("FLEET", 0.55), tServe = tl * 0.15;
    int cap = V; bool elim = best.nUn() == 0;
    Sol cur = best, s(I);
    auto startElim = [&] {
        cur = best; cap = best.used() - 1;
        int v = pickRouteToRemove(cur, rng, &absence);
        vector<int> r0 = cur.r[v], tmp; removeSet(cur, r0, tmp);
        seen.clear();
    };
    if (elim && best.used() > 1) startElim(); else if (elim) cap = 0;
    double km0 = max(1.0, best.kmTot());
    const double T1 = P("T1", 0.01) * km0; const int acc1 = (int)P("ACC", 1); const int onlyR = (int)P("ONLYR", -1), onlyI = (int)P("ONLYI", -1);
    long it = 0;
    double curF = f1(cur);
    while (cap > 0 && tm.sec() < tFleet) {
        if (!elim && tm.sec() > tServe) { elim = true; startElim(); curF = f1(cur); }
        it++;
        int ro = AR.pick(rng), io = AI.pick(rng), no = AN.pick(rng);
        if (onlyR >= 0) ro = onlyR; if (onlyI >= 0) io = onlyI;
        s = cur; pool.clear(); collectAbsent(s, pool);
        destroy(s, ro, qmin + rng.randint(qmax - qmin + 1));
        repair(s, io, no, cap);
        for (int k : pool) absence[k] += 1;
        double f = f1(s), score = 0;
        uint64_t h = hashSol(s); bool fresh = seen.insert(h).second;
        if (s.better(best)) {
            score = sig1; best = s; LS.run(best, rng, cap, &tm, tl * 0.9);
            if (best.nUn() == 0 || elim) {
                elim = true; if (best.used() <= 1) break;
                startElim(); curF = f1(cur);
            } else { cur = best; curF = f1(cur); }
        } else if (acc1 == 1) {
            double p = s.pen(), a = 0, pc = cur.pen(), ac = 0;
            for (int k = 0; k < N; k++) { if (s.rt[k] < 0) a += absence[k]; if (cur.rt[k] < 0) ac += absence[k]; }
            if (p < pc - 1e-9) { if (fresh) score = sig2; cur = s; curF = f; }
            else if (a < ac) { if (fresh) score = sig3; cur = s; curF = f; }
        } else if (f < curF - 1e-9) { if (fresh) score = sig2; cur = s; curF = f; }
        else if (f < curF - T1 * log(rng.uni() + 1e-300)) { if (fresh) score = sig3; cur = s; curF = f; }
        AR.add(ro, score); AI.add(io, score); AN.add(no, score);
        if (it % segLen == 0) { AR.update(react); AI.update(react); AN.update(react); }
    }

    // --- фаза 2: отжиг по км при фиксированном парке ---
    cap = best.used(); cur = best; seen.clear();
    km0 = max(1.0, best.kmTot());
    const double T0 = P("T0", 0.01) * km0, Tf = P("TF", 0.0002) * km0;
    double t1 = tm.sec(), span = max(1e-9, tl * 0.97 - t1), T = T0;
    double curCost = cur.cost(); const double lsTh = P("LSTH", 0.3);
    for (long j = 0;; j++) {
        if ((j & 7) == 0) { double el = tm.sec(); if (el > tl * 0.97) break; T = T0 * pow(Tf / T0, (el - t1) / span); }
        it++;
        int ro = AR.pick(rng), io = AI.pick(rng), no = AN.pick(rng);
        s = cur; pool.clear(); collectAbsent(s, pool);
        destroy(s, ro, qmin + rng.randint(qmax - qmin + 1));
        repair(s, io, no, cap);
        double c = s.cost(), score = 0;
        if (lsTh > 0 && c < best.cost() + lsTh * km0) { LS.run(s, rng, cap, &tm, tl * 0.97); c = s.cost(); }
        bool fresh = seen.insert(hashSol(s)).second;
        if (s.better(best)) { score = sig1; best = s; cur = s; curCost = c; }
        else if (c < curCost - 1e-9) { if (fresh) score = sig2; cur = s; curCost = c; }
        else if (c < curCost - T * log(rng.uni() + 1e-300)) { if (fresh) score = sig3; cur = s; curCost = c; }
        AR.add(ro, score); AI.add(io, score); AN.add(no, score);
        if (it % segLen == 0) { AR.update(react); AI.update(react); AN.update(react); }
    }
    if (getenv("DEBUG")) fprintf(stderr, "it=%ld fleetEnd=%.3f used=%d un=%d km=%.1f wR=%.2f %.2f %.2f %.2f %.2f wI=%.2f %.2f %.2f wN=%.2f %.2f\n", it, t1, best.used(), best.nUn(), best.kmTot(), AR.w[0], AR.w[1], AR.w[2], AR.w[3], AR.w[4], AI.w[0], AI.w[1], AI.w[2], AN.w[0], AN.w[1]);
    LS.run(best, rng, best.used(), &tm, tl * 0.99);
    return finalize(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "12_alns"); }
