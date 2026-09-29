// Column-generation pricing server (ESPPRC / ng-route labeling) for the open heterogeneous VRPTW.
// Usage: cg_price <instance> [ngsize=10]
// Protocol (stdin, whitespace separated):
//   PRICE <level> <maxcols> <beta>  then T numbers alpha_t, then N numbers pi_k
//      level 1 = heuristic (dominance ignores ng-memory), 2 = exact ng-route labeling
//      reduced cost of route r of type t = alpha_t + beta*km(r) - sum_{k in r} pi_k
//      reply: "COLS n complete" then line "MINRC m_1..m_T" (valid lower bound on min rc per type if exact
//             & complete), then n lines "t km rc len k1 ... klen"
//   ENUM <gap> <beta> <maxroutes> <file> then alpha_t, pi_k : all ELEMENTARY routes with rc <= gap,
//      deduplicated by (type, order set) keeping min km. reply "ENUM n complete"; routes written to file
//      as lines "t km rc len k1 ... klen".
//   QUIT
#include "common.hpp"
#include <unordered_map>

static const int W = 3;  // 192 bits
struct Bits {
    uint64_t w[W];
    void clear() { memset(w, 0, sizeof w); }
    bool has(int k) const { return (w[k >> 6] >> (k & 63)) & 1; }
    void set(int k) { w[k >> 6] |= 1ULL << (k & 63); }
    bool subsetOf(const Bits& o) const { for (int i = 0; i < W; i++) if (w[i] & ~o.w[i]) return false; return true; }
    bool operator==(const Bits& o) const { for (int i = 0; i < W; i++) if (w[i] != o.w[i]) return false; return true; }
};
struct BitsHash { size_t operator()(const Bits& b) const { uint64_t h = 1469598103934665603ULL; for (int i = 0; i < W; i++) { h ^= b.w[i]; h *= 1099511628211ULL; h ^= h >> 29; } return h; } };

static const int CW = 8;  // up to 512 cuts
struct CBits {
    uint64_t w[CW];
    void clear() { memset(w, 0, sizeof w); }
    bool has(int k) const { return (w[k >> 6] >> (k & 63)) & 1; }
    void set(int k) { w[k >> 6] |= 1ULL << (k & 63); }
};
struct Label { int node; int parent; double t; double cost; double km; Bits mem; CBits cs; bool alive; };
// limited-memory subset-row cuts (3-SRC, multiplier 1/2): cut c on orders S_c with dual sig_c <= 0 and memory M_c;
// state bit is reset when a node outside M_c is visited; a route pays -sig_c each time the state reaches 1.
int NC = 0, NCW = 0; vector<double> csig; vector<vector<int>> cutsOf;  // per order: list of cuts
vector<CBits> keepMask;  // per order: cuts whose memory contains the order
inline void applyCuts(Label& L, int j) {
    for (int w = 0; w < NCW; w++) L.cs.w[w] &= keepMask[j].w[w];
    for (int c : cutsOf[j]) { if (L.cs.has(c)) { L.cs.w[c >> 6] &= ~(1ULL << (c & 63)); L.cost -= csig[c]; } else L.cs.set(c); }
}
inline double cutGap(const CBits& a, const CBits& b) {  // sum of |sig| over cuts with a=1, b=0
    double g = 0;
    for (int w = 0; w < NCW; w++) { uint64_t x = a.w[w] & ~b.w[w]; while (x) { int c = w * 64 + __builtin_ctzll(x); x &= x - 1; g -= csig[c]; } }
    return g;
}

Instance I;
struct VType { int start, mode, mask, count; vector<int> vehs; };
vector<VType> types;
int N, T;
vector<Bits> ng;          // ng neighbourhoods
vector<vector<int>> succ;  // per type: list of allowed orders
double tt(int t, int i, int j) { return I.T[types[t].mode][i * I.M + j]; }
double dd(int t, int i, int j) { return I.D[types[t].mode][i * I.M + j]; }

// completion bound F[t][k][u]: min reduced-cost of continuing after finishing order k at time u (<=0)
vector<vector<vector<double>>> F;
vector<double> alpha, pi_;
double beta_;

