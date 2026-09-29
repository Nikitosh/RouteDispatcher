// проверка nb_util: перевставка каждой заявки (kmax=0) через insertEject против прямого перебора insDelta
#include "coop_util.hpp"
#include "nb_util.hpp"
static Routes readRoutes(const char* f, int V) {
    Routes R(V); ifstream in(f); string line;
    while (getline(in, line)) { if (line.rfind("ROUTE", 0)) continue; istringstream ss(line.substr(6)); int v, k; ss >> v; while (ss >> k) R[v].push_back(k); }
    return R;
}
int main(int argc, char** argv) {
    Instance I = loadInstance(argv[1]); l2::Prob Pb(I); Rng rng(1); Timer tm;
    l2::Sol s; s.fromRoutes(Pb, readRoutes(argv[2], I.V));
    nb::NB E(Pb, rng, tm); E.lock.assign(Pb.N, 0); E.kmMode = true; E.lam = 0; E.kmax = 0;
    int bad = 0, imp = 0;
    for (int k = 0; k < Pb.N; k++) {
        l2::Sol t = s; int v = t.rt[k], i = t.posOf(k); double g = t.remGain(Pb, v, i);
        memmove(t.r[v] + i, t.r[v] + i + 1, (t.len[v] - i - 1) * sizeof(int)); t.len[v]--; t.rt[k] = -1; t.rebuild(Pb, v);
        double best = 1e18; for (int u = 0; u < Pb.V; u++) if (Pb.can(u, k) && t.len[u]) for (int p = 0; p <= t.len[u]; p++) best = min(best, t.insDelta(Pb, u, p, k));
        double km1 = t.kmTot(); bool ok = E.insertEject(t, k); double d = ok ? t.kmTot() - km1 : 1e18;
        if (fabs(d - best) > 1e-6) { bad++; if (bad < 5) printf("k=%d direct=%.3f nb=%.3f ok=%d bestPs=%.3f\n", k, best, d, ok, E.bestPs); }
        if (best < g - 1e-6) imp++;
    }
    printf("mismatch=%d improving=%d of %d\n", bad, imp, Pb.N);
    // kmax=1: перебор (маршрут, позиция, выкинутая) через routeFeasible
    E.kmax = 1; E.lam = 5; bad = 0;
    for (int k = 0; k < Pb.N; k++) {
        l2::Sol t = s; int v = t.rt[k], i = t.posOf(k);
        memmove(t.r[v] + i, t.r[v] + i + 1, (t.len[v] - i - 1) * sizeof(int)); t.len[v]--; t.rt[k] = -1; t.rebuild(Pb, v);
        double best = 1e18;
        for (int u = 0; u < Pb.V; u++) if (Pb.can(u, k) && t.len[u]) {
            vector<int> r(t.r[u], t.r[u] + t.len[u]);
            for (int p = 0; p <= (int)r.size(); p++) for (int e = -1; e < (int)r.size(); e++) {
                vector<int> q; for (int j = 0; j <= (int)r.size(); j++) { if (j == p) q.push_back(k); if (j < (int)r.size() && j != e) q.push_back(r[j]); }
                double km; if (!routeFeasible(I, u, q, &km)) continue;
                best = min(best, km - t.km[u] + (e >= 0 ? 5 : 0));
            }
        }
        E.insertEject(t, k);
        if (fabs(E.bestPs - best) > 1e-6 && !(best > 1e17 && E.bestPs > 1e17)) { bad++; if (bad < 5) printf("k=%d brute=%.3f nb=%.3f\n", k, best, E.bestPs); }
    }
    printf("kmax1 mismatch=%d\n", bad);
}
