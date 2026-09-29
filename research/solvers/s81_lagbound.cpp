// 81. Fast Lagrangian lower bounds (agent lagcg, logs/lagcg.md). Not a solver: given an incumbent (env INIT=<.out file>
// with ROUTE lines, e.g. best/<set>/<inst>.out), runs LP column generation (own simplex, heuristic + exact ng pricing)
// and prints valid Lagrangian bounds:  fleet LB = ceil(sum pi + sum_t n_t min(0, minrc_t)) for cost 1 per route, and
// km LB at K = incumbent fleet (valid for "min km with <= K vehicles serving the incumbent's orders").
// Output: ROUTE lines of the incumbent + "LB fleet=<int> fleetL=<x> km=<x> ..." on stdout.
// Env: FLF (share of time for the fleet bound, 0.3), NCUT (3-SRC cuts per separation round, 0 = none; 30 recommended),
//      CUTF (share of the km budget before the first separation, 0.5), CUTPO (max cuts per order, 3), XEVERY (exact
//      pricing every n CG rounds; 10 recommended), NG (ng size, 8), XCAP (label cap per type, 2e5).
#include "lag_util.hpp"
int main(int argc, char** argv) {
    Instance I = loadInstance(argv[1]); double tl = argc > 2 ? atof(argv[2]) : 1.0;
    l2::Prob Pb(I); lg::Types TY(I); lg::Pool PL(Pb, TY); lg::HPricer HP(Pb, TY, (int)l2::P("NB", 25)); lg::XPricer XP(Pb, TY, (int)l2::P("NG", 8));
    Routes R(I.V);
    { ifstream in(getenv("INIT")); string line; while (getline(in, line)) { istringstream ss(line); string w; ss >> w; if (w != "ROUTE") continue; int v, k; ss >> v; while (ss >> k) R[v].push_back(k); } }
    l2::Sol best; best.fromRoutes(Pb, R); PL.addSol(best);
    Timer tm; double tF = tl * l2::P("FLF", 0.3);
    int fleetLB = -1; double fleetL = -1e18; bool fconv = false;
    if (tF > 0) {
        lg::CGRun G(Pb, TY, PL, HP); G.beta = 0; G.cfix = 1; G.X = &XP;
        vector<char> rm(Pb.N); for (int k = 0; k < Pb.N; k++) rm[k] = best.rt[k] >= 0;
        G.setup(rm, TY.cnt, Pb.V, best.used());
        vector<int> bs(G.m); for (int i = 0; i < G.m; i++) bs[i] = i;
        for (int v = 0; v < Pb.V; v++) if (best.len[v]) { int c = PL.add(TY.tOf[v], best.r[v], best.len[v], best.km[v]); bs[G.rowIdx[best.r[v][0]]] = G.toLP(c); }
        G.stopL = best.used() - 1 + 1e-6; G.lp.setBasis(bs); G.cgLoop(tF, tm);
        fleetL = G.bestL; fconv = G.converged; fleetLB = fleetL > -1e17 ? (int)ceil(fleetL - 1e-6) : -1;
        fprintf(stderr, "fleet: LP=%.4f L=%.4f conv=%d rounds=%d x=%d t=%.3f\n", G.lp.obj(), fleetL, (int)fconv, G.rounds, G.xrounds, tm.sec());
    }
    double tK0 = tm.sec();
    lg::CGRun G(Pb, TY, PL, HP); G.X = &XP;
    int NCUT = (int)l2::P("NCUT", 0), ncuts = 0, crounds = 0;
    if (!NCUT) G.run(best, tl, tm);
    else {   // CG, then rounds of 3-SRC separation + re-optimisation (CG continues with cut-aware exact pricing)
        G.run(best, tK0 + (tl - tK0) * l2::P("CUTF", 0.5), tm);
        while (tm.sec() < tl) {
            int a = G.separate(NCUT, (int)l2::P("CUTPO", 3)); if (!a) break;
            ncuts += a; crounds++; G.cgLoop(tm.sec() + (tl - tm.sec()) * (crounds < 4 ? 0.5 : 1.0), tm);
            if (getenv("DEBUG")) fprintf(stderr, "  cuts %d (+%d): LP=%.4f L=%.4f t=%.3f rounds=%d x=%d piv=%lld conv=%d lpcols=%d\n", ncuts, a, G.lp.obj(), G.bestL, tm.sec(), G.rounds, G.xrounds, G.lp.pivots, (int)G.converged, G.lp.ncols());
        }
    }
    fprintf(stderr, "km: LP=%.4f L=%.4f conv=%d rounds=%d x=%d t=%.3f\n", G.lp.obj(), G.bestL, (int)G.converged, G.rounds, G.xrounds, tm.sec() - tK0);
    for (int v = 0; v < I.V; v++) { printf("ROUTE %d", v); for (int i = 0; i < best.len[v]; i++) printf(" %d", best.r[v][i]); printf("\n"); }
    printf("LB fleet=%d fleetL=%.4f fconv=%d K=%d km=%.4f kconv=%d LP=%.4f UB=%.4f cuts=%d t=%.3f\n", fleetLB, fleetL, (int)fconv, best.used(), G.bestL, (int)G.converged, G.lp.obj(), best.kmTot(), ncuts, tm.sec());
}