void computeF(int t) {
    auto& Ft = F[t]; Ft.assign(N, vector<double>(722, 0.0));
    auto& al = succ[t];
    for (int u = 720; u >= 0; u--) {
        for (int k : al) {
            double best = 0; int nk = I.node(k);
            // only relevant for u within [a_k+svc, b_k+svc], but cheap enough: skip if impossible
            if (u < I.ord[k].a + I.ord[k].svc - 1) { Ft[k][u] = Ft[k][u + 1 <= 720 ? u + 1 : 720]; continue; }
            for (int j : al) {
                if (j == k) continue;
                int nj = I.node(j);
                double st = max(u + tt(t, nk, nj), I.ord[j].a);
                if (st > I.ord[j].b + EPS) continue;
                double fin = st + I.ord[j].svc; if (fin > SHIFT + EPS) continue;
                int uf = (int)floor(fin + 1e-9); if (uf > 720) uf = 720;
                double c = beta_ * dd(t, nk, nj) - pi_[j] + Ft[j][uf];
                if (c < best) best = c;
            }
            Ft[k][u] = best;
        }
    }
}
double Fbound(int t, int k, double fin) { int u = (int)floor(fin + 1e-9); if (u > 720) u = 720; if (u < 0) u = 0; return F[t][k][u]; }
double depotBound(int t) {  // min over first orders
    double best = 0; int s = types[t].start;
    for (int j : succ[t]) {
        int nj = I.node(j); double st = max(tt(t, s, nj), I.ord[j].a);
        if (st > I.ord[j].b + EPS) continue; double fin = st + I.ord[j].svc; if (fin > SHIFT + EPS) continue;
        double c = beta_ * dd(t, s, nj) - pi_[j] + Fbound(t, j, fin);
        best = min(best, c);
    }
    return best;
}


// forward optimistic DP: H[k][u] = min cost (incl. pi_k) of any (non-elementary) path depot->k with service start <= u
vector<vector<double>> H;
void computeH(int t) {
    const double INFc = 1e18; H.assign(N, vector<double>(722, INFc));
    vector<vector<double>> h(N, vector<double>(722, INFc));
    int s = types[t].start;
    for (int j : succ[t]) {
        int nj = I.node(j); double st = max(tt(t, s, nj), I.ord[j].a);
        if (st > I.ord[j].b + EPS || st + I.ord[j].svc > SHIFT + EPS) continue;
        int u = (int)floor(st + 1e-9); h[j][u] = min(h[j][u], beta_ * dd(t, s, nj) - pi_[j]);
    }
    for (int u = 0; u <= 720; u++) for (int k : succ[t]) {
        if (h[k][u] >= INFc) continue; int nk = I.node(k);
        for (int j : succ[t]) {
            if (j == k) continue; int nj = I.node(j);
            double st = max(u + I.ord[k].svc + tt(t, nk, nj), I.ord[j].a);
            if (st > I.ord[j].b + EPS || st + I.ord[j].svc > SHIFT + EPS) continue;
            int uj = (int)floor(st + 1e-9); if (uj <= u) uj = u + 1;  // cannot happen (svc>0), safety
            if (uj > 720) continue;
            double c = h[k][u] + beta_ * dd(t, nk, nj) - pi_[j];
            if (c < h[j][uj]) h[j][uj] = c;
        }
    }
    for (int k = 0; k < N; k++) { double m = INFc; for (int u = 0; u <= 720; u++) { m = min(m, h[k][u]); H[k][u] = m; } }
}
double Hq(int k, double L) { int u = (int)floor(L + 1e-9); if (u > 720) u = 720; if (u < 0) return 1e18; return H[k][u]; }

