"""Задачи из контрольных данных: реальный состав бригад, навыки = типы заявок, которые бригада реально выполняла
(вариант 'inf') или все навыки (вариант 'all'). Транспорт: все 'pt' или все 'car'.
Бригадам, чьи заявки в основном в Кашире, Ступино или Домодедово, дом в этом городе.
Также оценивается реальный план диспетчера: для каждой бригады подбирается лучший порядок её заявок."""
import csv, glob, json, itertools, math, os
from collections import Counter, defaultdict
from data import load, osrm_table, pt_time, hav
import solve2
SKI={'L':0,'C':1,'A':2}; MODES=['car','pt','bike','foot']; FAR=('Кашира','Ступино','Домодедово')
TR={'Восток':'vostok','Югоцентр':'yugocentr','Юго-восток':'yugovostok'}
def build(region):
    f=glob.glob(f"utf8/{region} Контрольное*.csv")[0]
    rows=list(csv.reader(open(f,encoding='utf-8'),delimiter=';')); h=rows[0]; d=[r for r in rows[1:] if r and r[0]]
    office,orders=load(region); bi=h.index('Бригада')
    names=[b for b,_ in Counter(r[bi] for r in d if r[bi]).most_common()]
    starts=[(office['lat'],office['lon'])]; bstart={}
    for b in names:
        mine=[o for r,o in zip(d,orders) if r[bi]==b]
        town,cnt=Counter(o['district'] for o in mine).most_common(1)[0]
        if town in FAR and cnt*2>=len(mine):
            pts=[(o['lat'],o['lon']) for o in mine if o['district']==town]
            starts.append((sum(p[0] for p in pts)/len(pts),sum(p[1] for p in pts)/len(pts))); bstart[b]=len(starts)-1
        else: bstart[b]=0
    skills={b:{solve2.SK[o['bk']] for r,o in zip(d,orders) if r[bi]==b} for b in names}
    plan={b:[k for k,r in enumerate(d) if r[bi]==b] for b in names}
    pts=starts+[(o['lat'],o['lon']) for o in orders]; n=len(pts)
    if os.environ.get('ROADS'):
        import roads; T,D=roads.all_modes(pts)
        return dict(region=region,orders=orders,starts=starts,names=names,bstart=bstart,skills=skills,plan=plan,T=T,D=D,S=len(starts))
    car=osrm_table(pts,f"osrm_control_{region}.json")
    H=[[hav(pts[i],pts[j])*1.3 for j in range(n)] for i in range(n)]
    T={'car':car['dur'],'pt':[[pt_time(pts[i],pts[j]) if i!=j else 0 for j in range(n)] for i in range(n)],
       'foot':[[H[i][j]/4.5*60 for j in range(n)] for i in range(n)],'bike':[[H[i][j]/14*60 for j in range(n)] for i in range(n)]}
    D={'car':car['dist'],'pt':H,'foot':H,'bike':H}
    return dict(region=region,orders=orders,starts=starts,names=names,bstart=bstart,skills=skills,plan=plan,T=T,D=D,S=len(starts))
def write(C, mode, skillvar, path):
    orders=C['orders']; S=C['S']; M=S+len(orders)
    with open(path,'w') as f:
        f.write(f"control_{TR[C['region']]}_{mode}_{skillvar}\n{len(orders)} {len(C['names'])} {S}\n")
        for o in orders: f.write(f"{o['id']} {o['svc']} {o['a']} {min(o['b'],720-o['svc'])} {o['pri']} {SKI[solve2.SK[o['bk']]]}\n")
        for b in C['names']:
            sk=C['skills'][b] if skillvar=='inf' else {'L','C','A'}
            f.write(f"{C['bstart'][b]} {MODES.index(mode)} {sum(1<<SKI[s] for s in sk)}\n")
        for m in MODES:
            for i in range(M): f.write(' '.join(f"{C['T'][m][i][j]:.3f}" for j in range(M))+'\n')
            for i in range(M): f.write(' '.join(f"{C['D'][m][i][j]:.4f}" for j in range(M))+'\n')
def eval_real(C, mode):
    """Реальный план: для каждой бригады лучший порядок её заявок (группы по началу окна, перебор перестановок внутри группы).
    Возвращает км, число опозданий (начало после окна) и суммарное опоздание в минутах."""
    orders=C['orders']; S=C['S']; T=C['T'][mode]; D=C['D'][mode]
    tot=dict(km=0,late=0,late_min=0,after22=0,used=0,unassigned=0)
    assigned=set(k for ks in C['plan'].values() for k in ks); tot['unassigned']=len(orders)-len(assigned)
    for b,ks in C['plan'].items():
        tot['used']+=1; node=C['bstart'][b]; t=0
        groups=defaultdict(list)
        for k in ks: groups[orders[k]['a']].append(k)
        for a in sorted(groups):
            best=None
            for perm in itertools.permutations(groups[a]):
                tt,nd,km,late,lm=t,node,0,0,0
                for k in perm:
                    o=orders[k]; n=S+k; beg=max(tt+T[nd][n],o['a'])
                    b_=min(o['b'],720-o['svc'])
                    if beg>b_+1e-6: late+=1; lm+=beg-b_
                    km+=D[nd][n]; tt=beg+o['svc']; nd=n
                key=(late,lm,km)
                if best is None or key<best[0]: best=(key,tt,nd)
            (late,lm,km),t,node=best; tot['km']+=km; tot['late']+=late; tot['late_min']+=lm
        if t>720+1e-6: tot['after22']+=1
    return tot
OUT=os.environ.get('OUT','instances_control')
if __name__=='__main__':
    os.makedirs(OUT,exist_ok=True); real={}
    for region in TR:
        C=build(region)
        print(f"{region}: бригад {len(C['names'])}, стартов {C['S']} (дома в области: {[b for b in C['names'] if C['bstart'][b]]})")
        for mode in ['pt','car']:
            for sv in ['inf','all']: write(C,mode,sv,f"{OUT}/control_{TR[region]}_{mode}_{sv}.txt")
            r=eval_real(C,mode); real[f"{TR[region]}_{mode}"]=r
            print(f"   реальный план, {mode}: бригад {r['used']}, без бригады {r['unassigned']}, км {r['km']:.1f}, опозданий {r['late']} (всего {r['late_min']:.0f} мин), бригад после 22:00 {r['after22']}")
        print("   навыки:",{b.split()[-1]:''.join(sorted(s)) for b,s in C['skills'].items()})
    json.dump(real,open(f'real_plan_eval_{OUT}.json','w'),ensure_ascii=False,indent=1)
