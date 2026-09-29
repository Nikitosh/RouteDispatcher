"""Разбор решения: маршруты бригад с районами, пробегом и временем. python3 inspect_sol.py <задача> <файл решения> [регион]"""
import sys, csv, glob
from validate import load_instance, parse_output
MODES=['car','pt','bike','foot']; SK='LCA'
def districts(region):
    f=glob.glob(f"utf8/{region} Синтетические*.csv")[0]
    rows=list(csv.reader(open(f,encoding='utf-8'),delimiter=';'))
    return [r[5] for r in rows[1:] if r and r[0] and not r[0].lower().startswith('адрес')]
I=load_instance(sys.argv[1]); R,_=parse_output(open(sys.argv[2]).read())
reg={'vostok':'Восток','yugocentr':'Югоцентр','yugovostok':'Юго-восток'}
name=sys.argv[1].split('/')[-1]; rk=[k for k in reg if k in name.split('_')]; rk=sorted(rk,key=len)[-1]
dist=districts(reg[rk]) if 'gen' not in sys.argv[1] else None
tot=0
for v,ve in enumerate(I['veh']):
    r=R[v] if v<len(R) else []
    m=ve['mode']; t=0; prev=ve['start']; km=0; seq=[]
    for k in r:
        o=I['ords'][k]; n=I['S']+k; tr=I['T'][m][prev][n]; beg=max(t+tr,o['a'])
        km+=I['D'][m][prev][n]; t=beg+o['svc']; prev=n
        d=dist[k][:8] if dist and k<len(dist) else ''
        seq.append(f"{int(beg//60)+10:02d}:{int(beg%60):02d}{SK[o['skill']]}/{d}")
    tot+=km
    mask=''.join(SK[s] for s in range(3) if (ve['mask']>>s)&1)
    print(f"бр{v:2d} старт{ve['start']} {MODES[m]:4} {mask:3} {len(r):2d} зак {km:6.1f} км: "+' '.join(seq))
print('итого км',round(tot,1))
