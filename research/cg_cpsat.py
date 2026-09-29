"""Experimental: solve a dumped pool (CG_DUMP pickle) with CP-SAT. Usage: cg_cpsat.py <pickle> [tl] [workers]"""
import sys, pickle, time
from ortools.sat.python import cp_model
d = pickle.load(open(sys.argv[1], 'rb')); tl = float(sys.argv[2]) if len(sys.argv) > 2 else 120; W = int(sys.argv[3]) if len(sys.argv) > 3 else 2
N, R = d['N'], d['routes']; m = cp_model.CpModel()
x = [m.NewBoolVar(f'x{j}') for j in range(len(R))]
by = [[] for _ in range(N)]
for j, (t, km, r) in enumerate(R):
    for k in r: by[k].append(x[j])
for k in range(N): m.AddExactlyOne(by[k])
for t, ty in enumerate(d['types']): m.Add(sum(x[j] for j in range(len(R)) if R[j][0] == t) <= ty['count'])
m.Add(sum(x) <= d['K'])
for S in d['cuts']:
    m.Add(sum(x[j] for j in range(len(R)) if len(S & set(R[j][2])) >= 2) <= 1)
m.Minimize(sum(int(round(km * 1000)) * x[j] for j, (t, km, r) in enumerate(R)))
s = cp_model.CpSolver(); s.parameters.max_time_in_seconds = tl; s.parameters.num_workers = W
t0 = time.time(); st = s.Solve(m)
print(s.StatusName(st), s.ObjectiveValue() / 1000, s.BestObjectiveBound() / 1000, 'UB', d['UB'], 'LP', d['lp'], f'{time.time()-t0:.1f}s')
