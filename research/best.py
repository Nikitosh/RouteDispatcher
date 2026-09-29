"""Хранилище лучших известных решений и таблица результатов.
Источники решений:
  1) bench_<папка>_tl*.json (прогоны bench.py, поле routes); старые bench_tl*.json относятся к папке instances;
  2) runs/results/<папка_задач>/<метка>/<задача>.out — вывод решателя (строки ROUTE v k1 k2 ...).
Нижние границы: lb/<папка_задач>/<задача>.json с полями lb_used (бригады) и lb_km (км при лучшем числе бригад), по желанию lb_pen.
Результат: best/<папка_задач>/<задача>.out и таблица. python3 best.py [папка_задач ...]"""
import glob, json, os, sys
from validate import load_instance, check, parse_output
PEN={1:100,2:50,3:20}
def score(I,routes):
    c=check(I,routes)
    if not c['ok']: return None
    served={k for r in routes for k in r}
    pen=sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in served)
    return (pen,c['used'],round(c['km'],3))
def collect(d):
    cands={}   # inst -> list of (routes, tag)
    files=glob.glob(f'bench_{d}_tl*.json')+(glob.glob('bench_tl*.json')+glob.glob('runs/old_bench/bench_tl*.json') if d=='instances' else [])
    for f in files:
        try: rows=json.load(open(f))
        except Exception: continue
        for r in rows:
            if r.get('routes'): cands.setdefault(r['inst'],[]).append((r['routes'],f"{r['solver']}@{os.path.basename(f)}"))
    for f in glob.glob(f'runs/results/{d}/**/*.out',recursive=True):
        inst=os.path.basename(f)[:-4]; routes,_=parse_output(open(f).read())
        if routes: cands.setdefault(inst,[]).append((routes,os.path.relpath(f,f'runs/results/{d}')))
    bf=f'best/{d}'
    for f in glob.glob(f'{bf}/*.out'):
        inst=os.path.basename(f)[:-4]; routes,_=parse_output(open(f).read())
        if routes: cands.setdefault(inst,[]).append((routes,'best'))
    return cands
def run(d):
    os.makedirs(f'best/{d}',exist_ok=True); cands=collect(d); table=[]
    meta=json.load(open(f'best/{d}/_meta.json')) if os.path.exists(f'best/{d}/_meta.json') else {}
    for p in sorted(glob.glob(f'{d}/*.txt')):
        inst=os.path.basename(p)[:-4]; I=load_instance(p); bestv=None
        for routes,tag in cands.get(inst,[]):
            routes=(routes+[[]]*I['V'])[:I['V']]
            s=score(I,routes)
            if s and (bestv is None or s<bestv[0]): bestv=(s,routes,tag if tag!='best' else meta.get(inst,{}).get('tag','best'))
        if not bestv: table.append((inst,None,None,None)); continue
        s,routes,tag=bestv
        with open(f'best/{d}/{inst}.out','w') as f:
            f.write(f"SOLVER best:{tag}\n"); [f.write(f"ROUTE {v} {' '.join(map(str,r))}\n") for v,r in enumerate(routes)]
        meta[inst]={'tag':tag,'pen':s[0],'used':s[1],'km':s[2]}
        lbf=f'lb/{d}/{inst}.json'; lb=json.load(open(lbf)) if os.path.exists(lbf) else None
        table.append((inst,s,tag,lb))
    json.dump(meta,open(f'best/{d}/_meta.json','w'),ensure_ascii=False,indent=1)
    print(f"\n== {d}: лучшие известные решения")
    print(f"{'задача':34} {'штраф':>5} {'бр':>3} {'км':>8} | {'НГ бр':>5} {'НГ км':>8} {'разрыв':>7} | источник")
    tot=[0,0,0.0]; lbt=[0,0.0,0]
    for inst,s,tag,lb in table:
        if not s: print(f"{inst:34} нет решения"); continue
        tot[0]+=s[0]; tot[1]+=s[1]; tot[2]+=s[2]
        lbs=''
        if lb:
            gap='' 
            if lb.get('lb_used') is not None and lb['lb_used']==s[1] and lb.get('lb_km'): gap=f"{(s[2]/lb['lb_km']-1)*100:6.2f}%"; lbt[1]+=lb['lb_km']; lbt[2]+=1
            elif lb.get('lb_used') is not None: gap=f"+{s[1]-lb['lb_used']} бр"
            lbs=f"{lb.get('lb_used','-')!s:>5} {lb.get('lb_km') or 0:8.1f} {gap:>7}"
        else: lbs=f"{'':>5} {'':>8} {'':>7}"
        print(f"{inst:34} {s[0]:5d} {s[1]:3d} {s[2]:8.1f} | {lbs} | {tag[:40]}")
    print(f"{'ИТОГО':34} {tot[0]:5d} {tot[1]:3d} {tot[2]:8.1f}")
if __name__=='__main__':
    for d in (sys.argv[1:] or ['instances_control_v2','instances_days_v2','instances_newctl_v2']): run(d)
