import csv, glob, json, math, os, urllib.request
cache=json.load(open('geocache.json'))
SVC={'Подключение':70,'Глобальная проблема':80,'Локальная заявка':30,'Дозаказ':20}
PRI={'Глобальная проблема':1,'Подключение':2,'Локальная заявка':3,'Дозаказ':3}
def hm(s):
    t=s.split()[1]; h,m=map(int,t.split(':')); return h*60+m-600   # минуты от 10:00
def hav(a,b):
    R=6371; la1,lo1,la2,lo2=map(math.radians,(a[0],a[1],b[0],b[1]))
    return 2*R*math.asin(math.sqrt(math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2))
def load(region):
    f=glob.glob(f"utf8/{region} Синтетические*.csv")[0]
    rows=list(csv.reader(open(f,encoding='utf-8'),delimiter=';'))
    office=None; orders=[]
    for r in rows[1:]:
        if not r or not r[0]: continue
        if r[0].lower().startswith('адрес'): office=r[1]; continue
        g=cache[r[6]]
        a,b=max(0,hm(r[3])),min(720,hm(r[4]))
        orders.append(dict(id=r[0],bk=r[1],hd=r[2],a=a,b=b,district=r[5],addr=r[6],lat=g['lat'],lon=g['lon'],svc=SVC[r[1]],pri=PRI[r[1]]))
    g=cache[office]
    return dict(lat=g['lat'],lon=g['lon'],addr=office), orders
def osrm_table(points, cachefile):
    if os.path.exists(cachefile): return json.load(open(cachefile))
    coords=';'.join(f"{p[1]:.6f},{p[0]:.6f}" for p in points)
    url=f"https://router.project-osrm.org/table/v1/driving/{coords}?annotations=duration,distance"
    r=json.load(urllib.request.urlopen(url,timeout=60))
    assert r['code']=='Ok', r
    res={'dur':[[x/60 for x in row] for row in r['durations']],'dist':[[x/1000 for x in row] for row in r['distances']]}
    json.dump(res,open(cachefile,'w')); return res
def pt_time(a,b):
    """Упрощённая модель: пешком до 1.5 км, иначе метро/автобус; дальние поездки электричкой."""
    d=hav(a,b)*1.3
    if d<1.5: return d/4.5*60
    if d<25: return 12+d/20*60
    return 12+25/20*60+(d-25)/45*60
