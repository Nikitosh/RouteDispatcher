# cgc: branch-price-and-cut with rounded fleet cuts for far-home instances (agent: cgc)

Files: `solvers/cgc_price.cpp` -> `bin/cgc_price` (copy of cg_price + fleet-cut resources + forbidden arcs always on),
`cgc_master.py` (root CG + fleet cuts + 3-SRC + best-first B&P), `cgc_sub.py` (k(S) = ceil of vehicle-min LP on the
sub-instance of S), `cgc_tri.py` (checks t_ij <= t_ik + s_k + t_kj, i.e. dropping an order keeps a route time-feasible),
`cgc_lp.py` (LP dump for analysis). Logs of runs: results/cgc_tmp/logs/.

## Diagnosis (09:40, yugovostok_s1)
* Depots: 0 = Moscow (9 vehicles), 1,2 = far towns (3 vehicles, 15 min apart, 76 min from Moscow by car).
  Orders nearest to depot: 63 Moscow / 16 / 4. Best: 9 vehicles = 3 far + 6 Moscow, 537.47 km.
* km-LP (ng10 + 150 SRC, old method) = 448.4: 1.2 far vehicles and 7.8 Moscow vehicles used fractionally;
  sum of x over routes visiting the Moscow cluster = 7.43, but the vehicle-min LP on the Moscow orders alone is 7.045
  => every integer solution has >= 8 routes entering Moscow.

## Method: rounded fleet cuts
sum_{r: r visits S} x_r >= k(S), k(S) = ceil(LP_vehmin(S)) with exact ng pricing on the sub-instance with orders S and
the full fleet (type counts, masks, modes). Valid because (a) best pen = 0 so every order is served in any solution
counted by lb_km, (b) removing the non-S orders from a route keeps it feasible (triangle condition verified = 0
violation on all target instances). Also sum x_r = K (lb_used = K).
Pricing: each fleet cut c is a label resource bit (entered S_c or not); the dual (any sign) is paid once on entering.
Dominance adds sum of positive duals of cuts entered by the dominating label but not by the dominated one (+ the
negative-dual analogue); completion bounds (F, backward G, H) ignore the cuts and subtract the sum of positive duals
of cuts not entered yet -> still valid; exactness of the LP bound unchanged.
Candidate sets: clusters by nearest start depot (car time), unions, and cluster orders restricted to window-start
ranges [lo,hi) (+ unions with other clusters). Separation computes k(S) lazily (cached in results/cgc_tmp/kcache_*).

## Experiments
* s1 root: LP without SRC 378.6 -> 468.5 with 8 fleet cuts (Moscow >= 8, Moscow orders with start < 240 >= 8,
  far cluster >= 2); + 150 SRC -> 486.81 (old lb 445.41, UB 537.47): gap 20.7% -> 10.4%.
* Candidate families extended (09:40): geographic complete-linkage clusters at 35 and 20 min car time (e.g. s4:
  Moscow 51 / Domodedovo 12 / far towns 20), unions, window-start ranges, skill-restricted subsets.
  k(S) monotone in S -> supersets with k <= lhs prune the k computations; picked cuts: smallest set per bound.
* s1 B&P 900 s (fleet cuts + 150 SRC; branching on type counts, then on the number of routes entering a candidate set):
  root 487.43, 42 nodes, LB 498.08 (old 445.41). NOTE: a bug (columns fixed by arc branching not released when
  moving to a node without arc restrictions) could have made bounds too high if arc branching occurred; no arc
  branch was logged, but the lb file was restored to the old value and s1 is re-run with the fixed code.
* s4: fleet cuts barely help (root 458.0 vs old 458.27). LP uses exactly the integer fleet; the gap is far-home
  vehicles commuting to Domodedovo: LP 1.35 far routes enter Domodedovo/Moscow, best solution 2. Checked:
  - rank-1 CG cuts from the vehicle-min duals (multipliers theta*pi, theta=1..6): violations <= 0.25 -> not implemented.
  - 3-SRC inside the vehicle-min LP of S (to push k(M u D)=7.000 to 8): LP stays 7.000.
  - full-instance bound min sum_{far routes entering M u D} (cgc_kfull.py, fleet cuts included): 0.29 -> ceil 1 < 1.35.
  => the s4 gap is cost-driven, not count-driven; counting cuts cannot close it.
