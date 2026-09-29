"""Стенд для быстрых экспериментов: решатель с переменными окружения на рабочем наборе (88 задач, модель продукта).
python3 evalcfg.py <имя> <решатель> <время> <сиды> [VAR=VAL ...]   (WORKERS по умолчанию 3)
Сравнение с лучшими известными: лишний штраф, лишние бригады, средний разрыв км при равных бригадах, число равных лучшему."""
import glob, json, os, subprocess, sys, statistics as st
from concurrent.futures import ThreadPoolExecutor
from validate import load_instance, check, parse_output
name,solver,tl,nseeds=sys.argv[1],sys.argv[2],float(sys.argv[3]),int(sys.argv[4])
env=dict(os.environ); [env.__setitem__(*kv.split('=',1)) for kv in sys.argv[5:]]
W=int(os.environ.get('WORKERS',7)); PEN={1:100,2:50,3:20}; SEED0=int(os.environ.get('SEED0',1))  # сиды SEED0..SEED0+n-1
# FREEZE=1: на время замера заморозить процессы доказательств (SIGSTOP), потом разморозить (SIGCONT)
FREEZE=os.environ.get('FREEZE','1')=='1'
import signal
def _bp_pids():
    out=subprocess.run(['pgrep','-f','cg_bp|cgx_bp|cgm_bp|cgc_master|cg_master|cg_price|cgx_price|cgm_price|cgc_price'],capture_output=True,text=True).stdout.split()
    return [int(p) for p in out if int(p)!=os.getpid()]
frozen=[]
if FREEZE:
    for p in _bp_pids():
        try: os.kill(p,signal.SIGSTOP); frozen.append(p)
        except Exception: pass
# рабочий набор с 29.09: контрольный день (24) + новые реальные дни (40) + сгенерированные участки (24) = 88
SETS=os.environ.get('SETS','instances_control_v2,instances_days_v2,instances_newctl_v2').split(',')
subprocess.run(['make','-s',f'bin/{solver}'],capture_output=True)
jobs=[]; meta={}; INST={}
for d in SETS:
    meta[d]=json.load(open(f'best/{d}/_meta.json'))
    for p in sorted(glob.glob(f'{d}/*.txt')):
        i=os.path.basename(p)[:-4]
        if i in meta[d]: INST[(d,i)]=load_instance(p); jobs+=[(d,i,sd) for sd in range(SEED0,SEED0+nseeds)]
def run(j):
    d,i,sd=j; I=INST[(d,i)]
    out=subprocess.run([f'bin/{solver}',f'{d}/{i}.txt',str(tl),str(sd)],capture_output=True,text=True,env=env).stdout
    R,_=parse_output(out); R=(R+[[]]*I['V'])[:I['V']]; c=check(I,R)
    sv={k for r in R for k in r}; pen=sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in sv)
    return dict(d=d,i=i,sd=sd,ok=c['ok'],pen=pen,used=c['used'],km=c['km'],routes=R)
import time as _t; _t0=_t.time()
try:
    with ThreadPoolExecutor(W) as ex: rows=list(ex.map(run,jobs))
finally:
    for p in frozen:
        try: os.kill(p,signal.SIGCONT)
        except Exception: pass
WALL=_t.time()-_t0
json.dump(dict(name=name,solver=solver,tl=tl,args=sys.argv[5:],rows=rows),open(f'runs/final/cfg/{name}.json','w'))
# сохранить улучшения лучших известных
for r in rows:
    m=meta[r['d']][r['i']]
    if r['ok'] and (r['pen'],r['used'],r['km'])<(m['pen'],m['used'],m['km']-1e-6):
        od=f"runs/results/{r['d']}/cfg_{name}"; os.makedirs(od,exist_ok=True)
        with open(f"{od}/{r['i']}.out",'w') as f: f.write('SOLVER cfg\n'); [f.write(f"ROUTE {v} {' '.join(map(str,x))}\n") for v,x in enumerate(r['routes'])]
line=[f"{name:22}"]
for d in SETS:
    rs=[r for r in rows if r['d']==d]; bad=sum(not r['ok'] for r in rs)
    dp=sum(r['pen']-meta[d][r['i']]['pen'] for r in rs)/nseeds
    du=sum(r['used']-meta[d][r['i']]['used'] for r in rs if r['pen']==meta[d][r['i']]['pen'])/nseeds
    g=[r['km']/meta[d][r['i']]['km']-1 for r in rs if (r['pen'],r['used'])==(meta[d][r['i']]['pen'],meta[d][r['i']]['used'])]
    eq=sum(1 for r in rs if (r['pen'],r['used'])==(meta[d][r['i']]['pen'],meta[d][r['i']]['used']) and r['km']<=meta[d][r['i']]['km']*1.005)/nseeds
    line.append(f"{d.replace('instances_','').replace('_road',''):8} ош{bad} шт+{dp:4.0f} бр+{du:4.1f} км{st.mean(g)*100:5.2f}% =лучш{eq:5.1f}")
# итог по всем наборам: лишний штраф, лишние бригады, средний разрыв км (все задачи равного парка)
TP=TU=0; GG=[]
for d in SETS:
    rs=[r for r in rows if r['d']==d]
    TP+=sum(r['pen']-meta[d][r['i']]['pen'] for r in rs)/nseeds
    TU+=sum(r['used']-meta[d][r['i']]['used'] for r in rs if r['pen']==meta[d][r['i']]['pen'])/nseeds
    GG+=[r['km']/meta[d][r['i']]['km']-1 for r in rs if (r['pen'],r['used'])==(meta[d][r['i']]['pen'],meta[d][r['i']]['used'])]
line.append(f'ИТОГ шт+{TP:.0f} бр+{TU:.2f} км{st.mean(GG)*100:.2f}%'); line.append(f'{WALL:.0f}с'); print(' | '.join(line),flush=True)
with open('runs/final/cfg/_summary.txt','a') as f: f.write(' | '.join(line)+'  '+' '.join(sys.argv[5:])+f'  [{solver} {tl}s x{nseeds}]\n')
