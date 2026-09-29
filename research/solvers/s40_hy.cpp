// 40. Гибрид (матэвристика): старт из готовых решений (INIT=файл1,файл2,... — строки ROUTE) или собственная фаза
// сокращения парка (как в SISR), затем чередование:
//   * точная переоптимизация пар маршрутов: все допустимые маршруты обеих бригад по объединению их заявок
//     перечисляются динамикой по меткам (маска, последняя, время, км) с доминированием, затем лучшая пара
//     масок-дополнений (в т.ч. пустой маршрут — так исчезает бригада);
//   * отжиг SISR по км при фиксированном парке.
// Параметры окружения: INIT, CAP (лимит меток ДП, 1.5e6), FLEET (доля времени на парк без INIT, 0.5), DPFRAC (0.3).
#include "hy_util.hpp"
#include <unordered_map>
using namespace hy;
static double P(const char* n, double d) { const char* e = getenv(n); return e ? atof(e) : d; }

static Routes loadSol(const Instance& I, const string& path) {
    ifstream in(path); Routes R(I.V); string line;
    while (getline(in, line)) {
        istringstream ss(line); string w; ss >> w; if (w != "ROUTE") continue;
        int v; ss >> v; int k; while (ss >> k) if (v >= 0 && v < I.V && k >= 0 && k < I.N) R[v].push_back(k);
    }
    return R;
}

