# Lagrangian / dual-driven column generation, fast (1-3 s) — agent lagcg

Files: `solvers/lag_util.hpp` (toolkit), `solvers/s80_lagcg.cpp` (solver). Harness: `evalcfg.py`, WORKERS=2.

## Components (lag_util.hpp)
* `Types` — vehicles grouped by (start, mode, mask); `Pool` — columns (type, order set) dedup by set, min km kept.
* `HPricer` — heuristic labeling: forward in time, per-node label cap LC, dominance (time, cost) ignoring memory,
  elementary via visited bitset, granular successor lists (NB nearest by km + 0.02*wait). ~1 ms per type.
* `XPricer` — exact ng-route labeling (ng=8) with a completion bound F computed on time windows only in 2-min
  buckets (optimistic floor => valid), label cap -> fallback to the completion bound. Returns valid min rc per type
  and elementary negative columns.
* `LP` — own revised simplex for the restricted master (dense B^-1, m = #orders + #types + 1 <= ~150, Harris ratio
  test, RHS perturbation, partial pricing, Gauss-Jordan refactor every 60 pivots). ~20 us / pivot.
* `Lagr` — subgradient on the pool Lagrangian (type counts kept in the subproblem, covering rows + K row dualised).
* `CapKm` — copy of l2::KmSearch that writes every accepted route into the pool (column capture).

## Experiments
* 00:25 v0: s33 fleet phase (0.6T) -> CapKm (captures ~50k route adds, ~10k distinct cols in 1 s) -> subgradient
  Lagrangian on pool + heuristic pricing -> rc-fixing -> DFS. Subgradient converges far too slowly: control_vostok_car_all
  pool Lagrangian 167.6 after ~100 its, 184.8 after 2000 its (Polyak/deflection/CFT window rules all tried), while
  the pool LP optimum is >= 187.2. => subgradient alone useless for rc-fixing at this time scale; wrote an LP.
* 00:30 LP master: starting from the incumbent basis is massively degenerate (LP stuck at UB for 1000+ pivots);
  artificial start with bigM=100 better. Full Dantzig pricing over 5k cols = 0.3 s -> partial pricing (segments of 400).
* CG convergence (3 s runs, CG phase ~0.4 s): some instances converge in 0.08-0.1 s (control_yugocentr_car_all
  LP=171.155 = valid bound, vostok_pt_inf 151.907), others tail off (hundreds of rounds, heuristic pricing keeps
  finding a few columns). Wentges smoothing (alpha 0.5) no clear gain.
* Price-and-dive (fix x>=0.999 or max-x column, re-solve residual master with a few pricing rounds): poor —
  188.85 vs UB 188.01 on vostok_car_all, 168-172 vs 160.5 on vostok_pt_inf (LP gap 2% without cuts; greedy fixing
  lands in bad corners). DFS over columns with rc <= UB-LP sometimes improves (vostok_pt_inf 158.28 -> 157.22, then
  CapKm from it reached the proven optimum 155.077).
* NOTE: machine load average 40-55 during all runs (8 cores shared) -> absolute times are ~3x slower than nominal;
  always compare paired runs. Scratchpad files of other agents collide -> my helpers live in scratchpad/lagcg/.
* 00:38 s80a (1 s): LAGF=0.6 LAGD=0.3 (CG phase at 0.84-0.96 T) vs s33 same load: control km 4.88 vs 4.66%,
  road 5.09 vs 3.81%, gen 6.27 vs 6.36% -> no gain (per-instance 20/24, 25/30, 66/60 better/worse). At 1 s the CG
  phase gets ~50-100 ms of CPU: LP not converged, DFS over pool (rc <= UB-LP) finds nothing.
* LP rounding + regret repair (fleet cap K): leaves 1-5 orders unassigned at the minimal fleet (e.g. mix2).
* Fleet LB: CG with cost 1 per route (beta=0). Lagrangian bound sum pi + sum_t n_t min(0,minrc_t) is weak before
  convergence; added the Farley bound  sum max(pi,0) / max_route sum max(pi,0)  (valid for the covering relaxation)
  -> fleet proven in ~0.1 s on several control instances. km bound: K slots allocated greedily to the most negative
  per-type min rc (tighter than sum_t n_t min(0,.) when V > K).
* 00:47 s80b (1 s): fleet LB after the greedy (0.15 of fleet window) used to stop the fleet phase early (SA elimination
  + FleetRR skipped once used <= LB) -> more km time. Result vs s33: vehicles +2/+4/+3 worse (fleet window lost to
  the bound computation when LB is not tight), km -0.6..+0.5%. => not worth it at 1 s under this load.