* s2 root: 534.80 -> 559.70 (UB 609.08; gap 13.9% -> 8.8%).
* 10:00 vehicle-level branching added: before plain set branching, branch on the number of routes of a far-home
  type (single type, one start-depot group, or all non-main-depot types) that enter a cluster union (family sets
  without time/skill restriction); implemented as type-restricted fleet rows (pricer: cut applies to listed types).
  s1 re-run (fixed code): root 487.93, node 11 LB 502.44 (old-code run: 498.08 after 42 nodes).
* Validity fixes: (1) columns fixed to 0 by arc branching are now always released when leaving the node
  (fixed_any flag); (2) k = ceil(LP - 1e-4) (all logged k-LP values checked: none within 1e-3 above an integer).
  s2 result of the pre-fix code (563.09, 47 nodes, no arc branching logged) was reverted and s2 is queued again.
* s3 root 450.88 < old 455.26 (SRC capped at 90 because exact pricing got slow): s3 is cost-driven like s4.
* 10:40 results so far (720 s per instance): s1 445.41 -> 502.93 (root 487.93, 15 nodes), s3 no gain (root 450.88,
  6 slow nodes), control mix1_inf root 382.83 -> 502.40 (K=8: cuts "Moscow+Domodedovo orders starting before 240
  need 7 routes", ...), s6 root ~496, B&P 498.4 after 16 nodes (old 451.35).
* Checked for s4: k(S) recomputed with the weak Moscow types (masks 2/4/6) removed from the fleet: unchanged
  (7.000 for Moscow+Domodedovo) -> count structure really is satisfied by the LP; gap is routing cost.
* slowprice (stop adding SRC when one exact pricing takes longer) raised 12 -> 25 s for the later runs.
* control mix1_inf B&P (720 s): root 502.40, 10 nodes, LB 508.08 (old 382.83; gap 50.3% -> 13.2%).
* s6 B&P (720 s): root 488.61, 97 nodes, LB 508.22 (old 451.35; gap 19.0% -> 5.7%).
* 10:30 limited-memory 3-SRC (bin/cgc_price2, W=5 words -> up to 300 cuts): memory = triple + the segments between
  the triple's visits in the LP routes; coefficient = Pecin et al. memory rule, same code in master (FM.lmcoef) and
  pricer; --check compares pricer rc vs master rc for every returned column: max 1e-9.
  s4 root: full SRC 90 cuts 458.0 in 160 s; lm-SRC 150 cuts 453.4 in 32 s; lm-SRC 300 cuts 456.1 in 68 s.
  Default for later runs: lm on, 300 cuts (faster nodes for B&P).
* Validation (write disabled) on a far-home instance with a proven optimum: control_yugovostok_mix1_all root LP =
  optimum 335.6453 (tree closed at the root, LB never above the optimum). More validation queued (scarce_yugovostok_5,
  yugovostok_s5).
* s8 root (full SRC 150): 536.16 -> 564.55 (UB 623.86). real_yugovostok_2 root 260.74 (old 259.68): cost-driven.
* 10:50 k(S) sub-LPs are seeded with the current LP support columns restricted to S (valid extra columns; big sets
  took 10-30 s each on real_yugovostok_8 and exhausted the k budget after 12 sets).
* More results (720 s): s8 536.16 -> 566.25; real_yugovostok_2 259.68 -> 261.02 (cost-driven);
  real_yugovostok_8 364.78 -> 378.97 (root 367.37, only 1 fleet cut; 81 fast lm nodes, vehicle-level branches move
  children up to 408); s9 root ~519.9, B&P 526.8 at 550 s (old 475.66, UB 541.96).
* s9 (720 s): 475.66 -> 527.41 (root 518.31, 71 nodes; gap 13.9% -> 2.8%).
* Strong branching lite (--sb N: evaluate the default candidate + fractional types + best vehicle-level sets by
  solving both children, children get their LP bounds): s9 420 s -> LB 524.92 with 8 nodes vs ~525 without SB at the
  same time: no clear gain, not used in the final runs.
* walk_yugovostok_8 (720 s): 316.47 -> 376.22 (root 333.45, 250 fast lm nodes, arc branching after the fleet-row cap;
  UB 380.45, gap 1.1%) -> long run (2700 s) scheduled at the end.
* 11:15 root enumeration option (--enum 0.03): if UB - rootLP <= 3% UB, enumerate all elementary routes with
  reduced cost <= UB - LP w.r.t. the root duals (fleet-cut bonuses and lm-SRC handled in ENUM, completion bounds
  corrected by the positive fleet duals) and solve the exact pool MIP (cg_pool2.Pool): proves optimality if complete.
* Node enumeration option (--nenum g): at a converged node with UB - z <= g*UB, enumerate routes with rc <= UB - z
  (node duals; forbidden arcs respected in ENUM; bin/cgc_price3 = ENUM label cap 25*maxroutes) and solve the pool
  MIP with the node's type bounds (fleet-row branching bounds dropped -> relaxation, still a valid node bound; any
  pool MIP solution is globally feasible -> incumbent). Closed statuses optimal/infeasible/lp_closed prune the node.
