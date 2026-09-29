# cgm: merged branch-price-and-cut (cgx engine + cgc fleet cuts) for the far-home "yugovostok" family (agent cgm)

Files: `solvers/cgm_price.cpp` -> `bin/cgm_price` (clang++ -O2 -march=native -DBP), `cgm_bp.py` (B&P driver).
Temp files / k(S) cache: scratchpad `cgm_tmp/` (cgc's cache results/cgc_tmp/kcache_* is read, never written).
Outputs: lb/<set>/<inst>.json (only when higher), results/<set>/cgm/<inst>.out.

## Merge (14:05-14:12)
* Pricer = cgx_price (ng labeling, lm-SRC up to 512 with keep masks, SRC-aware backward completion bound G, A-mask
  elementary completion bound GM, forbidden arcs) + cgc fleet-cut resources: cut c (order set S_c, type set T_c,
  dual rg_c of any sign) is a 64-bit label flag, the route pays -rg_c on first entry; dominance adds the cgc rGap
  term; every completion bound (F, G, GM, depot bound, H-based backward pruning) subtracts the positive duals of the
  cuts not entered yet (label.rem / remT) -> valid. ENUM: same-set dominance unchanged (fleet state is a function of
  the visited set).
* Driver = cgx_bp (artificials dropped once 0, Lagrangian bound every iteration incl. with artificials -> infeasible
  children pruned early, column purge with a generic column-index remap because fleet-row artificials are interleaved
  with route columns, node enumeration + exact pool at node gap < enum-gap, SB on arcs, 5-min bound checkpoints)
  + cgc: route-count row = K (only when lb_used = K is proven), rounded fleet cuts k(S) (cgc candidate families,
  k cache reused, new k(S) via cgc_sub.kS with a time budget, fleet separation before SRC at the root, cached k only
  at nodes), triangle check (cgc_tri) required, branching: type counts -> routes of a far-home type group entering a
  base cluster union (vset) -> routes visiting a candidate set -> arcs (cgx SB).
* Validation: control_yugovostok_mix1_all (proven 335.6453): root LP with 2 fleet cuts + 60 lm-SRC = 335.6453,
  |rc pricer - rc master| = 8e-10 (--check).

## Pricer fixes (14:20-14:55)
* First s0 root: exact AND heuristic pricing 15-50 s per call once > 200 SRC + 8 fleet cuts. Two causes:
  (1) the completion bound subtracted ALL positive fleet duals not yet entered (large: "Moscow orders before 240 need
  7 routes" etc.) -> almost no bound pruning. Fix: fleet-aware completion bound FF[k][u][m] with the exact entered
  state m of the 4 largest positive-dual fleet cuts of the type (16 states, double precision), remaining cuts via
  the remaining-bonus term; also used by depotBound (Lagrangian MINRC) and ENUM. (2) level-1 (heuristic) dominance
  used the SRC cutGap/rGap terms -> long label lists; heuristic level now ignores them (exact level unchanged).
  s0 root: 875 s (unfinished, 240 SRC) -> 90 s (240 SRC).
* SRC separation stops when an exact pricing call exceeds 6 s (--slowprice, cgc idea).
* ENUM time limit (CGM_ENUM_TL, --enum-tl 60) in the pricer; enum-gap for this family 1.2% (s6: an enumeration at
  gap 20 km ran 375 s and failed; at gap 4.5 km 71 s complete with 18.6k routes, pool infeasible -> node closed).
* NOTE: overwriting a running/signed binary with cp kills new launches on macOS -> always build to a temp name + mv.

## Results
* 14:55 yugovostok_s0 PROVEN OPTIMAL 395.1265 (old lb 382.89, gap 3.2%): fleet cuts raise the root LP 364.2 ->
  383.2 (8 cuts, e.g. "Moscow-cluster orders with window start < 240 need 7 routes"), + 240 lm-SRC -> 393.01
  (gap 0.54%); root enumeration (gap 2.12 km, SRC-aware + fleet-aware bounds) complete: 9403 routes; pool LP with
  474 full-memory 3-SRC 394.30, pool MIP optimal = 395.1265 = UB. 935 s (enumeration ~14 min, old binary w/o
  the level-1 fix in enumeration's backward labeling).
* yugovostok_s6 (2400 s, --check on, first binary): 106 nodes, LB 511.82 < stored 511.94 (not written); time:
  pricing 715 s, enumeration 980 s (5 calls, 2 closed nodes), rest ~700 s (partly the --check rc recomputation).
* 15:00 fleet-aware backward pruning for ENUM: forward optimistic DP HF[k][u][m] with the same top-4 fleet states
  (prefix bonus exact for the top cuts), backward labels carry the top set q entered by the suffix; prune with
  min_m HF[k][L][m] - bonus(q \ m) - (other positive duals); dominance adds bonus(q_Y \ q_X). Validation
  scarce_yugovostok_5 (proven 293.853, --root-only --root-enum, no write): root 289.02 (cgc 287.99), root
  enumeration at gap 4.83 km COMPLETE in 4.0 s, 28k routes (cgc: incomplete after 130 s at 5.9 km); pool LP 289.26
  <= opt; pool MIP time-limited (45 s cap).
* 15:15 forward ENUM bound also fleet-aware: G table per node stores, for each top-state m, the prefix-min of
  (suffix cost - bonus(q \ m)); forward label uses it minus only the NON-top remaining bonus. scarce_5 root enum
  (240 SRC, gap 5.17): complete in 1.4 s, 30.7k routes; pool LP 289.2616 identical to the previous (independent)
  enumeration -> consistent.
* 15:10 unconverged nodes (node time limit / label cap) are now BRANCHED on the restricted-master solution with their
  valid inherited/Lagrangian bound (never closed); before they were frozen as a hard floor of the global LB (cgx
  behaviour), which blocked the bound on instances with slow exact pricing (s1: 12-25 s exact calls at nodes).
* Overrides file (scratchpad cgm_tmp/cgm_overrides.json) read at start of every run: maxcuts 240, slowprice 4 s.
* 15:31 yugovostok_s2 (1800 s): root 561.32 (240->300 SRC), 160 nodes, LB 566.67 -> 569.39 (gap 7.5% -> 6.97%),
  written. Time split: pricing 758 s, LP re-solves 786 s (0.45 s each at nodes vs 0.08 s at the root), arc SB 242 s
  -> overrides sb 4 / 100 iterations. Primal simplex for CG re-solves tested (s8 root): no gain, not used.
* 15:45 yugovostok_s8 (1800 s): root 567.46, 235 nodes, LB 573.96 -> 577.68 (gap 8.7% -> 8.0%), written.
  Branching mix: 134 arc / 44 set / 51 vset / 3 type -> most nodes fell back to arcs because the fleet-row cap
  (63 rows, pricer state = one uint64) was reached. Pricer fleet state widened to 256 bits (4 words), Python cap
  250 rows; rc check on s8 (49 nodes) max |rc diff| 1.3e-7 (km printed with 6 decimals).
* 15:56 control_yugovostok_mix1_inf (1500 s): root 514.98 (cgc root 503.56), 57 nodes (slow: exact pricing ~5 s,
  node enumerations), LB 506.88 -> 531.05 (gap 13.5% -> 8.3%), written.
* Tried: integer k(S) (enumerate zero-rc routes of the vehicle-min LP on S + partition MIP, cgm_kint.py) for the
  Moscow/Domodedovo sets of s4 (LP 7.000 / 6.956): enumeration > 500k routes (degenerate duals) -> abandoned.
* Added: price-and-branch MIP over the master columns right after the root (60 s) when the root gap > 3%.
* 16:00 real_yugovostok_8 (1500 s): root 442.32 (cgc 439.79), 146 nodes, LB 450.18 -> 455.07 (gap 12.0% -> 10.8%).
* 16:15 extra fleet-cut candidate families (--xcands): ALL orders / each depot cluster / pairs of depot clusters
  restricted to window-start ranges (x skill subsets) and window-end ranges (+308 sets on s8). s8 root 567.46 ->
  568.41 (new cuts e.g. "all skill-1 orders starting before 120 need 8 routes"). Enabled for later runs.
* 16:20 scarce_yugovostok_2 (1200 s): root 451.19, 79 nodes, LB 457.48 -> 461.22 (gap 18.3% -> 17.35%).
* 16:20 large_yugovostok_2 (1200 s, N=125): root 378.45, 126 nodes, LB 381.16 -> 382.47 (gap 18.7% -> 18.3%);
  price-and-branch MIPs found only the known UB.
* 16:37 scarce_yugovostok_8 (1200 s, first run WITH the extra families): LP 419.86 -> 627.04 after only 5 fleet
  cuts, root 671.90 with 240 SRC (cgc root 544.34), 111 nodes, LB 548.65 -> 678.59 (gap 30.4% -> 5.4%), written.
  Decisive cuts: "window-start [120,240) orders of depot cluster 1 need 3 routes" (|S|=6, LP 2.5),
  "skill-1 orders of clusters 1+4 starting in [120,240) need 4" (|S|=7, LP 3.5), "all skill-0/1 orders of cluster 0
  starting before 240 need 6" (|S|=24, LP 5.10), "cluster 0+4 orders with window end in [120,360) need 7" (|S|=28,
  LP 6.10), f0c2[120,360)+c0 >= 7. VERIFIED independently (cgm_tmp/cgm_verify.py): each k(S) recomputed from
  scratch without seed columns (same LP values, converged), and all 14 known full-service solutions of the instance
  satisfy every cut (tight: min #routes visiting S = k).
* 16:40 real_yugovostok_2 (with extra families): root 262.27 (stored 260.74), LB 286.9 after 97 nodes and rising.
* Queue re-prioritised (cgmq2): reruns WITH the extra families for scarce_2, real_8, large_2, mix1_inf, then
  tight_2, s1, s6, s3, s4, peak_2, allday_5/2/8, s2, s8.
* 16:41 real_yugovostok_2 (1200 s): root 262.27, 133 nodes, LB 260.74 -> 287.96 (gap 17.4% -> 6.3%), written.
  Its 10 root fleet cuts re-verified from scratch (k(S) LPs converged, all 17 known full-service solutions satisfy
  them, tight). Separation now computes uncached SMALL sets first and no longer skips integral-lhs sets of size
  <= 30 (--small-first; the large-first order spent the whole k budget on 80-order sets on large_8, 20-30 s each).
* 16:48 s4 root test with the extra families + small-first: 13 fleet cuts (e.g. "depot-1 orders starting before
  240 with skill 0/1 need 2 routes", "depot 0+2 orders starting before 240 need 8") -> root 460.95 with only 150 SRC
  (slow-pricing stop) vs stored 458.27 and cgc root 458.0: the "cost-driven" instances do respond a little to the
  time-window count cuts.
* 17:13 peak_yugovostok_2 (cgc: root CG never converged; stored lb 332.85, gap 17.2%): 16 fleet cuts from the
  small-first separation (e.g. "depot-3 skill-0/1 orders starting before 240 need 2 routes", "depot 1+3 orders
  with window end in [480,690) need 3", "depot-0 orders with window end >= 600 need 5") -> LP 301.6 -> 373.8 ->
  root 389.255 with 240 lm-SRC vs UB 390.212 (gap 0.25%), written (332.85 -> 389.255). Root enumeration (gap
  0.957) hit the 60 s ENUM limit after 3 of the types -> dedicated proof run (enum-tl 1500, enum-max 3M).
* allday_yugovostok_5 (first run on the allday family): root 244.54 (stored 231.95), LB 255.4 after 55 nodes.
* 17:24 peak_yugovostok_2 PROVEN OPTIMAL 390.2118 (old lb 332.85, gap 17.2%; cgc could not converge its root):
  proof run root 388.87 (16 fleet cuts + 240 lm-SRC), root enumeration gap 1.342 km COMPLETE in 88.7 s (10,319
  elementary routes, all 10 types, SRC- and fleet-aware completion bounds), pool LP with full-memory 3-SRC rounds
  388.87 -> 388.97 -> 389.75 -> 389.87 -> 390.2118 = UB (lp_closed, integral) -> no solution below the UB. 550 s.
* 17:17 allday_yugovostok_5 (935 s): root 244.54, 94 nodes, LB 231.95 -> 256.90 (gap 27.2% -> 14.8%).
* 17:38 yugovostok_s4 (1200 s; "cost-driven" per cgc): root 460.95 (150 SRC), 51 nodes, LB 458.27 -> 496.79 (gap
  21.7% -> 12.3%), written. The tree moves fast here: type/vset branching on the far-home vehicles + time-window
  set branching on the new candidate families.
* 17:45 yugovostok_s3 (1200 s): root 458.62 (180 SRC, slow-pricing stop), 50 nodes (15-25 s each), LB 455.26 ->
  465.62 (gap 17.7% -> 15.1%), written.
* 17:53 allday_yugovostok_2 (930 s): root 368.85 (stored 350.86), only 11 nodes (all-day windows: slow pricing),
  LB 350.86 -> 375.90 (gap 33.0% -> 24.1%), written.
* 18:00 allday_yugovostok_8 (930 s): root 394.39 (stored 361.27), 64 nodes, LB 361.27 -> 398.20 (gap 13.9% -> 3.3%).
* 18:01 tight_yugovostok_2 PROVEN OPTIMAL with a NEW BEST SOLUTION 371.5285 (old best 380.7126, old lb 362.73):
  root 365.91 (extra-family fleet cuts; cgc root 347.57), LB 368.86 at 300 s, the B&P found an integral node LP
  371.5285 at 415 s (validated: 90/90 orders, 10 vehicles) and every open node had bound >= it -> tree exhausted
  at 438 s (63 nodes). best.py registered results/instances_gen_road/cgm/tight_yugovostok_2.out; finalize: proven.
* 18:21 yugovostok_s6 (1200 s, with the extra families): root 491.01, 185 nodes, LB 509.98 < stored 511.94 (cgc) ->
  not written (third attempt; this instance does not profit from the new families).
* 18:21 yugovostok_s1 (1200 s): root 489.65 (240 SRC), 110 nodes, "lb": 522.778816959702 (stored 500.97; gap 7.3% -> see table).
* 18:41 large_yugovostok_8 (1230 s, N=117): root 407.78 (stored 371.60), 109 nodes, LB 412.62 (gap 63.0% -> 46.8%);
  price-and-branch MIPs found nothing better than the UB 605.73 (the UB is probably far from optimal).
* 18:45 yugovostok_s4 rerun (1455 s): 484.42 < first run 496.79 (not written). 18:58 allday_yugovostok_8 rerun:
  399.11 (written).

## Final summary (19:00) - cg_finalize (no inconsistency), best.py, final_report run on all three sets
| instance | best km | old lb | new lb | gap old -> new |
|---|---|---|---|---|
| yugovostok_s0 | 395.126 | 382.89 | 395.126 | 3.2% -> PROVEN (root enumeration + pool MIP) |
| peak_yugovostok_2 | 390.212 | 332.85 | 390.212 | 17.2% -> PROVEN (root enumeration + pool LP) |
| tight_yugovostok_2 | 371.528 (new, was 380.71) | 362.73 | 371.528 | 5.0% -> PROVEN (new best + tree exhausted) |
| allday_yugovostok_8 | 411.450 | 361.27 | 399.11 | 13.9% -> 3.1% |
| yugovostok_s1 | 537.465 | 500.97 | 522.78 | 7.3% -> 2.8% |
| scarce_yugovostok_8 | 715.231 | 548.65 | 678.59 | 30.4% -> 5.4% |
| real_yugovostok_2 | 306.022 | 260.74 | 287.96 | 17.4% -> 6.3% |
| yugovostok_s2 | 609.078 | 566.67 | 569.39 | 7.5% -> 7.0% |
| yugovostok_s8 | 623.863 | 573.96 | 577.68 | 8.7% -> 8.0% |
| control_yugovostok_mix1_inf | 575.218 | 506.88 | 531.05 | 13.5% -> 8.3% |
| real_yugovostok_8 | 504.278 | 450.18 | 455.07 | 12.0% -> 10.8% |
| yugovostok_s4 | 557.920 | 458.27 | 496.79 | 21.7% -> 12.3% |
| allday_yugovostok_5 | 295.026 | 231.95 | 256.90 | 27.2% -> 14.8% |
| yugovostok_s3 | 535.747 | 455.26 | 465.62 | 17.7% -> 15.1% |
| scarce_yugovostok_2 | 541.249 | 457.48 | 461.71 | 18.3% -> 17.2% |
| large_yugovostok_2 | 452.555 | 381.16 | 382.47 | 18.7% -> 18.3% |
| allday_yugovostok_2 | 466.565 | 350.86 | 375.90 | 33.0% -> 24.1% |
| large_yugovostok_8 | 605.728 | 371.60 | 412.62 | 63.0% -> 46.8% |
| yugovostok_s6 | 537.153 | 511.94 | 511.94 | unchanged (3 runs reached 509.98-511.82) |
After finalize: road proven 19/30, gen_road 46/70, control_road 21/24.
What mattered: (1) the fleet-aware completion bounds (top-4 fleet-cut states in F/H/G) that made pricing AND
enumeration fast with fleet cuts present (cgc's enumeration never completed); (2) new rounded fleet-cut families
(all orders / depot clusters / cluster pairs restricted to window-start or window-end ranges, x skill) with a
small-sets-first separation: the decisive cuts are small time-window packing sets (|S| 5-30, LP 2.5 -> k 3);
(3) cgx's engine: artificial dropping, Lagrangian pruning, lm-SRC, node/root enumeration + exact pool;
(4) branching on unconverged nodes instead of freezing them; fleet-row cap 63 -> 250 (more set branching).
Failed / not helping: integer k(S) via enumeration (degenerate vehicle-min duals, >500k routes), primal simplex for
CG re-solves, reruns from scratch (s6, s4 rerun, real_8 rerun: tree variance, no warm start), s6 in general.
Not handled: instances with best penalty > 0 (tight_yugovostok_8, yugovostok_s7): fleet cuts need full service.
