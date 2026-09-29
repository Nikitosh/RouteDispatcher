"""Таблица по задачам: лучший (бригады, км) каждой метки (минимум по сидам). python3 hy_inst.py <dir> <regex...>"""
import glob, os, re, sys
from collections import defaultdict
from validate import load_instance, check, parse_output
d = sys.argv[1]; pats = sys.argv[2:]; R = defaultdict(dict); Is = {}
for f in glob.glob(f'runs/hy_runs/{d}/*/*.out'):
    tag = f.split('/')[2]; base = re.sub(r'_s\d+$', '', tag)
    if pats and not any(re.fullmatch(p, base) for p in pats): continue
    inst = os.path.basename(f)[:-4]; I = Is.setdefault(inst, load_instance(f'{d}/{inst}.txt'))
    r, _ = parse_output(open(f).read()); c = check(I, (r + [[]] * I['V'])[:I['V']])
    sc = (c['unserved'], c['used'], round(c['km'], 1)) if c['ok'] else (99, 99, 0)
    R[inst][base] = min(R[inst].get(base, sc), sc)
labs = sorted({b for i in R for b in R[i]})
print('inst'.ljust(28), *[l[:11].rjust(11) for l in labs])
for i in sorted(R):
    b = min(R[i].values())
    print(i[:28].ljust(28), *[((('*' if R[i].get(l) == b else '') + (f"{R[i][l][1]}/{R[i][l][2]}" if l in R[i] else '-'))).rjust(11) for l in labs])
