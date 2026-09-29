"""Прогрев кэша реальных матриц для всех наборов задач, затем сборка задач в *_road папки."""
import os, sys, threading, time, subprocess
import roads, control, gen, solve2
from data import load
sets=[]
# контрольные: точки через подмену all_modes
rec=[]
orig=roads.all_modes
def fake(pts): rec.append(list(pts)); raise StopIteration
roads.all_modes=fake; os.environ['ROADS']='1'
for region in ['Восток','Югоцентр','Юго-восток']:
    try: control.build(region)
    except StopIteration: pass
roads.all_modes=orig
sets+= [('control',p) for p in rec]
for region in ['Восток','Югоцентр','Юго-восток']:
    office,orders=load(region); starts=[(office['lat'],office['lon'])]+solve2.EXTRA.get(region,[])
    sets.append(('orig',starts+[(o['lat'],o['lon']) for o in orders]))
for fam in ['real','large','tight','peak','walk','allday','scarce']:
    for i in range(10):
        reg=list(gen.REG)[i%3]; sets.append((f'gen_{fam}_{i}',gen.gen(fam,reg,i,None,points_only=True)))
print('наборов точек',len(sets),'точек всего',sum(len(p) for _,p in sets),flush=True)
def worker(profs):
    for k,(name,pts) in enumerate(sets):
        for pr in profs:
            t=time.time(); roads.matrix(pts,pr)
            print(f"[{pr}] {k+1}/{len(sets)} {name} ({len(pts)} точек) {time.time()-t:.0f} с",flush=True)
th=[threading.Thread(target=worker,args=([p],)) for p in ('car','foot','bike')]
[t.start() for t in th]; [t.join() for t in th]
print('кэш готов',flush=True)
env=dict(os.environ,ROADS='1')
subprocess.run([sys.executable,'control.py'],env=dict(env,OUT='instances_control_road'),check=True)
subprocess.run([sys.executable,'gen.py'],env=dict(env,OUT='instances_gen_road'),check=True)
subprocess.run([sys.executable,'export_road.py'],env=env,check=True)
print('ГОТОВО',flush=True)
