import sys, time, random, json
from data import *
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
SK={'Локальная заявка':'L','Подключение':'C','Дозаказ':'C','Глобальная проблема':'A'}
EXTRA={'Юго-восток':[(54.834,38.153),(54.886,38.078)]}
def roster(n, seed, region):
    rnd=random.Random(seed); out=[]
    modes=['pt']*round(n*0.5)+['car']*round(n*0.25)+['bike']*round(n*0.15)
    modes+=['foot']*(n-len(modes)); rnd.shuffle(modes)
    for v in range(n):
        k=rnd.choices([1,2,3],[0.3,0.3,0.4])[0]
        sk=set(rnd.sample(['L','C','A'],k))
        out.append(dict(name=f'Бригада {v+1}',mode=modes[v],skills=sk,start=0))
    # каждый навык хотя бы у трёх бригад
    for s in 'LCA':
        have=[b for b in out if s in b['skills']]
        for b in out:
            if len(have)>=3: break
            if s not in b['skills']: b['skills'].add(s); have.append(b)
    if region in EXTRA:   # две бригады живут в Кашире и Ступино, у них машины
        out[0].update(start=1,mode='car',skills={'L','C','A'}); out[1].update(start=2,mode='car',skills={'L','C','A'})
    return out
def matrices(region):
    office,orders=load(region)
    starts=[(office['lat'],office['lon'])]+EXTRA.get(region,[])
    pts=starts+[(o['lat'],o['lon']) for o in orders]
    car=osrm_table(pts,f"osrm_{region}_{len(starts)}.json"); n=len(pts)
    H=[[hav(pts[i],pts[j])*1.3 for j in range(n)] for i in range(n)]
    T={'car':car['dur'],'pt':[[pt_time(pts[i],pts[j]) if i!=j else 0 for j in range(n)] for i in range(n)],
       'foot':[[H[i][j]/4.5*60 for j in range(n)] for i in range(n)],'bike':[[H[i][j]/14*60 for j in range(n)] for i in range(n)]}
    D={'car':car['dist'],'pt':H,'foot':H,'bike':H}
    return orders,len(starts),T,D
def greedy(region, ros):
    orders,S,T,D=matrices(region)
    st=[dict(node=b['start'],t=0,km=0,seq=[]) for b in ros]; miss=[]
    for k,o in enumerate(orders):
        done=False
        for v,b in enumerate(ros):
            if SK[o['bk']] not in b['skills']: continue
            s=st[v]; j=S+k
            arr=s['t']+T[b['mode']][s['node']][j]; beg=max(arr,o['a'])
            if beg<=o['b'] and beg+o['svc']<=720:
                s['km']+=D[b['mode']][s['node']][j]; s['node']=j; s['t']=beg+o['svc']; s['seq'].append(k); done=True; break
        if not done: miss.append(k)
    return dict(served=len(orders)-len(miss),total=len(orders),used=sum(1 for s in st if s['seq']),km=sum(s['km'] for s in st),miss=miss)
def optimize(region, ros, secs=20):
    orders,S,T,D=matrices(region); N=len(orders); END=S+N; nv=len(ros)
    man=pywrapcp.RoutingIndexManager(S+N+1,nv,[b['start'] for b in ros],[END]*nv); R=pywrapcp.RoutingModel(man)
    svc=[0]*S+[o['svc'] for o in orders]+[0]
    tcs=[];dcs=[]
    for b in ros:
        m=b['mode']
        def tcb(fi,ti,m=m):
            i,j=man.IndexToNode(fi),man.IndexToNode(ti)
            return int(round(svc[i]+(0 if END in(i,j) else T[m][i][j])))
        def dcb(fi,ti,m=m):
            i,j=man.IndexToNode(fi),man.IndexToNode(ti)
            return 0 if END in(i,j) else int(D[m][i][j]*1000)
        tcs.append(R.RegisterTransitCallback(tcb)); dcs.append(R.RegisterTransitCallback(dcb))
    for v in range(nv): R.SetArcCostEvaluatorOfVehicle(dcs[v],v); R.SetFixedCostOfVehicle(1_000_000,v)
    R.AddDimensionWithVehicleTransits(tcs,720,720,True,'Time'); TD=R.GetDimensionOrDie('Time')
    for k,o in enumerate(orders):
        idx=man.NodeToIndex(S+k)
        TD.CumulVar(idx).SetRange(o['a'],min(o['b'],720-o['svc']))
        R.AddDisjunction([idx],{1:100_000_000,2:50_000_000,3:20_000_000}[o['pri']])
        if o['b']-o['a']>200: TD.SetCumulVarSoftUpperBound(idx,0,200)
        R.VehicleVar(idx).SetValues([-1]+[v for v,b in enumerate(ros) if SK[o['bk']] in b['skills']])
    p=pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    p.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds=secs
    sol=R.SolveWithParameters(p); served=0; used=0; km=0; miss=[]
    for v in range(nv):
        i=R.Start(v); c=0
        while not R.IsEnd(i):
            nx=sol.Value(R.NextVar(i)); km+=R.GetArcCostForVehicle(i,nx,v)/1000
            if S<=man.IndexToNode(i)<S+N: c+=1
            i=nx
        served+=c; used+=c>0
    served_set=set()
    for v in range(nv):
        i=R.Start(v)
        while not R.IsEnd(i):
            nd=man.IndexToNode(i)
            if S<=nd<S+N: served_set.add(nd-S)
            i=sol.Value(R.NextVar(i))
    return dict(served=served,total=N,used=used,km=km-1000*used,miss=[orders[k] for k in range(N) if k not in served_set])
if __name__=='__main__':
    region=sys.argv[1]; n=int(sys.argv[2]); seeds=range(int(sys.argv[3])); secs=int(sys.argv[4])
    for sd in seeds:
        ros=roster(n,sd,region); g=greedy(region,ros); o=optimize(region,ros,secs)
        mix=dict(__import__('collections').Counter(b['mode'] for b in ros))
        print(f"{region:10} seed {sd}: ЖАДНЫЙ {g['served']}/{g['total']} бригад {g['used']} {g['km']:.0f} км | OR-TOOLS {o['served']}/{o['total']} бригад {o['used']} {o['km']:.0f} км | транспорт {mix}",flush=True)
