"""Генератор разнообразных задач, похожих на исходные данные.
Точки: реальные адреса участка со сдвигом ~400 м. Окна и доля дальних заявок: из исходных данных участка.
Типы заявок по умолчанию: 40% локальных, 40% подключений, 10% аварий, 10% дозаказов (ориентир жюри).
Время на машине: модель, подогнанная по матрицам OSRM, с шумом ±15%. Остальные виды транспорта как в data.py."""
import math, random, zlib, os
from collections import Counter
from data import load, pt_time, hav
SVC={'L':30,'C':70,'A':80,'D':20}; PRI={'A':1,'C':2,'L':3,'D':3}; SKI={'L':0,'C':1,'A':2,'D':1}
FAR=('Кашира','Ступино','Домодедово'); MODES=['car','pt','bike','foot']
REG={'vostok':('Восток',12),'yugocentr':('Югоцентр',11),'yugovostok':('Юго-восток',12)}
def car(h):
    t=3.30+1.59*h if h<15 else 27.15+0.84*(h-15)
    d=0.92+1.35*h if h<15 else 21.17+1.16*(h-15)
    return t,d
def jitter(rnd,p,km=0.4):
    return (p[0]+rnd.gauss(0,km)/111.0, p[1]+rnd.gauss(0,km)/(111.0*math.cos(math.radians(p[0]))))
def gen(fam, reg, seed, path, points_only=False):
    rnd=random.Random(zlib.crc32(f"{fam}|{reg}|{seed}".encode()))
    region,Vreal=REG[reg]; office,real=load(region)
    pool=[(o['lat'],o['lon'],o['district']) for o in real]
    slots=[o['a'] for o in real if o['b']-o['a']<=130]
    Nreal=len(real)
    # параметры семейства
    N=round(Nreal*rnd.uniform(0.85,1.15)); V=Vreal
    mix={'L':0.4,'C':0.4,'A':0.1,'D':0.1}
    modes_w={'pt':0.5,'car':0.25,'bike':0.15,'foot':0.1}
    allday_p=0.9 if reg=='yugovostok' else 0.3
    prof={'CL':0.55,'ACL':0.25,'C':0.07,'L':0.05,'AL':0.04,'AC':0.04}
    peak=False
    if fam=='large': N=rnd.randint(100,130); V=16
    if fam=='peak': peak=True
    if fam=='walk': modes_w={'pt':0.75,'foot':0.25}
    if fam=='allday': mix={'L':0.35,'C':0.35,'A':0.2,'D':0.1}; allday_p=0.85
    if fam=='scarce': prof={'CL':0.3,'L':0.35,'ACL':0.1,'C':0.1,'AL':0.1,'AC':0.05}
    # заявки
    ords=[]
    for _ in range(N):
        t=rnd.choices(list(mix),list(mix.values()))[0]
        if t=='A' and rnd.random()<allday_p: a,b=0,720
        elif peak: a=rnd.choice([0,120,480,600] if rnd.random()<0.7 else [240,360]); b=a+120
        else: a=rnd.choice(slots); b=a+120
        p=rnd.choice(pool); lat,lon=jitter(rnd,p[:2])
        ords.append(dict(t=t,a=a,b=min(b,720-SVC[t]),lat=lat,lon=lon,far=p[2] in FAR,town=p[2]))
    work=sum(SVC[o['t']] for o in ords)
    if fam=='tight': V=max(3,math.ceil((work+14*N)/640))
    # старты: офис + дома в дальних городах
    starts=[(office['lat'],office['lon'])]; homes=[]
    towns=Counter(o['town'] for o in ords if o['far'])
    for town,cnt in towns.items():
        tw=sum(SVC[o['t']]+25 for o in ords if o['town']==town)
        k=max(1,round(tw/600)) if cnt>=3 else 0
        pts=[(o['lat'],o['lon']) for o in ords if o['town']==town]
        c=(sum(p[0] for p in pts)/len(pts),sum(p[1] for p in pts)/len(pts))
        for _ in range(k): starts.append(c); homes.append(len(starts)-1)
    # бригады
    veh=[]
    for v in range(V):
        m=rnd.choices(list(modes_w),list(modes_w.values()))[0]
        sk=rnd.choices(list(prof),list(prof.values()))[0]
        st=0
        if v<len(homes): st=homes[v]; m='car'; sk='ACL'
        veh.append([st,m,{s for s in sk}])
    for s,minc in (('A',2),('C',max(2,round(V*0.35))),('L',2)):
        have=[x for x in veh if s in x[2]]
        for x in veh:
            if len(have)>=minc: break
            if s not in x[2]: x[2].add(s); have.append(x)
    # матрицы
    pts=starts+[(o['lat'],o['lon']) for o in ords]; M=len(pts)
    if points_only: return pts
    if os.environ.get('ROADS'):
        import roads; T,D=roads.all_modes(pts)
        return _write(fam,reg,seed,path,N,V,starts,ords,veh,T,D)
    T={m:[[0.0]*M for _ in range(M)] for m in MODES}; D={m:[[0.0]*M for _ in range(M)] for m in MODES}
    for i in range(M):
        for j in range(M):
            if i==j: continue
            h=hav(pts[i],pts[j]); tc,dc=car(h); f=rnd.uniform(0.85,1.15)
            T['car'][i][j]=tc*f; D['car'][i][j]=dc*f
            T['pt'][i][j]=pt_time(pts[i],pts[j]); T['bike'][i][j]=h*1.3/14*60; T['foot'][i][j]=h*1.3/4.5*60
            for m in ('pt','bike','foot'): D[m][i][j]=h*1.3
    return _write(fam,reg,seed,path,N,V,starts,ords,veh,T,D)
def _write(fam,reg,seed,path,N,V,starts,ords,veh,T,D):
    M=len(starts)+len(ords)
    with open(path,'w') as f:
        f.write(f"gen_{fam}_{reg}_{seed}\n{N} {V} {len(starts)}\n")
        for k,o in enumerate(ords): f.write(f"G{k} {SVC[o['t']]} {o['a']} {o['b']} {PRI[o['t']]} {SKI[o['t']]}\n")
        for st,m,sk in veh: f.write(f"{st} {MODES.index(m)} {sum(1<<'LCA'.index(s) for s in sk)}\n")
        for m in MODES:
            for i in range(M): f.write(' '.join(f"{T[m][i][j]:.3f}" for j in range(M))+'\n')
            for i in range(M): f.write(' '.join(f"{D[m][i][j]:.4f}" for j in range(M))+'\n')
    return N,V,len(starts)
OUT=os.environ.get('OUT','instances_gen')
if __name__=='__main__':
    os.makedirs(OUT,exist_ok=True)
    fams=['real','large','tight','peak','walk','allday','scarce']; n=0
    for fam in fams:
        for i in range(10):
            reg=list(REG)[i%3]; N,V,S=gen(fam,reg,i,f"{OUT}/{fam}_{reg}_{i}.txt"); n+=1
            if i<3: print(fam,reg,'заявок',N,'бригад',V,'стартов',S)
    print('всего задач',n)
