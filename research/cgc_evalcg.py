"""cgc: evaluate rank-1 Chvatal-Gomory cuts from the vehicle-min duals of a subset S on a dumped LP solution:
sum_r ceil(theta*(sum_{i in r&S} pi_i + mu_t(r))) x_r >= ceil(theta*z_S)."""
import sys, os, math, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance
from cgc_master import build_cands
from cgc_sub import kS
inst = sys.argv[1]; name = os.path.basename(inst)[:-4]
I = load_instance(inst); D = pickle.load(open(f'runs/results/cgc_tmp/{name}_lp.pkl', 'rb'))
cand = build_cands(I)
want = sys.argv[2].split(',')
print('LP z', D['z'])
for S, nm in cand.items():
    if nm not in want: continue
    z, conv, pi, mu, types = kS(inst, S, want_duals=True)
    rc_lhs = sum(x for (t, km, r), x in D['sol'] if set(r) & S)
    out = [f'{nm} |S|={len(S)} z={z:.3f} RC lhs {rc_lhs:.3f} rhs {math.ceil(z-1e-6)}']
    for th in (1, 1.5, 2, 3, 4, 6):
        rhs = math.ceil(th * z - 1e-6)
        lhs = sum(x * math.ceil(th * (sum(pi.get(i, 0) for i in r) + mu[t]) - 1e-6) for (t, km, r), x in D['sol'])
        out.append(f'th{th}: {lhs:.3f} >= {rhs} viol {rhs-lhs:.3f}')
    print(' | '.join(out), flush=True)
