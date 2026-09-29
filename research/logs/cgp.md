# cgp: integer proofs of penalty / fleet levels (branch-and-price)

Files: `solvers/cgp_price.cpp` -> `bin/cgp_price` (copy of cg_price with per-type forbidden nodes/arcs for branching),
`cgp_bp.py` (B&P for stage 1 = min penalty, stage 2 = min #vehicles), `cgp_update.py` (raise lb_pen / lb_used).

## Method
* Master: set partitioning LP (HiGHS): order rows =1 with unserved slack y_k, type rows lo..up, penalty row,
  global 3-subset-row cuts (sep. on fractional LP, <=150). Artificial columns keep branched nodes feasible.
* Stage 1: obj = sum p_k y_k, all vehicles available. Goal: show no integer solution with penalty <= Pbest-10
  (penalties are multiples of 10) -> prune node when LP (or Lagrangian bound) > Pbest-10.
* Stage 2: obj = #routes, penalty <= Pbest; prune when LP > K-1.
* Node bound = converged LP with exact ng-route pricing, or Lagrangian bound
  sum(duals*rhs) + sum_t up_t*min(0, minrc_t) + y terms (valid also for heuristic/incomplete pricing via F bound).
* Branching: y_k (stage 1), vehicles per type, order-to-type assignment (k not by t / k only by t), arc flow.

## Experiments
* 09:40 stage 1 (penalty) - root LP with 3-SRC cuts is already much stronger than the old cut-free penalty LP:
  | inst | best | old LB | cgp result |
  |---|---|---|---|
  | road yugocentr_s5 | 50 | 30 (LP 25) | proven 50: 3 nodes, 6 s (branch y14: both children LP = 50) |
  | road yugocentr_s7 | 100 | 80 (LP 75) | proven 100: 5 nodes, 1.4 s |
  | gen allday_vostok_0 | 100 | 80 (LP 75) | proven 100: root LP with 23 cuts = 100 (0.6 s) |
  | road vostok_s7 | 150 | 140 (LP 135) | proven 150: 321 nodes, 57 s (LP stays 135 for many nodes, y-branching) |
  | road yugovostok_s7 | 400 | 390 (LP 390) | proven 400: root LP with 30 cuts = 400 (0.8 s) |
  Checked with --nolagr (no Lagrangian pruning, converged exact LPs only): same result.
  | gen tight_yugovostok_8 | 20 | 0 (LP 0) | proven 20: root LP with 150 cuts 6.32, 9 nodes (y-branching; every y_k=1 child = 20, the all-served chain reaches 11.43 > 10), 66 s |
* HiGHS: primal simplex (`--simplex 4`) is 2-6x faster than the default for this CG (columns added keep primal feasibility).
* 09:34 stage 2 (fleet): gen large_yugovostok_5: converged root LP with 30 cuts = 10.0145 > 10 -> lb_used 11 = best (PROVEN).
  (old run had an unconverged pricing -> Lagrangian 10.018 LP not trusted, lb 10).
* 09:40-10:50 fleet B&P on the other targets (road/gen, 2 cores on an overloaded machine, load ~25):
  - walk_yugovostok_2 (K=11): root LP 9.9405 (150 cuts), ng16 + 270 cuts 9.9511. B&P: after type branching every open
    node has LP = 10.0000 exactly ("pinned": with type counts fixed the objective is constant, the LP is just a
    fractional feasibility certificate); arc/assignment branching went to depth 30 without pruning.
  - control_yugovostok_mix2_inf (K=10): root 8.90 (270 cuts); same picture: all open nodes LP = 9.0000.
  - large_yugocentr_4 (K=12): root 10.728 (240 cuts). Nodes after type branching need 10+ min (pricing with beta=0 and
    240 cuts: labels explode in dominance lists) -> stopped.
  - walk_yugovostok_5: root pricing (exact ng labeling, beta=0, 10-hour windows) did not finish in 8 min -> stopped.
  - Column-fixing dive on mix2_inf: fixing either of the 2 largest fractional columns gives child bounds 12.47 / 10.78
    (> 9): the K-1 LP solutions are far from integral.
  - Added: capped exact pricing level (small label cap first), pricer early stop at 20*maxcols negative columns,
    column pool purge, slack cut purge, phase-1 infeasibility test (artificials), strong branching / probing
    (arc-force side first: if its LP > K-1 the node reduces to the other child, no second evaluation).
  - Probing on the pinned nodes works (arc-force children get LP 210 = infeasible), tree stays narrow (2-5 open nodes).
* 11:05 FALSE ALARM caught: strong-branching runs "proved" mix2_inf (300 s) and walk2 (452 s), but every probe was
  the arc-force side with LP = K-1+200 (2 artificials). Cause: the force-arc forbid list contained (i,i) and (j,j),
  which in the cgp encoding (i==j) means "forbid node" -> orders i and j were removed. Fixed (exclude k==i/j).
  Those two results are DISCARDED (no lb written). Penalty proofs re-checked with --verbose: they use only y-branching
  (nforb=0 in every node) and root LPs -> unaffected; vostok_s7 re-proved with the fixed code (305 nodes).
* 11:31 gen walk_yugovostok_5 (K=9): with capped/early-stop pricing the root CG converges: exact LP with 150 3-SRC cuts
  = 8.0616 > 8 (Lagrangian 8.0567 in the first run, converged LP confirmed with --nolagr, 569 s) -> lb_used 9 PROVEN.
  (old value 8 came from an LP of 7.9 with the cut loop stopped early.)
* mix2_inf root with ng=40: 8.903 (ng=10: 8.901) -> elementarity is not what keeps the LP at K-1.
* After fixing the arc bug, strong branching on mix2_inf pinned nodes gives zs=[9.0, 9.0] for all candidates (no probe).
* 11:40 exact enumeration at the root (all routes with rc <= K-1-LP, then MIP): walk2 gap 0.049 / mix2 gap 0.096 ->
  > 3M routes for the multi-vehicle type alone (fleet duals are degenerate: pi ~ 1/K per order, every "full" route has
  rc ~ 0) -> incomplete. Column-fixing dive on walk2: 16 nodes, fixing any large fractional column gives LP > 10.
* Root with unlimited 3-SRC rounds (stall 1e-6): walk2 9.9512 (322 cuts, no more violated), lc4 10.7285 (357 cuts),
  mix2 8.9037 (445 cuts). Added 5-row rank-1 cuts (multiplier 1/3, pricer bin/cgp_price5 with 3-state cut memory,
  rc of priced columns cross-checked in Python): the heuristic separator found no violated 5-row cut on mix2.

## Final status (12:00)
| target | before | now | how |
|---|---|---|---|
| road vostok_s7 pen | 150/140 | 150 PROVEN | penalty B&P, 305-321 nodes (y-branching) |
| road yugocentr_s5 pen | 50/30 | 50 PROVEN | penalty B&P, 3 nodes |
| road yugocentr_s7 pen | 100/80 | 100 PROVEN (now fully optimal) | penalty B&P, 5 nodes |
| road yugovostok_s7 pen | 400/390 | 400 PROVEN | root LP + 30 3SRC = 400 |
| gen allday_vostok_0 pen | 100/80 | 100 PROVEN (now fully optimal) | root LP + 23 3SRC = 100 |
| gen tight_yugovostok_8 pen | 20/0 | 20 PROVEN | penalty B&P, 9 nodes |
| gen large_yugovostok_5 fleet | 11/10 | 11 PROVEN | converged root LP + 30 3SRC = 10.0145 |
| gen walk_yugovostok_5 fleet | 9/8 | 9 PROVEN | converged root LP + 150 3SRC = 8.0616 |
| control mix2_inf fleet | 10/9 | open (LP 8.904) | B&P: nodes pinned at LP = 9 |
| gen walk_yugovostok_2 fleet | 11/10 | open (LP 9.951) | B&P: nodes pinned at LP = 10 |
| gen large_yugocentr_4 fleet | 12/11 | open (LP 10.729) | nodes too slow (pricing) |
No new solutions (no K-1 found by B&P incumbents, column MIPs, dives).
* 12:01 FINAL RE-VERIFICATION with the final code, --nolagr (only converged exact LPs used for pruning) and --checkrc
  (reduced costs of priced columns recomputed in Python incl. cut terms, 0 mismatches): all 8 proofs reproduced
  (tight_yugovostok_8 now closes at the root: LP 10.007 > 10 with more root cut rounds; vostok_s7 287 nodes).
* 12:04 local-search separation of 5-row rank-1 cuts: mix2_inf 15 cuts, LP 8.90368 -> 8.90372 (useless); walk2 none.
  Conclusion: the remaining fleet gaps (mix2_inf, walk2, large_yugocentr_4) are structural for the ng+SRC relaxation;
  with type counts fixed the LP stays exactly K-1 and B&P becomes a blind feasibility search.
