# SA improvement log (agent "sa")

Working file: `solvers/s20_sa2.cpp` (copy of s10_sa + changes). Private runner/compare scripts live in the
agent scratchpad (run.py: runs a binary over instances x seeds with WORKERS=2, validates with validate.py;
cmp.py: per-instance mean comparison, Δused and Δkm on instances with equal mean vehicles).
All numbers: WORKERS=2 (machine shared by 4 agents, so timings are noisy).

## 0. Baseline s10_sa, 1 s
- control (12 inst x 3 seeds): avg used 87.67, avg km 2623.0; best-of-3 87 / 2609.8.
- instances (30 x 2): pen 650 (4 infeasible instances), used 215.00, km 9021.9.
- gen (70 x 2): pen 445, used 535.50, km 16572.9.
- 10 s single seed on control: 87 / 2619 — no better than best of 1 s runs, i.e. the SA gets stuck, extra time is wasted.

Phase timing on control (SA_STAT): greedy already gives the final vehicle count on almost every control instance;
the route-elimination phase makes only 2-5 attempts (1M iterations each) and fails, but uses 55% of the time
(until fElim=0.6), leaving ~0.37 s for km.

## 1. Finding: bimodal km on yugovostok (vehicle-set trap)
control_yugovostok_car_all / car_inf: the same seed gives either ~323 km or ~495-555 km (timing-dependent).
Cause: heterogeneous fleet with far "home" starts. In the bad basin a depot/other-town vehicle drives to a far town
while the vehicle whose home is in that town stays unused. Fixing it needs "open vehicle e + close route b" at once;
the km phase has a fixed vehicle count and no move can do that.
- Higher km temperature (T0 20/50): helps car_all, not car_inf (still 495 in 1/3-2/3 runs).
- Ruin&recreate move (pRuin=1%): (0) k + nearest neighbours; (1) remove a small route b, allow opening one empty
  vehicle e (plus orders nearest to e's start). Helps km a bit but does not remove the trap.
- Allow opening in km phase (Wv=100): the SA finds 9 vehicles / 275 km and never comes back to 8 (Wv too small
  vs the km saved); returns a trapped best.
- **Start the SA from a greedy with cheap vehicle opening (openCost=20)** instead of the vehicle-minimal
  multiGreedy best: more vehicles at the start, the elimination phase removes the *small* routes, the far-town home
  vehicle keeps its big route. Control 1 s x3: no traps, avg 87.00 veh (-0.67), km -0.5% on equal-vehicle
  instances; yugovostok_pt_inf now 11 vehicles in 3/3 runs (was 12 in 2/3). openCost=60 does not work (traps).
- Same start (openCost=20) on the other sets: gen 1 s x2: Δpen +10, Δused 0, km +0.58% (34 better / 34 worse);
  instances: Δused +0.50, km +1.5%. Not a universal fix -> not kept as default. Per-instance deltas on gen show
  the same trap on many yugovostok instances (allday_yugovostok_5: 428 -> 297, allday_yugovostok_8: 411 -> 527):
  the km result of a single 1 s run has a spread of up to +-100 km depending on the vehicle set chosen early.

## 2. Ruin & recreate move alone (pRuin=1%), gen 1 s x2
Δpen +5, km +1.10% (31/37). The move is ~50x more expensive than a normal move, iteration rate drops from ~5.9 to
~3.7 M/s; the diversification does not pay for the lost iterations at 1 s. Not kept (pRuin=0 by default later).

## 3. Measurement problem: machine load
Load average went up to ~100 (other agents), wall-clock 1 s runs got a fraction of a core: results of runs made at
different times are not comparable (e.g. a plain re-run of the s10 logic was +5.8% km on control).
Fix: `STimer` in `solvers/sa2_util.hpp` — with `SA_CPU=1` the solver measures process CPU time instead of wall time.
All comparisons below are CPU-timed and the baseline is s22 with the new features switched off
(`SA_PRUIN=0 SA_FSE=0 SA_REASSIGN=0`, identical logic to s10). (Also found: zsh does not word-split `$cfg`, so a
few early multi-variable env tests only applied the first variable; they were re-done.)

## 4. Split+eliminate (SE) and Hungarian vehicle reassignment in the km phase (s22)
- SE cycle (second half of the km phase, fSE=0.5): from the best solution move the tail of some route (from
  position i, i=0 = whole route) to an empty vehicle, choosing the (route, i, empty vehicle) with best Δkm + noise;
  then eliminate a small route (1/len^2) with the pool-SA of phase 2 (300k iterations) and quench km (300k it.).
  This changes the *set* of vehicles at constant count — exactly the missing move in the yugovostok trap.
- Hungarian reassignment of whole routes to vehicles (routes x vehicles, cost = km of the route driven by that
  vehicle, inf if infeasible): at km-phase start, after each successful SE elimination and on the final best.
- gen 1 s x2, CPU-timed: vs baseline Δpen -5, Δused -0.50, km -0.30% on equal-vehicle instances, 37 better / 30 worse.

## 5. Time-warp ("squeeze") in the route-elimination phase
Copied sa_util into a private namespace `sa2` inside sa2_util.hpp and added Vidal-style time-warp data to routes
(prefix TW, suffix (L, TW) concatenation), `evalCandTW` (O(changed part) like evalCand), `S.tw`.
In the elimination phase (twMode) late arrivals are allowed with penalty λ·TW (minutes) in every move and in the
pool insertions; success = pool empty AND TW = 0; checkBest ignores states with TW > 0.
- First version (λ0=1, λ×1.2 per 20k it., pressure β=0.5, Wv=100): collapsed to 1 route (the −β·len² pressure
  is unbounded when TW is cheap) / over-eliminated and pushed 10-36 orders to the pool. Fixed: β=0, Wv=0 in TW mode.
- Hard subset (11 gen instances where 1 s runs are above the best known vehicle count), 1 s CPU x4 seeds,
  vs s22 without TW (100.50 vehicles total):
  λ=20 fixed: +0.25; λ=50 fixed: +0.25; **λ0=5, +1 per 20k iterations while TW>0: -1.75 vehicles** (km +1.7% on
  the 4 instances with equal vehicle counts — expected, fewer vehicles).
- Workload lower bound LB = ceil(Σ(svc + min incoming travel)/720): tight on 4/12 control instances
  (vostok_car 6, yugocentr_car 5). When used == LB, the elimination phase is skipped (whole time to km).
- λ variants on the hard subset (1 s CPU x4, vs no-TW 100.50): λ0=5 +1/20k it. (default): -1.50 (re-run; first run
  -1.75); λ0=2 +0.5: 0.00; λ0=5 +2: -0.75; λ0=10 +1: +0.25; TW also inside SE cycles: -1.50 but km +4.9% -> off.
  The growth must be slow: λ reaching ~50 after 1M iterations is the sweet spot (TW cheap early = real squeeze,
  expensive late = repair).

## 6. SE + LB (no TW) on control / instances, 1 s CPU
- control x3: Δused -0.33, km -0.49% (8 better / 3 worse). SE alone: km +0.02% (7/4) — the gain on control is
  mostly from LB (4 instances get the full second to km) and pt_inf reaching 11.
- instances x2: Δused -0.50, **km -3.25%** (20 better / 8 worse).

## 7. Full-set check of TW (s23 with TW) — TW rejected for the 1 s mode
1 s CPU: gen x2: TW vs no-TW Δused **+1.00** (km -1.7%), control x3 +0.33, instances x2 +0.50 (vostok_s9 better,
yugovostok_s2/s8, control yugovostok_pt_all worse). Re-check on 12 hard gen instances with **8 seeds**:
TW -0.12 vehicles vs no-TW (noise), alternate TW/non-TW attempts +0.12. The earlier -1.5 on the hard subset was
noise (4 seeds, per-instance vehicle count flips between runs). Conclusion: at 1 s, squeeze-by-TW inside the SA
is not better than the pool-only SA; the per-instance vehicle count is dominated by luck in the few (2-5)
elimination attempts. Kept in code (SA_TW=1), default off.
Elimination attempt length (8 seeds, 12 hard): 300k it. +1.62 vehicles, 3M it. +0.50 -> default 1M is right.

Product config (s23 = SA2 + SE + Hungarian + LB, no TW), 1 s CPU vs baseline (s10 logic):
- control x3: Δused -0.33, km -0.49% (8/3)
- instances x2: Δused -0.50, km -3.25% (20/8)
- gen x2: Δused -1.50, km +0.32% on equal-vehicle instances (38/29)

## 8. Parameter sweeps of the km phase (1 s CPU, control x3 + instances x2) — nothing beats the defaults
Re-running the default config itself gives Δused +0.33/+0.50 and km -0.13%/+0.75% (noise floor).
fSE 0.3: ctl +1.9% km, ins -0.3%; fSE 0.7: ctl -0.2%, ins +1.8%; temperature scaled by km/N (1.5, 3):
ins +5.5..6.3% (bad); SE quench T0 1 / 4: within noise. Defaults kept (fSE=0.5, T0=5, T1=0.05, seT0=2).

## 9. Smarter choice of the route to eliminate — rejected
pickEasyRoute: trial greedy elimination of every route, pick min (leftover penalty + 0.05·len + 1.5·times tried
+ noise). 1 s CPU: hard12 x8 +0.88 vehicles, control x3 +0.67, instances x2 +1.50. The route that is easiest to
empty greedily is not the one whose removal the SA can repair; the random 1/len^2 choice (diversity) is better.

## 10. Long mode (offline), control, 10 s CPU x2 seeds
single s23 run: 87.00 veh / 2590.4 km; s21 multi-run (runLen 1.25 s, half of the runs restart from the best):
87 / 2589.1; runLen 2.5 s: **86.5 / best-of-2 86** (control_yugovostok_pt_inf: 10 vehicles, best known was 11);
fromBest=0: 87 / 2587.5; TW on: 87 / 2592.0. All other control instances give identical km in every 10 s
configuration (probably optimal or very close).
- 30 s CPU on the two control instances where vehicles can still drop (yugovostok_pt_all / pt_inf), 2-4 seeds:
  runLen 2.5 / 5 / 5+TW all give 10 / 11 vehicles; pt_inf 10 was reached only with the elimination-focused
  offline variant: half of the restarts from the best run with fElim=0.85, maxFails=1000 (1 seed of 4, runLen 5,
  10 veh / 410.7 km — saved in results/instances_control/sa/). The 86-vehicle 10 s result above could not be
  reproduced with the same seed (timing), and was not saved (runner saved only with a label; fixed: all later
  runs keep the per-instance best in results/<dir>/sa/).
- s21 defaults now: runLen = max(1, tl/6), fresh starts cycle {multiGreedy, openCost 20, multiGreedy, 0, 60},
  50% of runs restart from the best, half of those elimination-focused.
- Rejected: stop elimination after 4 consecutive fails (hard12 x8: +0.62 vehicles).

## 11. Road instances (final evaluation), 1 s CPU, s23 vs baseline (s10 logic in the same binary)
- control_road x3: 88.67 -> 88.33 vehicles (Δ -0.33), km -0.81% on equal-vehicle instances, 10 better / 2 worse.
- instances_road x2: Δused -0.50, km -3.59% (20/10).
- gen_road x2: Δpen -15 (fewer unserved), Δused 0, km +0.12% (37/31).

## 12. Long mode on control_road (s21, 30 s CPU x2 seeds)
87 vehicles / 2800.9 km best-of-2 (avg 2802.9). Equal to the best known (other agents' CG with lower bounds
proving optimality) on 9/12 instances in both seeds. Misses: yugocentr_pt_all/pt_inf 178.2 vs optimal 176.4
(homogeneous fleet, both seeds stop in the same local optimum — the optimum differs by a chain of 2-opt*/relocate
moves across 4 routes), yugovostok_pt_inf 352.7 vs best 346.6 (LB 327.2).
- instances_road, s21 30 s x1: 213 vehicles (= best known on every instance), km equal-or-better than the best
  known on 20/30, worse by 0.05-2.4% on 10.
- gen_road, s21 30 s x1: pen 430 / 533 veh / 16988.9 km (1 s s23: 465 / 537.5). Equal-or-better than the best
  known on 63/70; one vehicle miss (large_yugovostok_8: 11 vs 10 by another agent), 6 km-only misses.
  After best.py: 50/70 best-known gen_road solutions come from results/instances_gen_road/sa.
- Km-trap test (control_road yugocentr_pt_all/pt_inf, yugovostok_pt_inf; s21 30 s x2): base found the optimum
  176.4 on pt_inf once; T0=10 found 346.6 (= best known) on yugovostok_pt_inf once; ruin&recreate (0.5%) and
  T0=2 never. No config is systematically better — extra seeds are the lever for best-known hunting.
- 60 s x4 seeds on the 3 unresolved control_road instances: T0=5: 709.6 km avg (never reaches the best known);
  **T0=10: 704.4** — yugovostok_pt_inf 346.6 (= best known) in 2/4 seeds, yugocentr_pt_all 176.4 (= optimum) in
  1/4. s21 now uses T0=10 by default. With these runs our results/instances_control_road/sa ties the best known
  on all 12 control_road instances (87 vehicles / 2791.2 km).
- But on the 10 instances_road instances where we were behind, 60 s with T0=10 was worse than 30 s with T0=5 on
  6/10 (e.g. yugovostok_s4 578 vs 560, yugocentr_s5 242 vs 234; 1 seed). Final s21: temperature portfolio,
  T0=5 on even restarts and T0=10 on odd ones. Meanwhile other agents (hy, lns) improved instances_road bests;
  26/30 instances_road bests are now theirs.
- T0=10 in the 1 s product (control_road x3, instances_road x2): km +0.5% / +0.46%, worse -> product keeps T0=5.
- gen_road 60 s push on the 7 instances where we were behind: no new bests. The remaining gaps are fleet-size
  gaps against the exact pair-reoptimisation hybrid s40_hy of another agent (large_yugovostok_8 10 vs our 11,
  real_yugovostok_8 9 vs our 10, each costing +250 km) — the SA pool-elimination cannot find those at 60 s.
- 120 s on large_yugovostok_8 / real_yugovostok_8 (gen_road), with and without TW: still 11 / 10 vehicles.

## 13. Official bench.py records (wall clock, loaded machine, WORKERS=2)
`bench_instances_control_road_tl1.0_s23.json` (3 seeds), `bench_instances_road_tl1.0_s23.json` (2 seeds).
analyze.py, 1 s: control_road — s23 win 14/36 (39%), Δveh +0.14, gap 1.2%; s10 4/12, +0.17, 1.7%;
s12_alns 6/12, +0.00, 2.0%. instances_road — s23 22/60 (37%), +0.17, 3.9%; s10 9/30, +0.27, 4.4%;
s12_alns 13/30, **+0.00**, 4.2%. s23 is the best SA and has the smallest km gap of all 1 s solvers, but ALNS (s12,
SISR-style absence-counter acceptance in its fleet phase) is more reliable on the vehicle count at 1 s.

## 14. Elimination-phase parameters, hard12 x8 seeds, 1 s CPU (baseline 109.00 vehicles)
wp growth every 5k it.: -0.12; Winc 5: -0.25; Te 20: +2.38; Te 100: +1.00; pressure β=0: +1.00. All noise or
worse: the s10 elimination parameters are already at a local optimum.
fElim 0.75 (more time for elimination): hard12 +0.12 vehicles, control_road km +0.9%, instances_road km +2.7% -> no.

## Summary (05:05 UTC)
Files: `solvers/sa2_util.hpp` (private SA2 core: copy of sa_util with time-warp data, STimer, SA2 struct with all
new moves), `solvers/s23_sa3.cpp` — **new best 1 s solver**, `solvers/s21_multi.cpp` — offline long mode
(restarts, temperature portfolio, elimination-focused restarts from the best), `s22_se.cpp` — experiment harness,
`s20_sa2.cpp` — first instrumented copy (ruin move), not recommended.
What helped (1 s, CPU-timed, vs s10 logic): workload LB (skip hopeless elimination), split+eliminate cycles and
Hungarian vehicle reassignment in the km phase (the heterogeneous-fleet trap), measured on 6 sets:
control -0.33 veh / -0.49% km; instances -0.50 / -3.25%; gen -1.50 veh / +0.32%; control_road -0.33 / -0.81%;
instances_road -0.50 / -3.59%; gen_road -15 penalty, 0 veh, +0.12% km.
Long mode (s21, 30-60 s): ties the best known on all 12 control_road instances (87 / 2791.2; 11 of them proven
optimal by other agents' bounds), all 30 instances_road vehicle counts; gen_road 430 / 533 / 16989 vs 1 s
465 / 537.5.
What did not help: time-warp squeeze (noise-level at 1 s), ruin&recreate (too expensive per move), smarter route
choice for elimination, temperature scaling by km/N, T0=10 at 1 s, shorter/longer elimination attempts, earlier
stop of elimination, more elimination time, cheap-opening start (fixes control traps but hurts elsewhere).
