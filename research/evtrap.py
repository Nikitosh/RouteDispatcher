"""Стенд по ловушечным задачам: python3 evtrap.py <имя> <решатель> <сек> <сидов> [ПЕР=ЗНАЧ ...]
Задачи — TRAP (через запятую, набор/задача) или список по умолчанию. Печатает по задачам: бригады и км к лучшим."""
import json, os, subprocess, sys, statistics as st, signal
from concurrent.futures import ThreadPoolExecutor
from validate import load_instance, check, parse_output
name,solver,tl,nseeds=sys.argv[1],sys.argv[2],float(sys.argv[3]),int(sys.argv[4])
env=dict(os.environ); [env.__setitem__(*kv.split('=',1)) for kv in sys.argv[5:]]
W=int(os.environ.get('WORKERS',1)); SEED0=int(os.environ.get('SEED0',1))
DEF=('gen/scarce_yugovostok_8,gen/real_yugovostok_8,gen/large_yugovostok_8,control/control_yugovostok_mix2_all,'
     'road/yugovostok_s0,gen/walk_yugovostok_5,gen/scarce_yugovostok_5,control/control_yugovostok_pt_all')
TR=[x.split('/') for x in os.environ.get('TRAP',DEF).split(',')]
full=lambda d:{'gen':'instances_gen_road','road':'instances_road','control':'instances_control_road'}[d]
subprocess.run(['make','-s',f'bin/{solver}'],capture_output=True)
pids=[int(p) for p in subprocess.run(['pgrep','-f','cg_bp|cgx_bp|cgm_bp|cgc_master'],capture_output=True,text=True).stdout.split()]
for p in pids:
    try: os.kill(p,signal.SIGSTOP)
    except Exception: pass
meta={}; INST={}; jobs=[]
for d,i in TR:
    D=full(d); meta.setdefault(D,json.load(open(f'best/{D}/_meta.json'))); INST[(D,i)]=load_instance(f'{D}/{i}.txt')
    jobs+=[(D,i,sd) for sd in range(SEED0,SEED0+nseeds)]
def run(j):
    D,i,sd=j; I=INST[(D,i)]
    out=subprocess.run([f'bin/{solver}',f'{D}/{i}.txt',str(tl),str(sd)],capture_output=True,text=True,env=env).stdout
    R,_=parse_output(out); R=(R+[[]]*I['V'])[:I['V']]; c=check(I,R)
    sv={k for r in R for k in r}; pen=sum({1:100,2:50,3:20}[I['ords'][k]['pri']] for k in range(I['N']) if k not in sv)
    return dict(d=D,i=i,sd=sd,ok=c['ok'],pen=pen,used=c['used'],km=c['km'],routes=R)
try:
    with ThreadPoolExecutor(W) as ex: rows=list(ex.map(run,jobs))
finally:
    for p in pids:
        try: os.kill(p,signal.SIGCONT)
        except Exception: pass
os.makedirs('runs/final/trap',exist_ok=True); json.dump(dict(name=name,solver=solver,tl=tl,args=sys.argv[5:],rows=rows),open(f'runs/final/trap/{name}.json','w'))
TU=0; GG=[]; out=[f'{name:18}']
for D,i in [(full(d),i) for d,i in TR]:
    m=meta[D][i]; rs=[r for r in rows if r['i']==i]
    du=sum(r['used']-m['used'] for r in rs)/len(rs); TU+=du
    g=[r['km']/m['km']-1 for r in rs if (r['pen'],r['used'])==(m['pen'],m['used'])]
    GG+=g; bad=sum(not r['ok'] or r['pen']>m['pen'] for r in rs)
    out.append(f"{i.replace('yugovostok','yv').replace('control_',''):12} {'!'*bad}бр{du:+.2f} км{(st.mean(g)*100 if g else float('nan')):5.1f}")
out.append(f'ИТОГ бр{TU:+.2f} км{st.mean(GG)*100:.2f}%')
print(' | '.join(out),flush=True)
with open('runs/final/trap/_summary.txt','a') as f: f.write(' | '.join(out)+'  '+' '.join(sys.argv[5:])+f'  [{solver} {tl}s x{nseeds}]\n')
