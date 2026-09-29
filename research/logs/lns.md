# LNS / SISR / HGS improvements (agent lns)

Files: `solvers/lns2_util.hpp` (shared), `solvers/s31_sisr2.cpp`, `solvers/s33_sasisr.cpp`, ...
Runner: private scripts (lrun.py/cmp.py) — run solver binary on a dir with seeds, validate with validate.py.
All timings with 2 parallel workers while 3 other agents also run jobs => time-limited results are noisy (±1 vehicle
on "lucky" instances, ±1% km).

## Baselines, instances_control, 1 s, 3 seeds (sum over 12 instances, mean over seeds)
| solver | vehicles | km |
|---|---|---|
| s10_sa | 87.67 | 2616.4 |
| s12_alns | 87.00 | 2733.2 |
| s13_sisr | 88.67 | 2527.4 |
| s14_hgs_lite | 86.67 | 2698.3 |

## Exp 1. lns2_util: fixed-array solution + in-place R&R with undo (s31 v1)
Idea: s12/s13 copy `vector<vector<>>` solutions every iteration and scan all insertion positions.
Change: `Sol` on fixed arrays, `Undo` saves only touched routes + rt[], position scan stops once dep[p-1] > b_k,
blink via integer threshold. Fleet phase = adaptive {SISR, random, worst, Shaw, route, slot} × {greedy+blinks, regret-2,
regret-3} with absence acceptance; km phase = SISR SA (T relative to mean edge length) + LS polish of new bests.
Result: km-phase iterations 3x of s13 (≈150k vs ≈45k in 0.45 s on 83 orders).
Control 1 s: 88.00 veh / 2560.8 km (vs SA 87.67/2616): win 10 / loss 2 vs SA per instance, −1.0% km at equal fleet.

## Exp 2. Elimination attempts get stuck
Observation (yugovostok_pt_all): 10 vehicles either found in ~1300 iterations or never within 28k iterations —
the absence counters keep choosing the same route to remove. Change: after the first attempt, with prob. ERND=0.5 the
removed route is random with weight 1/len². Restarting attempts every ERST iterations did not help on control (ERST=1000/3000:
+0.2% km, same fleet) but helped slightly on hard gen instances (63.3 vs 65.3 veh on 9 hard ones) — kept off by default.

## Exp 3. Wrong vehicle set frozen in km phase
Observation: yugovostok_car_inf seed 1: 8 veh / 577 km vs 326 km for other seeds; the remote-home vehicle (start 3)
route zigzags 223 km. SISR strings never free a whole route, so the km phase cannot change which vehicles are used.
Change: in km phase with prob PROUTE remove one whole route (+neighbours); with prob PPAIR remove a long route (weight
~km) together with the route of the vehicle whose start is closest to one of its orders; recreate by regret-2 or greedy.
Also T0 2→5 mean edges. Result control 1 s: v3 (PROUTE=0.1, T0=5) 87.00 veh / 2650.9 km — fewer vehicles than SA (87.67)
and −0.3% km at equal fleet. PROUTE=0.2: worse (87.33/2703).

## Exp 4. Fleet weakness on instances_gen (1 s, seed 1)
s31: 543 vehicles vs SA 535 (+8), km at equal fleet −1.1% vs SA. On 9 hardest instances (3 seeds) R&R fleet phase gives
63–66 vehicles vs SA 60 regardless of FLEET share (0.5/0.7), operator set (FOPS) or restarts. The absence-based R&R
cannot find these reductions (they need big km increase: e.g. 5 veh/172 km vs 6 veh/123 km) within 0.5 s.

## Exp 5. Hybrid s33_sasisr: SA fleet phases (copied from s10) + SISR km phase
Change: copy of s10_sa.cpp, SA stops after phase 2 (elimination), then KmSearch (lns2_util) on SA best.
Hard gen subset 1 s ×3: 60.00 veh / 1992 km vs SA 60.00 / 2030 (−1.6% km at equal fleet), win 7 / loss 1.
Control 1 s ×3: 87.67 / 2604.0 (SA 87.67 / 2616.4).
Variant: SA elimination until 0.45·tl, then FleetRR until 0.6·tl, then km: control 87.00 / 2608.3, hard gen 60.67 / 1971.

Full instances_gen, 1 s, 2 seeds (sum over 70 instances, mean over seeds):
| solver | penalty | vehicles | km | per-instance win/loss vs SA | km gap at equal fleet vs SA |
|---|---|---|---|---|---|
| s10_sa | 465 | 538.0 | 16705 | – | – |
| s33 (SA elim to 0.6) | 445 | 536.0 | 16650 | 42/28 | −0.77% |
| s33 (SA elim to 0.45 + RR to 0.6) | 440 | 534.5 | 16564 | 39/30 | −1.03% |
| s31 (RR fleet only) | 450 | 543.5 | 16358 | 32/38 | −0.14% |
=> SA elimination + R&R elimination complement each other (SA finds "expensive" reductions, R&R finds others,
e.g. control yugovostok_pt_inf 12→11). Default of s33 set to SA elim 0.45·T, RR until 0.6·T.

