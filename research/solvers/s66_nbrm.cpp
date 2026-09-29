// 66. Прототип: сокращение парка по Нагате–Бройси (nb_util.hpp) — FleetRR до F1·T, затем удаление маршрутов
// вставкой с выталкиванием до NBK заявок до F2·T, затем км (SISR). Проверка механизма на ловушках Юго-востока.
#include "coop_util.hpp"
#include "nb_util.hpp"

Routes solve(const Instance& I, double tl, uint64_t seed) {
    Timer tm; l2::Prob Pb(I); Rng rng(seed * 2654435761ULL + 17);
    double f1 = l2::P("F1", 0.2) * tl, f2 = l2::P("F2", 0.8) * tl; long nbit = (long)l2::P("NBIT", 3000);
    bool dbg = getenv("DEBUG");
    l2::Sol b = coop2::construct(Pb, rng);
    l2::KmSearch KS(I, Pb, rng, tm); l2::FleetRR FR(I, Pb, rng, tm, KS);
    FR.run(b, f1 * 0.5, f1, f1);
    if (dbg) fprintf(stderr, "FR t=%.3f used=%d un=%d km=%.1f\n", tm.sec(), b.used(), b.nUn(), b.kmTot());
    nb::NB E(Pb, rng, tm); int att = 0;
    while (b.nUn() == 0 && tm.sec() < f2 && b.used() > 1) {
        l2::Sol s = b;
        double sw = 0, w[l2::MAXV]; for (int u = 0; u < Pb.V; u++) { w[u] = s.len[u] ? 1.0 / s.len[u] : 0; sw += w[u]; }
        double x = rng.uni() * sw; int v = -1; for (int u = 0; u < Pb.V; u++) { x -= w[u]; if (w[u] > 0 && x <= 0) { v = u; break; } }
        if (v < 0) break;
        if (getenv("NBV") && s.len[atoi(getenv("NBV"))]) v = atoi(getenv("NBV"));
        att++;
        int v2 = -1; if (getenv("NBV2") && s.len[atoi(getenv("NBV2"))]) v2 = atoi(getenv("NBV2"));
        if (E.eliminate(s, v, f2, nbit, v2)) {
            KS.polish(s, s.used(), tm.sec() + 0.02 * tl);
            if (dbg) fprintf(stderr, "NB t=%.3f att=%d used=%d km=%.1f ej=%ld\n", tm.sec(), att, s.used(), s.kmTot(), E.nEject);
            b = s;
        } else if (dbg && getenv("DEBUG2")) fprintf(stderr, "  fail v=%d len=%d minEP=%d\n", v, b.len[v], E.minEP);
    }
    if (dbg) fprintf(stderr, "NBend t=%.3f att=%d used=%d ej=%ld ins=%ld pert=%ld\n", tm.sec(), att, b.used(), E.nEject, E.nIns, E.nPert);
    KS.run(b, tl * 0.97);
    return l2::finalizeR(I, b);
}
int main(int c, char** v) { return runMain(c, v, solve, "66_nbrm"); }