* Validation scarce_yugovostok_5 (proven opt 293.853): root 287.99, B&P 300 s LB 288.69 <= opt; rc check 1e-9.
  Root enumeration at a 2.0% gap (5.9 km) was incomplete after 130 s (labels explode: dominance only on equal sets)
  -> thresholds lowered: root 1%, node 0.5-0.6%.
* tight_yugovostok_5: first run closed the tree (root 350.09, 19 nodes, LB = UB = 351.008).
* 11:20 VALIDITY BUG found and fixed: a node whose CG hit the node time limit (bound inherited) was still closed when
  the restricted-master solution happened to be integral. That is not a proof (the node LP is not converged), and a
  wrongly closed subtree can also inflate the min-over-open-nodes LB. Fix: integral + unconverged -> node bound goes
  to the global LB, no closure. All B&P values written by the pre-fix code were reverted to the (valid) converged root
  LP values (s1 487.93, s6 488.61, s8 564.55, s9 518.31, mix1_inf 502.40, real_2 260.74, real_8 367.37, scarce_2
  450.53, tight_2 347.51, walk_8 333.45); tight_5 reverted to the old value. Everything is re-run with the fixed code
  (cgc_final1.sh / cgc_final2.sh, logs *_bp_fix.log); real_2 and scarce_2 keep the root value (no time).
* Fixed-code runs: large_yugovostok_8 337.77 -> 371.60 (root 370.01, 50 nodes; UB 605.73, gap still 63%);
  peak_yugovostok_2: root CG did not converge within 470 s (pricing slow) -> no bound written.
* 11:40 tight_yugovostok_5 PROVEN OPTIMAL with the fixed code: root LP (exact pricing, 16 fleet cuts + 30 SRC) =
  351.008 = UB, 12 s with the cached k values (log tight_yugovostok_5_bp_fix2.log). First fixed rerun wasted time in
  a root enumeration at gap 0 (ENUM bounds ignore SRC -> label explosion, memory ran low) -> killed; root/node
  enumeration now skipped when the gap is <= 1e-6, root enumeration disabled by default, node-enum label cap 5M.
* scarce_yugovostok_8 (fixed code, 720 s): 287.90 (old bound was for K=10) -> 548.65 (root 544.34, 55 nodes; UB 715.23).
* Node enumeration in practice (walk_8, 300 lm-SRC): incomplete even at a 0.03 km gap (label cap 5M): the ENUM
  completion bounds (F, G, H) ignore the SRC duals, which carry most of the dual value after 300 cuts -> useless here.
  Would need SRC-aware bounds or enumeration before adding SRC.