// backward ng labeling -> per node step function G: min suffix cost (excl. pi_k) over suffixes feasible with start at k <= L
vector<char> forbStart, forbEnd, forbArc;  // branching restrictions (only read when compiled with -DBP)
struct BL { int node; double L, cost; Bits mem; CBits cs; bool alive; };
vector<vector<pair<double, double>>> Gs;  // per node: (L desc, prefix-min cost)
// backward ng labeling; suffix cost includes the cut costs of the nodes AFTER the first one (valid lower bound:
// cross prefix/suffix cut costs are >= 0 and ignored). Respects forbidden arcs/ends.
size_t backwardG(int t, double al, double gap, size_t cap, bool& ok) {
    vector<BL> bp; vector<vector<int>> at(N);
    typedef pair<double, int> QE; priority_queue<QE> pq;  // max L first
    auto tryAdd = [&](BL X) {
        if (al + Hq(X.node, X.L) + X.cost > gap + 1e-7) return;
        auto& lst = at[X.node];
        for (int id : lst) { const BL& Y = bp[id]; if (Y.alive && Y.L >= X.L - 1e-9 && Y.cost + (NC ? cutGap(Y.cs, X.cs) : 0) <= X.cost + 1e-9 && Y.mem.subsetOf(X.mem)) return; }
        int nid = bp.size(); size_t w = 0;
        for (size_t i = 0; i < lst.size(); i++) { BL& Y = bp[lst[i]]; if (!Y.alive) continue; if (X.L >= Y.L - 1e-9 && X.cost + (NC ? cutGap(X.cs, Y.cs) : 0) <= Y.cost + 1e-9 && X.mem.subsetOf(Y.mem)) { Y.alive = false; continue; } lst[w++] = lst[i]; }
        lst.resize(w); lst.push_back(nid); bp.push_back(X); pq.push({X.L, nid});
    };
    for (int k : succ[t]) { if (forbEnd[k]) continue; BL X; X.node = k; X.L = I.ord[k].b; X.cost = 0; X.mem.clear(); X.mem.set(k); X.cs.clear(); X.alive = true; tryAdd(X); }
    ok = true;
    while (!pq.empty()) {
        auto [Lv, id] = pq.top(); pq.pop();
        if (!bp[id].alive) continue;
        if (bp.size() > cap) { ok = false; break; }
        BL X = bp[id]; int nk = I.node(X.node);
        // cut effect of X.node (it becomes an interior node of the suffix of any predecessor)
        CBits cs2 = X.cs; double cadd = 0;
        if (NC) {
            for (int w = 0; w < NCW; w++) cs2.w[w] &= keepMask[X.node].w[w];
            for (int c : cutsOf[X.node]) { if (cs2.has(c)) { cs2.w[c >> 6] &= ~(1ULL << (c & 63)); cadd -= csig[c]; } else cs2.set(c); }
        }
        for (int i : succ[t]) {
            if (X.mem.has(i) || forbArc[i * N + X.node]) continue; int ni = I.node(i);
            double Li = min(I.ord[i].b, X.L - tt(t, ni, nk) - I.ord[i].svc);
            if (Li < I.ord[i].a - 1e-9) continue;
            BL Y; Y.node = i; Y.L = Li; Y.cost = X.cost + beta_ * dd(t, ni, nk) - pi_[X.node] + cadd; Y.cs = cs2;
            for (int w = 0; w < W; w++) Y.mem.w[w] = X.mem.w[w] & ng[i].w[w];
            Y.mem.set(i); Y.alive = true; tryAdd(Y);
        }
    }
    Gs.assign(N, {});
    // all labels ever created (alive or not) are valid suffixes; dominated ones are harmless for a min
    for (auto& X : bp) Gs[X.node].push_back({X.L, X.cost});
    for (auto& g : Gs) {
        sort(g.begin(), g.end(), [](auto& a, auto& b) { return a.first > b.first; });
        for (size_t i = 1; i < g.size(); i++) g[i].second = min(g[i].second, g[i - 1].second);
    }
    return bp.size();
}
// ---- A-mask completion bound: backward labeling elementary w.r.t. a small set A of high-dual orders.
// GM[k][b][m] = min suffix cost (after k) over suffixes using only A-orders in m, with latest start L >= GBW*b.
// A forward label with exact prefix set P can only be completed by suffixes disjoint from P -> query m = ~P.
const int GBW = 20, GNB = 37;
int NA = 0; vector<int> Aidx, Alist; vector<float> GM; bool gmOK = false;
struct BM { int node; double L, cost; Bits mem; CBits cs; uint32_t um; bool alive; };
size_t backwardGM(int t, double al, double gap, size_t cap, bool& ok, int maxA) {
    Aidx.assign(N, -1); Alist.clear();
    { vector<pair<double, int>> c; for (int k : succ[t]) c.push_back({-pi_[k], k}); sort(c.begin(), c.end());
      NA = min(maxA, (int)c.size()); for (int i = 0; i < NA; i++) { Aidx[c[i].second] = i; Alist.push_back(c[i].second); } }
    vector<BM> bp; vector<vector<int>> at(N);
    typedef pair<double, int> QE; priority_queue<QE> pq;
    auto tryAdd = [&](BM X) {
        if (al + Hq(X.node, X.L) + X.cost > gap + 1e-7) return;
        auto& lst = at[X.node];
        for (int id : lst) { const BM& Y = bp[id]; if (Y.alive && Y.L >= X.L - 1e-9 && (Y.um & ~X.um) == 0 && Y.cost + (NC ? cutGap(Y.cs, X.cs) : 0) <= X.cost + 1e-9 && Y.mem.subsetOf(X.mem)) return; }
        int nid = bp.size(); size_t w = 0;
        for (size_t i = 0; i < lst.size(); i++) { BM& Y = bp[lst[i]]; if (!Y.alive) continue; if (X.L >= Y.L - 1e-9 && (X.um & ~Y.um) == 0 && X.cost + (NC ? cutGap(X.cs, Y.cs) : 0) <= Y.cost + 1e-9 && X.mem.subsetOf(Y.mem)) { Y.alive = false; continue; } lst[w++] = lst[i]; }
        lst.resize(w); lst.push_back(nid); bp.push_back(X); pq.push({X.L, nid});
    };
    for (int k : succ[t]) { if (forbEnd[k]) continue; BM X; X.node = k; X.L = I.ord[k].b; X.cost = 0; X.mem.clear(); X.mem.set(k); X.cs.clear(); X.um = 0; X.alive = true; tryAdd(X); }
    ok = true;
    while (!pq.empty()) {
        auto [Lv, id] = pq.top(); pq.pop();
        if (!bp[id].alive) continue;
        if (bp.size() > cap) { ok = false; break; }
        BM X = bp[id]; int nk = I.node(X.node);
        CBits cs2 = X.cs; double cadd = 0;
        if (NC) {
            for (int w = 0; w < NCW; w++) cs2.w[w] &= keepMask[X.node].w[w];
            for (int c : cutsOf[X.node]) { if (cs2.has(c)) { cs2.w[c >> 6] &= ~(1ULL << (c & 63)); cadd -= csig[c]; } else cs2.set(c); }
        }
        uint32_t um2 = X.um | (Aidx[X.node] >= 0 ? (1u << Aidx[X.node]) : 0u);
        for (int i : succ[t]) {
            if (X.mem.has(i) || forbArc[i * N + X.node]) continue;
            if (Aidx[i] >= 0 && ((um2 >> Aidx[i]) & 1)) continue;
            int ni = I.node(i);
            double Li = min(I.ord[i].b, X.L - tt(t, ni, nk) - I.ord[i].svc);
            if (Li < I.ord[i].a - 1e-9) continue;
            BM Y; Y.node = i; Y.L = Li; Y.cost = X.cost + beta_ * dd(t, ni, nk) - pi_[X.node] + cadd; Y.cs = cs2; Y.um = um2;
            for (int w = 0; w < W; w++) Y.mem.w[w] = X.mem.w[w] & ng[i].w[w];
            Y.mem.set(i); Y.alive = true; tryAdd(Y);
        }
    }
    gmOK = ok;
    if (!ok) return bp.size();
    size_t M2 = 1u << NA; GM.assign((size_t)N * GNB * M2, 1e30f);
    for (auto& X : bp) {
        int b = (int)floor(X.L / GBW + 1e-9); if (b >= GNB) b = GNB - 1; if (b < 0) continue;
        float& g = GM[((size_t)X.node * GNB + b) * M2 + X.um]; if (X.cost < g) g = (float)(X.cost - 1e-4);
    }
    for (int k = 0; k < N; k++) {
        for (int b = GNB - 2; b >= 0; b--) { float* lo = &GM[((size_t)k * GNB + b) * M2]; float* hi = lo + M2; for (size_t m = 0; m < M2; m++) lo[m] = min(lo[m], hi[m]); }
        for (int b = 0; b < GNB; b++) { float* g = &GM[((size_t)k * GNB + b) * M2];
            for (int i = 0; i < NA; i++) for (size_t m = 0; m < M2; m++) if (m >> i & 1) g[m] = min(g[m], g[m ^ (1u << i)]); }
    }
    return bp.size();
}
inline double GMq(int k, double s, const Bits& P) {
    int b = (int)floor(s / GBW + 1e-9); if (b < 0) b = 0; if (b >= GNB) b = GNB - 1;
    uint32_t pm = 0; for (int i = 0; i < NA; i++) if (P.has(Alist[i])) pm |= 1u << i;
    uint32_t q = ((1u << NA) - 1) & ~pm;
    return GM[((size_t)k * GNB + b) * (1u << NA) + q];
}
double Gq(int k, double s) {  // s = actual service start at k
    auto& g = Gs[k];
    // entries with L >= s - eps: prefix of g
    int lo = 0, hi = g.size();
    while (lo < hi) { int m = (lo + hi) / 2; if (g[m].first >= s - 1e-7) lo = m + 1; else hi = m; }
    if (lo == 0) return 1e18;
    return g[lo - 1].second;
}
vector<Label> pool;
vector<int> pathOf(int li) { vector<int> r; while (li >= 0) { r.push_back(pool[li].node); li = pool[li].parent; } reverse(r.begin(), r.end()); return r; }

