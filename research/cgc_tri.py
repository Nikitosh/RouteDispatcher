"""cgc: check the 'dropping an order keeps a route feasible' condition t_ij <= t_ik + svc_k + t_kj (i any node, k,j orders)."""
import sys, numpy as np
from validate import load_instance
def check(path):
    I = load_instance(path); S, N = I['S'], I['N']; svc = np.array([o['svc'] for o in I['ords']], float)
    modes = sorted(set(v['mode'] for v in I['veh'])); worst = 0
    for m in modes:
        T = np.array(I['T'][m]); O = T[:, S:]   # to orders
        for k in range(N):
            via = T[:, S + k][:, None] + svc[k] + T[S + k, S:][None, :]   # i -> k -> j
            viol = O - via; viol[:, k] = -1e9; viol[S + k, :] = -1e9
            worst = max(worst, viol.max())
    return worst
if __name__ == '__main__':
    for p in sys.argv[1:]: print(p, check(p))