## Exp 6. km-phase parameter tuning on gen (s33, 1 s, 2 seeds) — inconclusive (noise)
Variants KMOPS=1 (adaptive ALNS ops in km phase), T0∈{2,10}, TF∈{0.02,0.2}, LSB=0, CBAR2=15, RREND=0.7.
All within ±0.5% km of each other; a re-run of the *default* config differed by 1.7% km (16851 vs 16564) from its
first run, i.e. machine-load noise (4 agents on 8 cores, time-limited runs) is larger than the effects. Defaults kept
(T0=5, TF=0.05, CBAR2=10, LSB=1, PROUTE=PPAIR=0.05). s30_alns2 = s31 with KMOPS=1 (kept as a file, no measurable gain).

## Exp 7. HGS-2 (s32): memetic km phase after the s33 fleet phase
SREX crossover (heterogeneous fleet, per-vehicle routes), regret-2 repair under fleet cap, education = KmSearch with
2000 iterations at low T (1→0.02 edges) + LS, broken-pairs diversity, MU=8/LAMBDA=8.
gen 1 s ×2: 538.0 veh / 17091 km (+2.5% km vs s33) — too few generations (≈40) in 0.4 s.
control 10 s ×2: 87 / 2613.5 vs s33 87 / 2582.8 — worse than plain SISR-SA with the same time.
Added elimination mutation (short R&R elimination on a child, PEL=0.15): no fleet gain on yugovostok road (10 s).
Conclusion: at these sizes and budgets one long SISR-SA trajectory beats a population; HGS-2 not recommended.

## Exp 8. Long mode (control, 10 s, 2 seeds)
| config | veh | km |
|---|---|---|
| s10_sa | 87 | 2605.9 |
| s33 single run | 87 | 2582.8 |
| s33 RST=3 (3 independent runs) | 87 | 2582.1 |
| s33 KMR=3 (3 km restarts from fleet best) | 87 | 2581.7 |
| s33 CYC=3 (R&R elimination attempt + km, 3 cycles) | 87 | 2582.3 |
| s31 | 87 | 2584.4 |
| s32 | 87 | 2613.5 |
Control is nearly saturated: 8 of 12 instances converge to the same km in all runs (several equal the LB in lb/).
Restart schemes are equivalent on km. Fleet: the fleet phase is the bottleneck on the hardest (yugovostok_pt_*):
seed 2 never finds 10 vehicles on road yugovostok_pt_all even in 10 s, seed 1 finds it in 1 s -> basin effect.
Change: FLR = number of independent fleet phases (SA+R&R, each on its own time slice), best goes to km phase.
Road yugovostok_pt_* 10 s ×2: FLR=1 21.5 veh, FLR=4 21.0 veh (10 found on both seeds).

## Road instances (instances_*_road), first look, 1 s
control_road ×3: s10 88.67/2780.3, s33 87.67/2755.7 (win 11/loss 1 vs SA), s31 87.67/2757.6, s12 88.00/2830.5,
s13 89.33/2672.8, s14 88.00/2835.8.
instances_road ×2: s10 219.0/9379, s33 217.5/9130 (−2.7% km at eq. fleet), s31 220.0/9228, **s12 214.0**/9836,
s13 225.0/8716, s14 218.5/9413.  gen_road ×1: s10 538/18286 (pen 480), s33 538/17707 (pen 450), s31 541/17569.
control_road 10 s ×2: s10 87.00/2815.3, s33 FLR=4 87.50/2755.4 (missed 10 veh on yugovostok_pt_all seed 1:
four 1.9 s fleet phases are each too short for SA; with FLR=1 SA finds it in 10 s). => FLR>1 not made default.

## Exp 9. Why is old s12 better at fleet on instances_road (214 vs 220)? Two findings
(a) Operator set: s12 uses only ALNS ops + regret-K (K=1,2,3) with adaptive noise. Added FleetRR FMODE=1 (same set):
    s31 220→218, s33 217.5→215.5 vehicles.
(b) **Bug (also in s13/s14/my first version): stale Σabsence of the current solution.** Acceptance "Σabsence(new) <
    Σabsence(cur)" used the value of cur stored when it was accepted; absences only grow, so the stored value is too
    small and later candidates are rejected far too often. s12 recomputes both sums with current counters.
    Fix: recompute Σabsence over cur's absent set after this iteration's increments (FRESH=1).
    instances_road 1 s ×2: s31 220.0→215.5 veh; s31+FMODE=1: 218.0→214.5; s33+FMODE=1: 215.5→214.5 (s12: 214.0).
    FMODE=1 made default.