struct Col { int t; double km, rc; vector<int> r; };

// labeling for one type. level 1: heuristic dominance (ignores memory); 2: exact ng
// returns min rc (valid if complete & level 2), fills cols with negative ones
double labelType(int t, int level, vector<Col>& out, size_t labelCap, bool& complete) {
    pool.clear(); vector<vector<int>> atNode(N);
    typedef pair<double, int> QE; priority_queue<QE, vector<QE>, greater<QE>> pq;
    double minrc = alpha[t];  // empty route not allowed, but this is only used as a bound: rc of any route >= ... keep as init +inf
    minrc = 1e18;
    int s = types[t].start; double al = alpha[t];
    auto tryAdd = [&](Label L) {
        // bound prune
        double lb = al + L.cost + Fbound(t, L.node, L.t);
        if (lb >= -1e-9 && level >= 1) { // cannot produce negative column; still need minrc bound
            // record for minrc: this label's best completion >= lb
            if (lb < minrc) minrc = lb;
            return;
        }
        auto& lst = atNode[L.node];
        for (int id : lst) {
            const Label& Y = pool[id]; if (!Y.alive) continue;
            if (Y.t <= L.t + 1e-9 && Y.cost <= L.cost + 1e-9 && (level == 1 || Y.mem.subsetOf(L.mem)) && (NC == 0 || Y.cost + cutGap(Y.cs, L.cs) <= L.cost + 1e-9)) return;
        }
        int nid = pool.size();
        size_t w = 0;
        for (size_t i = 0; i < lst.size(); i++) {
            Label& Y = pool[lst[i]]; if (!Y.alive) continue;
            if (L.t <= Y.t + 1e-9 && L.cost <= Y.cost + 1e-9 && (level == 1 || L.mem.subsetOf(Y.mem)) && (NC == 0 || L.cost + cutGap(L.cs, Y.cs) <= Y.cost + 1e-9)) { Y.alive = false; continue; }
            lst[w++] = lst[i];
        }
        lst.resize(w); lst.push_back(nid);
        pool.push_back(L); pq.push({L.t, nid});
    };
    for (int j : succ[t]) {
        if (forbStart[j]) continue;
        int nj = I.node(j); double st = max(tt(t, s, nj), I.ord[j].a);
        if (st > I.ord[j].b + EPS) continue; double fin = st + I.ord[j].svc; if (fin > SHIFT + EPS) continue;
        Label L; L.node = j; L.parent = -1; L.t = fin; L.km = dd(t, s, nj); L.cost = beta_ * L.km - pi_[j]; L.mem.clear(); L.mem.set(j); L.cs.clear(); applyCuts(L, j); L.alive = true;
        tryAdd(L);
    }
    complete = true;
    while (!pq.empty()) {
        auto [tm, id] = pq.top(); pq.pop();
        if (!pool[id].alive) continue;
        if (pool.size() > labelCap) { complete = false; break; }
        Label X = pool[id];
        double rc = al + X.cost;
        if (rc < minrc) minrc = rc;
        if (rc < -1e-7 && !forbEnd[X.node]) out.push_back({t, X.km, rc, pathOf(id)});
        int nk = I.node(X.node);
        for (int j : succ[t]) {
            if (X.mem.has(j) || forbArc[X.node * N + j]) continue;
            int nj = I.node(j); double st = max(X.t + tt(t, nk, nj), I.ord[j].a);
            if (st > I.ord[j].b + EPS) continue; double fin = st + I.ord[j].svc; if (fin > SHIFT + EPS) continue;
            Label L; L.node = j; L.parent = id; L.t = fin; L.km = X.km + dd(t, nk, nj); L.cost = X.cost + beta_ * dd(t, nk, nj) - pi_[j];
            for (int w = 0; w < W; w++) L.mem.w[w] = X.mem.w[w] & ng[j].w[w];
            L.mem.set(j); L.cs = X.cs; applyCuts(L, j); L.alive = true;
            tryAdd(L);
        }
    }
    return minrc;
}

