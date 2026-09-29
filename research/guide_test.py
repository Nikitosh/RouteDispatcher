import subprocess, glob, os, json, statistics as st
from validate import load_instance, check, parse_output
insts=sorted(glob.glob('instances_control_road/control_yugovostok_*.txt'))+sorted(glob.glob('instances_road/yugovostok_*.txt'))
res={}
for p in insts:
    I=load_instance(p); name=os.path.basename(p)[:-4]
    for pen in (1,2,3,5):
        g=p if pen==1 else f'runs/tmp_guide/{name}_p{pen}.txt'
        if pen>1 and not os.path.exists(g): subprocess.run(['python3','guide.py',p,g,str(pen)])
        for sd in (1,2,3,4):
            o=subprocess.run(['bin/s60_coop',g,'1',str(sd)],capture_output=True,text=True).stdout
            R,_=parse_output(o); c=check(I,R); res[(name,pen,sd)]=(c['used'],round(c['km'],1))
    line=' | '.join(f"p{pen}: "+' '.join(f"{res[(name,pen,sd)][0]}/{res[(name,pen,sd)][1]:.0f}" for sd in (1,2,3,4)) for pen in (1,2,3,5))
    print(f"{name:28} {line}",flush=True)
json.dump({f'{k[0]}|{k[1]}|{k[2]}':v for k,v in res.items()},open('runs/final/guide_test.json','w'))
