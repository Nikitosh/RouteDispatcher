"""Refresh proven_optimal flags in lb/<dir>/*.json against the current best known solutions and sanity-check
(no LB may exceed a feasible solution value). Usage: cg_finalize.py <dir> [...]"""
import sys, os, glob, json
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
for d in sys.argv[1:]:
    for f in sorted(glob.glob(f'lb/{d}/*.json')):
        name = os.path.basename(f)[:-5]
        if name.startswith('_'): continue
        bf = f'best/{d}/{name}.out'
        if not os.path.exists(bf): continue
        I = load_instance(f'{d}/{name}.txt'); R, _ = parse_output(open(bf).read()); R = (R + [[]] * I['V'])[:I['V']]
        c = check(I, R)
        if not c['ok']: continue
        served = {k for r in R for k in r}; pen = sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served)
        L = json.load(open(f))
        # combine with the cheap assignment bound (valid when all orders are served, K <= K_cheap) and clamp at 0
        chf = f'lb/{d}/_cheap.json'
        ch = json.load(open(chf)).get(name, {}) if os.path.exists(chf) else {}
        if L.get('lb_km') is not None and L['lb_km'] < 0: L['lb_km_cg_raw'] = L['lb_km']; L['lb_km'] = 0.0
        if pen == 0 and ch.get('lb_km_assign') and ch.get('K', 0) >= c['used'] and L.get('km_K', c['used']) >= c['used']:
            if (L.get('lb_km') or 0) < ch['lb_km_assign']:
                L['lb_km'] = round(ch['lb_km_assign'] - 1e-4, 4); L['lb_km_source'] = 'assignment bound (CG weaker)'
        bad = []
        if L.get('lb_pen', 0) > pen: bad.append(f"lb_pen {L['lb_pen']} > pen {pen}")
        if L.get('lb_pen', 0) >= pen and L.get('lb_used', 0) > c['used']: bad.append(f"lb_used {L['lb_used']} > used {c['used']}")
        if L.get('lb_km') and L.get('lb_used') == c['used'] and L.get('lb_pen', 0) >= pen and L.get('km_K', c['used']) >= c['used'] and L['lb_km'] > c['km'] + 1e-3:
            bad.append(f"lb_km {L['lb_km']} > km {c['km']:.4f}")
        if bad: print('!!! INCONSISTENT', d, name, bad)
        po = L.get('lb_pen', 0) >= pen and L.get('lb_used', -1) >= c['used'] and L.get('lb_km') is not None and L['lb_km'] >= c['km'] - 1e-3 and L.get('km_K', c['used']) >= c['used']
        L['proven_optimal'] = bool(po); L['best_at_finalize'] = [pen, c['used'], round(c['km'], 4)]
        json.dump(L, open(f, 'w'), indent=1)
    print(d, 'finalized')