(c) Bug found on yugovostok_s5: when SA's short fleet phase ended with unserved orders, FleetRR started route
    elimination at once (tServe=0) and returned 9 veh + penalty 40 instead of 10 veh + penalty 0. Fix: serve phase for
    half of the R&R slice when best has unserved orders; KmSearch allows opening vehicles (cap=V) if orders are unserved.

## Exp 10. FMODE=2 (union: SISR+ALNS ops × greedy-blink+regret-1/2/3): within noise of FMODE=1
gen 1 s ×2 (s31): FMODE=0 538.5/16771, FMODE=1 539.0/16499, FMODE=2 536.5/16776; instances_road: 215.5 / 215.0 / 216.0.
Kept FMODE=1.

## Exp 11. Vehicle-exclusion portfolio (VEX=1 in s33, off by default)
Idea: on control_road yugovostok_pt_inf (11 of 12 vehicles, remote homes) we are 5.9% above the CG lower bound; maybe
the wrong vehicle is idle. For each used vehicle u: forbid u, drop its route, let KmSearch (cap = fleet) repair and
optimise for a slice. 4 s test: all 11 alternatives worse (365–519 km vs 359 km) — the idle vehicle chosen by the
fleet phase is already the right one; the gap is inside the routing. Left as an option.

## Final 1 s comparison (v6 binaries = FRESH + FMODE=1; sums over instances, mean over seeds; kmgap = km vs s10 on
## runs with the same penalty and fleet)
| set (seeds) | s10_sa | s12_alns | s13_sisr | s14_hgs | **s31_sisr2** | **s33_sasisr** |
|---|---|---|---|---|---|---|
| control (3) veh / km / kmgap | 87.67 / 2616 / 0 | 87.00 / 2733 / +3.3% | 88.67 / 2527 / +0.1% | 86.67 / 2698 / +1.0% | 86.67 / 2631 / −0.8% | 87.00 / 2602 / −0.7% |
| instances (2) | 214.5 / 9272 | 213.5 / 9272 / +0.7% | 217.0 (pen+50) / 8848 / +0.6% | 215.5 / 9266 / +1.8% | 213.0 / 9295 / +0.9% | **212.5 / 9035 / −1.7%** |
| gen (2) pen / veh / kmgap | 465 / 538.0 | – | – | – | 445 / 539.0 / −0.5% | **445 / 537.0 / −1.0%** |
| control_road (3) | 88.67 / 2780 | 88.00 / 2831 / +2.7% | 89.33 / 2673 / +0.4% | 88.00 / 2836 / +3.0% | 88.00 / 2722 / −0.2% | 88.00 / 2722 / −0.3% |
| instances_road (2) | 219.0 / 9379 | 214.0 / 9836 / −0.9% | 225.0 / 8716 / −1.7% | 218.5 / 9413 / −1.1% | 215.0 / 9535 / −2.9% | 215.0 / 9627 / −1.9% |
| gen_road (1) pen / veh / kmgap | 480 / 538 | – | – | – | 450 / 536 / −2.1% | 450 / 538 / −2.5% |
s30_alns2 (control_road 88.00/2715, instances_road 215.5/9553, gen_road 536/18068) ≈ s31.

## Long mode, control_road 30 s
s10 (2 seeds) 87 / 2811.3; s33 (3 seeds) 87 / 2798.7; s31 (2 seeds) 87 / 2797.0. Best-of-seeds of s33 equals the
lower bound (lb/) on 9 of 12 instances; misses: yugocentr_pt_all/inf 178.2 vs 176.4 (an 8 s run of s33 found 176.4 —
a deep attractor, reached or not by luck), yugovostok_pt_inf 346.6 vs LB 327.2 (all seeds and all solvers end at
346.6 → the CG bound is probably weak there).

## Long mode, more results
- control (euclidean) 30 s, s31 ×2: **86 vehicles / 2679.5 km in both seeds** (10 vehicles on yugovostok_pt_inf,
  400.1 km). The original target best known was 87 / 2603 → one vehicle fewer (lexicographically better).
  All other 11 instances equal the best known / LB values (131.9, 140.7, 145.0, 179.4, 188.0, 194.9, 332.3 ...).
- instances_road 10 s ×1: s10 215 / 9486; s33 **213** / 9516 (−0.6% km at equal fleet); s31 213 / 9597.
- control_road: lower bound on fleet = 87 (lb/), all three solvers reach it at 30 s.

