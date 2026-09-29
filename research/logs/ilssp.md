# ILS-SP matheuristic log (s70_ilssp, solvers/sp_util.hpp)

Idea: the km phase (SISR annealing, as in s33 KmSearch) puts EVERY feasible route it touches into a pool
(key = vehicle group (start, mode) × order set, best sequence kept, vmask = vehicles of the group whose skills cover
the route); periodically a set-partitioning (SP) solver over the pool picks the best combination, and the search
continues from the SP solution. Fleet phase is copied from s33 (SA s10 + FleetRR).

Harness: evalcfg.py, 124 instances (control / road / gen), 2 seeds. Machine load is very high (load avg 15–22 on
8 cores from other agents), so baseline and candidate are run CONCURRENTLY with WORKERS=1 each (2 processes total,
same load) — only these paired numbers are compared.

## Exp 0. Calibration
- s33 1 s ×2, WORKERS=2, 00:20: control бр+2.0 км 4.83% | road бр+1.5 км 3.08% | gen шт+20 бр+5.0 км 5.30%.
- Pool statistics (vostok_s0, 1 s): km phase does ~50k SISR iterations/0.4 s, ~110k route adds, 36k distinct
  routes. Pool of the whole run 56k–69k columns.

## Exp 1. Exact SP over the whole pool (Lagrangian + subgradient + reduced-cost fixing + DFS) — failed
- Subgradient on the covering constraints (inner problem: ≤cap per vehicle group, ≤K routes) converges far too
  slowly: after 600 iterations LB 169.6 vs UB 199.1 (vostok_s0), no column fixed, DFS 5M nodes without improvement.
- Offline check with HiGHS on the dumped pool: the LP bound itself is weak — LP 177.5 vs incumbent 201.9 (12%),
  large_vostok_0 LP 251.5 vs 268.2 (6%). HiGHS MIP on the full pool (56k cols) finds no feasible solution in 60 s,
  CP-SAT (2 workers, hint = incumbent) 60 s: no improvement / no solution. On the 3000 best-rc columns HiGHS proves
  197.47 vs incumbent 198.88 (−0.7%) in 15 s; 1000 cols: 198.42 in 0.23 s.
- Conclusion: the pool of all visited routes contains only small improvements (≤1%) at any single moment, and a
  global SP with LP-gap 6–12% cannot be solved exactly in ms. Switched to a neighbourhood SP.

## Exp 2. Neighbourhood SP (sp::Recomb)
For every connected set S of k ≤ 4 routes of the incumbent: candidate columns = pool routes whose orders all lie in
S (bucketed by the mask of incumbent routes they touch → 2^k bucket lookups), exact DFS partition of S's orders
(cost W·count + km, so fewer routes = fleet reduction is possible), vehicle assignment by bipartite matching on
vmask (vehicles of S + unused). First improvement applied, index rebuilt (counting sort by mask, ~1 ms).
- v1 (RCK=4, 20k nodes/sub, NSP=4 SP calls of ≤0.03·T each), 1 s ×2, paired with s33 (both WORKERS=1, concurrent):
  s33  control бр+2.0 км 5.06% | road бр+3.5 км 4.14% | gen шт+20 бр+5.5 км 5.57%
  v1   control бр+1.0 км 4.78% | road бр+2.5 км 5.04% | gen шт+10 бр+6.0 км 6.33%
  per-run paired (cmp.py, v1 vs s33): control veh −2, km@eq +0.10%; road veh −2, km@eq +1.10%; gen pen −20,
  veh +1, km@eq +0.69%. → SP costs ~30% of the 0.4 s km phase (4×30 ms) and gains less than the lost search.
- Profiling: improvements come from k=2 and k=3 (k=1 and k=4 almost never), many subproblems hit the node limit.
  Fixes: (a) ≤k columns (k+1 columns cost ≥ (k+1)·W > k·W+km), and the LAST column is found by hash lookup of the
  remaining order set in each group (Zobrist XOR, no enumeration); (b) extra bound W·⌈uncovered/maxLen⌉ + Σ km-share;
  (c) RCK=3 default; (d) time check inside DFS. Dynamic "fewest compatible columns" branching tried: fewer nodes but
  3–5× slower (counting per node) → reverted.
