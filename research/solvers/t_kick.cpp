// Тест: t_kick <задача> <решение> <секунд> [сид] — сфокусированные удары по решению из файла.
#include "kick_util.hpp"
int main(int argc, char** argv) {
    Instance I = loadInstance(argv[1]); ifstream in(argv[2]); string line; Routes R(I.V);
    while (getline(in, line)) { istringstream ss(line); string w; ss >> w; if (w == "ROUTE") { int v, k; ss >> v; while (ss >> k) R[v].push_back(k); } }
    double tl = atof(argv[3]); Rng rng(argc > 4 ? atoll(argv[4]) : 1); Timer tm; l2::Prob Pb(I); l2::Sol s; s.fromRoutes(Pb, R);
    int u0 = s.used(); long att = 0; int ml = 1 << 30; kick::fleetKick(I, Pb, s, rng, tm, tl, &att, 0.35, &ml);
    printf("было %d бригад -> стало %d, км %.1f, попыток %ld, минимум невставленных %d, за %.2f с\n", u0, s.used(), s.kmTot(), att, ml, tm.sec());
}
