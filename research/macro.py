"""Планирование верхнего уровня: какая бригада в каком кластере в каком двухчасовом слоте (CP-SAT), затем стартовое решение
жадной вставкой по плану. python macro.py <задача> <выход.out> [предел бригад K]"""
import sys, math, statistics as st
from validate import load_instance, check
from ortools.sat.python import cp_model
path,out=sys.argv[1],sys.argv[2]; I=load_instance(path); N,V,S=I['N'],I['V'],I['S']
T=I['T']; D=I['D']; O=I['ords']; VE=I['veh']
# кластеры: ближайшая различная стартовая точка по машине
locs=[]; start_cl={}
for s in range(S):
    same=[c for c,l in enumerate(locs) if T[0][l][s]<3 and T[0][s][l]<3]
    if same: start_cl[s]=same[0]
    else: locs.append(s); start_cl[s]=len(locs)-1
C=len(locs); cl=[min(range(C),key=lambda c:T[0][locs[c]][S+k]) for k in range(N)]
SL=6
def slot(k):
    o=O[k]
    return None if o['b']-o['a']>200 else min(SL-1,int(o['a']//120))
load=[[0.0]*SL for _ in range(C)]; sl=[None]*N
for k in range(N):
    s=slot(k)
    if s is not None: sl[k]=s; load[cl[k]][s]+=O[k]['svc']
for k in range(N):
    if sl[k] is None:
        c=cl[k]; s=min(range(SL-1),key=lambda s:load[c][s]); sl[k]=s; load[c][s]+=O[k]['svc']
# средний внутренний переезд по кластеру и виду транспорта
intra={}
for c in range(C):
    ks=[k for k in range(N) if cl[k]==c]
    for m in range(4):
        vals=[min(T[m][S+i][S+j] for j in ks if j!=i) for i in ks if len(ks)>1]
        intra[(c,m)]=st.mean(vals) if vals else 10
avgsvc={c:(st.mean([O[k]['svc'] for k in range(N) if cl[k]==c]) if any(cl[k]==c for k in range(N)) else 50) for c in range(C)}
CAP=150
m=cp_model.CpModel()
y={(b,c,s):m.NewBoolVar('') for b in range(V) for c in range(C) for s in range(SL)}
u=[m.NewBoolVar('') for b in range(V)]
for b in range(V):
    for s in range(SL):
        m.Add(sum(y[b,c,s] for c in range(C))<=1)
        for c in range(C): m.Add(y[b,c,s]<=u[b])
# переходы и стартовые переезды съедают ёмкость (в минутах, масштаб 1)
trans=[]; cost_terms=[]
capv={}
for b in range(V):
    mb=VE[b]['mode']; hc=start_cl[VE[b]['start']]
    for s in range(SL):
        for c in range(C):
            eff=CAP*avgsvc[c]/(avgsvc[c]+intra[(c,mb)])
            capv[b,c,s]=int(eff)
    for c in range(C):
        if c!=hc:
            t=int(T[mb][locs[hc]][locs[c]]); cost_terms.append(t*y[b,c,0])
    for s in range(SL-1):
        for c in range(C):
            for c2 in range(C):
                if c!=c2:
                    z=m.NewBoolVar(''); m.Add(z>=y[b,c,s]+y[b,c2,s+1]-1)
                    t=int(T[mb][locs[c]][locs[c2]]); trans.append((b,c2,s+1,t,z)); cost_terms.append(t*z)
# ёмкость по кластеру-слоту и по навыкам
for c in range(C):
    for s in range(SL):
        ks=[k for k in range(N) if cl[k]==c and sl[k]==s]
        if not ks: continue
        need=sum(O[k]['svc'] for k in ks)
        loss=[t*z for (b,c2,s2,t,z) in trans if c2==c and s2==s]
        m.Add(sum(capv[b,c,s]*y[b,c,s] for b in range(V))-sum(loss)>=int(need))
        for g in range(3):
            ng=sum(O[k]['svc'] for k in ks if O[k]['skill']==g)
            if ng: m.Add(sum(capv[b,c,s]*y[b,c,s] for b in range(V) if (VE[b]['mask']>>g)&1)>=int(ng))
K=int(sys.argv[3]) if len(sys.argv)>3 else None
if K: m.Add(sum(u)<=K)
m.Minimize(100000*sum(u)+sum(cost_terms))
sv=cp_model.CpSolver(); sv.parameters.max_time_in_seconds=float(__import__('os').environ.get('MTL','5')); sv.parameters.num_workers=4
st_=sv.Solve(m)
if st_ not in (cp_model.OPTIMAL,cp_model.FEASIBLE): print('план не найден'); sys.exit(1)
plan={(b,c,s) for (b,c,s),v in y.items() if sv.Value(v)}
used=[b for b in range(V) if sv.Value(u[b])]
print(f"кластеров {C}, план: бригад {len(used)}, межкластерных минут {sum(sv.Value(t) for t in [0]) if False else int(sv.ObjectiveValue())%100000}")
# жадная вставка по плану
routes=[[] for _ in range(V)]
def feasible(b,r):
    mb=VE[b]['mode']; t=0; p=VE[b]['start']
    for k in r:
        if not (VE[b]['mask']>>O[k]['skill'])&1: return None
        beg=max(t+T[mb][p][S+k],O[k]['a'])
        if beg>O[k]['b']+1e-6: return None
        t=beg+O[k]['svc']
        if t>720+1e-6: return None
        p=S+k
    return True
def kmr(b,r):
    mb=VE[b]['mode']; p=VE[b]['start']; s=0
    for k in r: s+=D[mb][p][S+k]; p=S+k
    return s
order=sorted(range(N),key=lambda k:(O[k]['b'],O[k]['a']))
left=[]
for k in order:
    best=None
    for pref in (True,False):
        cands=[b for b in used if (not pref or (b,cl[k],sl[k]) in plan)]
        for b in cands:
            for pos in range(len(routes[b])+1):
                r=routes[b][:pos]+[k]+routes[b][pos:]
                if feasible(b,r):
                    dl=kmr(b,r)-kmr(b,routes[b])
                    if best is None or dl<best[0]: best=(dl,b,pos)
        if best: break
    if best: routes[best[1]].insert(best[2],k)
    else: left.append(k)
c=check(I,routes); print(f"стартовое решение: бригад {c['used']}, км {c['km']:.1f}, не вставлено {len(left)}, допустимо {c['ok']}")
with open(out,'w') as f:
    f.write('SOLVER macro\n'); [f.write(f"ROUTE {v} {' '.join(map(str,r))}\n") for v,r in enumerate(routes)]