## Exp 12. s33 time split / polish A/B, instances_road 1 s × 4 seeds (paired)
| config | veh | km | kmgap vs base |
|---|---|---|---|
| base (SA elim to 0.45·T, R&R to 0.6·T, LS polish of km-phase records) | 214.50 | 9507 | – |
| SAF=0.5 (SA to 0.3·T, R&R 0.3·T) | 215.25 | 9569 | +1.2% |
| LSB=0 (no polish of records) | 215.50 | 9521 | +1.0% |
| RREND=0.5 (km phase 50%) | 214.75 | 9608 | +1.1% |
Defaults confirmed. gen_road 10 s ×1: s10 534 / 17334, s33 **533** / 17246 (−0.9% km at equal fleet).

## Exp 13. PPAIR 0.05 → 0.15 (targets the remote-vehicle trap: large_yugovostok_5 1 s ×6 seeds mean 458 → 419 km)
Does not generalise: instances_road ×4 +0.9% km, gen_road ×2 +0.7%, gen ×2 −0.3%. Kept 0.05.
- control 30 s, s33 ×2: 86.5 veh / 2633 km (10 vehicles on yugovostok_pt_inf in one seed of two; s31 in both) —
  for fleet in long mode the pure R&R fleet phase of s31 (50% of time) is more reliable on this instance.
- instances (euclidean) 10 s ×1: s10 213 / 9041; s33 **212** / 8929 (−0.4% at equal fleet); s31 212 / 9140 (+1.8%).

## Exp 14. gen at 3 s: s33 loses to SA on the "large" family
gen 3 s ×2: s10 531.0 veh / 16791, s33 533.0 / 16647 (+0.3% km at equal fleet). Per family (km at equal fleet):
allday −4.8%, tight −1.9%, peak −0.2%, scarce +0.2%, real +1.4%, walk +1.4%, **large +5.4%** (also +3.3% at 1 s).
On large_vostok_0 (≈100 orders) SISR-only km phase ends at 260 km, SA 238–247. Hypothesis: on long routes the
cheap SA neighbourhoods (2-opt, or-opt with reversal, cross-exchange, 2-opt*) repair intra-route inefficiencies that
insertion-based SISR only fixes slowly. Change: option SAKM — first SAKM share of the km phase runs the s10 km
annealing (phase 3 only) from the fleet-phase best, then KmSearch continues. large_vostok_0 3 s: 260.4 → 242.1.
Follow-up (large family, 3 s × 2 seeds, 20 runs, same load): SAKM=0 3037.6 km, 0.3 3185.8, 0.6 3069.0, 1.0 3134.5,
s10 3049.4 (all 111 veh). With SAKM=0 s33 is on par with SA on "large" — the +5.4% of the gen 3 s run was a load
artefact (that run of s33 overlapped heavy jobs of other agents). SAKM left off (0). Lesson: single time-limited runs
on a shared 8-core machine can differ by 5% on 100-order instances; only paired multi-seed runs are trustworthy.
Control 30 s, yugovostok_pt_inf, 4 seeds: s33 default 10 veh / 400.1 in 4/4, CYC=3 10 / 402.9, s31 10 / 400.1.

## Summary / recommendation
- Product mode (1 s): **s33_sasisr** — SA fleet phases (s10 copy) + FleetRR + KmSearch. vs s10 on the same seeds:
  control 87.00 vs 87.67 veh (−0.7% km at equal fleet), instances 212.5 vs 214.5 (−1.7%), gen 537.0 vs 538.0 and
  penalty 445 vs 465 (−1.0%), control_road 88.0 vs 88.67 (−0.3%), instances_road 215.0 vs 219.0 (−1.9%),
  gen_road 538 vs 538 and penalty 450 vs 480 (−2.5%).  s31_sisr2 (pure R&R, no SA) is a close second and better on
  fleet on road sets.
- Offline / long mode: s33 (and s31 for fleet on control). 10–30 s: control_road 87 veh (= LB) / 2798.7,
  instances_road 213 veh (s10 215), gen_road 533 (s10 534), control 86 veh (target best known 87/2603).
- Not recommended: s32_hgs2 (population does not pay off at these budgets); VEX, KMR/CYC/RST restarts, FLR>1 (no gain).
- Bugs worth porting to old solvers: stale Σabsence in s13_sisr / s14_hgs_lite (big fleet effect, see Exp 9b).

## Final long runs (saved to results/<dir>/lns/)
instances 30 s ×1: s33 **211** veh / 8961 (best known before: 212) · instances_gen 10 s: 530 / 16574 (pen 440) ·
gen_road 20 s: 532 / 17222 (pen 430) · instances_road 30 s: 213 / 9486.
best.py after this session: control 86 / 2677.9, control_road 87 / 2791.2, instances 211 / 8817, instances_road
213 / 9353, gen 528 / 16414, gen_road 531 / 17241; results/*/lns are the source of 1 / 1 / 9 / 12 / 29 / 28
instances respectively (ties are attributed to whichever file best.py reads first).
