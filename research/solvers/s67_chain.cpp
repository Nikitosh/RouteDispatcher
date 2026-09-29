// 67. Прототип км-хода «цепочка выталкиваний» (nb_util.hpp, Chain): старт с решения из файла INIT (или s64-подобного
// построения), затем случайные цепочки от дорогих заявок (∝ выигрыш удаления²) с участком соседей того же маршрута.
#include "coop_util.hpp"
#include "nb_util.hpp"

static Routes readRoutes(const char* f, int V) {
    Routes R(V); ifstream in(f); string line;
    while (getline(in, line)) { if (line.rfind("ROUTE", 0)) continue; istringstream ss(line.substr(6)); int v, k; ss >> v; while (ss >> k) R[v].push_back(k); }
    return R;
}
Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; l2::Prob Pb(I); Rng rng(seed * 2654435761ULL + 5);
    l2::Sol s; s.fromRoutes(Pb, readRoutes(getenv("INIT"), I.V));
    bool dbg = getenv("DEBUG");
    if (s.nUn()) {   // старт с выпавшими заявками: regret-3 + NB-ремонт при том же парке
        int K = s.used(); vector<int> pl; l2::collectAbsent(s, pl); l2::Regret RG0; RG0.run(Pb, s, pl, rng, 3, K, W_VEHICLE, nullptr);
        if (s.nUn()) { nb::NB R0(Pb, rng, tm); R0.irand = 0; R0.repair(s, K, 0.2, 2000); }
        if (dbg) fprintf(stderr, "ремонт: un=%d used=%d km=%.1f\n", s.nUn(), s.used(), s.kmTot());
    }
    if (l2::P("CMODE", 0) == 5) { l2::KmSearch K5(I, Pb, rng, tm); K5.run(s, tl * 0.97); if (dbg) fprintf(stderr, "km-поиск: %.1f\n", s.kmTot()); return l2::finalizeR(I, s); }
    double km0 = s.kmTot();
    nb::NB E(Pb, rng, tm); nb::Chain C(E);
    int maxSteps = (int)l2::P("CST", 8), kmax = (int)l2::P("CK", 2), seg = (int)l2::P("CSEG", 2);
    double lamF = l2::P("CLAM", 1.0);
    double edge = km0 / max(1, Pb.N - s.nUn()); int seeds[8];
    int mode = (int)l2::P("CMODE", 0); l2::Regret RG; l2::Undo U; vector<int> pool; long bt = 0, bo = 0;
    while (mode == 1 && tm.sec() < tl * 0.97) {
        // разрушение: маршрут бригады v (дальней с вероятностью FARP) + все заявки в полосе времени у всех бригад
        int far[l2::MAXV], nf = 0, all[l2::MAXV], na = 0;
        for (int v = 0; v < Pb.V; v++) if (s.len[v]) { all[na++] = v; if (Pb.st[v] != 0) far[nf++] = v; }
        int v = (nf && rng.uni() < l2::P("FARP", 0.8)) ? far[rng.randint(nf)] : all[rng.randint(na)];
        double w = l2::P("BW0", 60) + rng.uni() * l2::P("BW1", 120), t0 = rng.uni() * (SHIFT - w);
        vector<int> rem(s.r[v], s.r[v] + s.len[v]);
        for (int u = 0; u < Pb.V; u++) if (u != v) for (int i = 0; i < s.len[u]; i++) {
            double st = s.dep[u][i] - Pb.svc[s.r[u][i]]; if (st >= t0 && st <= t0 + w) rem.push_back(s.r[u][i]); }
        double c0 = s.cost(Pb); int cap = s.used(); bt++;
        U.begin(s); pool.clear(); l2::removeSet(Pb, s, rem.data(), rem.size(), pool, &U);
        for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
        RG.run(Pb, s, pool, rng, 2 + rng.randint(2), cap, W_VEHICLE, &U, rng.uni() < 0.5 ? 0.02 * Pb.maxD : 0);
        if (s.cost(Pb) < c0 - 1e-6) { bo++; if (dbg) fprintf(stderr, "t=%.3f km=%.1f rem=%zu\n", tm.sec(), s.kmTot(), rem.size()); }
        else U.restore(Pb, s);
    }
    if (mode == 1 && dbg) fprintf(stderr, "block tries=%ld ok=%ld km %.1f -> %.1f\n", bt, bo, km0, s.kmTot());
    if (mode == 2) {
        // кластеры: ближайший старт (старты ближе 3 мин — один кластер); офис — кластер старта 0
        int S = I.S, M = I.M; vector<int> rep(S);
        for (int a = 0; a < S; a++) { rep[a] = a; for (int q = 0; q < a; q++) if (I.T[0][q * M + a] < 3 && I.T[0][a * M + q] < 3) { rep[a] = rep[q]; break; } }
        vector<int> cl(Pb.N); for (int k = 0; k < Pb.N; k++) { int b = 0; for (int a = 1; a < S; a++) if (I.T[0][a * M + I.node(k)] < I.T[0][b * M + I.node(k)]) b = a; cl[k] = rep[b]; }
        int office = rep[0]; double blocks[][2] = {{0, 180}, {0, 300}, {0, 720}, {300, 720}, {420, 720}, {120, 360}};
        double tLS = l2::P("TLS", 0.03), tLS2 = l2::P("TLS2", 0.05);
        l2::Sol base = s; double bestKm = s.kmTot();
        auto P2 = std::make_unique<l2::Prob>(Pb);
        for (int v = 0; v < Pb.V; v++) {
            if (rep[Pb.st[v]] == office) continue;
            for (auto& B : blocks) {
                // запрет плана для v
                for (int k = 0; k < Pb.N; k++) {
                    bool inB = Pb.a[k] >= B[0] - 1e-9 && Pb.b[k] <= B[1] + 1e-9, ok = cl[k] == office ? inB : !inB;
                    P2->canMask[k] = ok ? Pb.canMask[k] : (Pb.canMask[k] & ~(1 << v));
                }
                l2::Sol c; c.fromRoutes(*P2, base.routes());   // недопустимые для плана у v выпадут
                vector<int> rem;
                for (int u = 0; u < Pb.V; u++) for (int i = 0; i < c.len[u]; i++) { int k = c.r[u][i]; if (cl[k] == office && Pb.a[k] >= B[0] && Pb.b[k] <= B[1]) rem.push_back(k); }
                pool.clear(); l2::removeSet(*P2, c, rem.data(), rem.size(), pool, nullptr); l2::collectAbsent(c, pool);
                sort(pool.begin(), pool.end()); pool.erase(unique(pool.begin(), pool.end()), pool.end());
                for (int i = (int)pool.size() - 1; i > 0; i--) swap(pool[i], pool[rng.randint(i + 1)]);
                RG.run(*P2, c, pool, rng, 3, base.used(), W_VEHICLE, nullptr);
                if (c.nUn()) { nb::NB R2(*P2, rng, tm); R2.repair(c, base.used(), tm.sec() + 0.05, (long)l2::P("RIT", 2000)); }
                int un = c.nUn(); double km_r = c.kmTot();
                l2::Sol d; d.fromRoutes(Pb, c.routes()); double km_a = 0, km_b = 0;
                if (!un) { l2::Sol e = d; l2::KmSearch K0(I, Pb, rng, tm); K0.run(e, tm.sec() + l2::P("TLSA", 0.01)); km_a = e.kmTot(); }
                l2::KmSearch K2(I, *P2, rng, tm); if (!un) K2.run(c, tm.sec() + tLS);
                d.fromRoutes(Pb, c.routes()); l2::KmSearch K1(I, Pb, rng, tm); if (!un && d.used() <= base.used()) K1.run(d, tm.sec() + tLS2);
                if (dbg) fprintf(stderr, "v=%d blk=%g-%g un=%d used=%d km=%.1f  regret=%.1f ls10=%.1f\n", v, B[0], B[1], d.nUn(), d.used(), d.kmTot(), km_r, km_a);
                if (d.nUn() == 0 && d.used() <= base.used() && d.kmTot() < bestKm) { bestKm = d.kmTot(); s = d; }
            }
        }
        if (dbg) fprintf(stderr, "plan: km %.1f -> %.1f t=%.3f\n", km0, s.kmTot(), tm.sec());
    }
    if (mode == 9) {   // кандидаты обмена голов (тот же K, любой км) как старты NB-удаления маршрута
        l2::Sol base = s; int K = base.used(); long nc = 0, ns = 0; nb::NB E(Pb, rng, tm); long nbit = (long)l2::P("NBIT", 1000);
        double cuts[] = {120, 180, 240, 300, 360};
        for (int u = 0; u < Pb.V && tm.sec() < tl; u++) for (int v = u + 1; v < Pb.V; v++) {
            if (!base.len[u] || !base.len[v] || (Pb.st[u] == 0 && Pb.st[v] == 0)) continue;
            for (double tc : cuts) {
                auto cut = [&](int w) { int i = 0; while (i < base.len[w] && base.dep[w][i] - Pb.svc[base.r[w][i]] < tc) i++; return i; };
                int i = cut(u), j = cut(v); Routes R = base.routes();
                vector<int> nu(R[v].begin(), R[v].begin() + j); nu.insert(nu.end(), R[u].begin() + i, R[u].end());
                vector<int> nv(R[u].begin(), R[u].begin() + i); nv.insert(nv.end(), R[v].begin() + j, R[v].end());
                R[u] = nu; R[v] = nv; l2::Sol c; c.fromRoutes(Pb, R); pool.clear(); l2::collectAbsent(c, pool);
                RG.run(Pb, c, pool, rng, 3, K, W_VEHICLE, nullptr);
                if (c.nUn()) { nb::NB R2(Pb, rng, tm); R2.irand = 0; R2.repair(c, K, tm.sec() + 0.05, 300); }
                if (c.nUn() || c.used() > K) continue;
                nc++;
                for (int rep = 0; rep < (int)l2::P("NREP", 2); rep++) {
                    l2::Sol d = c; double sw = 0, w[l2::MAXV]; for (int x = 0; x < Pb.V; x++) { w[x] = d.len[x] ? 1.0 / d.len[x] : 0; sw += w[x]; }
                    double y = rng.uni() * sw; int vr = -1; for (int x = 0; x < Pb.V; x++) { y -= w[x]; if (w[x] > 0 && y <= 0) { vr = x; break; } }
                    if (E.eliminate(d, vr, tl, nbit)) { ns++; if (dbg) fprintf(stderr, "УСПЕХ u=%d v=%d t=%g rm=%d used=%d km=%.1f t=%.2f\n", u, v, tc, vr, d.used(), d.kmTot(), tm.sec()); if (d.used() < s.used()) s = d; }
                }
            }
        }
        if (dbg) fprintf(stderr, "hs+nb: кандидатов %ld успехов %ld used %d -> %d t=%.2f\n", nc, ns, K, s.used(), tm.sec());
    }
    if (mode == 8) {   // вынуть SEEDK, запретить их текущей бригаде, NB-ремонт по Σp, км-поиск
        int K = s.used(); auto P8 = std::make_unique<l2::Prob>(Pb); vector<int> sk;
        for (const char* q = getenv("SEEDK"); *q;) { sk.push_back(strtol(q, (char**)&q, 10)); while (*q == ',') q++; }
        for (int k : sk) { int v = s.rt[k]; if (v >= 0) P8->canMask[k] &= ~(1 << v); }
        l2::Sol c; c.fromRoutes(*P8, s.routes());
        for (int rep = 0; rep < (int)l2::P("NREP", 5); rep++) {
            l2::Sol d = c; nb::NB R8(*P8, rng, tm); R8.irand = (int)l2::P("NBIR", 0);
            double edge8 = s.kmTot() / Pb.N;
            bool ok = R8.repair(d, K, tm.sec() + 0.2, (long)l2::P("RIT", 5000), l2::P("RLAM", 0) * edge8);
            double k1 = d.kmTot(); l2::Sol e; e.fromRoutes(Pb, d.routes()); l2::KmSearch K8(I, Pb, rng, tm); if (ok) K8.run(e, tm.sec() + l2::P("TLS", 0.1));
            if (dbg) fprintf(stderr, "rep %d ok=%d un=%d km %.1f -> %.1f\n", rep, ok, d.nUn(), k1, e.kmTot());
            if (ok && e.nUn() == 0 && e.kmTot() < s.kmTot()) s = e;
        }
    }
    if (mode == 7) {   // передать маршрут v неиспользуемой бригаде u (+ремонт), затем NB-удаление одного маршрута
        l2::Sol base = s; int K = base.used(); long nt = 0, ns = 0; nb::NB E(Pb, rng, tm); long nbit = (long)l2::P("NBIT", 3000);
        vector<int> usedV, freeV; for (int v = 0; v < Pb.V; v++) (base.len[v] ? usedV : freeV).push_back(v);
        for (int u : freeV) for (int v : usedV) {
            if (tm.sec() > tl) break;
            Routes R = base.routes(); R[u] = R[v]; R[v].clear();
            l2::Sol c; c.fromRoutes(Pb, R); pool.clear(); l2::collectAbsent(c, pool);
            RG.run(Pb, c, pool, rng, 3, K, W_VEHICLE, nullptr);
            if (c.nUn()) { nb::NB R2(Pb, rng, tm); R2.irand = 0; R2.repair(c, K, tm.sec() + 0.05, 500); }
            if (c.nUn() || c.used() > K) continue;
            nt++;
            for (int rep = 0; rep < (int)l2::P("NREP", 3); rep++) {
                l2::Sol d = c; double sw = 0, w[l2::MAXV]; for (int x = 0; x < Pb.V; x++) { w[x] = d.len[x] ? 1.0 / d.len[x] : 0; sw += w[x]; }
                double y = rng.uni() * sw; int vr = -1; for (int x = 0; x < Pb.V; x++) { y -= w[x]; if (w[x] > 0 && y <= 0) { vr = x; break; } }
                if (E.eliminate(d, vr, tl, nbit)) { ns++; if (dbg) fprintf(stderr, "УСПЕХ u=%d<-v=%d rm=%d used=%d km=%.1f t=%.2f\n", u, v, vr, d.used(), d.kmTot(), tm.sec()); if (d.used() < s.used()) s = d; }
            }
        }
        if (dbg) fprintf(stderr, "transfer: переносов %ld успехов %ld used %d -> %d t=%.2f\n", nt, ns, K, s.used(), tm.sec());
    }
    if (mode == 6) {   // обмен временными блоками [t1,t2) между маршрутами u,v + ремонт + км-поиск
        l2::Sol base = s; double bestKm = s.kmTot(); int K = base.used(); double tLS = l2::P("TLS", 0.01); long n = 0;
        for (int u = 0; u < Pb.V; u++) for (int v = u + 1; v < Pb.V; v++) {
            if (!base.len[u] || !base.len[v]) continue;
            if (Pb.st[u] == 0 && Pb.st[v] == 0) continue;
            for (double t1 = 0; t1 < 720; t1 += 120) for (double w = 120; t1 + w <= 720 + 1e-9; w += 120) {
                if (t1 == 0 && w >= 720) continue;
                auto seg = [&](int x, int& a, int& b) { a = 0; while (a < base.len[x] && base.dep[x][a] - Pb.svc[base.r[x][a]] < t1) a++; b = a; while (b < base.len[x] && base.dep[x][b] - Pb.svc[base.r[x][b]] < t1 + w) b++; };
                int a1, b1, a2, b2; seg(u, a1, b1); seg(v, a2, b2); if (a1 == b1 && a2 == b2) continue;
                Routes R = base.routes();
                vector<int> nu(R[u].begin(), R[u].begin() + a1); nu.insert(nu.end(), R[v].begin() + a2, R[v].begin() + b2); nu.insert(nu.end(), R[u].begin() + b1, R[u].end());
                vector<int> nv(R[v].begin(), R[v].begin() + a2); nv.insert(nv.end(), R[u].begin() + a1, R[u].begin() + b1); nv.insert(nv.end(), R[v].begin() + b2, R[v].end());
                R[u] = nu; R[v] = nv; n++;
                l2::Sol c; c.fromRoutes(Pb, R); pool.clear(); l2::collectAbsent(c, pool);
                for (int q = (int)pool.size() - 1; q > 0; q--) swap(pool[q], pool[rng.randint(q + 1)]);
                RG.run(Pb, c, pool, rng, 3, K, W_VEHICLE, nullptr);
                if (c.nUn()) { nb::NB R2(Pb, rng, tm); R2.irand = 0; R2.repair(c, K, tm.sec() + 0.05, 300); }
                if (c.nUn() || c.used() > K) continue;
                l2::KmSearch K1(I, Pb, rng, tm); K1.run(c, tm.sec() + tLS);
                if (c.kmTot() < bestKm) { bestKm = c.kmTot(); s = c; if (dbg) fprintf(stderr, "  u=%d v=%d [%g,%g) km=%.1f\n", u, v, t1, t1 + w, c.kmTot()); }
            }
        }
        if (dbg) fprintf(stderr, "blockswap: n=%ld km %.1f -> %.1f t=%.3f\n", n, km0, s.kmTot(), tm.sec());
    }
    if (mode == 3) {   // обмен головами маршрутов u,v (разрез по времени t) + ремонт + км-поиск
        l2::Sol base = s; double bestKm = s.kmTot(); int K = base.used();
        double cuts[] = {120, 180, 240, 300, 360}; double tLS = l2::P("TLS", 0.03);
        for (int u = 0; u < Pb.V; u++) for (int v = u + 1; v < Pb.V; v++) {
            if (!base.len[u] || !base.len[v]) continue;
            if (Pb.st[u] == 0 && Pb.st[v] == 0) continue;   // хотя бы одна дальняя (старт не офис)
            for (double tc : cuts) {
                auto cut = [&](int w) { int i = 0; while (i < base.len[w] && base.dep[w][i] - Pb.svc[base.r[w][i]] < tc) i++; return i; };
                int i = cut(u), j = cut(v);
                Routes R = base.routes();
                vector<int> nu(R[v].begin(), R[v].begin() + j); nu.insert(nu.end(), R[u].begin() + i, R[u].end());
                vector<int> nv(R[u].begin(), R[u].begin() + i); nv.insert(nv.end(), R[v].begin() + j, R[v].end());
                R[u] = nu; R[v] = nv;
                l2::Sol c; c.fromRoutes(Pb, R);   // недопустимые выпадают
                pool.clear(); l2::collectAbsent(c, pool);
                for (int q = (int)pool.size() - 1; q > 0; q--) swap(pool[q], pool[rng.randint(q + 1)]);
                RG.run(Pb, c, pool, rng, 3, K, W_VEHICLE, nullptr);
                if (c.nUn()) { nb::NB R2(Pb, rng, tm); R2.irand = 0; R2.repair(c, K, tm.sec() + 0.05, 300); }
                if (c.nUn() || c.used() > K) continue;
                double km1 = c.kmTot();
                l2::KmSearch K1(I, Pb, rng, tm); K1.run(c, tm.sec() + tLS);
                if (dbg) fprintf(stderr, "u=%d v=%d t=%g drop=%zu rep=%.1f km=%.1f\n", u, v, tc, pool.size(), km1, c.kmTot());
                if (c.kmTot() < bestKm) { bestKm = c.kmTot(); s = c; }
            }
        }
        if (dbg) fprintf(stderr, "headswap: km %.1f -> %.1f t=%.3f\n", km0, s.kmTot(), tm.sec());
    }
    while (mode == 0 && tm.sec() < tl * 0.97) {
        // выбор заявки ∝ remGain^2
        double sw = 0; static double w[l2::MAXN];
        for (int k = 0; k < Pb.N; k++) { int v = s.rt[k]; w[k] = 0; if (v < 0) continue; double g = s.remGain(Pb, v, s.posOf(k)); w[k] = g * g; sw += w[k]; }
        double x = rng.uni() * sw; int k0 = 0; for (int k = 0; k < Pb.N; k++) { x -= w[k]; if (x <= 0 && w[k] > 0) { k0 = k; break; } }
        int v = s.rt[k0], i = s.posOf(k0), ns = 0; seeds[ns++] = k0;
        int lo = i, hi = i, want = rng.randint(seg + 1);
        while (ns < 1 + want) { bool l = lo > 0, h = hi + 1 < s.len[v]; if (!l && !h) break; if (l && (!h || rng.uni() < 0.5)) seeds[ns++] = s.r[v][--lo]; else seeds[ns++] = s.r[v][++hi]; }
        double lam = lamF * edge * rng.uni();
        if (getenv("SEEDK")) { ns = 0; for (const char* q = getenv("SEEDK"); *q;) { seeds[ns++] = strtol(q, (char**)&q, 10); while (*q == ',') q++; } lam = lamF * edge; }
        if (C.run(s, seeds, ns, maxSteps, lam, kmax) && dbg) fprintf(stderr, "t=%.3f km=%.1f seeds=%d\n", tm.sec(), s.kmTot(), ns);
    }
    if (dbg) fprintf(stderr, "tries=%ld ok=%ld km %.1f -> %.1f\n", C.nTry, C.nOk, km0, s.kmTot());
    return l2::finalizeR(I, s);
}
int main(int c, char** v) { return runMain(c, v, solve, "67_chain"); }
