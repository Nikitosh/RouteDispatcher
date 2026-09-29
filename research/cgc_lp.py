"""cgc: analysis helper - stage-3 km LP (existing pricer) and dump of LP solution (pickle) for cut design."""
import sys, os, time, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cg_master as CM
from validate import load_instance, check, parse_output
HERE = os.path.dirname(os.path.abspath(__file__))

def run(inst, cuts=True, tl=300):
    d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    I = load_instance(inst); N = I['N']
    Rb, _ = parse_output(open(os.path.join(HERE, 'best', d, name + '.out')).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    P, K, UB = CM.score(I, Rb)
    pr = CM.Pricer(inst, 10); types = pr.types; T = len(types)
    vtype = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
    M = CM.Master(I, types)
    for v, r in enumerate(Rb):
        if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
    for t in range(T):
        for k in range(N):
            c = check(I, [[]] * types[t]['vehs'][0] + [[k]])
            if c['ok']: M.add(t, c['km'], (k,))
    M.set_stage(3, P, K)
    t0 = time.time(); level = 1
    while True:
        z, du, x = M.solve()
        pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        cc = [(S[0], S[1], S[2], du[row]) for S, row in zip(M.cuts, M.cutrow)]
        alpha = [-mu[t] - lam for t in range(T)]
        cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 1.0, alpha, pi, cuts=cc)
        added = sum(M.add(t, km, r) for (t, km, rc, r) in cols)
        if not added:
            if level == 1: level = 2; continue
            print(f'conv z={z:.3f} cuts={len(M.cuts)} comp={comp} t={time.time()-t0:.0f}', flush=True)
            if not cuts or len(M.cuts) >= 150: break
            new = M.separate(x, 30)
            if not new: break
            for S in new: M.add_cut(S)
            level = 1; continue
        if level == 2 and added < 5: level = 1
    sol = [(M.cols[j], x[M.hidx[j]]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-6]
    pickle.dump(dict(types=types, sol=sol, z=z, UB=UB, K=K, cuts=M.cuts, cols=M.cols), open(f'runs/results/cgc_tmp/{name}_lp.pkl', 'wb'))
    pr.close()
    return z

if __name__ == '__main__':
    print(run(sys.argv[1], cuts=len(sys.argv) < 3))
