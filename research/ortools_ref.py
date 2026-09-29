"""Эталон: OR-Tools (GLS) на тех же задачах и с той же целевой функцией. Вывод в формате решателей."""
import sys, time
from validate import load_instance
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
def main(path, tl=1.0, seed=1):
    I=load_instance(path); N,V,S=I['N'],I['V'],I['S']; END=S+N
    man=pywrapcp.RoutingIndexManager(S+N+1,V,[v['start'] for v in I['veh']],[END]*V); R=pywrapcp.RoutingModel(man)
    svc=[0]*S+[o['svc'] for o in I['ords']]+[0]; tcs=[]
    for v,ve in enumerate(I['veh']):
        m=ve['mode']; Tm=I['T'][m]; Dm=I['D'][m]
        def tcb(fi,ti,Tm=Tm):
            i,j=man.IndexToNode(fi),man.IndexToNode(ti); return int(svc[i]+(0 if END in(i,j) else -(-Tm[i][j]//1)))  # время вверх, чтобы не нарушать окна
        def dcb(fi,ti,Dm=Dm):
            i,j=man.IndexToNode(fi),man.IndexToNode(ti); return 0 if END in(i,j) else int(Dm[i][j]*1000)
        tcs.append(R.RegisterTransitCallback(tcb)); R.SetArcCostEvaluatorOfVehicle(R.RegisterTransitCallback(dcb),v)
        R.SetFixedCostOfVehicle(10_000_000,v)   # 1e4 км
    R.AddDimensionWithVehicleTransits(tcs,720,720,True,'Time'); TD=R.GetDimensionOrDie('Time')
    for k,o in enumerate(I['ords']):
        idx=man.NodeToIndex(S+k); TD.CumulVar(idx).SetRange(int(-(-o['a']//1)),int(o['b']//1))
        R.AddDisjunction([idx],int({1:100,2:50,3:20}[o['pri']]*1e9))
        R.VehicleVar(idx).SetValues([-1]+[v for v,ve in enumerate(I['veh']) if (ve['mask']>>o['skill'])&1])
    p=pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    p.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromMilliseconds(int(tl*1000))
    t0=time.time(); sol=R.SolveWithParameters(p); ms=(time.time()-t0)*1000
    print("SOLVER ortools_gls")
    for v in range(V):
        i=R.Start(v); seq=[]
        while not R.IsEnd(i):
            n=man.IndexToNode(i)
            if S<=n<S+N: seq.append(n-S)
            i=sol.Value(R.NextVar(i))
        print("ROUTE",v,*seq)
    print(f"RESULT ms={ms:.1f}")
if __name__=='__main__': main(sys.argv[1], float(sys.argv[2]) if len(sys.argv)>2 else 1.0)
