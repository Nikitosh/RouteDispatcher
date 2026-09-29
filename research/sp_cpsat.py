"""Точное разбиение на маршруты (set partitioning) для cg_master.py / cg_pool2.py: гонка CP-SAT и HiGHS.
Модель та же, что у прежней MIP HiGHS: каждая заявка ровно один раз (маршрутом или y_k «не выполнена», если Pbest > 0),
бригад типа t от tlo_t до tup_t, всего бригад <= K (этап 3), штраф <= Pbest, 3-SRC-отсечения (маршрутов с >= 2 заявками
тройки S — не больше одного). Цель: км (этап 3) или число маршрутов (этап 2).

Оба решателя — в дочерних процессах (OR-Tools несёт свою копию libhighs, в одном процессе с highspy из .venv они
конфликтуют): CP-SAT (этот файл, режим child) на CG_WORKERS−1 потоках и та же модель HiGHS, что раньше (highs_mip.py,
1 поток). Кто первым закончит с окончательным ответом (оптимум или «решений нет»), тот и победил, второй снимается.
Если окончательного ответа нет ни у кого — лучшая из двух границ и лучшее из двух решений.
Замер 29.09 на 6 пулах: CP-SAT (8 потоков) быстрее HiGHS в 3–20 раз на пулах до ~15 тыс. маршрутов, на 58 тыс. — HiGHS
(504 с против 555); отсюда гонка.

Км в CP-SAT масштабируются на SC = 1e6 и округляются ВНИЗ, поэтому его граница — честная нижняя граница настоящих км
(ошибка <= V / SC, меньше допуска 1e-4 в cg_master). CG_WORKERS — потоков на задачу (cg_batch.py делит ядра);
CG_MIP=highs — только HiGHS в своём процессе, как раньше."""
import math, os, pickle, subprocess, sys, tempfile, time, json

SC = 10 ** 6
HERE = os.path.dirname(os.path.abspath(__file__))


def use_cpsat(): return os.environ.get('CG_MIP', 'cpsat') == 'cpsat'


def workers(): return int(os.environ.get('CG_WORKERS', os.cpu_count() or 8))


def _cpsat(p):
    """дочерний процесс: модель CP-SAT по параметрам p, ответ — dict(status, x (индексы выбранных), bound)"""
    from ortools.sat.python import cp_model
    N, types, routes, stage = p['N'], p['types'], p['routes'], p['stage']; T = len(types)
    m = cp_model.CpModel(); x = [m.NewBoolVar('') for _ in routes]
    cover = [[] for _ in range(N)]
    for j, (t, km, r) in enumerate(routes):
        for k in r: cover[k].append(x[j])
    if p['Pbest'] > 0:
        y = [m.NewBoolVar('') for _ in range(N)]
        for k in range(N): cover[k].append(y[k])
        m.Add(sum(int(p['pk'][k]) * y[k] for k in range(N)) <= int(math.floor(p['Pbest'] + 1e-9)))
    for k in range(N): m.AddExactlyOne(cover[k])
    byT = [[] for _ in range(T)]
    for j, (t, km, r) in enumerate(routes): byT[t].append(x[j])
    lo = p['tlo'] if p['tlo'] is not None else [0] * T; up = p['tup'] if p['tup'] is not None else [ty['count'] for ty in types]
    for t in range(T):
        m.Add(sum(byT[t]) <= int(up[t]))
        if lo[t] > 0: m.Add(sum(byT[t]) >= int(lo[t]))
    if stage == 3: m.Add(sum(x) <= int(p['K']))
    for S in p['cuts']:
        S = set(S); z = [x[j] for j, (t, km, r) in enumerate(routes) if len(S.intersection(r)) >= 2]
        if len(z) > 1: m.AddAtMostOne(z)
    cost = [math.floor(km * SC) if stage == 3 else 1 for (t, km, r) in routes]
    obj = sum(c * v for c, v in zip(cost, x))
    ub = p['ub']
    if ub is not None: m.Add(obj <= (math.floor(ub * SC + 1e-9) if stage == 3 else math.floor(ub + 1e-9)))
    m.Minimize(obj)
    if p['hint']:
        for j, (t, km, r) in enumerate(routes): m.AddHint(x[j], 1 if (t, tuple(r)) in p['hint'] else 0)
    s = cp_model.CpSolver(); s.parameters.max_time_in_seconds = float(p['tl']); s.parameters.num_workers = int(p['workers'])
    st = s.Solve(m)
    status = {cp_model.OPTIMAL: 'Optimal', cp_model.FEASIBLE: 'Feasible', cp_model.INFEASIBLE: 'Infeasible'}.get(st, 'Unknown')
    sel = [j for j in range(len(routes)) if s.Value(x[j])] if status in ('Optimal', 'Feasible') else None
    return dict(status=status, sel=sel, bound=math.inf if status == 'Infeasible' else s.BestObjectiveBound() / (SC if stage == 3 else 1))


