# CG / exact lower bounds log (agent: cg)

Files: `solvers/cg_price.cpp` -> `bin/cg_price` (pricing server), `cg_master.py` (CG master, HiGHS LP),
`cg_pool.py` (exact solve over complete enumerated pool: LP + 3-SRC cuts + rc-fixing + MIP), `cg_batch.py` (runner).
Outputs: `lb/<dir>/<inst>.json`, improved solutions `results/<dir>/cg/<inst>.out`, per-instance run logs `results/<dir>/cg/<inst>.log`.

## Method
* Vehicles grouped into types (start, mode, skillmask) with counts.
* Master (set partitioning LP, HiGHS): order rows =1 (with y_k unserved slack), type rows <= count,
  total vehicles row <= K (stage 3), penalty row sum P_k y_k <= Pbest (stages 2,3).
  Stage 1 (only if best pen>0): min penalty. Stage 2: min #routes. Stage 3: min km with <= Kbest routes.
* Pricing: ng-route labeling (ng=10 nearest time-compatible orders), labels (node, finish time, cost, ng-mem),
  processed in time order; pruning by a completion bound F[k][minute] (non-elementary backward DP over integer
  minutes, optimistic rounding => valid). Level 1 heuristic (dominance ignoring memory) then level 2 exact.
  Bound valid: LP optimum with exact pricing proving no negative column; otherwise Lagrangian bound
  sum(pi)+lam*K+sig*P + sum_t count_t*min(0, minrc_t) with minrc_t from exact labeling or the F-bound.
* Exactness: enumerate ALL elementary routes with reduced cost <= UB - LP (forward labeling with same-set
  dominance, pruned by backward ng-labeling bound G(k, start) and forward optimistic DP H), dedupe by
  (type, order-set) keeping min km. Over this complete pool: LP + 3-subset-row cuts separated on the pool,
  reduced cost fixing, then HiGHS MIP -> proven optimum (or valid bound min(UB, pool LP/MIP dual bound)).

## Experiments
* 02:40 control_vostok_car_all: fleet LP 5.774 -> lb 6 = best (proven). km ng-LP 185.28 vs UB 187.955 (1.4%).
  First enumeration with only the non-elementary F bound: 40M labels, incomplete (F too weak: cycles collect pi).
  Added backward ng-labeling bound G + forward DP H: 1929 backward labels, 183k fwd labels, 86,635 routes in 0.3 s.
  Plain HiGHS MIP over 86k routes: dual bound only 185.95 after 300 s (bad).
  Pool LP + 3-SRC cuts: 185.28 -> 187.82 in 16 rounds, rc-fixing 86k -> 302 cols, MIP optimal 187.9552 = UB.
  => PROVEN OPTIMAL (0 pen, 6 veh, 187.955 km) in 33 s.
* 02:30 control batch (old dirs, no cuts in CG yet): proven optimal vostok_car_all (33 s), vostok_car_inf (100 s, 325k routes),
  vostok_pt_all (18 s; found 131.9367 < best 131.941 -> results/instances_control/cg), yugocentr_pt_all (15 s).
  yugocentr_car_*: ng-LP 171.20 vs UB 179.40 (4.6% gap) -> enumeration > 3M routes, incomplete. vostok_pt_inf,
  yugocentr_pt_inf: pool MIP slow (1.17M routes in pool for yugocentr_pt_inf).
* Cheap bounds (cg_cheap.py -> lb/<dir>/_cheap.json): load/clique vehicle bound is 1-4 vehicles below best on
  8/12 control instances (CG proves all 12 fleet sizes: ceil(LP)=best). Assignment km bound ~25-35% below best.
* 02:40 added 3-SRC cuts inside CG (pricing tracks cut parity bits; dominance c1 + sum_{s1=1,s2=0}|sig| <= c2;
  completion bounds ignore cuts = still valid). yugocentr_car_all: LP 171.20 -> 177.77 with 150 cuts (10 s),
  enumeration then 70k routes (gap 1.63), pool LP+cuts 178.257, but HiGHS MIP over 17.8k cols timed out (450 s).
  lb_km 178.257 vs UB 179.404 (0.64%).
* 02:45 road instances ready -> launched instances_control_road batch (2 workers, tl 600, pool-tl 500).
* 03:00 control_road so far: proven optimal vostok_car_inf, vostok_pt_all, yugocentr_car_all (MIP over 13.8k pool cols,
  402 s), yugocentr_pt_all, yugovostok_car_all (CG+cuts found 315.673 < best 318.757, LP = UB), yugovostok_car_inf
  (LP with 65 cuts = UB 322.713). vostok_car_all: new solution 187.98 (< 188.007) found & proven.
  Problem: vostok_pt_inf LP+3SRC 154.44 vs UB 158.87 (CG found 158.868 < 159.756) and 3-SRC exhausted on the pool
  (no violated cut), pool 105k cols -> HiGHS MIP hopeless. yugocentr_car_inf: 1.8M routes, pool LP rounds took
  minutes (building HiGHS model with 1.8M cols) -> killed.
* Fixes: (1) pool LP by sifting (working set 15k lowest-rc cols, price the rest by sparse A^T y, add negatives):
  140k-col pool LP in 2 s instead of 11 s. (2) MIP time capped at 60 s when >20k cols and gap >0.5%.
  (3) depth-1 branching on the vector of vehicles per type (sum = K when fleet is proven): each combo = CG with
  type rows fixed (artificial cols keep LP feasible -> still a relaxation), LB = min over combos; surviving combos
  get their own enumeration + pool MIP. Motivation: in *_inf instances only a few vehicles have skill 2.