- v2 (with the fixes above) 1 s ×2 paired with s33: s33 control бр+2.5 км 3.96% | road бр+3.0 км 4.30% | gen бр+5.5
  км 6.79%; v2 control бр+1.0 км 4.59% | road бр+4.0 км 5.10% | gen бр+7.0 км 6.33%. Per run (v2 vs s33): control
  veh −3 km@eq −0.18%; road veh +2 km@eq +0.88%; gen veh +3 km@eq −0.26%. Vehicle differences are fleet-phase noise
  (identical code, time-dependent); km effect within noise.
- Final-pool diagnostic: after a normal 1 s run (SP at the end of the km phase), a further Recomb k≤4 with 2 s of time
  over the whole final pool finds 0 improvements on 7/8 instances (0.006% on one). The SA best is already
  "pool-locally optimal": one SISR trajectory produces highly correlated routes.

## Exp 3. Diversity: several km runs sharing one pool (MULTI) + global SP
- MULTI=3 (3 independent SISR runs from the fleet solution, 2 s): Recomb k≤4 on the best run finds nothing.
- Offline HiGHS on the MULTI=3 pool, top-1000 columns by LP reduced cost: vostok_s3 161.99 → 158.17 (−2.4%, below
  the best known 158.77!), walk_yugocentr_1 143.62 → 143.22; the MIP solution keeps 0 of the 7 incumbent routes.
  With MULTI=1 (one trajectory) the same MIP gives exactly the incumbent. → gains need routes from DIFFERENT
  trajectories combined globally (all routes replaced), not k-route neighbourhoods.
