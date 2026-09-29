"""Сравнение прогонов из runs/hy_runs/<instdir>: python3 hy_cmp.py <instdir> <tag-regex ...>
Для каждой метки (без _s<seed>): среднее бригад, км, число «лучших» (по best-known среди сравниваемых), средний gap км при равном парке."""
import glob, os, re, sys, statistics
from collections import defaultdict
from validate import load_instance, check, parse_output
PEN = {1: 100, 2: 50, 3: 20}
d = sys.argv[1]; pats = sys.argv[2:]
rows = defaultdict(dict); Is = {}
for f in glob.glob(f'runs/hy_runs/{d}/*/*.out'):
    tag = f.split('/')[2]; base = re.sub(r'_s\d+$', '', tag)
    if pats and not any(re.fullmatch(p, base) for p in pats): continue
    inst = os.path.basename(f)[:-4]
    if inst not in Is: Is[inst] = load_instance(f'{d}/{inst}.txt')
    I = Is[inst]; r, _ = parse_output(open(f).read()); r = (r + [[]] * I['V'])[:I['V']]; c = check(I, r)
    if not c['ok']: sc = (10**9, 99, 1e9)
    else:
        served = {k for x in r for k in x}; sc = (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served), c['used'], c['km'])
    rows[base].setdefault(inst, {})[re.search(r'_s(\d+)$', tag).group(1)] = sc
# виртуальные ансамбли: ENS="s10_sa_t3+s12_alns_t3;..." -> минимум по тем же сидам
for e in filter(None, os.environ.get('ENS', '').split(';')):
    parts = e.split('+'); name = 'min(' + ','.join(parts) + ')'
    for inst in Is:
        if all(inst in rows.get(p_, {}) for p_ in parts):
            for sd in rows[parts[0]][inst]:
                if all(sd in rows[p_][inst] for p_ in parts): rows[name].setdefault(inst, {})[sd] = min(rows[p_][inst][sd] for p_ in parts)
rows = {b: {i: list(v.values()) for i, v in R.items()} for b, R in rows.items()}
insts = sorted(Is); best = {i: min(min(v[i]) for v in rows.values() if i in v) for i in insts}
print(f"{'метка':28} {'n':>4} {'штраф':>6} {'бригад':>7} {'км':>8} {'лучших':>7} {'gap км':>7}  (среднее по сидам)")
for base in sorted(rows):
    R = rows[base]; sc = [s for i in R for s in R[i]]
    nb = sum(1 for i in R for s in R[i] if s[:2] == best[i][:2] and s[2] <= best[i][2] + 0.05)
    gaps = [s[2] / best[i][2] - 1 for i in R for s in R[i] if s[:2] == best[i][:2]]
    print(f"{base[:28]:28} {len(sc):4d} {statistics.mean(s[0] for s in sc):6.1f} {statistics.mean(s[1] for s in sc):7.3f} {statistics.mean(s[2] for s in sc):8.1f} {nb:7d} {statistics.mean(gaps)*100 if gaps else 0:6.2f}%")
