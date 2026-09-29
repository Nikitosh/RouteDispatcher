import csv, glob, json, os, re, time, urllib.parse, urllib.request
CACHE='geocache.json'
cache=json.load(open(CACHE)) if os.path.exists(CACHE) else {}
UA="lct-hackathon-feasibility/0.1"
REPL=[(r'\bпр-кт\.?','проспект '),(r'\bпер\.','переулок '),(r'\bпроезд\.','проезд '),(r'\bул\.?\s','улица '),(r'\bул\.','улица '),
      (r'\bш\.','шоссе '),(r'\bб-р\.?','бульвар '),(r'\bнаб\.','набережная '),(r'\bпл\.','площадь '),(r'\bмкр\.?','микрорайон '),(r'\bтуп\.','тупик ')]
def variants(addr):
    a=addr.replace('Город Москва','Москва').replace('г. Москва','Москва').replace('г.Москва','Москва')
    for p,s in REPL: a=re.sub(p,s,a)
    a=re.sub(r'\s+',' ',a).strip()
    # "д. 10 к 2" -> "10к2"; "д. 28 стр. 1"
    m=re.match(r'^(.*?),\s*(.+?),\s*д\.?\s*([\w/]+)(?:\s*к\s*(\w+))?(?:\s*(?:стр\.?|с)\s*(\w+))?.*$',a)
    out=[]
    if m:
        city,street,h,k,s=m.groups()
        hk=h+(f'к{k}' if k else '')+(f'с{s}' if s else '')
        out+= [f'{city}, {street}, {hk}', f'{city}, {street}, {h}', f'{city}, {street}']
    out.append(a)
    return list(dict.fromkeys(out))
def q(s):
    url="https://nominatim.openstreetmap.org/search?"+urllib.parse.urlencode({'q':s,'format':'json','limit':1,'countrycodes':'ru'})
    r=json.load(urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=20))
    time.sleep(1.1)
    return (float(r[0]['lat']),float(r[0]['lon']),r[0]['display_name']) if r else None
addrs=[]
for f in sorted(glob.glob("utf8/*Синтетические*.csv")):
    rows=list(csv.reader(open(f,encoding='utf-8'),delimiter=';'))
    for r in rows[1:]:
        if not r or not r[0]: continue
        addrs.append(r[1] if r[0].lower().startswith('адрес') else r[6])
addrs=[a for a in dict.fromkeys(addrs) if a]
print(len(addrs),'unique addresses')
for a in addrs:
    if a in cache: continue
    res=None; lvl=None
    for i,v in enumerate(variants(a)):
        try: res=q(v)
        except Exception as e: print('ERR',e); time.sleep(3); continue
        if res: lvl=i; break
    cache[a]={'lat':res[0],'lon':res[1],'name':res[2],'level':lvl} if res else None
    json.dump(cache,open(CACHE,'w'),ensure_ascii=False,indent=1)
bad=[a for a in addrs if not cache.get(a)]
print('not found:',len(bad)); [print('  ',a) for a in bad]
from collections import Counter
print('match level (0=дом с корпусом,1=дом,2=улица,3=как есть):',Counter(cache[a]['level'] for a in addrs if cache.get(a)))
