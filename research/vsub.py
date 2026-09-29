"""Поиск по наборам бригад: решатель запускается на подмножестве бригад (остальные исключены из задачи).
Старт: набор из лучшего известного решения. Соседи: заменить одну используемую бригаду на неиспользуемую
(с учётом одинаковых бригад), а также убрать одну бригаду. Принимается лучшее улучшение.
python3 vsub.py <папка> <задача> [решатель=s33_sasisr] [время=3] [потоков=2] [итераций=4]"""
import os, sys, subprocess, json
from concurrent.futures import ThreadPoolExecutor
from validate import load_instance, check, parse_output
d,inst=sys.argv[1],sys.argv[2]; solver=sys.argv[3] if len(sys.argv)>3 else 's33_sasisr'
tl=float(sys.argv[4]) if len(sys.argv)>4 else 3; W=int(sys.argv[5]) if len(sys.argv)>5 else 2; iters=int(sys.argv[6]) if len(sys.argv)>6 else 4
path=f'{d}/{inst}.txt'; I=load_instance(path); lines=open(path).read().split('\n')
N,V,S=map(int,lines[1].split()); vlines=lines[2+N:2+N+V]
PEN={1:100,2:50,3:20}
def score(routes):
    c=check(I,routes)
    if not c['ok']: return None
    sv={k for r in routes for k in r}
    return (sum(PEN[I['ords'][k]['pri']] for k in range(N) if k not in sv),c['used'],round(c['km'],3))
def run(subset,seed=1):
    subset=sorted(subset); tag='_'.join(map(str,subset))
    f=f'runs/tmp_vsub/{inst}_{tag}.txt'
    if not os.path.exists(f):
        out=lines[:1]+[f"{N} {len(subset)} {S}"]+lines[2:2+N]+[vlines[v] for v in subset]+lines[2+N+V:]
        open(f,'w').write('\n'.join(out))
    o=subprocess.run([f'bin/{solver}',f,str(tl),str(seed)],capture_output=True,text=True).stdout
    R,_=parse_output(o); R=(R+[[]]*len(subset))[:len(subset)]
    full=[[] for _ in range(V)]
    for i,v in enumerate(subset): full[v]=R[i]
    return score(full),full
bf=f'best/{d}/{inst}.out'; B,_=parse_output(open(bf).read()); B=(B+[[]]*V)[:V]
best=score(B); cur=[v for v in range(V) if B[v]]
print('старт',best,'бригады',cur,flush=True)
sig=lambda v: vlines[v].strip()
for it in range(iters):
    unused=[v for v in range(V) if v not in cur]
    cands=set()
    for a in cur:
        for b in unused:
            if sig(a)!=sig(b): cands.add(tuple(sorted(set(cur)-{a}|{b})))
    for a in cur: cands.add(tuple(sorted(set(cur)-{a})))
    cands=list(cands)
    with ThreadPoolExecutor(W) as ex: res=list(ex.map(run,cands))
    ok=[(s,sub,R) for (s,R),sub in zip(res,cands) if s]
    ok.sort(key=lambda x:x[0])
    print(f"итерация {it}: вариантов {len(cands)}, лучший {ok[0][0]} {list(ok[0][1])}",flush=True)
    if ok[0][0]<best:
        best=ok[0][0]; cur=[v for v in range(V) if ok[0][2][v]]; R=ok[0][2]
        od=f'runs/results/{d}/vsub'; os.makedirs(od,exist_ok=True)
        with open(f'{od}/{inst}.out','w') as fo:
            fo.write('SOLVER vsub\n'); [fo.write(f"ROUTE {v} {' '.join(map(str,r))}\n") for v,r in enumerate(R)]
        print('  УЛУЧШЕНИЕ ->',best,flush=True)
    else: break
print('итог',best)
