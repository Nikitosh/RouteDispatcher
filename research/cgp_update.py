"""Raise lb_pen / lb_used in lb/<set>/<inst>.json after a completed cgp_bp proof. Usage: cgp_update.py <set> <inst> <lb_pen|lb_used> <value> <method> <seconds>"""
import sys, json, os
d, name, field, val, meth, secs = sys.argv[1:7]; val = int(val)
f = f'lb/{d}/{name}.json'; L = json.load(open(f))
old = L.get(field, 0) or 0
if val > old:
    L[field] = val; L['method'] = (L.get('method', '') + f' + cgp:{field}({meth})').strip(' +')
    L.setdefault('cgp', {})[field] = dict(old=old, new=val, method=meth, seconds=float(secs))
    json.dump(L, open(f, 'w'), indent=1); print('updated', f, field, old, '->', val)
else: print('not higher', f, field, old, val)
