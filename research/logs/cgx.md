# cgx: exact pricing / branch-and-price for hard km gaps (agent cgx)

Files: `solvers/cgx_price.cpp` -> `bin/cgx_price` (pricer, compiled with -DBP), `cgx_bp.py` (km-stage CG + B&P +
node enumeration), `cgx_queue.sh` (worker queue). Outputs: lb/<set>/<inst>.json (only when higher), results/<set>/cgx/.

## Diagnosis
* allday_yugocentr_4 "CG bound negative": not a labeling problem per se. The old run did stage 3 with K=7 after
  stage 2 left 150 vehicle-stage 3-SRC cuts in the master; the first km pricing call with those (irrelevant, full-memory)
  cuts took 528 s, CG stopped after 1 iteration -> Lagrangian bound -333. Best solution later reached K=6, so the stored
  lb_km (K=7, assignment bound 69.3) did not even apply. A clean km-stage master (K=6, no stage-2 cuts) converges
  in 1.7 s (27 pricing calls, 0.5 s pricing): LP 151.10.
* Slow B&P nodes: after branching, big-M artificial columns sit in the basis at level 0 (degenerate), their cost
  goes into the duals (L = -25788 in the log), every route of that type gets rc << 0, completion bounds prune
  nothing and exact labeling takes 17-40 s per call (vs 0.1-1 s). Fix: drop artificials as soon as they are 0.

## Method (cgx_bp.py + bin/cgx_price)
* km stage only (pen 0 and fleet K already proven on all targets): rows orders =1, type rows [lo,up], sum <= K.
  Warm start: best solution + all routes of all solutions in results/<set>/**/<inst>.out + singletons.
* Pricing: ng-route labeling (existing algorithm), heuristic level (dominance ignores ng memory) capped at 3e5 labels,
  exact level cap 5e6 labels. Lagrangian bound dual_obj + sum_t up_t*min(0,minrc_t) at every iteration.
* Limited-memory 3-SRC cuts (memory = S + nodes between S-visits on support routes; memory augmented when a cut is
  violated in full-memory form but not in lm form). Pricer keeps a 512-bit cut state, reset by keepMask[j].
  lm-cuts make pricing ~4x cheaper than full memory (300 cuts at root, 1-5 s total pricing), bound almost equal.
* B&P best-first: type-count branching first, then arc branching with strong branching (8 candidates, restricted
  master LP without pricing). Unused artificials are removed after the first LP of a node (big-M dual fix).
