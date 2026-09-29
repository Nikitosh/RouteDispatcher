"""Эталон: VROOM (C++ решатель с навыками, окнами, стартами и профилями транспорта). Вывод в формате решателей."""
import sys, time, math
from datetime import timedelta
from validate import load_instance
import vroom
PROF=['car','pt','bike','foot']
def main(path, tl=1.0, seed=1):
    I=load_instance(path); N,V,S=I['N'],I['V'],I['S']; M=S+N
    pi=vroom.Input()
    for m,name in enumerate(PROF):
        pi.set_durations_matrix(profile=name,matrix_input=[[math.ceil(I['T'][m][i][j]*60) for j in range(M)] for i in range(M)])
        pi.set_distances_matrix(profile=name,matrix_input=[[int(round(I['D'][m][i][j]*1000)) for j in range(M)] for i in range(M)])
    for v,ve in enumerate(I['veh']):
        pi.add_vehicle(vroom.Vehicle(id=v+1,start=ve['start'],profile=PROF[ve['mode']],skills={s for s in range(3) if (ve['mask']>>s)&1},
                                     time_window=vroom.TimeWindow(0,720*60),costs=vroom.VehicleCosts(fixed=10_000_000,per_hour=0,per_km=1000)))
    for k,o in enumerate(I['ords']):
        pi.add_job(vroom.Job(id=k+1,location=S+k,default_service=o['svc']*60,skills={o['skill']},priority={1:100,2:50,3:20}[o['pri']],
                             time_windows=[vroom.TimeWindow(math.ceil(o['a']*60),math.floor(o['b']*60))]))
    t0=time.time(); sol=pi.solve(exploration_level=5,nb_threads=1,timeout=timedelta(seconds=tl)); ms=(time.time()-t0)*1000
    df=sol.routes; routes=[[] for _ in range(V)]
    for _,r in df.iterrows():
        if r['type']=='job': routes[int(r['vehicle_id'])-1].append(int(r['id'])-1)
    print("SOLVER vroom")
    for v in range(V): print("ROUTE",v,*routes[v])
    print(f"RESULT ms={ms:.1f}")
if __name__=='__main__': main(sys.argv[1], float(sys.argv[2]) if len(sys.argv)>2 else 1.0)
