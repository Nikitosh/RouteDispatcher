"""PyVRP с тёплым стартом: python hy_pyvrp.py <instance> <tl> <seed> <init.out>
Модель как в pyvrp_ref.py; начальное решение — маршруты из init (формат ROUTE). Парк не растёт за счёт стоимости 1e7."""
import sys, time, math
from validate import load_instance, parse_output
from pyvrp import Model, Solution, Route
from pyvrp.stop import MaxRuntime
BIG = 10**9
def main(path, tl, seed, init):
    I = load_instance(path); N, V, S = I['N'], I['V'], I['S']
    m = Model()
    locs = [m.add_location(0, 0) for _ in range(S + N)]; endloc = m.add_location(0, 0)
    depots = [m.add_depot(locs[i]) for i in range(S)]; end = m.add_depot(endloc)
    for k, o in enumerate(I['ords']):
        m.add_client(locs[S + k], service_duration=o['svc'] * 60, tw_early=math.ceil(o['a'] * 60), tw_late=math.floor(o['b'] * 60),
                     required=False, prize={1: 100, 2: 50, 3: 20}[o['pri']] * 10**7)
    for v, ve in enumerate(I['veh']):
        p = m.add_profile(); Tm = I['T'][ve['mode']]; Dm = I['D'][ve['mode']]
        ok = [i < S or (ve['mask'] >> I['ords'][i - S]['skill']) & 1 for i in range(S + N)]
        for i in range(S + N):
            m.add_edge(locs[i], endloc, 0, 0, profile=p); m.add_edge(endloc, locs[i], BIG, BIG, profile=p)
            for j in range(S + N):
                if i == j: continue
                if j < S or not ok[j]: m.add_edge(locs[i], locs[j], BIG, BIG, profile=p)
                else: m.add_edge(locs[i], locs[j], int(round(Dm[i][j] * 1000)), math.ceil(Tm[i][j] * 60), profile=p)
        m.add_vehicle_type(1, start_depot=depots[ve['start']], end_depot=end, fixed_cost=10**7, tw_early=0, tw_late=720 * 60, profile=p)
    data = m.data()
    routes0, _ = parse_output(open(init).read())
    nd = data.num_depots
    init_sol = Solution(data, [Route(data, [k for k in r], v) for v, r in enumerate(routes0) if r])
    t0 = time.time(); res = m.solve(stop=MaxRuntime(tl), seed=seed, display=False, initial_solution=init_sol); ms = (time.time() - t0) * 1000
    routes = [[] for _ in range(V)]
    for r in res.best.routes():
        routes[r.vehicle_type()] = [a.idx for a in list(r) if a.is_client()]
    for v, r in enumerate(routes):   # ремонт опозданий
        ve = I['veh'][v]; mm = ve['mode']; keep = []; t = 0; prev = ve['start']
        for k in r:
            o = I['ords'][k]; n = S + k; beg = max(t + I['T'][mm][prev][n], o['a'])
            if beg > o['b'] + 1e-6 or beg + o['svc'] > 720 + 1e-6: continue
            keep.append(k); t = beg + o['svc']; prev = n
        routes[v] = keep
    print("SOLVER hy_pyvrp")
    for v in range(V): print("ROUTE", v, *routes[v])
    print(f"RESULT ms={ms:.1f}")
if __name__ == '__main__': main(sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