* 03:15 type-branch on control_road vostok_pt_inf: 12 combos (types 8/2/1/1 vehicles), 9 pruned by LP; survivors
  enumerated + pooled: found 156.884 (< 159.756); other agents then found 155.0773 = my type-branch LB -> proven.
  control_road yugovostok_pt_inf (K=11 of 12, pt mode): LP 327.16 vs UB 355 -> 346.57 (lns); enumeration of the
  critical combo (type-7 vehicle unused) incomplete (gap 28 km, >3M routes). Hard instance.
* Fleet dominance (cg_dominance.py): X_inf fleets are injective restrictions (same start/mode, subset masks) of X_all
  with identical orders/matrices -> OPT(X_inf) >= OPT(X_all) for same K. control_road yugocentr_car_inf LB
  178.199 -> 179.1743 (= best 179.1745, proven); yugocentr_pt_inf 174.86 -> 176.392.
* 03:30 cg_bp.py: best-first branch-and-price (branch on vehicles-per-type row bounds, then arc flow forbid/force;
  pricing binary built with -DBP reads forbidden arcs/starts/ends; global 3-SRC cuts; artificial cols keep nodes
  feasible). control_road yugocentr_pt_inf: 9 nodes, 10 s, found 176.3922 and exhausted tree -> PROVEN OPTIMAL
  (best was 178.207). yugovostok_pt_inf: LB 327.2 -> 331 after 66 nodes (running, 25 min budget).
* instances_road batch: type-branch combos explode for 9-type fleets (vostok_s0: >100 combos, 300 s wasted) ->
  capped at 40 combos. vostok_s0 road: LP 173.5 vs best 200.7 (13.5%!) -- heterogeneous fleets, weak UB or LP.
* 03:55 control_road yugovostok_pt_inf B&P 1500 s: 1706 nodes, LB 327.16 -> 335.58 (gap 5.9% -> 3.3%), tree not
  exhausted, no better incumbent (best 346.57 from lns). => control_road: 11/12 PROVEN OPTIMAL, 12/12 fleet proven.
* instances_road (heterogeneous 9-11 types, mixed modes): fleet proven on all so far, but km gaps 1.4-15.7% after
  CG+3SRC (LP weak with many vehicle types / modes, and/or weak UB). Type-branch has hundreds of combos -> skipped;
  enumeration skipped when gap > 3%. Queued: B&P sweep (150 s/instance) on instances_road after the batch.
  vostok_s7 (pen 150): penalty LP 135 -> lb_pen 140 (not proven).
* Column MIP (price-and-branch) now warm-started with best known solution (HiGHS setSolution) - earlier it
  sometimes returned no solution at all within the limit.
* 04:30 gen_road batch split over 2 workers with claim files (results/<dir>/cg/<inst>.claim); limits tl 240, pool 60,
  type-branch <= 12 combos. "allday" instances (640-min windows) make labeling explode -> exact pricing incomplete,
  only Lagrangian bounds (allday_yugocentr_4: CG bound negative!) -> finalize now takes max(CG, assignment bound)
  and clamps at 0. Some lb_km computed for K=7 while best later reached K=6: still valid (fewer vehicles).
* 05:15 B&P sweep on instances_road (120 s each, smallest gap first): vostok_s6 (0.05%) and vostok_s4 (0.22%) proven
  optimal in ~6 s each (5-7 nodes).
* 06:05 STOP. B&P sweeps proved many more (road: vostok_s1,s2,s3,s5 + yugocentr_s0; gen: tight_vostok_9,
  scarce_vostok_9 (km part), walk_yugocentr_1 ...). Old control yugocentr_car_all proven by B&P (51 nodes, 46 s),
  car_inf via dominance. Killed walk_yugovostok_5 (gen_road) stuck in vehicle-proof enumeration (beta=0 enumeration
  is weak) -> wrote lb_used=8 (converged LP 7.9) manually, lb_km null.

## Final summary (06:05)
| dir | instances | fleet+pen proven | proven optimal | median km gap |
|---|---|---|---|---|
| instances_control_road | 12 | 12 | 11 | 0% (only yugovostok_pt_inf open: 335.58 vs 346.57 = 3.3%) |
| instances_road | 30 | 26 | 8 | 0.98% (11 inst >3%, mostly yugovostok_s*) |
| instances_gen_road | 70 | 63 | 24 | 0.88% (24 inst >3%, allday/large/yugovostok) |
| instances_control (old) | 12 | 12 | 10 | yugovostok_pt_all 7.8%, pt_inf 11.0% open |
Not done: instances/ and instances_gen/ (old matrices) - only cheap bounds (lb/<dir>/_cheap.json).
Cheap bounds vs CG (control_road): load/clique fleet bound is 1-3 vehicles below best on 9/12 (CG proves all 12);
assignment km bound is 25-45% below best (CG: 0% on 11/12).
Limitations: pricing labels explode on 640-min windows (allday*) -> only Lagrangian/assignment bounds there;
3-SRC is the only cut family; B&P is best-first without strong branching; penalty stage has no integer proof
(only LP ceil to multiples of 10).
