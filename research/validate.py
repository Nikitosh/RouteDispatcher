"""Независимая проверка решения (не использует код решателей)."""
import sys
def load_instance(path):
    tok=open(path).read().split('\n'); name=tok[0]; it=iter(' '.join(tok[1:]).split())
    N,V,S=int(next(it)),int(next(it)),int(next(it)); M=S+N
    ords=[dict(id=next(it),svc=int(next(it)),a=float(next(it)),b=float(next(it)),pri=int(next(it)),skill=int(next(it))) for _ in range(N)]
    veh=[dict(start=int(next(it)),mode=int(next(it)),mask=int(next(it))) for _ in range(V)]
    T=[];D=[]
    for m in range(4):
        T.append([[float(next(it)) for _ in range(M)] for _ in range(M)])
        D.append([[float(next(it)) for _ in range(M)] for _ in range(M)])
    return dict(name=name,N=N,V=V,S=S,ords=ords,veh=veh,T=T,D=D)
def check(I, routes):
    errs=[]; seen=[0]*I['N']; km=0; used=0
    for v,r in enumerate(routes):
        if not r: continue
        used+=1; ve=I['veh'][v]; m=ve['mode']; t=0; prev=ve['start']
        for k in r:
            o=I['ords'][k]; n=I['S']+k; seen[k]+=1
            if not (ve['mask']>>o['skill'])&1: errs.append(f"бригада {v} без навыка для заявки {o['id']}")
            beg=max(t+I['T'][m][prev][n],o['a'])
            if beg>o['b']+1e-6: errs.append(f"заявка {o['id']}: начало {beg:.1f} позже окна {o['b']}")
            t=beg+o['svc']
            if t>720+1e-6: errs.append(f"бригада {v} заканчивает после 22:00")
            km+=I['D'][m][prev][n]; prev=n
    for k,c in enumerate(seen):
        if c>1: errs.append(f"заявка {I['ords'][k]['id']} назначена {c} раз")
    return dict(ok=not errs,errs=errs,unserved=sum(1 for c in seen if c==0),used=used,km=km)
def parse_output(text):
    routes={}; res={}
    for line in text.splitlines():
        p=line.split()
        if not p: continue
        if p[0]=='ROUTE': routes[int(p[1])]=[int(x) for x in p[2:]]
        if p[0]=='RESULT': res={kv.split('=')[0]:float(kv.split('=')[1]) for kv in p[1:]}
    return [routes.get(v,[]) for v in range(max(routes)+1)] if routes else [], res
if __name__=='__main__':
    I=load_instance(sys.argv[1]); routes,res=parse_output(open(sys.argv[2]).read()); print(check(I,routes))
