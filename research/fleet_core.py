"""Строгое «на K бригадах нельзя» для задачи (обычно ядра — подмножества заявок, cgc_tri: выкидывание заявок сохраняет
допустимость маршрутов, значит ядро невыполнимо => вся задача невыполнима). ЛП min числа маршрутов (точный ng-прайсинг,
3-SRC), перебор ВСЕХ элементарных маршрутов с приведённой стоимостью <= K - ЛП (как enum vehicles в cg_master) и точное
покрытие CP-SAT: каждая заявка ровно один раз, <= K маршрутов, бригад типа <= count. Infeasible — доказано.
Usage: fleet_core.py <задача> <K> [--enum-max 20000000] [--cpsat-tl 3600] [--maxsrc 150]"""
import sys, os, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check
import cg_master as CM
import sp_cpsat


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('K', type=int)
    ap.add_argument('--enum-max', type=int, default=20000000); ap.add_argument('--cpsat-tl', type=float, default=3600)
    ap.add_argument('--maxsrc', type=int, default=150); ap.add_argument('--ng', type=int, default=10)
    a = ap.parse_args(); t0 = time.time(); K = a.K
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    I = load_instance(a.inst); N = I['N']; pr = CM.Pricer(a.inst, a.ng); types = pr.types; T = len(types)
    M = CM.Master(I, types); M.stage = 2
    for t in range(T):
        for k in range(N):
            if check(I, [[]] * types[t]['vehs'][0] + [[k]])['ok']: M.add(t, 0.0, (k,))
    M.set_stage(2, 0, K + 1); M.set_types([0] * T, [ty['count'] for ty in types], 10.0 * I['V'])
    level = 1
    while True:
        z, du, x = M.solve(); pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        cuts = [(S[0], S[1], S[2], du[row]) for S, row in zip(M.cuts, M.cutrow)]
        alpha = [1.0 - mu[t] - lam for t in range(T)]
        cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 0.0, alpha, pi, cuts=cuts)
        added = sum(M.add(t, 0.0, r) for (t, km, rc, r) in cols)
        if added:
            if level == 2 and added < 5: level = 1
            continue
        if level == 1: level = 2; continue
        assert comp, 'точный прайсинг не завершён'
        if z > K + 1e-6: log(f'ЛП {z:.6f} > {K}: доказано уже по ЛП'); log('RESULT ДОКАЗАНО (ЛП)'); return
        if len(M.cuts) >= a.maxsrc: break
        new = M.separate(x, maxcuts=min(30, a.maxsrc - len(M.cuts)))
        if not new: break
        for S in new: M.add_cut(S)
        level = 1
    log(f'ЛП {z:.6f} (колонок {len(M.cols)}, SRC {len(M.cuts)}); перебор маршрутов с запасом {K - z:.6f}')
    gap = K - z + 1e-6
    routes, comp = pr.enum(gap, 0.0, a.enum_max, f'/tmp/fcore_{os.getpid()}.txt', alpha, pi, cuts)
    pr.close()
    log(f'перебор: маршрутов {len(routes)}, полный {bool(comp)}')
    if not comp: log('RESULT не доказано: перебор не уложился в лимит'); return
    el = [(t, km, tuple(r)) for (t, km, r) in routes if len(set(r)) == len(r)]
    res = sp_cpsat.solve(N, types, el, 2, 0, K + 1, [CM.PEN[o['pri']] for o in I['ords']], tl=a.cpsat_tl, ub=K)
    log(f'CP-SAT по полному перебору: {res["status"]}, маршрутов {res["obj"]}')
    if res['status'] == 'Infeasible': log(f'RESULT ДОКАЗАНО: на {K} бригадах нельзя')
    elif res['chosen']: log('RESULT НАЙДЕНО решение на', res['obj'], 'маршрутах', CM.score(I, CM.routes_to_vehicles(types, res['chosen'], I['V'])))
    else: log('RESULT не доказано: CP-SAT не успел')


if __name__ == '__main__':
    main()
