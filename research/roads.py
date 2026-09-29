"""Реальные матрицы по дорогам через OSRM: машина (router.project-osrm.org), пешком и велосипед (routing.openstreetmap.de).
Большие матрицы собираются блоками (параметры sources/destinations, не больше 100 точек в запросе).
Кэш по парам точек (координаты округлены до 5 знаков) в roads_cache_local/<profile>.json.
Общественный транспорт: пешком, если пешая дорога до 1,5 км; иначе 12 мин (подход и ожидание) + дорога на машине
со средней скоростью 20 км/ч до 25 км и 45 км/ч дальше (электричка). Пробег ОТ = пешая дорога или дорога на машине."""
import json, os, time, threading, urllib.request
# Локальные серверы OSRM в Docker (граф по выгрузке Geofabrik, обрезанной до Москвы и юга области, см. ../osrm):
#   docker run -d --name osrm-foot -p 5001:5000 -v $PWD/foot:/data osrm/osrm-backend osrm-routed --algorithm mld --max-table-size 2000 /data/msk.osrm
#   аналогично osrm-bike (5002, профиль bicycle) и osrm-car (5003, профиль car).
# Адреса можно переопределить переменными OSRM_CAR / OSRM_FOOT / OSRM_BIKE (например, в docker compose).
URL={'car':os.environ.get('OSRM_CAR','http://localhost:5003')+'/table/v1/driving/',
     'foot':os.environ.get('OSRM_FOOT','http://localhost:5001')+'/table/v1/driving/',
     'bike':os.environ.get('OSRM_BIKE','http://localhost:5002')+'/table/v1/driving/'}
CACHE='roads_cache_local'
os.makedirs(CACHE,exist_ok=True)
_lock=threading.Lock(); _cache={}
def _key(p): return f"{p[0]:.5f},{p[1]:.5f}"
def _load(prof):
    if prof not in _cache:
        f=f'{CACHE}/{prof}.json'; _cache[prof]=json.load(open(f)) if os.path.exists(f) else {}
    return _cache[prof]
def _save(prof):
    with _lock:
        tmp=f'{CACHE}/{prof}.json.tmp'; json.dump(_cache[prof],open(tmp,'w')); os.replace(tmp,f'{CACHE}/{prof}.json')
def _req(prof, src, dst):
    pts=list(dict.fromkeys(src+dst)); idx={p:i for i,p in enumerate(pts)}
    coords=';'.join(f"{p[1]:.6f},{p[0]:.6f}" for p in pts)
    url=URL[prof]+coords+f"?sources={';'.join(str(idx[p]) for p in src)}&destinations={';'.join(str(idx[p]) for p in dst)}&annotations=duration,distance"
    for attempt in range(8):
        try:
            r=json.load(urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'lct-hackathon/0.2'}),timeout=90))
            if r.get('code')=='Ok': return r
            raise RuntimeError(r.get('code'))
        except Exception as e:
            time.sleep(2+3*attempt)
    raise RuntimeError(f'OSRM {prof} failed')
def matrix(points, prof, block=500, pause=0.0):
    """Матрицы (минуты, км) между всеми точками по профилю car/foot/bike."""
    c=_load(prof); P=[(round(p[0],5),round(p[1],5)) for p in points]; n=len(P)
    need=[(i,j) for i in range(n) for j in range(n) if i!=j and _key(P[i])+'|'+_key(P[j]) not in c]
    if need:
        uniq=sorted(set(P)); 
        rows=sorted({P[i] for i,j in need}); cols=sorted({P[j] for i,j in need})
        for a in range(0,len(rows),block):
            for b in range(0,len(cols),block):
                src=rows[a:a+block]; dst=cols[b:b+block]
                if all(_key(s)+'|'+_key(d) in c for s in src for d in dst if s!=d): continue
                r=_req(prof,src,dst)
                with _lock:
                    for si,s in enumerate(src):
                        for di,d in enumerate(dst):
                            du=r['durations'][si][di]; di_=r['distances'][si][di]
                            if du is None: du,di_=1e6,1e6
                            c[_key(s)+'|'+_key(d)]=[du/60,di_/1000]
                time.sleep(pause)
        _save(prof)
    T=[[0.0]*n for _ in range(n)]; D=[[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i!=j and P[i]!=P[j]: T[i][j],D[i][j]=c[_key(P[i])+'|'+_key(P[j])]
    return T,D
def all_modes(points):
    """T,D по четырём видам транспорта: car, pt, bike, foot (как в формате задач)."""
    Tc,Dc=matrix(points,'car'); Tf,Df=matrix(points,'foot'); Tb,Db=matrix(points,'bike'); n=len(points)
    Tp=[[0.0]*n for _ in range(n)]; Dp=[[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i==j: continue
            if Df[i][j]<=1.5: Tp[i][j],Dp[i][j]=Tf[i][j],Df[i][j]
            else:
                d=Dc[i][j]; t=12+(d/20*60 if d<25 else 25/20*60+(d-25)/45*60)
                Tp[i][j],Dp[i][j]=min(t,Tf[i][j]),(Df[i][j] if Tf[i][j]<t else d)
    return {'car':Tc,'pt':Tp,'bike':Tb,'foot':Tf},{'car':Dc,'pt':Dp,'bike':Db,'foot':Df}