* 00:53 s81_lagbound (bounds only, incumbent = best known, 1 s: 0.3 s fleet CG + 0.7 s km CG), all 124 instances:
  fleet LB = best fleet on 14/24 control, 16/30 road, 29/70 gen (never above best -> no invalid bound);
  km LB gap to best median 5.4% / 6.1% / 12.9% (offline lb/: 0% / 1.3% / 1.25%). ng=8; ng=12/16 explodes labeling.
* 00:53 s80c (3 s; CG phase at 0.84-0.96 T, DFS+dive, no fleet LB) vs s33 3 s: control better/worse 24/15 (1 vehicle
  less), km at equal fleet +0.85%; road 29/27 +0.13%; gen 60/57 +0.33% -> parity.
* 01:05 s80d (3 s) = s80c + (a) DFS first fixes LP columns with x>=0.99 and searches the residual, (b) CapKm after the
  CG phase uses a dual-informed worst-removal ruin (p=0.1: remove orders with largest detour - pi_k), (c) CapKm restart
  from the LP rounding for half of the remaining time when rounding km < incumbent km. vs s33 3 s:
  control 23/17 better/worse, 1 veh less, km -0.36%; road 32/20, km -0.22%; gen 66/55, km +0.43% (outliers).
  evalcfg summary: control km 2.44% (s33 2.97%), road 2.29% (2.58%), gen 3.95% (4.00%); extra veh 1.0/0.5/4.0 (s33 1.5/0.5/4.0);
  =best 9.0/9.0/16.5 (s33 6.5/7.0/13.5).
* 01:18 s81_lagbound at 3 s (0.9 s fleet CG with early stop once ceil(L) >= K, rest km CG), incumbent = best known:
  fleet LB = best fleet on 17/24 control, 26/30 road, 38/70 gen (81/124; offline CG+B&P: 14/14, 30/30, 65/70 of
  those with lb files; the cheap load/clique bound proves far fewer). No fleet LB ever exceeds the best fleet.
  km LB (at K = incumbent fleet) gap to best known: median 4.5% / 6.1% / 11.2%; within 5% of best on 41/124,
  within 2% on 11/124 (1 s: 38 and 9). The Lagrangian bound is within ~0.1-1% of the restricted LP value in
  most cases, i.e. the weakness is the plain set-partitioning LP (no subset-row cuts, ng=8 relaxation), not dual
  convergence: e.g. road vostok_s0 LP 169.3 vs best 187.4; yugovostok_s* (pt+car mixed fleets) LP gaps 25-30%.
  Largest ng (12/16) blows up exact labeling (6 s) -> exact pricing now has a deadline (falls back to the
  completion bound, still valid).
* 01:21 s80e (3 s) = s80d with the CG phase earlier (LAGF=0.3: CG at 0.72-0.84 T, more CapKm afterwards): worse than
  s80d (better/worse vs s80d: 19/17, 21/31, 53/67) and ~parity with s33 -> keep LAGF=0.6, LAGD=0.3 (now defaults).
* 01:28 s80d at 1 s vs two s33 1 s baselines (c, d): control better/worse 25/15 and 28/13 (km at equal fleet -0.7/-1.2%),
  road 30/26 and 34/23 (3 vehicles fewer, km -0.7/-0.25%), gen 77/54 and 63/64 (km +0.1/+0.3%).
  evalcfg summary s80d 1 s: control km 4.23% (s33 5.07%), road 3.87% + 0.5 extra veh (s33 4.53% + 2.0), gen 5.66% (5.97%).
