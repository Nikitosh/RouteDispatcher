"""Fleet-dominance bounds between instance pairs X_all / X_inf: if the orders and matrices are identical and every
vehicle of X_inf can be injectively mapped to a vehicle of X_all with the same start & mode and a superset skill
mask, then every X_inf solution is an X_all solution, hence LB(X_inf) >= LB(X_all) (same K and penalty level).
Usage: cg_dominance.py <dir> ..."""
import sys, os, glob, json
from validate import load_instance
def dominated(Ii, Ia):
    if Ii['N'] != Ia['N'] or Ii['S'] != Ia['S'] or Ii['ords'] != Ia['ords'] or Ii['T'] != Ia['T'] or Ii['D'] != Ia['D']: return False
    used = [False] * Ia['V']
    for v in sorted(Ii['veh'], key=lambda v: -bin(v['mask']).count('1')):
        ok = False
        for j, w in enumerate(Ia['veh']):
            if not used[j] and w['start'] == v['start'] and w['mode'] == v['mode'] and (w['mask'] & v['mask']) == v['mask']:
                used[j] = True; ok = True; break
        if not ok: return False
    return True
for d in sys.argv[1:]:
    for pa in sorted(glob.glob(f'{d}/*_all.txt')):
        pi = pa.replace('_all.txt', '_inf.txt')
        if not os.path.exists(pi): continue
        na, ni = os.path.basename(pa)[:-4], os.path.basename(pi)[:-4]
        fa, fi = f'lb/{d}/{na}.json', f'lb/{d}/{ni}.json'
        if not (os.path.exists(fa) and os.path.exists(fi)): continue
        Ia, Ii = load_instance(pa), load_instance(pi)
        if not dominated(Ii, Ia): print(d, ni, 'not dominated by', na); continue
        A, B = json.load(open(fa)), json.load(open(fi))
        ch = []
        if A.get('best_pen', 0) == 0 and B.get('best_pen', 0) == 0:
            if A.get('lb_used', 0) > B.get('lb_used', 0): B['lb_used'] = A['lb_used']; ch.append('lb_used')
            if A.get('km_K') == B.get('km_K') and A.get('lb_km') and (B.get('lb_km') or 0) < A['lb_km']:
                B['lb_km'] = A['lb_km']; ch.append(f"lb_km<-{A['lb_km']}")
        if ch:
            B['dominance'] = f'from {na}: ' + ','.join(ch); B['method'] = B.get('method', '') + ' + fleet dominance'
            json.dump(B, open(fi, 'w'), indent=1)
        print(d, ni, 'dominated by', na, ch)
