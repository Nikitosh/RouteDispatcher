"""Summary table of CG lower bounds vs best known. Usage: cg_report.py <dir> [...]"""
import sys, os, glob, json
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
for d in sys.argv[1:]:
    cheap = json.load(open(f'lb/{d}/_cheap.json')) if os.path.exists(f'lb/{d}/_cheap.json') else {}
    print(f'\n== {d}')
    print(f"{'inst':30} {'pen':>4} {'LBp':>4} | {'veh':>3} {'LBv':>3} {'chp':>3} | {'km':>8} {'LBkm':>8} {'gap%':>6} {'asg':>7} | opt  sec  status")
    n = nopt = nveh = 0; gaps = []
    for p in sorted(glob.glob(f'{d}/*.txt')):
        name = os.path.basename(p)[:-4]; f = f'lb/{d}/{name}.json'
        bf = f'best/{d}/{name}.out'
        if not os.path.exists(bf): continue
        I = load_instance(p); R, _ = parse_output(open(bf).read()); R = (R + [[]] * I['V'])[:I['V']]
        c = check(I, R); served = {k for r in R for k in r}
        pen = sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served); used = c['used']; km = c['km']
        ch = cheap.get(name, {})
        if not os.path.exists(f):
            print(f"{name:30} {pen:4d} {'':>4} | {used:3d} {'':>3} {ch.get('lb_used_cheap',''):>3} | {km:8.2f}  (no LB yet)"); continue
        L = json.load(open(f)); n += 1
        lbk = L.get('lb_km'); gap = (km / lbk - 1) * 100 if (lbk and lbk > 0 and L.get('lb_used') == used and L.get('lb_pen', 0) >= pen) else None
        popt = L.get('lb_pen', 0) >= pen and L.get('lb_used', 0) >= used and L.get('lb_km') is not None and L['lb_km'] >= km - 1e-3
        nopt += popt; nveh += (L.get('lb_used', 0) >= used and L.get('lb_pen', 0) >= pen)
        if gap is not None: gaps.append(gap)
        print(f"{name:30} {pen:4d} {L.get('lb_pen',0):4d} | {used:3d} {L.get('lb_used','-'):>3} {ch.get('lb_used_cheap',''):>3} | {km:8.2f} {lbk or 0:8.2f} {gap if gap is not None else float('nan'):6.2f} {ch.get('lb_km_assign') or 0:7.1f} | {'YES' if popt else 'no ':3} {L.get('seconds',0):5.0f} {L.get('pool_status', '')}")
    if n: print(f"instances with LB: {n}; pen+fleet proven: {nveh}; fully proven optimal: {nopt}; mean km gap {sum(gaps)/max(1,len(gaps)):.3f}% max {max(gaps) if gaps else 0:.3f}%")
