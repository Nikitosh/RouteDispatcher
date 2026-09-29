"""Разбиение на множества по пулу маршрутов (route-pool set partitioning).
Берём все допустимые маршруты (бригада, последовательность), найденные любыми решателями во всех прогонах
(bench_*.json), добавляем их «подмаршруты» без одной заявки, и CP-SAT выбирает комбинацию:
каждая заявка не более одного раза, каждая бригада не более одного маршрута,
минимум: штраф за невыполненные * 1e6 + бригады * 1e4 + км (в метрах внутри).
python3 sp_pool.py [time_limit_per_instance=10]"""
import glob, json, sys, time
from collections import defaultdict
from validate import load_instance, check
from ortools.sat.python import cp_model
tl=float(sys.argv[1]) if len(sys.argv)>1 else 10
pool=defaultdict(set); src_best={}; best_routes={}
for f in glob.glob('bench_tl*.json'):
    for r in json.load(open(f)):
        if not r.get('routes') or not r['ok']: continue
        for v,rt in enumerate(r['routes']):
            if rt: pool[r['inst']].add((v,tuple(rt)))
        key=(r['unserved'],r['used'],r['km'])
        if key<src_best.get(r['inst'],(1e9,)): src_best[r['inst']]=key; best_routes[r['inst']]=r['routes']
def feas(I,v,rt):
    ve=I['veh'][v]; m=ve['mode']; t=0; prev=ve['start']; km=0
    for k in rt:
        o=I['ords'][k]
        if not (ve['mask']>>o['skill'])&1: return None
        n=I['S']+k; beg=max(t+I['T'][m][prev][n],o['a'])
        if beg>o['b']+1e-6: return None
        t=beg+o['svc']
        if t>720+1e-6: return None
        km+=I['D'][m][prev][n]; prev=n
    return km
out={}
print(f"{'задача':16} {'маршрутов':>9} {'лучший решатель':>22} {'пул+CP-SAT':>22} {'с':>5}")
for inst in sorted(pool):
    I=load_instance(f'instances/{inst}.txt'); N,V=I['N'],I['V']
    cand={}
    for v,rt in pool[inst]:
        # тот же маршрут для другой бригады + подмаршруты без одной заявки
        for w in range(V):
            for sub in [rt]+[rt[:i]+rt[i+1:] for i in range(len(rt))]:
                if sub and (w,sub) not in cand:
                    km=feas(I,w,sub)
                    if km is not None: cand[(w,sub)]=km
    cand=list(cand.items())
    m=cp_model.CpModel(); x=[m.NewBoolVar('') for _ in cand]
    byk=defaultdict(list); byv=defaultdict(list)
    for i,((v,rt),km) in enumerate(cand):
        byv[v].append(x[i]); [byk[k].append(x[i]) for k in rt]
    served=[m.NewBoolVar('') for _ in range(N)]
    for k in range(N): m.Add(sum(byk[k])==served[k])
    for v in range(V): m.Add(sum(byv[v])<=1)
    pen={1:100,2:50,3:20}
    m.Minimize(sum(int(pen[I['ords'][k]['pri']]*1e9)*(1-served[k]) for k in range(N))+sum((10_000_000+int(km*1000))*x[i] for i,((v,rt),km) in enumerate(cand)))
    hint={(v,tuple(rt)) for v,rt in enumerate(best_routes[inst]) if rt}
    for i,((v,rt),km) in enumerate(cand): m.AddHint(x[i],1 if (v,rt) in hint else 0)
    s=cp_model.CpSolver(); s.parameters.max_time_in_seconds=tl; s.parameters.num_workers=8
    t0=time.time(); st=s.Solve(m)
    routes=[[] for _ in range(V)]
    for i,((v,rt),km) in enumerate(cand):
        if s.Value(x[i]): routes[v]=list(rt)
    c=check(I,routes); out[inst]=dict(routes=routes,**{k:c[k] for k in('ok','unserved','used','km')})
    b=src_best[inst]
    print(f"{inst:16} {len(cand):9d} {b[0]:3d} невып {b[1]:2d} бр {b[2]:7.1f} км {c['unserved']:3d} невып {c['used']:2d} бр {c['km']:7.1f} км {time.time()-t0:5.1f} {'OPT' if st==cp_model.OPTIMAL else ''} {'' if c['ok'] else 'ОШИБКА'}")
json.dump(out,open('sp_pool_result.json','w'))