void readDuals() {
    alpha.assign(T, 0); pi_.assign(N, 0);
    for (auto& a : alpha) cin >> a;
    for (auto& p : pi_) cin >> p;
    cin >> NC; csig.assign(NC, 0); cutsOf.assign(N, {});
    if (NC > 64 * CW) { fprintf(stderr, "too many cuts\n"); exit(1); }
    NCW = (NC + 63) / 64; keepMask.assign(N, CBits()); for (auto& k : keepMask) k.clear();
    for (int c = 0; c < NC; c++) {
        int a, b, d, m; cin >> a >> b >> d >> csig[c] >> m; cutsOf[a].push_back(c); cutsOf[b].push_back(c); cutsOf[d].push_back(c);
        if (m < 0) { for (int k = 0; k < N; k++) keepMask[k].set(c); }
        else for (int i = 0; i < m; i++) { int k; cin >> k; keepMask[k].set(c); }
        keepMask[a].set(c); keepMask[b].set(c); keepMask[d].set(c);
    }
    forbStart.assign(N, 0); forbEnd.assign(N, 0); forbArc.assign(N * N, 0);
#ifdef BP
    int NF; cin >> NF;
    for (int f = 0; f < NF; f++) { int i, j; cin >> i >> j; if (i < 0) forbStart[j] = 1; else if (j < 0) forbEnd[i] = 1; else forbArc[i * N + j] = 1; }
#endif
}

