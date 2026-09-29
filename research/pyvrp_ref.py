"""Эталон: PyVRP (итеративный локальный поиск, наследник HGS) на тех же задачах. Вывод в формате решателей.
Навыки: у каждой бригады свой профиль, въезд к заявке без навыка запрещён огромной стоимостью.
Открытые маршруты: общий фиктивный финиш, до него 0 км и 0 минут."""
import sys, time, math
from validate import load_instance
from pyvrp import Model
from pyvrp.stop import MaxRuntime
BIG=10**9
def main(path, tl=1.0, seed=1):
    I=load_instance(path); N,V,S=I['N'],I['V'],I['S']
    m=Model()
    locs=[m.add_location(0,0) for _ in range(S+N)]; endloc=m.add_location(0,0)
    depots=[m.add_depot(locs[i]) for i in range(S)]; end=m.add_depot(endloc)
    for k,o in enumerate(I['ords']):
        m.add_client(locs[S+k],service_duration=o['svc']*60,tw_early=math.ceil(o['a']*60),tw_late=math.floor(o['b']*60),
                     required=False,prize={1:100,2:50,3:20}[o['pri']]*10**7)
    for v,ve in enumerate(I['veh']):
        p=m.add_profile(); Tm=I['T'][ve['mode']]; Dm=I['D'][ve['mode']]
        ok=[i<S or (ve['mask']>>I['ords'][i-S]['skill'])&1 for i in range(S+N)]
        for i in range(S+N):
            m.add_edge(locs[i],endloc,0,0,profile=p); m.add_edge(endloc,locs[i],BIG,BIG,profile=p)
            for j in range(S+N):
                if i==j: continue
                if j<S or not ok[j]: m.add_edge(locs[i],locs[j],BIG,BIG,profile=p)
                else: m.add_edge(locs[i],locs[j],int(round(Dm[i][j]*1000)),math.ceil(Tm[i][j]*60),profile=p)
        m.add_vehicle_type(1,start_depot=depots[ve['start']],end_depot=end,fixed_cost=10**7,tw_early=0,tw_late=720*60,profile=p)
    t0=time.time(); res=m.solve(stop=MaxRuntime(tl),seed=seed,display=False); ms=(time.time()-t0)*1000
    routes=[[] for _ in range(V)]
    for r in res.best.routes():
        routes[r.vehicle_type()]=[a.idx for a in list(r) if a.is_client()]
    # ремонт: если лучшее решение с опозданием, снимаем опаздывающие заявки (становятся невыполненными)
    for v,r in enumerate(routes):
        ve=I['veh'][v]; m=ve['mode']; keep=[]; t=0; prev=ve['start']
        for k in r:
            o=I['ords'][k]; n=S+k; beg=max(t+I['T'][m][prev][n],o['a'])
            if beg>o['b']+1e-6 or beg+o['svc']>720+1e-6: continue
            keep.append(k); t=beg+o['svc']; prev=n
        routes[v]=keep
    print("SOLVER pyvrp")
    for v in range(V): print("ROUTE",v,*routes[v])
    print(f"RESULT ms={ms:.1f}")
if __name__=='__main__': main(sys.argv[1], float(sys.argv[2]) if len(sys.argv)>2 else 1.0, int(sys.argv[3]) if len(sys.argv)>3 else 1)
