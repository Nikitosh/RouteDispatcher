"""Портфель: s60 (4 потока) на настоящей задаче + s60 (4 потока) на задаче с подсказкой-штрафом PEN, одновременно;
лучшее по настоящим км. python portfolio_eval.py <имя> <сек> <сидов> <PEN>"""
import glob, os, json, subprocess, sys, statistics as st
from concurrent.futures import ThreadPoolExecutor
from validate import load_instance, check, parse_output
name,tl,ns,pen=sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]
PENW={1:100,2:50,3:20}; os.makedirs('tmp_guide',exist_ok=True)
SETS=['instances_control_road','instances_road','instances_gen_road']; line=[f"{name:22}"]; allrows=[]
def score(I,R):
    c=check(I,R); sv={k for r in R for k in r}
    return (sum(PENW[I['ords'][k]['pri']] for k in range(I['N']) if k not in sv),c['used'],c['km']) if c['ok'] else None
for d in SETS:
    meta=json.load(open(f'best/{d}/_meta.json')); rows=[]
    for p in sorted(glob.glob(f'{d}/*.txt')):
        i=os.path.basename(p)[:-4]
        if i not in meta: continue
        I=load_instance(p)
        if os.environ.get('ONLYS') and I['S']<=1: continue
        g=f'runs/tmp_guide/{d}_{i}_p{pen}.txt'
        if not os.path.exists(g): subprocess.run(['python3','guide.py',p,g,pen])
        for sd in range(1,ns+1):
            with ThreadPoolExecutor(2) as ex:
                outs=list(ex.map(lambda f: subprocess.run(['bin/s60_coop',f,tl,str(sd)],capture_output=True,text=True).stdout,[p,g]))
            cands=[]
            for o in outs:
                R,_=parse_output(o); R=(R+[[]]*I['V'])[:I['V']]; s=score(I,R)
                if s: cands.append((s,R))
            s,R=min(cands,key=lambda x:x[0]); rows.append((i,s,[c[0] for c in cands])); allrows.append(dict(d=d,i=i,sd=sd,best=s,parts=[c[0] for c in cands]))
            m=meta[i]
            if s<(m['pen'],m['used'],m['km']-1e-6):
                od=f'runs/results/{d}/pf_{name}'; os.makedirs(od,exist_ok=True)
                with open(f'{od}/{i}.out','w') as f: f.write('SOLVER pf\n'); [f.write(f"ROUTE {v} {' '.join(map(str,x))}\n") for v,x in enumerate(R)]
    dp=sum(s[0]-meta[i]['pen'] for i,s,_ in rows)/ns
    du=sum(s[1]-meta[i]['used'] for i,s,_ in rows if s[0]==meta[i]['pen'])/ns
    gg=[s[2]/meta[i]['km']-1 for i,s,_ in rows if (s[0],s[1])==(meta[i]['pen'],meta[i]['used'])]
    eq=sum(1 for i,s,_ in rows if (s[0],s[1])==(meta[i]['pen'],meta[i]['used']) and s[2]<=meta[i]['km']*1.005)/ns
    guided_won=sum(1 for i,s,c in rows if len(c)==2 and c[1]<c[0])
    line.append(f"{d.replace('instances_','').replace('_road',''):8} шт+{dp:4.0f} бр+{du:4.1f} км{st.mean(gg)*100:5.2f}% =лучш{eq:5.1f} подсказка лучше в {guided_won}")
    print(line[-1],flush=True)
json.dump(allrows,open(f'runs/final/cfg/{name}.json','w'))
with open('runs/final/cfg/_summary.txt','a') as f: f.write(' | '.join(line)+f'  [portfolio s60+s60(pen{pen}) {tl}s x{ns}]\n')