- Own global SP (sp::GSP): LP relaxation by column generation over the pool with my own revised simplex (explicit
  B^-1, big-M skip columns, Bland fallback on degeneracy), then exact DFS over columns with rc < UB−LB (Σrc budget,
  last column by hash). LP: 1400–5700 pivots, 60–460 ms under the current load (too slow for 1 s); LP solution is
  completely fractional (0 columns at 1 → no rounding/fixing possible); gap UB−LB 12–20 km → 25k–75k columns with
  rc < gap, DFS aborts. Restricting to top-150/400 rc columns: DFS completes in <15 ms but finds nothing (sanity
  check with UB+0.5 finds the incumbent, so the mechanics are right; my duals are an arbitrary vertex of a highly
  degenerate dual face, the top-rc set differs from HiGHS'). Diving (fix max-x column, re-solve) also found nothing
  and costs 50–140 ms. Global exact/LP-based SP abandoned for the 1–3 s budget.
- Elite SP (all routes replaced by columns taken from the bests found by the 3 runs, ~120–270 columns, last column
  by hash from the whole pool): finds nothing on 4/4 instances — elite routes of different runs do not tile.

## Exp 4. Isolating the SP effect: same solver, NSP=0 (no SP = s33 km phase) vs NSP=4, 1 s ×2, concurrent
  NSP=0  control бр+1.5 км 4.98% | road бр+3.0 км 5.39% | gen шт+20 бр+8.0 км 7.58%
  NSP=4  control бр+2.0 км 3.63% | road бр+3.0 км 5.37% | gen шт+10 бр+7.5 км 6.09%
  per run NSP=4 vs NSP=0: control km@eq −0.20% (win/loss 28/17), gen −1.18% (72/51), road −0.01% (27/31).
  → the neighbourhood SP pays off on the larger gen instances, neutral on road. (Load avg ~30 during this run.)
- NOTE: rebuilding bin/s70_ilssp while an eval is running changes the binary mid-run; from now on every eval runs a
  frozen copy (bin/s70_snapN, evalcfg's make fails silently for it and runs the copy).
- Memo across SP calls (skip a route subset if no new column fell in its buckets since it was last proven locally
  optimal) and "dirty routes" (continue the pass after an improvement, skipping subsets with changed routes) added;
  memo rarely skips (pool grows by ~10k columns between calls, buckets almost always get new columns).
- FPOOL=1 (also pool the routes of the fleet-phase FleetRR), NSP=8 SPTL=0.02 RCNODES=30000, paired vs FPOOL=0:
  control −0.49% (27/11), gen +0.23% (54/70), road +0.78% (27/29) → mixed, FPOOL off.
- Per family (NSP=4 vs NSP=0, 1 s): allday −2.70%, peak −1.90%, tight −1.68%, walk −1.66%, real −1.37%, scarce
  −0.40%, control −0.20%, road families −0.3…+0.2%, large (100–130 orders) +1.39%.

## Exp 5. 3 s, s70 default (NSP=4, SPTL=0.03·T, RCK=3) vs s33, concurrent, ×2
  s33  control бр+1.5 км 4.19% | road бр+0.5 км 2.64% | gen шт+20 бр+4.0 км 4.78%
  s70  control бр+1.5 км 3.07% | road бр+1.0 км 3.36% | gen шт+10 бр+3.5 км 4.02%
  per run s70 vs s33: control km@eq −0.42% (22/18), gen pen −20 veh −1 km@eq −0.44% (72/49), road +0.84% (24/29).
- Is the road deficit real? road only, 1 s ×4 (120 runs): s70 NSP=0 vs s33 (concurrent) +0.88% (47/71, +5 veh);
  later s70 NSP=0 with s33's seed mapping and NO pool adds (ADD=3, i.e. an exact s33 replica) vs the same s33 run
  from 3 min earlier: −0.54% (68/47). Concurrent ADD=3 vs ADD=0 (pool overhead only): +0.11% (50/63).
  → pool bookkeeping costs ≈0.1%; the ±0.9% road differences are load noise even with 120 runs.
  Hash table now stores keys inline (one cache miss per probe instead of two).

## Exp 6. s71_racesp = s51_race (coordinator's race of min-fleet candidates + successive halving) + shared pool + SP
Coordinator's suggestion: race candidates have different vehicle sets → the diverse pool SP needs. s71: every
candidate's km search (PoolKm, same schedule as KmSearch::run(x, tEnd, -1, T0x)) feeds ONE shared pool; every
SPI·T seconds of search Recomb (k≤3) runs on the current candidate using the whole pool (incl. routes of other
candidates); the search continues from the SP result. Compared with s51 on the same settings (CPU_TIME=1 ADAPT=1
F0=0.5 FA=0.65), concurrent.
- 1 s ×2, CPU_TIME=1 ADAPT=1 F0=0.5 FA=0.65, concurrent:
  s51  control бр+1.0 км 3.67% | road бр+1.0 км 3.51% | gen шт+10 бр+5.5 км 4.72%
  s71  control бр+1.0 км 3.71% | road бр+2.0 км 2.42% | gen шт+20 бр+5.5 км 3.95%
  per run s71 vs s51: control km@eq +0.07% (17/15), road −0.86% (31/20, veh +2), gen −0.84% (61/59, pen +20).
- 3 s ×2, same settings, concurrent:
  s51  control бр+0.5 км 2.69% | road бр+0.5 км 1.82% | gen шт+10 бр+2.5 км 2.57%
  s71  control бр+0.5 км 2.83% | road бр+0.5 км 1.66% | gen шт+0 бр+2.5 км 2.44%
  per run s71 vs s51: control +0.15% (12/13), road −0.13% (24/23), gen −0.06% (60/49, pen −20) → neutral at 3 s:
  with 3× more search the SA itself reaches what the neighbourhood SP finds.
- 1 s ×2 variants (concurrent pair, both vs the s51 run from 20 min earlier):
  SPI=0.05 SPTL=0.02 (twice as many, shorter SP calls): vs s51 control −0.21%, gen +0.02%, road +0.87%;
  XSP=1 (after every race round Recomb k≤4 on all alive candidates over the shared pool): vs s51 +0.42%, +0.51%,
  +0.53%; SPI05 vs XSP directly: control −0.61%, gen −0.51%, road +0.44%.
  → the −0.85% of the first s71 run is not reproduced; the between-run noise even with CPU_TIME is ~0.5–0.9%.
  Re-running s71 default vs s51 with 4 seeds.
- 1 s ×4 (496 runs per solver), s71 default vs s51, concurrent:
  s51  control бр+1.0 км 2.25% | road бр+1.5 км 3.58% | gen шт+20 бр+5.2 км 4.59%
  s71  control бр+1.0 км 2.38% | road бр+1.8 км 2.87% | gen шт+15 бр+5.0 км 4.53%
  per run s71 vs s51: control +0.11% (38/33), road −0.50% (62/45), gen −0.07% (123/112, pen −20, veh −1).
  Pooled with the ×2 run (6 seeds): control ≈ +0.1%, road ≈ −0.6%, gen ≈ −0.3%.
- 3 s ×4, s71 default vs s51, concurrent:
  s51  control бр+1.0 км 1.65% | road бр+0.5 км 1.67% | gen шт+10 бр+2.5 км 2.86%
  s71  control бр+0.5 км 1.90% | road бр+0.2 км 1.89% | gen шт+10 бр+2.2 км 2.80%
  per run s71 vs s51: control veh −2 km@eq +0.25% (30/31), road veh −1 +0.20% (51/45), gen veh −1 +0.00% (121/106).
  → at 3 s the SP is neutral (vehicle deltas are fleet-phase noise, the fleet code is identical).
- s71 vs s33 directly, 1 s ×2, CPU_TIME=1, concurrent:
  s33  control бр+2.0 км 4.91% | road бр+1.5 км 3.29% | gen шт+20 бр+5.0 км 5.38%
  s71  control бр+1.0 км 3.55% | road бр+1.5 км 2.72% | gen шт+20 бр+5.5 км 4.59%
  per run s71 vs s33: control veh −2 km@eq −0.97% (28/12), road −0.53% (33/26), gen veh +1 −0.64% (79/50).
- s71 vs s33 directly, 3 s ×2, CPU_TIME=1, concurrent:
  s33  control бр+0.5 км 3.80% | road бр+0.0 км 1.71% | gen шт+20 бр+3.5 км 4.00%
  s71  control бр+0.5 км 3.03% | road бр+0.0 км 1.92% | gen шт+10 бр+2.5 км 2.31%
  per run s71 vs s33: control −0.18% (25/11), road +0.25% (27/26), gen pen −20 veh −2 km@eq −1.42% (81/36).

## Summary
- Best config: `s71_racesp` with `ADAPT=1 F0=0.5 FA=0.65` (s51 race + shared in-search route pool + neighbourhood SP
  every 0.1·T, ≤0.03·T per call, k≤3). vs s33 (CPU_TIME): 1 s control −0.97% / road −0.53% / gen −0.64% km at
  equal fleet; 3 s −0.18% / +0.25% / −1.42% (+ fewer vehicles/penalty on gen). Most of this is the race (s51); the
  SP's own share vs s51: 1 s ≈ −0.3…−0.6% on gen/road (6 seeds), 3 s neutral (4 seeds).
- Within one s33-style trajectory (s70, NSP=4 vs NSP=0, 1 s): gen −1.18%, control −0.20%, road 0.
- What failed and why: (1) exact/near-exact SP over the WHOLE pool — LP bound 6–12% below the incumbent (open
  routes make fractional LP solutions cheap), subgradient does not converge in ms, my column-generation LP needs
  60–460 ms and yields a fully fractional x (no fixing/rounding), DFS over rc<gap columns explodes, top-rc subsets
  miss the improving columns; HiGHS itself needs 0.2–15 s on 1000–3000 columns. (2) Routes of one trajectory are too
  correlated: at the end of a run the pool contains no better k≤4 recombination. (3) Pools of several runs DO contain
  better global combinations (HiGHS: −2.4%, even a new best known on vostok_s3), but they replace all routes at
  once — k-route neighbourhoods and elite-route SP cannot reach them. (4) FPOOL, XSP, more frequent SP: noise-level
  or negative. Machine noise (load 15–37) is ±0.5–0.9% per set even with 120–280 paired runs.