* Fixed-code reruns: walk_8 1200 s -> 376.92 (313 nodes, gap 0.94%); s9 900 s -> 527.53 (70 nodes, same as pre-fix).
* Fixed-code reruns: s6 -> 511.94 (root 489.09, 136 nodes; gap 4.9%); s1 -> 500.97 (root 488.70, 18 nodes).
* Fixed-code reruns: control mix1_inf -> 506.88 (root 503.56); tight_yugovostok_2 -> 362.73 (root 347.57, 99 nodes; gap 5.0%).
* Fixed-code reruns: real_yugovostok_8 -> 450.18 (root now 439.79 vs 367.37 before: the seeded k(S) sub-LPs allowed
  many more k computations -> many more violated fleet cuts; gap 38% -> 12%); s8 -> 573.96 (142 nodes).
* Last runs: s2 -> 566.67 (74 nodes); real_yugovostok_5 (420 s) -> 389.10 (gap 3.3%); large_yugovostok_2 (420 s) ->
  381.16; scarce_yugovostok_2 (420 s) -> 457.48.

## Final summary (13:00) — cg_finalize (no inconsistency), best.py, final_report run on all three sets
| instance | best | old lb | new lb | gap old -> new |
|---|---|---|---|---|
| tight_yugovostok_5 | 351.008 | 322.99 | 351.008 | 8.7% -> PROVEN (root LP with fleet cuts = UB) |
| walk_yugovostok_8 | 380.45 | 316.47 | 376.92 | 20.2% -> 0.9% |
| yugovostok_s9 | 541.96 | 475.66 | 527.53 | 13.9% -> 2.7% |
| real_yugovostok_5 | 401.98 | 359.80 | 389.10 | 11.7% -> 3.3% |
| yugovostok_s6 | 537.15 | 451.35 | 511.94 | 19.0% -> 4.9% |
| tight_yugovostok_2 | 380.71 | 310.64 | 362.73 | 22.6% -> 5.0% |
| yugovostok_s1 | 537.47 | 445.41 | 500.97 | 20.7% -> 7.3% |
| yugovostok_s2 | 609.08 | 534.80 | 566.67 | 13.9% -> 7.5% |
| yugovostok_s8 | 623.86 | 536.16 | 573.96 | 16.4% -> 8.7% |
| real_yugovostok_8 | 504.28 | 364.78 | 450.18 | 38.2% -> 12.0% |
| control_yugovostok_mix1_inf | 575.22 | 382.83 | 506.88 | 50.3% -> 13.5% |
| scarce_yugovostok_8 | 715.23 | 287.90 | 548.65 | 148% -> 30.4% |
| large_yugovostok_2 | 452.55 | 364.54 | 381.16 | 24.1% -> 18.7% |
| large_yugovostok_8 | 605.73 | 337.77 | 371.60 | 79.3% -> 63.0% |
| scarce_yugovostok_2 | 541.25 | 449.66 | 457.48 | 20.4% -> 18.3% |
| real_yugovostok_2 | 306.02 | 259.68 | 260.74 | 17.8% -> 17.4% |
Unchanged: yugovostok_s3, s4 (cost-driven, fleet cuts do not bite, slow nodes), peak_yugovostok_2 (root CG did not
converge in 470 s), allday_* (not run: labeling explodes on 640-min windows).
What mattered: rounded fleet cuts k(S) on time-restricted / skill-restricted cluster sets (e.g. "Moscow orders starting
before 240 need 8 routes"), vehicle-level branching (routes of a far-home type entering a cluster), lm-SRC (fast nodes).
What failed: CG rank-1 cuts from vehicle-min duals, SRC inside k(S) LPs, full-instance type-restricted k, fleet
subset k, strong branching (no gain), root/node route enumeration (ENUM bounds ignore SRC -> label explosion).