* 01:32 s80d 3 s replicate vs s33 3 s baselines a, b: control 24/13, 32/11 (km -0.6/-1.6%) — consistent gain;
  road 22/28, 27/28 (parity; the first run's 32/20 was noise); gen 55/63, 56/62 (km +0.6/+0.1%).
  Per family over all 3 s pairs: control_mix1 30/14 (km -2.5%), control_car 28/7, scarce 41/21, peak 44/31,
  road_vostok 42/27; losers: large (N>=104, 16 veh) 30/50 (km +1.4%), tight 21/36, real 32/36.
  At 1 s large is 12/28 (km +2.4%) -> CG phase too expensive for N>100 (labeling/LP ~N^2, eats CapKm iterations).
  => s80f: skip the CG phase when N > 100 (CGMAXN).
* 01:44 s80f (CG phase skipped for N>100) at 1 s vs s33 c/d: control 25/15, 23/17; gen 63/62, 66/62; road 29/28, 25/33.
  s80f vs s80d at 1 s on ROAD (identical code path for N<=66!): 25/33, +4 vehicles, km +1.5% -> the 1 s evaluation
  noise under this load is as large as the effects; only the control-set gain is reproducible at 1 s.
* 01:44 third s33 3 s baseline (c): s80d/s80d_r vs c: control 23/17, 26/11 (km -1.2/-1.4%), road 27/25, 18/34, gen 66/52, 61/59.
* 01:53 s80f (3 s) vs s33 a/b/c: control 21/17, 28/10, 23/17; gen 62/55, 68/54, 74/51 (skipping CG for N>100 fixes
  the 'large' family); road 25/29, 28/28, 23/32.
* 01:54 s80g (3 s) = s80f + fleet proof mid fleet-phase (FLB2=0.3 of the SA elimination window, budget 0.15 of the
  fleet window, early exit once ceil(LB) >= current fleet; if proven, SA elimination and FleetRR are skipped and the
  km phase starts early). vs s33 a/b/c: control 25/10, 31/9, 28/10 (km -1.1..+0.5%), gen 67/51, 68/50, 67/50,
  road 30/24, 34/22, 27/23 (+1 vehicle on one run each: LB time lost when not tight). Best version -> defaults.
  evalcfg summary: control km 3.31% (+1.0 veh), road 2.44% (+1.0), gen 3.90% (+4.0), =best 11.5/7.5/20.5.
* 01:57 3-SRC cuts in the bound tool: separation on the LP solution (pair weights + triple bitsets), <=3 cuts per
  order, master rebuilt with cut rows, cut-aware exact ng labeling (parity bits + Jepsen dominance gap, completion
  bound ignores cuts = still valid), Lagrangian bound adds sum sigma_c. Two LP bugs found on the way: (1) the RHS
  perturbation made the incumbent crash basis slightly infeasible (artificial = delta_r - delta_pivot < 0) -> simplex
  cycled 100k pivots with rising objective; fixed by choosing the pivot row of each incumbent route with the smallest
  perturbation and larger perturbation on <= rows; (2) too large <= perturbation (1e-3*K) made the LP visibly looser
  than the Lagrangian bound -> 1e-4 scale.
  control_vostok_car_all km LB 185.34 -> 186.19 (opt 187.98), control_vostok_pt_inf 150.05 -> 153.24 (opt 155.08).
* 02:01 s81_lagbound with cuts (NCUT=30 per round, CUTF=0.5: first half of the km budget plain CG, then rounds of
  separation + CG), incumbent = best known, all 124 instances, no bound ever above best / proven optima:
  | budget | fleet LB = best fleet | km LB within 1% / 2% / 5% of best | no km bound | median km gap ctrl/road/gen |
  | 1 s, no cuts | 59 | 1 / 9 / 38 | 30 | 5.4 / 6.1 / 12.9% |
  | 1 s, cuts    | 61 | 7 / 22 / 41 | 2 | 5.5 / 6.2 / 14.1% |
  | 3 s, no cuts | 81 | 1 / 11 / 41 | 27 | 4.5 / 6.1 / 11.2% |
  | 3 s, cuts    | 89 | 15 / 35 / 57 | 1 | 2.0 / 4.7 / 8.4% |
  ("no km bound" = no exact pricing round finished; the crash-basis fix also removed most of those.)
  Offline (minutes, Python+HiGHS, B&P): median 0 / 1.3 / 1.25%. Remaining gap: allday (640-min windows: exact labeling
  hits the cap -> weak completion-bound fallback), yugovostok pt/car mixed fleets (LP gaps 20-30%).
* 02:08 FINAL s80 (= s80g defaults + LP crash/perturbation fixes) at 1 s vs s33 1 s baselines c/d/e:
  control 26/17, 23/20, 29/15 (km at equal fleet -0.9/-0.4/-0.3%); gen 75/50, 77/51, 62/65 (km -0.4/-0.5/-0.1%,
  2-3 vehicles fewer vs c/d); road 29/28, 28/28, 22/33 (parity). Totals: control 78/52, gen 214/166, road 79/89.
* 02:14 FINAL s80 at 3 s vs s33 3 s baselines a/b/c/d: control 26/11, 31/11, 24/14, 22/17; gen 73/51, 66/48, 74/48,
  71/52; road 31/18, 35/19, 27/22, 23/29. Totals: control 103/53, gen 284/199, road 116/88.
  evalcfg summary s80 3 s: control km 3.53% +1.0 veh (s33: 2.97/4.48/4.34/2.20%, +1.5/1.0/1.5/1.0), road 2.45% +0.5
  (s33 2.58/2.64/2.39/2.01%), gen 3.72% +4.0 (s33 4.00/4.33/4.14/4.18%), =best 11/8/21.5 (s33 ~6-11 / 6-7 / 12.5-14).
  s80 prints "LAGLB fleet=<LB> km=<LB or -1> K=<K>" on stderr (fleet LB when the proof ran).
* 02:21 s80h (3 s, LAGF=0.7 LAGD=0.2, shorter/later CG): totals vs 4 baselines 102/53, 287/187, 116/93 = same as the
  final (103/53, 284/199, 116/88) -> keep defaults. Sanity: s33 run a vs b/c/d gives 56/59, 171/179, 77/75 (fair test).
  Version history totals vs the 4 baselines: s80d 91/71, 253/219, 118/94; s80f (+skip N>100) 91/66, 260/222, 98/120;
  s80g/final (+fleet proof) 107/43, 267/195, 118/96 and 103/53, 284/199, 116/88.
* 02:27 ABLATION s80nocg (3 s; fleet proof + CapKm/dual ruin, CG phase disabled, CGMAXN=0): totals vs the 4 s33
  baselines control 100/50, gen 248/225, road 140/64. Direct final vs nocg: control 15/18, gen 66/50, road 19/33.
  => the reproducible gain on control/road comes from the DUAL side (Lagrangian/Farley fleet proof -> fleet phase
  stopped early -> ~0.5-1 s more km search); the CG primal (SP/DFS/rounding) helps on gen but hurts road_yugovostok
  (13/41: mixed pt/car fleets, LP gaps 25-30%, CG time wasted) and road_yugocentr (18/32).
* 02:34 s80gap = final + GAPSKIP=0.08 (after the root CG, if (UB-LP)/UB > 8% skip rounding/dive/DFS and give the time
  back to CapKm). Totals vs 4 baselines: control 92/59, gen 331/146, road 117/85; vs final 17/15, 58/55, 24/26;
  vs nocg 11/21, 82/37, 22/28. evalcfg summary: control 2.37% (+1.0 veh), road 2.06% (+0.5), gen 3.34% (+4.0),
  =best 9.5/9.5/22.5 (s33 3 s runs: 2.2-4.5%, 2.0-2.6%, 4.0-4.3%, =best 6-11/6-7/12.5-14). -> GAPSKIP=0.08 default.
* 02:40 FINAL (GAPSKIP default) at 1 s vs s33 1 s baselines c/d/e: totals control 90/41, gen 219/166, road 99/64
  (s33 c vs d/e: 36/49, 130/124, 57/55). evalcfg summary: control km 4.40% +2.0 veh (s33 5.61/5.07/4.91%, +2.0),
  road 3.78% +2.0 (s33 4.20/4.53/3.84%, +2.0/2.0/2.5), gen 5.58% +5.5 (s33 5.71/5.97/5.69%, +6.0/6.5/4.5),
  =best 6/5/14 (s33 2.5-5 / 3-4.5 / 7.5-9).

## Final state
* `bin/s80_lagcg <inst> <tl> <seed>` — hybrid; defaults = final config (FLB2=0.3, FLBT=0.15, LAGF=0.6, LAGD=0.3,
  CGMAXN=100, GAPSKIP=0.08, PDUAL=0.1, RRF=0.5, FIXINT=0.99). stderr: `LAGLB fleet=.. km=.. K=..`.
* `INIT=best/<set>/<inst>.out NCUT=30 XEVERY=10 bin/s81_lagbound <inst> <tl>` — fast valid bounds (stdout `LB ...`).
* What did not work: subgradient/volume-style Lagrangian without an LP (far too slow to converge: 1.5% short after
  2000 iterations); price-and-dive (greedy fixing lands in bad corners, LP gaps 2-8% without cuts); exact set
  partitioning DFS at the minimal fleet (exact cover with <= K columns is a needle in a haystack; succeeds mostly after
  fixing near-integral LP columns); LP rounding + regret repair (1-5 orders left at the tight fleet); CG phase on
  mixed pt/car fleets (LP gaps 25-30%) and N>100; computing the fleet LB before the fleet phase at 1 s (loses vehicles).
* 02:44 ablation at 1 s: s80nocg (fleet proof only) totals vs s33 c/d/e: control 85/40, gen 208/168, road 76/93;
  final vs nocg 21/20, 67/61, 26/25 -> at 1 s the CG primal is roughly neutral, the fleet proof carries the gain.
* 02:45 FINAL replicate at 3 s: totals vs s33 a/b/c/d: control 104/47, gen 303/188, road 112/90 (first final-config run
  = s80gap: 92/59, 331/146, 117/85). evalcfg summary: control 3.55% (+1.0 veh), road 1.78% (+0.5), gen 3.59% (+4.0),
  =best 10.5/8/22 vs s33 3 s runs 2.2-4.5% / 2.0-2.6% / 4.0-4.3%, =best 6-11 / 6-7 / 12.5-14.
