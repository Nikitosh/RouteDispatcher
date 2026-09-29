"""cgm: integer strengthening of k(S) (vehicle-min on the order subset S): LP -> if LP converged, enumerate the
routes with reduced cost <= ceil(LP) - LP and solve the partitioning MIP (cgc_sub.kS integer=True); infeasible ->
k(S) = ceil(LP) + 1.  Usage: cgm_kint.py <inst> [maxsets]  (base cluster unions, largest first)"""
import sys, os, json, time, math
os.environ.setdefault('CGC_TMP', '/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/d8f727be-c46e-4a0a-ac5e-5e8ed1e794e5/scratchpad/cgm_tmp')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance
from cgc_master import build_cands
from cgc_sub import kS
inst = sys.argv[1]; mx = int(sys.argv[2]) if len(sys.argv) > 2 else 6
I = load_instance(inst); N = I['N']
cand = build_cands(I, print)
bs = sorted([S for S, nm in cand.items() if '[' not in nm and 'sk' not in nm], key=lambda S: -len(S))[:mx]
for S in bs:
    t0 = time.time(); v, conv = kS(inst, S, 10, integer=True, enum_max=500000, mip_tl=120)
    print(f'{cand[S]} |S|={len(S)}: bound {v:.4f} conv {conv} k={math.ceil(v - 1e-4)} ({time.time()-t0:.1f}s)', flush=True)