int GMA = 12;
int main(int argc, char** argv) {
    if (getenv("CGX_GMA")) GMA = atoi(getenv("CGX_GMA"));
    I = loadInstance(argv[1]); N = I.N;
    int ngsize = argc > 2 ? atoi(argv[2]) : 10;
    if (N > 64 * W) { fprintf(stderr, "N too large\n"); return 1; }
    for (int v = 0; v < I.V; v++) {
        bool f = false;
        for (auto& ty : types) if (ty.start == I.veh[v].start && ty.mode == I.veh[v].mode && ty.mask == I.veh[v].mask) { ty.count++; ty.vehs.push_back(v); f = true; break; }
        if (!f) types.push_back({I.veh[v].start, I.veh[v].mode, I.veh[v].mask, 1, {v}});
    }
    T = types.size();
    succ.resize(T);
    for (int t = 0; t < T; t++) for (int k = 0; k < N; k++) if ((types[t].mask >> I.ord[k].skill) & 1) succ[t].push_back(k);
    // ng sets: nearest by average symmetric travel time over used modes, restricted to time-compatible pairs
    ng.resize(N);
    for (int k = 0; k < N; k++) {
        vector<pair<double, int>> c;
        for (int j = 0; j < N; j++) if (j != k) {
            double d = 0; int cnt = 0;
            for (int t = 0; t < T; t++) { d += tt(t, I.node(k), I.node(j)) + tt(t, I.node(j), I.node(k)); cnt++; }
            d /= cnt;
            // time compatibility: can j follow k and k follow j (cycle possible)?
            bool kj = I.ord[k].a + I.ord[k].svc <= I.ord[j].b + 1e-9, jk = I.ord[j].a + I.ord[j].svc <= I.ord[k].b + 1e-9;
            if (!(kj && jk)) continue;  // a cycle k..j..k impossible -> no need to remember
            c.push_back({d, j});
        }
        sort(c.begin(), c.end());
        ng[k].clear(); ng[k].set(k);
        for (int i = 0; i < (int)c.size() && i < ngsize - 1; i++) ng[k].set(c[i].second);
    }
    F.resize(T);
    printf("TYPES %d\n", T);
    for (auto& ty : types) { printf("%d %d %d %d", ty.count, ty.start, ty.mode, ty.mask); for (int v : ty.vehs) printf(" %d", v); printf("\n"); }
    fflush(stdout);
    string cmd;
    while (cin >> cmd) {
        if (cmd == "QUIT") break;
        if (cmd == "PRICE") {
            int level, maxcols; double labcap; cin >> level >> maxcols >> beta_ >> labcap; readDuals();
            vector<Col> cols; vector<double> mins(T); bool allc = true;
            for (int t = 0; t < T; t++) {
                computeF(t);
                vector<Col> c; bool comp;
                double m = labelType(t, level, c, (size_t)labcap, comp);
                if (!comp) allc = false;
                if (m > 1e17) m = 0;  // no feasible route at all
                if (level < 2 || !comp) m = alpha[t] + depotBound(t);
                mins[t] = m;
                sort(c.begin(), c.end(), [](const Col& a, const Col& b) { return a.rc < b.rc; });
                // keep distinct order sets, at most maxcols per type
                set<vector<int>> seen; int kept = 0;
                for (auto& x : c) { if (kept >= maxcols) break; vector<int> key = x.r; sort(key.begin(), key.end()); if (!seen.insert(key).second) continue; cols.push_back(x); kept++; }
            }
            printf("COLS %zu %d\nMINRC", cols.size(), (int)(allc && level == 2));
            for (double m : mins) printf(" %.9f", m);
            printf("\n");
            for (auto& c : cols) { printf("%d %.6f %.9f %zu", c.t, c.km, c.rc, c.r.size()); for (int k : c.r) printf(" %d", k); printf("\n"); }
            fflush(stdout);
        } else if (cmd == "ENUM") {
            double gap; long long maxroutes; string file; cin >> gap >> beta_ >> maxroutes >> file; readDuals();
            FILE* fo = fopen(file.c_str(), "w"); bool complete = true; long long total = 0;
            for (int t = 0; t < T && complete; t++) {
                computeF(t);
                double al = alpha[t]; int s = types[t].start;
                computeH(t); bool gok;
                size_t nbm = GMA > 0 ? backwardGM(t, al, gap, (size_t)min(6000000LL, 10 * maxroutes), gok, min(GMA, N > 100 ? 11 : 12)) : 0;
                if (GMA <= 0) gmOK = false;
                size_t nb = backwardG(t, al, gap, (size_t)min(8000000LL, 20 * maxroutes), gok);
                fprintf(stderr, "enum type %d: backward labels %zu ok %d; A-mask labels %zu ok %d (|A|=%d)\n", t, nb, (int)gok, nbm, (int)gmOK, NA);
                if (!gok) { complete = false; break; }
                // labels with exact visited set; dominance only same node & same set
                pool.clear();
                unordered_map<Bits, vector<int>, BitsHash> bucket[1];
                vector<unordered_map<Bits, vector<int>, BitsHash>> at(N);
                unordered_map<Bits, pair<double, int>, BitsHash> bestSet;  // set -> (km, label)
                typedef pair<double, int> QE; priority_queue<QE, vector<QE>, greater<QE>> pq;
                auto tryAdd = [&](Label L) {
                    double gq = Gq(L.node, L.t - I.ord[L.node].svc);
                    if (gq > 1e17) return;
                    if (gmOK) { double gm = GMq(L.node, L.t - I.ord[L.node].svc, L.mem); if (gm > 1e29) return; gq = max(gq, gm); }
                    double lb = al + L.cost + max(Fbound(t, L.node, L.t), gq);
                    if (lb > gap + 1e-7) return;
                    auto& lst = at[L.node][L.mem];
                    for (int id : lst) { const Label& Y = pool[id]; if (Y.alive && Y.t <= L.t + 1e-9 && Y.km <= L.km + 1e-9 && Y.cost + (NC ? cutGap(Y.cs, L.cs) : 0) <= L.cost + 1e-9) return; }
                    int nid = pool.size(); size_t w = 0;
                    for (size_t i = 0; i < lst.size(); i++) { Label& Y = pool[lst[i]]; if (!Y.alive) continue; if (L.t <= Y.t + 1e-9 && L.km <= Y.km + 1e-9 && L.cost + (NC ? cutGap(L.cs, Y.cs) : 0) <= Y.cost + 1e-9) { Y.alive = false; continue; } lst[w++] = lst[i]; }
                    lst.resize(w); lst.push_back(nid); pool.push_back(L); pq.push({L.t, nid});
                };
                for (int j : succ[t]) {
                    if (forbStart[j]) continue;
                    int nj = I.node(j); double st = max(tt(t, s, nj), I.ord[j].a);
                    if (st > I.ord[j].b + EPS) continue; double fin = st + I.ord[j].svc; if (fin > SHIFT + EPS) continue;
                    Label L; L.node = j; L.parent = -1; L.t = fin; L.km = dd(t, s, nj); L.cost = beta_ * L.km - pi_[j]; L.mem.clear(); L.mem.set(j); L.cs.clear(); applyCuts(L, j); L.alive = true;
                    tryAdd(L);
                }
                while (!pq.empty()) {
                    auto [tm, id] = pq.top(); pq.pop();
                    if (!pool[id].alive) continue;
                    if ((long long)pool.size() > min(12000000LL, 30 * maxroutes)) { complete = false; break; }
                    Label X = pool[id];
                    if (al + X.cost <= gap + 1e-7 && !forbEnd[X.node]) {
                        auto it = bestSet.find(X.mem);
                        if (it == bestSet.end()) bestSet[X.mem] = {X.km, id};
                        else if (X.km < it->second.first) it->second = {X.km, id};
                        if ((long long)bestSet.size() + total > maxroutes) { complete = false; break; }
                    }
                    int nk = I.node(X.node);
                    for (int j : succ[t]) {
                        if (X.mem.has(j) || forbArc[X.node * N + j]) continue;
                        int nj = I.node(j); double st = max(X.t + tt(t, nk, nj), I.ord[j].a);
                        if (st > I.ord[j].b + EPS) continue; double fin = st + I.ord[j].svc; if (fin > SHIFT + EPS) continue;
                        Label L; L.node = j; L.parent = id; L.t = fin; L.km = X.km + dd(t, nk, nj); L.cost = X.cost + beta_ * dd(t, nk, nj) - pi_[j];
                        L.mem = X.mem; L.mem.set(j); L.cs = X.cs; applyCuts(L, j); L.alive = true;
                        tryAdd(L);
                    }
                }
                for (auto& [b, pr] : bestSet) {
                    int id = pr.second; auto r = pathOf(id);
                    fprintf(fo, "%d %.6f %.9f %zu", t, pool[id].km, al + pool[id].cost, r.size()); for (int k : r) fprintf(fo, " %d", k); fprintf(fo, "\n");
                }
                total += bestSet.size();
                fprintf(stderr, "enum type %d: labels %zu routes %zu\n", t, pool.size(), bestSet.size());
            }
            fclose(fo);
            printf("ENUM %lld %d\n", total, (int)complete); fflush(stdout);
        }
    }
}