// ---------- ДП-перечисление маршрутов бригады по подмножеству заявок ----------
// Отсечение по «мёртвым» заявкам: заявка j вне маски, до которой бригада уже не успевает (t + T > b_j), может
// достаться только партнёру(ам); мёртвое множество обязано быть допустимым маршрутом для партнёра (для пары) —
// проверка точной ДП по минимальному времени с запоминанием.
struct Lab { uint32_t mask, dead; int last; int par; double t, km; };
struct Enum {
    vector<Lab> L;
    unordered_map<uint32_t, int> best;   // маска -> индекс метки с минимальным км
    bool ok = true;
};
static long long g_cap = 1500000;
struct FeasMemo {
    const Instance* I; int w; const vector<int>* O; unordered_map<uint32_t, char> memo;
    unordered_map<uint64_t, double> E;   // (маска, последняя) -> раннее окончание
    vector<uint32_t> compat;             // compat[i] — с кем i совместима в каком-либо порядке
    bool init = false;
    double endAt(uint32_t m, int j) {
        uint64_t key = (uint64_t)m << 5 | j;
        auto it = E.find(key); if (it != E.end()) return it->second;
        const Order& o = I->ord[(*O)[j]]; int nj = I->node((*O)[j]);
        double best = 1e18; uint32_t r = m & ~(1u << j);
        if (!r) {
            double b = max(I->t(w, I->veh[w].start, nj), o.a);
            if (b <= o.b + TOL && b + o.svc <= SHIFT + TOL) best = b + o.svc;
        } else {
            for (uint32_t x = r; x; x &= x - 1) {
                int i = __builtin_ctz(x); double t = endAt(r, i); if (t >= 1e17) continue;
                double b = max(t + I->t(w, I->node((*O)[i]), nj), o.a);
                if (b <= o.b + TOL && b + o.svc <= SHIFT + TOL) best = min(best, b + o.svc);
            }
        }
        E[key] = best; return best;
    }
    bool feas(uint32_t m) {
        if (m == 0) return true;
        int n = O->size();
        if (!init) {
            init = true; compat.assign(n, 0);
            for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) if (i != j) {
                uint32_t pm = (1u << i) | (1u << j);
                if (endAt(pm, i) < 1e17 || endAt(pm, j) < 1e17) compat[i] |= 1u << j;
            }
        }
        auto it = memo.find(m); if (it != memo.end()) return it->second;
        bool res = true;
        for (uint32_t x = m; x && res; x &= x - 1) { int i = __builtin_ctz(x); if (!I->can(w, (*O)[i])) res = false; else if ((m & ~(1u << i)) & ~compat[i]) res = false; }
        if (res) {   // жадные порядки (по началу окна, по концу, по середине); точная ДП только для малых
            res = false; int q = __builtin_popcount(m);
            if (q <= 6) { for (uint32_t x = m; x; x &= x - 1) if (endAt(m, __builtin_ctz(x)) < 1e17) { res = true; break; } }
            else {
                vector<int> s; for (uint32_t x = m; x; x &= x - 1) s.push_back(__builtin_ctz(x));
                for (int mode = 0; mode < 3 && !res; mode++) {
                    auto key = [&](int x) { const Order& o = I->ord[(*O)[x]]; return mode == 0 ? o.a * 1000 + o.b : mode == 1 ? o.b * 1000 + o.a : o.a + o.b; };
                    sort(s.begin(), s.end(), [&](int x, int y) { return key(x) < key(y); });
                    vector<int> r; for (int j : s) r.push_back((*O)[j]);
                    res = routeFeasible(*I, w, r);
                }
            }
        }
        memo[m] = res; return res;
    }
};
static void enumerate(const Instance& I, int w, const vector<int>& O, Enum& E, FeasMemo* partner) {
    int n = O.size(); E.L.clear(); E.best.clear(); E.ok = true;
    vector<char> canw(n); for (int j = 0; j < n; j++) canw[j] = I.can(w, O[j]);
    uint32_t dead0 = 0; for (int j = 0; j < n; j++) if (!canw[j]) dead0 |= 1u << j;
    if (partner && !partner->feas(dead0)) return;
    E.L.push_back({0u, dead0, -1, -1, 0.0, 0.0}); E.best[0] = 0;
    vector<int> cur = {0}, nxt;
    unordered_map<uint64_t, vector<int>> bucket;
    while (!cur.empty()) {
        nxt.clear(); bucket.clear();
        for (int li : cur) {
            Lab lb = E.L[li];
            int prevNode = lb.last < 0 ? I.veh[w].start : I.node(O[lb.last]);
            for (int j = 0; j < n; j++) {
                if (!canw[j] || ((lb.mask | lb.dead) >> j & 1)) continue;
                int k = O[j]; const Order& o = I.ord[k]; int nd = I.node(k);
                double beg = max(lb.t + I.t(w, prevNode, nd), o.a);
                if (beg > o.b + TOL) continue;
                double e = beg + o.svc; if (e > SHIFT + TOL) continue;
                double km = lb.km + I.d(w, prevNode, nd);
                uint32_t m = lb.mask | (1u << j);
                // новое мёртвое множество
                uint32_t dead = lb.dead;
                for (int q = 0; q < n; q++) if (!((m | dead) >> q & 1)) {
                    const Order& oq = I.ord[O[q]];
                    double bq = max(e + I.t(w, nd, I.node(O[q])), oq.a);
                    if (bq > oq.b + TOL || bq + oq.svc > SHIFT + TOL) dead |= 1u << q;
                }
                if (partner && dead != lb.dead && !partner->feas(dead)) continue;
                uint64_t key = (uint64_t)m * 64 + j;
                auto& vec = bucket[key]; bool dom = false;
                for (int q : vec) { const Lab& x = E.L[q]; if (x.t <= e + 1e-9 && x.km <= km + 1e-9) { dom = true; break; } }
                if (dom) continue;
                int id = E.L.size();
                for (size_t a = 0; a < vec.size();) {
                    Lab& x = E.L[vec[a]];
                    if (e <= x.t + 1e-9 && km <= x.km + 1e-9) { x.par = -2; vec[a] = vec.back(); vec.pop_back(); } else a++;
                }
                E.L.push_back({m, dead, j, li, e, km}); vec.push_back(id); nxt.push_back(id);
                if ((long long)E.L.size() > g_cap) { E.ok = false; return; }
            }
        }
        cur.clear();
        for (int id : nxt) if (E.L[id].par != -2) {
            cur.push_back(id);
            auto it = E.best.find(E.L[id].mask);
            if (it == E.best.end() || E.L[it->second].km > E.L[id].km) E.best[E.L[id].mask] = id;
        }
    }
}
static vector<int> recon(const Enum& E, const vector<int>& O, int id) {
    vector<int> r; while (id > 0) { r.push_back(O[E.L[id].last]); id = E.L[id].par; }
    reverse(r.begin(), r.end()); return r;
}
static FILE* g_colOut = nullptr; static double g_slack = 5;
static set<pair<int, vector<int>>> g_cols;
static vector<int> g_typeRep;   // для каждой бригады — представитель её типа (старт, режим, навыки)
static void emitCol(const Instance& I, const vector<int>& r) {
    set<int> done;
    for (int v = 0; v < I.V; v++) {
        int t = g_typeRep[v]; if (done.count(t)) continue; done.insert(t);
        if (!routeFeasible(I, t, r)) continue;
        if (g_cols.insert({t, r}).second) {
            double km = routeKm(I, t, r);
            fprintf(g_colOut, "COL %d %.4f", t, km); for (int k : r) fprintf(g_colOut, " %d", k); fprintf(g_colOut, "\n");
        }
    }
}
static long long g_lab = 0, dpCalls = 0, dpOk = 0, dpImp = 0;
// точная переоптимизация маршрутов бригад a и b (возвращает true при улучшении)
static bool pairOpt(const Instance& I, Sol& s, int a, int b, Enum& Ea, Enum& Eb) {
    vector<int> O; for (int k : s.r[a]) O.push_back(k); for (int k : s.r[b]) O.push_back(k);
    int n = O.size(); if (n == 0 || n > 31) return false;
    dpCalls++;
    FeasMemo fa{&I, a, &O}, fb{&I, b, &O};
    enumerate(I, a, O, Ea, &fb); if (!Ea.ok) { if (getenv("DEBUG2")) fprintf(stderr, "capA n=%d\n", n); return false; }
    enumerate(I, b, O, Eb, &fa); if (!Eb.ok) return false;
    g_lab += Ea.L.size() + Eb.L.size();
    if (getenv("DEBUG2")) fprintf(stderr, "pair %d %d n=%d la=%zu lb=%zu ma=%zu mb=%zu memo=%zu,%zu E=%zu,%zu\n", a, b, n, Ea.L.size(), Eb.L.size(), Ea.best.size(), Eb.best.size(), fa.memo.size(), fb.memo.size(), fa.E.size(), fb.E.size());
    dpOk++;
    uint32_t full = n == 32 ? 0xffffffffu : ((1u << n) - 1);
    if (g_colOut) {   // все разбиения в пределах SLACK км от текущего — в пул столбцов
        double cur0 = s.km[a] + s.km[b] + (s.r[a].empty() ? 0 : W_VEHICLE) + (s.r[b].empty() ? 0 : W_VEHICLE);
        for (auto& [m, id] : Ea.best) {
            auto it = Eb.best.find(full ^ m); if (it == Eb.best.end()) continue;
            double c = Ea.L[id].km + (m ? W_VEHICLE : 0) + Eb.L[it->second].km + ((full ^ m) ? W_VEHICLE : 0);
            if (c > cur0 + g_slack) continue;
            if (m) emitCol(I, recon(Ea, O, id));
            if (full ^ m) emitCol(I, recon(Eb, O, it->second));
        }
    }
    double curC = s.km[a] + s.km[b] + (s.r[a].empty() ? 0 : W_VEHICLE) + (s.r[b].empty() ? 0 : W_VEHICLE);
    double bestC = curC - 1e-6; int ba = -1, bb = -1;
    for (auto& [m, id] : Ea.best) {
        auto it = Eb.best.find(full ^ m); if (it == Eb.best.end()) continue;
        double c = Ea.L[id].km + (m ? W_VEHICLE : 0) + Eb.L[it->second].km + ((full ^ m) ? W_VEHICLE : 0);
        if (c < bestC) { bestC = c; ba = id; bb = it->second; }
    }
    if (getenv("DEBUG2")) {
        double mn = 1e18; for (auto& [m, id] : Ea.best) { auto it = Eb.best.find(full ^ m); if (it == Eb.best.end()) continue;
            mn = min(mn, Ea.L[id].km + (m ? W_VEHICLE : 0) + Eb.L[it->second].km + ((full ^ m) ? W_VEHICLE : 0)); }
        fprintf(stderr, "   cur=%.3f dpmin=%.3f\n", curC, mn);
    }
    if (ba < 0) return false;
    s.r[a] = recon(Ea, O, ba); s.r[b] = recon(Eb, O, bb); s.rebuild(a); s.rebuild(b);
    for (int k : s.r[a]) s.rt[k] = a; for (int k : s.r[b]) s.rt[k] = b;
    dpImp++;
    return true;
}
// Сокращение парка: заявки маршрутов a, b, c (n<=31) пытаемся уложить в две из трёх бригад (точная ДП с отсечением).
static long long elimCalls = 0, elimOk = 0;
static bool elimOpt(const Instance& I, Sol& s, int a, int b, int c, Enum& Ea, Enum& Eb) {
    vector<int> O; for (int v : {a, b, c}) for (int k : s.r[v]) O.push_back(k);
    int n = O.size(); if (n > 31) return false;
    uint32_t full = (1u << n) - 1; elimCalls++;
    int tri[3] = {a, b, c}; double bestC = 1e18; vector<int> ra, rb; int px = -1, py = -1;
    for (int z = 0; z < 3; z++) {
        int x = tri[(z + 1) % 3], y = tri[(z + 2) % 3];
        FeasMemo fx{&I, x, &O}, fy{&I, y, &O};
        enumerate(I, x, O, Ea, &fy); if (!Ea.ok || Ea.best.empty()) continue;
        enumerate(I, y, O, Eb, &fx); if (!Eb.ok || Eb.best.empty()) continue;
        for (auto& [m, id] : Ea.best) {
            auto it = Eb.best.find(full ^ m); if (it == Eb.best.end()) continue;
            double cc = Ea.L[id].km + Eb.L[it->second].km;
            if (cc < bestC) { bestC = cc; ra = recon(Ea, O, id); rb = recon(Eb, O, it->second); px = x; py = y; }
        }
    }
    if (px < 0) return false;
    for (int v : {a, b, c}) { s.r[v].clear(); }
    s.r[px] = ra; s.r[py] = rb;
    for (int v : {a, b, c}) s.rebuild(v);
    for (int k : ra) s.rt[k] = px; for (int k : rb) s.rt[k] = py;
    elimOk++; return true;
}
// тройки: каждый маршрут (от коротких) + пары ближайших к нему маршрутов
static bool elimSweep(const Instance& I, const Ctx& C, Sol& s, const Timer& tm, double tEnd) {
    Enum Ea, Eb; bool any = false, imp = true;
    while (imp && tm.sec() < tEnd) {
        imp = false;
        vector<int> rs; for (int v = 0; v < I.V; v++) if (!s.r[v].empty()) rs.push_back(v);
        sort(rs.begin(), rs.end(), [&](int x, int y) { return s.r[x].size() < s.r[y].size(); });
        for (int c : rs) {
            if (tm.sec() > tEnd || imp) break;
            vector<pair<double, int>> nb;
            for (int v : rs) if (v != c) { double ds = 0; for (int x : s.r[c]) for (int y : s.r[v]) ds += C.g(x, y); nb.push_back({ds / (s.r[c].size() * s.r[v].size()), v}); }
            sort(nb.begin(), nb.end());
            int L = min((int)nb.size(), 5);
            for (int i = 0; i < L && !imp; i++) for (int j = i + 1; j < L && !imp; j++) {
                if (tm.sec() > tEnd) break;
                int a = nb[i].second, b = nb[j].second;
                if ((int)(s.r[a].size() + s.r[b].size() + s.r[c].size()) > 31) continue;
                if (elimOpt(I, s, a, b, c, Ea, Eb)) { imp = true; any = true; }
            }
        }
    }
    return any;
}
// проход по всем парам (ближние первыми); пары «маршрут + пустая бригада» тоже (перенос/раздел не выгоден, но
// ДП находит лучший порядок для другой бригады)
static bool dpSweep(const Instance& I, const Ctx& C, Sol& s, const Timer& tm, double tEnd, Rng& rng) {
    bool any = false, imp = true;
    if (getenv("NODP")) return false;
    Enum Ea, Eb;
    while (imp && tm.sec() < tEnd) {
        imp = false;
        vector<pair<double, pair<int, int>>> pr;
        for (int a = 0; a < I.V; a++) for (int b = a + 1; b < I.V; b++) {
            if (s.r[a].empty() && s.r[b].empty()) continue;
            if (s.r[a].empty() || s.r[b].empty()) continue;
            double dsum = 0; int c = 0;
            for (int x : s.r[a]) for (int y : s.r[b]) { dsum += C.g(x, y); c++; }
            pr.push_back({dsum / max(1, c) + 1e-6 * rng.uni(), {a, b}});
        }
        sort(pr.begin(), pr.end());
        for (auto& [d, ab] : pr) {
            if (tm.sec() > tEnd) break;
            if (s.r[ab.first].empty() || s.r[ab.second].empty()) continue;
            if (pairOpt(I, s, ab.first, ab.second, Ea, Eb)) { imp = true; any = true; }
        }
    }
    return any;
}

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; Ctx C(I); Rng rng(seed);
    g_cap = (long long)P("CAP", 1.5e6);
    SisrRuin ruin; ruin.cbar = P("CBAR", 15); double blink = P("BLINK", 0.01);
    LocalSearch LS(I);
    Sol best(I);
    const char* init = getenv("INIT");
    bool haveInit = false;
    if (init && *init) {
        string all = init; size_t p = 0;
        while (p <= all.size()) {
            size_t q = all.find(',', p); if (q == string::npos) q = all.size();
            string f = all.substr(p, q - p); p = q + 1;
            if (f.empty()) continue;
            Routes R = loadSol(I, f); Sol s(I); s.r = R; s.r.resize(I.V);
            for (int v = 0; v < I.V; v++) for (int k : s.r[v]) s.rt[k] = v;
            s.rebuildAll();
            if (!haveInit || s.better(best)) { best = s; haveInit = true; }
        }
    }
    g_typeRep.assign(I.V, 0);
    for (int v = 0; v < I.V; v++) { g_typeRep[v] = v; for (int u = 0; u < v; u++) if (I.veh[u].start == I.veh[v].start && I.veh[u].mode == I.veh[v].mode && I.veh[u].mask == I.veh[v].mask) { g_typeRep[v] = g_typeRep[u]; break; } }
    if (getenv("COLS")) {   // режим генерации столбцов: для каждого решения из INIT все пары, разбиения в пределах SLACK
        g_colOut = fopen(getenv("COLS"), "w"); g_slack = P("SLACK", 5);
        string all = init ? init : ""; size_t p = 0; Enum Ea, Eb;
        while (p <= all.size() && tm.sec() < tl) {
            size_t q = all.find(',', p); if (q == string::npos) q = all.size();
            string f = all.substr(p, q - p); p = q + 1; if (f.empty()) continue;
            Sol s(I); s.r = loadSol(I, f); s.r.resize(I.V);
            for (int v = 0; v < I.V; v++) for (int k : s.r[v]) s.rt[k] = v;
            s.rebuildAll();
            for (int v = 0; v < I.V; v++) if (!s.r[v].empty()) {
                emitCol(I, s.r[v]);
                if (getenv("MUT")) {   // мутации: без одной заявки, с лучшей вставкой одной чужой заявки
                    const auto& r = s.r[v];
                    for (size_t i = 0; i < r.size() && r.size() > 1; i++) { vector<int> q = r; q.erase(q.begin() + i); emitCol(I, q); }
                    for (int k = 0; k < I.N; k++) {
                        if (find(r.begin(), r.end(), k) != r.end() || !I.can(v, k)) continue;
                        double bk = 1e18; vector<int> bq;
                        for (size_t pp = 0; pp <= r.size(); pp++) { vector<int> q = r; q.insert(q.begin() + pp, k); double km; if (routeFeasible(I, v, q, &km) && km < bk) { bk = km; bq = q; } }
                        if (!bq.empty()) emitCol(I, bq);
                    }
                }
            }
            Sol s0 = s;
            for (int a = 0; a < I.V && tm.sec() < tl; a++) for (int b = a + 1; b < I.V; b++) {
                if (s0.r[a].empty() || s0.r[b].empty()) continue;
                Sol t = s0; pairOpt(I, t, a, b, Ea, Eb);
                if (t.better(best)) best = t;
            }
        }
        fclose(g_colOut); g_colOut = nullptr;
        if (getenv("DEBUG")) fprintf(stderr, "cols=%zu calls=%lld ok=%lld imp=%lld\n", g_cols.size(), dpCalls, dpOk, dpImp);
        return finalize(I, best);
    }
    vector<int> pool; pool.reserve(I.N);
    // с INIT фаза парка идёт, только если INITFLEET>0 (доля времени), от лучшего из INIT
    double initFleet = P("INITFLEET", 0);
    if (!haveInit || initFleet > 0) {
        if (!haveInit) {
            Sol a(I); vector<int> pl(I.N); iota(pl.begin(), pl.end(), 0);
            regretInsert(a, pl, rng, 2, I.V, W_VEHICLE);
            best = a;
        }
        LS.run(best, rng, I.V, &tm, tl * 0.9);
        // фаза сокращения парка (как в s13)
        vector<double> absence(I.N, 0);
        auto sumAbs = [&](const Sol& s) { double x = 0; for (int k = 0; k < I.N; k++) if (s.rt[k] < 0) x += absence[k]; return x; };
        Sol cur = best, s(I);
        const double tFleet = tl * (haveInit ? initFleet : P("FLEET", 0.5));
        int cap = I.V; bool elim = best.nUn() == 0;
        auto startElim = [&] { cur = best; cap = best.used() - 1; int v = pickRouteToRemove(cur, rng, &absence); vector<int> rem = cur.r[v], tmp; removeSet(cur, rem, tmp); };
        if (elim && best.used() > 1) startElim(); else if (elim) cap = 0;
        double curPen = cur.pen(), curAbs = sumAbs(cur);
        while (cap > 0 && tm.sec() < tFleet) {
            if (!elim && tm.sec() > tFleet * 0.3) { elim = true; startElim(); curPen = cur.pen(); curAbs = sumAbs(cur); }
            s = cur; pool.clear(); collectAbsent(s, pool);
            ruin.apply(s, C, rng, pool); sisrSort(pool, C, rng);
            greedyInsert(s, pool, rng, blink, cap, W_VEHICLE);
            for (int k : pool) absence[k] += 1;
            double p = s.pen(), a2 = sumAbs(s);
            if (p < curPen - 1e-9 || a2 < curAbs) { cur = s; curPen = p; curAbs = a2; }
            if (s.better(best)) {
                best = s; LS.run(best, rng, cap, &tm, tl * 0.9);
                if (best.nUn() == 0 || elim) { elim = true; if (best.used() <= 1) break; startElim(); curPen = cur.pen(); curAbs = sumAbs(cur); }
                else { cur = best; curPen = cur.pen(); curAbs = sumAbs(cur); }
            }
        }
    }
    LS.run(best, rng, best.used(), &tm, tl * 0.95);
    if (P("ELIM", 1) > 0) { double te = tm.sec() + (tl - tm.sec()) * P("ELIMFRAC", 0.3); if (elimSweep(I, C, best, tm, te)) LS.run(best, rng, best.used(), &tm, tl * 0.95); }
    double t0 = tm.sec();
    dpSweep(I, C, best, tm, t0 + (tl - t0) * P("DPFRAC", 0.3), rng);
    LS.run(best, rng, best.used(), &tm, tl * 0.95);

    // отжиг SISR по км; при каждом новом рекорде — короткий ДП-проход по рекорду в конце блока
    int cap = best.used(); Sol cur = best, s(I); ruin.cbar = P("CBAR2", 10);
    double km0 = max(1.0, best.kmTot());
    const double T0 = P("T0", 0.03) * km0, Tf = P("TF", 0.003) * km0;
    double t1 = tm.sec(), tEndSA = tl * 0.97, span = max(1e-9, tEndSA - t1);
    double curCost = cur.cost(); long it = 0; double T = T0; bool newBest = false; double lastDp = t1;
    while (true) {
        if ((it & 15) == 0) {
            double el = tm.sec(); if (el > tEndSA) break;
            T = T0 * pow(Tf / T0, (el - t1) / span);
            if (newBest && el - lastDp > max(0.2, tl * 0.1)) {
                LS.run(best, rng, cap, &tm, tEndSA);
                if (dpSweep(I, C, best, tm, tEndSA, rng)) { cur = best; curCost = cur.cost(); }
                newBest = false; lastDp = tm.sec();
            }
        }
        it++;
        s = cur; pool.clear(); collectAbsent(s, pool);
        ruin.apply(s, C, rng, pool); sisrSort(pool, C, rng);
        greedyInsert(s, pool, rng, blink, cap, W_VEHICLE);
        double c = s.cost();
        if (c < curCost - T * log(rng.uni() + 1e-300)) { cur = s; curCost = c; }
        if (s.better(best)) { best = s; newBest = true; }
    }
    LS.run(best, rng, best.used(), &tm, tl * 0.99);
    if (newBest) dpSweep(I, C, best, tm, tl * 0.995, rng);
    if (getenv("DEBUG")) fprintf(stderr, "elim %lld/%lld lab=%lld it=%ld dp calls=%lld ok=%lld imp=%lld used=%d km=%.2f\n", elimOk, elimCalls, g_lab, it, dpCalls, dpOk, dpImp, best.used(), best.kmTot());
    return finalize(I, best);
}
int main(int c, char** v) { return runMain(c, v, solve, "40_hy"); }