* Node enumeration: when UB - node LP <= 3% UB, enumerate all elementary routes with rc <= gap (ENUM respects the
  node's forbidden arcs; dominance same set + km + cost-with-cut-gap so that it stays exact with lm cuts), then
  cg_pool2.Pool (LP + full-memory 3-SRC + rc fixing + MIP) with the node's type bounds -> node closed or bound.
  A failed enumeration is retried only at nodes with gap < 0.7 x failed gap.

## Enumeration on all-day windows (the real breakdown) -> A-mask completion bound
* Enumeration (elementary routes with rc <= gap) on allday_vostok_3: with gap 1.12 km the old bounds (non-elementary
  time-indexed F, backward ng-labeling G, both ignoring cut duals) let 15M forward labels through and produced 102
  routes, incomplete. Cause: the completion bound can revisit orders already in the prefix; all-day orders
  (80-min service, largest duals) can be "collected twice" at any time of day, so partial paths never look bad.
* Fix 1: backward G now includes the cut costs of the suffix (cross prefix/suffix cut terms ignored -> valid) and
  respects forbidden arcs.
* Fix 2 (decisive): A-mask bound. A = 12 orders with largest duals of the type. Backward labeling elementary w.r.t. A
  (state: ng memory + exact A-subset used after the first node), stored as GM[node][20-min bucket][mask] with a
  subset-min (zeta) transform; forward label with exact prefix set P queries GM[k][b][~P]. Test allday_vostok_3 root,
  gap 1.5: old 12M labels, 0 routes of type 2, incomplete (25 s); new 21k routes COMPLETE in 1.4 s.

## Results so far
* 09:28 allday_yugocentr_4 (K=6): root LP 151.10 -> 156.22 with 150 cuts; B&P 131 nodes, 487 s, found 160.5914
  (< best 161.285) and exhausted the tree -> PROVEN OPTIMAL 160.5914 (old lb 69.26 for K=7; gap was 133%).
* 09:25 yugocentr_s2: root 207.86 (300 lm cuts), type branching lifts the bound 207.9 -> 217.5 in 3 levels;
  29 nodes, 70 s, exhausted -> PROVEN OPTIMAL 219.7864 (old lb 207.49, 5.9%). Old B&P: 3 nodes in 21 min.
* 09:44 yugocentr_s4: 3 nodes + node enumeration (3.6k routes) + pool MIP, 46 s -> PROVEN OPTIMAL 219.7864.
* Strong branching with full LP re-solves cost 15-20 s/node on peak_vostok_3 (300 cut rows, 13k cols) -> dual
  simplex iteration limit 150 per candidate and an arc->columns index.
* A pool that does not close must not end the node (first version kept the pool LP as a hard floor, which froze
  the global bound below the stored one on real_vostok_9 / peak_vostok_0) -> now the node is branched further.
* 09:58 allday_vostok_3: PROVEN OPTIMAL 187.4044 (old lb 185.05, 1.27%): root 183.20 (300 lm cuts), B&P 51 nodes,
  most leaves closed by node enumeration (2-20k routes, ~1.5 s) + pool LP (lp_closed/infeasible). 808 s.
  Before the A-mask bound the same enumerations were incomplete (15M labels, 102 routes).
* cgx_pool.py = copy of cg_pool2 with MIP skipped when > 15000 active columns (HiGHS ignored the time limit on a
  104k/197k-column pool for 7-10 min).
* 10:09 allday_vostok_6: PROVEN OPTIMAL 204.2865 (old lb 199.83, 2.23%): root 200.50, 41 nodes, 605 s.
* 10:14 vostok_s0 (road, 9 types): root 173.91 vs UB 187.36 (7%); 15 min, 209 nodes -> 183.32 < stored 184.15
  (from earlier 2 x 15 min B&P) -> not written. Needs a longer single run.
* 10:24 real_vostok_9: 15 min, 103 nodes, lb 190.92 < stored 192.39 (not written). Time split: pricing 31 s,
  strong branching 186 s, enumeration+pool ~400 s (9 pools, 6 closed their node). The old B&P did 431 nodes in
  the same time (no SB, 150 cuts) -> on these vostok instances node throughput matters more than node strength.
  Fixes: column insertion via order->cuts index (lmcoef only for cuts hit twice), vectorised node bound reset via
  arc->columns index, SB 4 candidates x 100 dual simplex iterations, pool budget 100 s.
* 10:29 peak_vostok_0: 165 nodes, 168.31 < stored 168.77 (another agent's 1 h B&P). Not written.
* 10:40 large_vostok_9 (N=107, 13 vehicles): 15 min, 304 nodes -> lb 219.57 -> 220.64 (gap 2.34% -> 1.84%), written.
* allday_vostok_9 (30-min run): B&P found two better solutions, 139.41 -> 139.1878 (results/instances_gen_road/cgx).
* 10:42 control_yugovostok_pt_inf: root 330.26 vs UB 346.57 (4.7%) with 300 lm cuts; pricing on public-transport
  matrices ~0.25 s/call (41 s at root), nodes 10-20 s; stored lb 336.69 needs >1000 nodes -> stopped after 2 min.
* 10:48 allday_vostok_9: PROVEN OPTIMAL 138.4738 with a NEW best solution (old best 139.41, old lb 137.30):
  root 134.98 (3.2% gap), 365 nodes, 172 node enumerations (most close their node in ~1-2 s), 1095 s.
  The B&P found incumbents 139.19 and 138.47 from integral node LPs / pool MIPs.
* 11:18 peak_vostok_3: 30 min, 291 nodes -> lb 221.12 -> 221.65 (gap 1.64% -> 1.40%), written. SB still 410 s
  (31k columns).
* tight_vostok_3: 30-min run reached ~162.5+ (old 157.77 = plain root LP) but was killed by my own process
  cleanup (grep "source" matched "Python.app/Contents/Resources") 1 s before writing. -> cgx_bp now checkpoints
  the global bound to the lb json every 5 min; re-queued.
* 11:44 large_vostok_0 (25 min, 220 nodes): lb 236.40 -> 239.19 (gap 2.60% -> 1.41%), written.
* 11:44 large_yugocentr_7 (25 min, 261 nodes): lb 267.66 -> 270.29 (gap 2.93% -> 1.93%), written.
* 12:05 tight_vostok_3 (20 min, 394 nodes): lb 157.77 -> 163.10 (gap 6.05% -> 2.59%), written. Root with 300 lm
  cuts alone is 158.11.
* 12:05 large_vostok_3 (20 min, 273 nodes): lb 232.06 -> 234.93 (gap 5.16% -> 3.88%), written.
* 12:25 real_vostok_9 (20 min, 192 nodes): lb 192.39 -> 192.64 (gap 1.16% -> 1.03%), written.
* 12:25 node-throughput fixes: (a) Lagrangian bound computed also when artificials are in the LP (valid for any
  sign-correct duals) -> infeasible "force" children are pruned after a few iterations instead of converging to
  z ~ 900 with big-M; (b) column purge: when > 14000 columns, delete all but the 60% lowest-reduced-cost ones
  (+ basic + seed columns) -- LP re-solves after bound changes took ~2 s with 25k columns.
* 12:25 large_yugocentr_1 (20 min, 358 nodes): lb 258.24 -> 261.16 (gap 4.06% -> 2.90%), written.
* 12:46 large_vostok_6 (20 min, 491 nodes): lb 269.04 -> 270.53 (gap 3.25% -> 2.68%), written.
* 12:46 vostok_s0 (20 min, 504 nodes; 2.4x the nodes of the 10:14 run thanks to purge + Lagrangian pruning):
  lb 184.15 -> 184.25 (gap 1.74% -> 1.69%), written. Root LP is 7% below UB; type branching over 9 types dominates.
* 12:59 peak_vostok_0: PROVEN OPTIMAL 169.2732 (stored lb 168.77 from a 1-h B&P; my 10:29 run reached 168.31):
  345 nodes, 152 node enumerations, 811 s, with purge + Lagrangian pruning (node rate ~2.4x).
* 13:15 large_vostok_0 (29 min, 437 nodes): NEW best solution 240.1789 (old best 242.562) and lb 239.19 -> 239.66:
  gap 0.21% (was 2.60%). Re-queued instead of large_yugocentr_7.
* 13:28 peak_vostok_3 (29 min, 367 nodes): lb 221.65 -> 222.00 (gap 1.40% -> 1.24%), written.
* 13:35 real_vostok_9: PROVEN OPTIMAL 194.6271 (old lb 192.39, 1.16%): 149 nodes, 1236 s.
* 13:54 large_vostok_9 (25 min, 802 nodes): lb 220.64 -> 220.99 (gap 1.68%), written.
* 14:01 large_vostok_0 rerun: another better solution 240.1181 (lb stays 239.66 from the first run -> gap 0.19%).

## Final summary (14:05)
Proven optimal (9 of 18 targets): allday_yugocentr_4 (160.5914, new best), allday_vostok_3, allday_vostok_6,
allday_vostok_9 (138.4738, new best), peak_vostok_0, real_vostok_9 (gen_road), yugocentr_s2, yugocentr_s4 (road).
Bound improved, still open: large_vostok_0 0.19% (new best 240.1181), peak_vostok_3 1.24%, vostok_s0 1.69%,
large_vostok_9 1.68%, large_yugocentr_7 1.93%, tight_vostok_3 2.59% (was 6.05%), large_vostok_6 2.68%,
large_yugocentr_1 2.90%, large_vostok_3 3.88%. Unchanged: control_yugovostok_pt_inf (root 330.3 vs UB 346.6; pt
pricing 0.25 s/call, nodes 10-20 s; did not get enough time).
After finalize: gen_road proven 43/70, road 18/30, control_road 21/24.