def solve(N, types, routes, stage, Pbest, K, pk, cuts=(), tlo=None, tup=None, tl=60.0, ub=None, hint=None, highs=None):
    """routes: [(t, km, tuple)] (только элементарные); ub: принимать решения с целью <= ub; hint: {(t, route)};
    highs: (h, decode, objective_bound) — собранная модель HiGHS той же задачи (decode(col_values) -> chosen) или None.
    Возвращает dict(status 'Optimal'|'Feasible'|'Infeasible'|'Unknown', obj (км или маршрутов) или None,
    chosen [(t, km, r)] или None, bound — нижняя граница цели, by — чей ответ)."""
    d = tempfile.mkdtemp(prefix='spmip_'); f = lambda n: os.path.join(d, n)
    pickle.dump(dict(N=N, types=types, routes=routes, stage=stage, Pbest=Pbest, K=K, pk=pk, cuts=[tuple(S) for S in cuts],
                     tlo=tlo, tup=tup, tl=tl, ub=ub, hint=hint, workers=max(1, workers() - (1 if highs else 0))), open(f('in.pkl'), 'wb'))
    procs = {'cpsat': subprocess.Popen([sys.executable, os.path.abspath(__file__), 'child', f('in.pkl'), f('cpsat.pkl')])}
    if highs:
        h, decode, ob = highs; h.writeModel(f('m.mps'))
        procs['highs'] = subprocess.Popen([sys.executable, os.path.join(HERE, 'highs_mip.py'), f('m.mps'), str(tl),
                                           'none' if ob is None else repr(float(ob)), f('highs.json')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got = {}

    def read(name):
        try:
            if name == 'cpsat':
                r = pickle.load(open(f('cpsat.pkl'), 'rb')); ch = [routes[j] for j in r['sel']] if r['sel'] is not None else None
                return dict(status=r['status'], chosen=ch, bound=r['bound'],
                            obj=None if ch is None else (sum(km for (t, km, rr) in ch) if stage == 3 else len(ch)))
            r = json.load(open(f('highs.json')))
            st = 'Optimal' if r['status'].endswith('kOptimal') else 'Infeasible' if 'Infeasible' in r['status'] else ('Feasible' if r['primal'] else 'Unknown')
            return dict(status=st, chosen=decode(r['x']) if r['primal'] and st != 'Infeasible' else None,
                        obj=r['obj'] if r['primal'] and st != 'Infeasible' else None, bound=math.inf if st == 'Infeasible' else r['bound'])
        except Exception:
            return None
    winner = None
    while procs and winner is None:
        for name, p in list(procs.items()):
            if p.poll() is None: continue
            del procs[name]; r = read(name)
            if r: got[name] = r
            if r and r['status'] in ('Optimal', 'Infeasible'): winner = name; break
        if winner is None and procs: time.sleep(0.02)
    for p in procs.values(): p.kill(); p.wait()
    for n in os.listdir(d): os.remove(f(n))
    os.rmdir(d)
    if winner: return dict(got[winner], by=winner)
    res = dict(status='Unknown', obj=None, chosen=None, bound=-math.inf, by='none')
    for name, r in got.items():
        res['bound'] = max(res['bound'], r['bound'] if r['bound'] is not None else -math.inf)
        if r['chosen'] is not None and (res['obj'] is None or r['obj'] < res['obj'] - 1e-9):
            res.update(obj=r['obj'], chosen=r['chosen'], status='Feasible', by=name)
    return res


if __name__ == '__main__' and sys.argv[1:2] == ['child']:
    pickle.dump(_cpsat(pickle.load(open(sys.argv[2], 'rb'))), open(sys.argv[3], 'wb'))
