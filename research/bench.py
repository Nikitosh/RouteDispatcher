"""Прогон всех решателей на всех задачах, независимая проверка, сводная таблица.
python3 bench.py [time_limit=1] [seeds=1] [solver_filter]"""
import glob, os, subprocess, sys, statistics, json, re
from validate import load_instance, check, parse_output
tl=float(sys.argv[1]) if len(sys.argv)>1 else 1.0; nseeds=int(sys.argv[2]) if len(sys.argv)>2 else 1
flt=sys.argv[3] if len(sys.argv)>3 else ''
srcs=[p for p in sorted(glob.glob('solvers/s*.cpp')) if flt in p]
for p in srcs:
    b='bin/'+os.path.basename(p)[:-4]
    r=subprocess.run(['make','-s',b],capture_output=True,text=True)
    if r.returncode: print('BUILD FAILED',p,r.stderr[-500:])
solvers=[s for s in sorted(glob.glob('bin/s*')) if flt in s and os.path.exists('solvers/'+os.path.basename(s)+'.cpp')]
EXT={'ortools':'ortools_ref.py','pyvrp':'pyvrp_ref.py','vroom':'vroom_ref.py'}
for e in EXT:
    if flt in e: solvers.append(e)
INST=os.environ.get('INST','instances')
insts=sorted(glob.glob(f'{INST}/*.txt')); I={p:load_instance(p) for p in insts}
rows=[]
from concurrent.futures import ThreadPoolExecutor
WORKERS=int(os.environ.get('WORKERS',max(1,(os.cpu_count() or 2)//2)))
def run(job):
    s,p,sd=job
    cmd=[sys.executable,EXT[s],p,str(tl),str(sd)] if s in EXT else [s,p,str(tl),str(sd)]
    out=subprocess.run(cmd,capture_output=True,text=True,timeout=tl*20+120).stdout
    routes,res=parse_output(out); c=check(I[p],routes)
    pen=sum({1:100,2:50,3:20}[I[p]['ords'][k]['pri']] for k in range(I[p]['N']) if all(k not in r for r in routes))
    return dict(pen=pen,solver=os.path.basename(s),inst=os.path.basename(p)[:-4],region=os.path.basename(p).split('_')[0],seed=sd,
                ok=c['ok'],unserved=c['unserved'],used=c['used'],km=c['km'],ms=res.get('ms',0),errs=c['errs'][:2],routes=routes)
jobs=[(s,p,sd) for s in solvers for p in insts for sd in range(1,nseeds+1)]
with ThreadPoolExecutor(WORKERS) as ex: rows=list(ex.map(run,jobs))
json.dump(rows,open(f'bench_{os.path.basename(INST)}_tl{tl}_{flt or "all"}.json','w'),ensure_ascii=False,indent=0)
# лучший известный результат по задаче: (unserved, used, km)
best={}
for r in rows:
    if r['ok']:
        key=(r.get('pen',r['unserved']),r['used'],r['km']); best[r['inst']]=min(best.get(r['inst'],key),key)
print(f"\nЛимит {tl} с, сидов {nseeds}. gap = пробег относительно лучшего найденного на той же задаче при том же числе бригад")
print(f"{'решатель':24} {'ошибок':>6} {'все заявки':>10} {'бригад':>7} {'км':>7} {'лучших':>7} {'gap км':>7} {'мс':>8}")
for s in dict.fromkeys(r['solver'] for r in rows):
    rs=[r for r in rows if r['solver']==s]
    bad=sum(not r['ok'] for r in rs); full=sum(r['ok'] and r['unserved']==0 for r in rs)
    nb=sum(1 for r in rs if r['ok'] and (r.get('pen',r['unserved']),r['used'])==best[r['inst']][:2] and r['km']<=best[r['inst']][2]*1.0005)
    gaps=[r['km']/best[r['inst']][2]-1 for r in rs if r['ok'] and (r.get('pen',r['unserved']),r['used'])==best[r['inst']][:2]]
    print(f"{s:24} {bad:6d} {full:5d}/{len(rs):<4d} {statistics.mean(r['used'] for r in rs):7.2f} {statistics.mean(r['km'] for r in rs):7.1f} {nb:7d} "
          f"{(statistics.mean(gaps)*100 if gaps else float('nan')):6.1f}% {statistics.mean(r['ms'] for r in rs):8.1f}")
    for r in rs:
        if r['errs']: print("    ",r['inst'],r['errs']); break
