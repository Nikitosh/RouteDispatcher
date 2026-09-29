// Тест: полировка с полным перебором обменов участков между двумя маршрутами (головы, хвосты, средние участки любой
// длины) и переноса участка любой длины. Лучшее улучшение до сходимости. t_xpolish <задача> <решение>
#include "common.hpp"
static double cost(const Instance& I, int v, const vector<int>& r) { double km; if (!routeFeasible(I, v, r, &km)) return 1e18; return km + (r.empty() ? 0 : W_VEHICLE); }
int main(int argc, char** argv) {
    Instance I = loadInstance(argv[1]); ifstream in(argv[2]); string line; Routes R(I.V);
    while (getline(in, line)) { istringstream ss(line); string w; ss >> w; if (w == "ROUTE") { int v, k; ss >> v; while (ss >> k) R[v].push_back(k); } }
    Timer tm; Score s0 = evaluate(I, R); long evals = 0; int it = 0;
    vector<double> c(I.V); for (int v = 0; v < I.V; v++) c[v] = cost(I, v, R[v]);
    while (true) {
        double bestD = -1e-7; Routes bestR; int ba = -1, bb = -1;
        for (int a = 0; a < I.V; a++) for (int b = 0; b < I.V; b++) {
            if (a == b) continue;
            const auto &A = R[a], &B = R[b]; int la = A.size(), lb = B.size();
            // обмен участков A[i1,i2) <-> B[j1,j2) (включая пустые: это перенос)
            for (int i1 = 0; i1 <= la; i1++) for (int i2 = i1; i2 <= la; i2++)
            for (int j1 = 0; j1 <= lb; j1++) for (int j2 = j1; j2 <= lb; j2++) {
                if (i1 == i2 && j1 == j2) continue;
                if (a > b && i1 != i2 && j1 != j2) continue;   // симметричные обмены один раз
                vector<int> nA(A.begin(), A.begin() + i1); nA.insert(nA.end(), B.begin() + j1, B.begin() + j2); nA.insert(nA.end(), A.begin() + i2, A.end());
                vector<int> nB(B.begin(), B.begin() + j1); nB.insert(nB.end(), A.begin() + i1, A.begin() + i2); nB.insert(nB.end(), B.begin() + j2, B.end());
                evals++;
                double d = cost(I, a, nA) + cost(I, b, nB) - c[a] - c[b];
                if (d < bestD) { bestD = d; ba = a; bb = b; bestR = {nA, nB}; }
            }
        }
        if (ba < 0) break;
        R[ba] = bestR[0]; R[bb] = bestR[1]; c[ba] = cost(I, ba, R[ba]); c[bb] = cost(I, bb, R[bb]); it++;
    }
    Score s1 = evaluate(I, R);
    printf("было %d бр %.1f км -> стало %d бр %.1f км; шагов %d, оценок %ld, %.2f с\n", s0.used, s0.km, s1.used, s1.km, it, evals, tm.sec());
    printf("SOLVER xpolish\n"); for (int v = 0; v < I.V; v++) { printf("ROUTE %d", v); for (int k : R[v]) printf(" %d", k); printf("\n"); }
}
