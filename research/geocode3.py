import json, time, urllib.parse, urllib.request, math
cache=json.load(open('geocache.json'))
UA="lct-hackathon-feasibility/0.1"
def q(params):
    params={**params,'format':'json','limit':1,'countrycodes':'ru'}
    url="https://nominatim.openstreetmap.org/search?"+urllib.parse.urlencode(params)
    for _ in range(3):
        try:
            r=json.load(urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=20)); time.sleep(1.1)
            return r[0] if r else None
        except Exception as e: print('ERR',e); time.sleep(3)
FIX={
 'Город Москва, ул.1-я Дубровская, д. 6':('Москва','1-я Дубровская улица',['6','6с1']),
 'Город Москва, ул.Дубининская, д. 59 к 2':('Москва','Дубининская улица',['59 к2','59к2','59']),
 'Город Москва, ул.1-я Новокузьминская, д. 16 к 1':('Москва','1-я Новокузьминская улица',['16 к1','16к1','16']),
 'Город Москва, ул.Зеленодольская, д. 28Б':('Москва','Зеленодольская улица',['28Б','28 Б','28б','28']),
 'г.Город Москва, б-р.Самаркандский, д. 32к1':('Москва','Самаркандский бульвар',['32 к1','32к1','32']),
 'Город Москва, ул.6-я Радиальная, д. 5 к 3':('Москва','6-я Радиальная улица',['5 к3','5к3','5']),
 'Город Москва, ул.6-я Радиальная, д. 7/1 к 2':('Москва','6-я Радиальная улица',['7/1 к2','7/1','7 к2','7']),
 'Домодедово, проезд.Советский 1-й, д. 1А':('Домодедово','1-й Советский проезд',['1А','1а','1']),
 'Город Москва, проезд.3-й Павелецкий, д. 9':('Москва','3-й Павелецкий проезд',['9','9с1']),
 'Город Москва, проезд.3-й Павелецкий, д. 7 к 2':('Москва','3-й Павелецкий проезд',['7 к2','7к2']),
 'МО, г. Кашира Кржижановского ул. д. 5/1':('Кашира','улица Кржижановского',['5/1','5 к1','5']),
 'МО, г. Кашира Кржижановского ул. д. 5/2':('Кашира','улица Кржижановского',['5/2','5 к2','5']),
 'МО, г. Кашира Кржижановского ул. д. 5/3':('Кашира','улица Кржижановского',['5/3','5 к3','5']),
 'Кашира, ул.8 Марта, д. 22':('Кашира','улица 8 Марта',['22']),
}
def hav(a,b):
    R=6371; la1,lo1,la2,lo2=map(math.radians,(a[0],a[1],b[0],b[1]))
    return 2*R*math.asin(math.sqrt(math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2))
for a,(city,street,hs) in FIX.items():
    old=cache[a]; got=None
    for h in hs:
        r=q({'street':f'{h} {street}','city':city})
        if r and r.get('addresstype') in ('building','house','place') or (r and r['class'] in('building','place')):
            got=(r,h); break
    if got:
        r,h=got; new={'lat':float(r['lat']),'lon':float(r['lon']),'name':r['display_name'],'level':'fix:'+h}
        shift=hav((old['lat'],old['lon']),(new['lat'],new['lon']))
        cache[a]=new; print(f"FIXED ({shift:.2f} км сдвиг) {a}\n    -> {new['name'][:100]}")
    else: print("NO BUILDING", a, "| оставляем:", old['name'][:80])
json.dump(cache,open('geocache.json','w'),ensure_ascii=False,indent=1)
