"""Нижняя граница ЧИСЛА БРИГАД с округлёнными отсечениями по парку k(S) (как в cgc_master.py, но на этапе 2).
ЛП: min число маршрутов, каждая заявка ровно один раз, бригад типа t <= count_t; отсечения
    sum_{маршруты, заходящие в S} x_r >= k(S),  k(S) = ceil(ЛП минимума бригад на подзадаче из заявок S) (cgc_sub.kS).
Верно, потому что в лучшем решении выполнены все заявки (штраф 0) и выкидывание заявок не ломает маршрут (cgc_tri).
Граница = ЛП после сходимости с точным ng-прайсингом (cgc_price); ceil(ЛП) > lb_used — граница парка выросла.
Usage: fleet_ks.py <inst> [--tl 1800] [--kstl 900] [--ng 10] [--write 1]"""
import sys, os, json, time, math, argparse
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output
import cg_master as CM
from cgc_master import FM, Pricer, build_cands, HERE
from cgc_sub import kS
from cgc_tri import check as tri_check


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('--tl', type=float, default=1800)
    ap.add_argument('--kstl', type=float, default=900); ap.add_argument('--ng', type=int, default=10); ap.add_argument('--write', type=int, default=1)
    ap.add_argument('--typebranch', action='store_true'); ap.add_argument('--part', default='0/1'); ap.add_argument('--combo-tl', type=float, default=300); ap.add_argument('--node', default='', help='состав через запятую: только он + перебор маршрутов и CP-SAT'); ap.add_argument('--enum-max', type=int, default=5000000); ap.add_argument('--bp', action='store_true', help='с --node: ветвление по дугам вместо перебора'); ap.add_argument('--maxsrc', type=int, default=150)
    ap.add_argument('--mip', type=float, default=0, help='>0: после ЛП без k(S) искать CP-SAT разбиение на K-1 маршрут по пулу (колонки ЛП + маршруты всех прошлых решений), лимит в секундах')
    a = ap.parse_args(); t0 = time.time()
    inst = a.inst; d = os.path.basename(os.path.dirname(os.path.abspath(inst))); name = os.path.basename(inst)[:-4]
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    I = load_instance(inst); N = I['N']
    Rb, _ = parse_output(open(os.path.join(HERE, 'best', d, name + '.out')).read()); Rb = (Rb + [[]] * I['V'])[:I['V']]
    Pbest, K, UB = CM.score(I, Rb)
    lbf = os.path.join(HERE, 'lb', d, name + '.json'); L0 = json.load(open(lbf)) if os.path.exists(lbf) else {}
    log(name, 'best', (Pbest, K, round(UB, 3)), 'lb_used', L0.get('lb_used'))
    assert Pbest == 0, 'k(S) cuts need every order served (best penalty 0)'
    tri = tri_check(inst); assert tri <= 1e-9, f'triangle condition violated {tri}'
    pr = Pricer(inst, a.ng, lm=False); types = pr.types; T = len(types)
    vtype = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
    M = FM(I, types); M.stage = 2
    for v, r in enumerate(Rb):
        if r: M.add(vtype[v], check(I, [[]] * v + [r])['km'], tuple(r))
    for t in range(T):
        for k in range(N):
            c = check(I, [[]] * types[t]['vehs'][0] + [[k]])
            if c['ok']: M.add(t, c['km'], (k,))
    M.set_stage(2, Pbest, K)
    big = 10.0 * I['V']
    cand = build_cands(I, log)
    kcache = {}; ks_time = [0.0]; have = set()

    def kval(S, seed):
        if S not in kcache:
            if ks_time[0] > a.kstl: return None
            ts = time.time(); v, conv = kS(inst, S, a.ng, seed_cols=seed); ks_time[0] += time.time() - ts
            kcache[S] = int(math.ceil(v - 1e-4)) if v > -1e17 and conv else 0
            if kcache[S] > 1: log(f'   k(S) {cand.get(S, "?")} |S|={len(S)}: LP {v:.4f} conv={conv} -> {kcache[S]} ({time.time()-ts:.1f}s)')
        return kcache[S]

    def separate(x, maxn=8):
        act = [(M.cols[j], x[M.hidx[j]]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-9]
        seed = [(c[0], c[2]) for c, v in act]
        L = {S: sum(v for (t, km, r), v in act if any(k in S for k in r)) for S in cand}
        viol = []
        for S, v in sorted(L.items(), key=lambda kv: (kv[0] not in kcache, -len(kv[0]))):
            if S in have: continue
            if S not in kcache and any(kv2 <= v + 1e-3 and S <= S2 for S2, kv2 in kcache.items()): continue  # k монотонна по S
            k = kval(S, seed)
            if k is not None and k > v + 1e-3: viol.append((k - v, S, k))
        viol.sort(key=lambda z: (-round(z[0], 3), len(z[1]))); out = []
        for g, S, k in viol[:maxn]:
            M.add_frow(S, None, float(k), CM.INF, big); have.add(S); out.append((cand.get(S, '?'), len(S), k, round(g, 3)))
        return out

    if a.mip > 0:   # маршруты всех прошлых решений этой задачи
        import glob
        nold = len(M.cols)
        for f in glob.glob(os.path.join(HERE, 'results', d, '*', name + '.out')) + glob.glob(os.path.join(HERE, 'results', d, '*', '*', name + '.out')):
            try: R, _ = parse_output(open(f).read())
            except Exception: continue
            for v, r in enumerate(R[:I['V']]):
                if r:
                    c = check(I, [[]] * v + [r])
                    if c['ok']: M.add(vtype[v], c['km'], tuple(r))
        log(f'маршрутов из прошлых решений добавлено {len(M.cols) - nold}')
    if a.typebranch:   # ветвление по составу: все наборы из K-1 бригад по типам; состав отпадает, если ЛП > K-1
        import itertools

        def cg_conv(tl, maxsrc):
            """ЛП min числа маршрутов при текущих границах типов, точный прайсинг + 3-SRC; (z, сошлась ли)"""
            level = 1; ts = time.time()
            while True:
                z, du, x = M.solve()
                pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
                cuts = [(S[0], S[1], S[2], du[row], Mem) for S, row, Mem in zip(M.cuts, M.cutrow, M.mem)]
                alpha = [1.0 - mu[t] - lam for t in range(T)]
                cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 0.0, alpha, pi, cuts=cuts)
                added = sum(M.add(t, km, r) for (t, km, rc, r) in cols)
                if time.time() - ts > tl: return z, False
                if added:
                    if level == 2 and added < 5: level = 1
                    continue
                if level == 1: level = 2; continue
                if not comp: return z, False
                if z > K - 1 + 1e-6 or len(M.cuts) >= maxsrc: return z, True
                new = M.separate(x, maxcuts=min(30, maxsrc - len(M.cuts)))
                if not new: return z, True
                for S in new: M.add_cut(S)
                level = 1
        combos = [m for m in itertools.product(*[range(ty['count'] + 1) for ty in types]) if sum(m) == K - 1]
        if a.node:
            m = tuple(map(int, a.node.split(','))); M.set_types(list(m), list(m), big)
            z, cv = cg_conv(a.combo_tl, 150); log(f'  узел {m}: LP {z:.6f} сошлась {cv}, колонок {len(M.cols)}, SRC {len(M.cuts)}')
            if not cv or z > K - 1 + 1e-6: pr.close(); return
            if a.bp:   # ветвление по дугам (как в cgc_master): дуга i->j (i=-1 — начало маршрута, j=-1 — конец)
                def arcs(r): return [(-1, r[0])] + [(r[i], r[i + 1]) for i in range(len(r) - 1)] + [(r[-1], -1)]

                def apply(forb):
                    fs = set(forb); pr.forb = list(forb)
                    for j, col in enumerate(M.cols):
                        M.h.changeColBounds(M.hidx[j], 0.0, CM.INF if not any(e in fs for e in arcs(col[2])) else 0.0)
                stack = [()]; nodes = 0; closed = 0; tb = time.time()
                while stack and time.time() - t0 < a.tl:
                    forb = stack.pop(); nodes += 1; apply(forb)
                    z, cv = cg_conv(a.combo_tl, a.maxsrc)
                    if not cv: log(f'   узел {nodes} (запретов {len(forb)}): ЛП не сошлась — ветвление не закончено'); stack = [None]; break
                    if z > K - 1 + 1e-6: closed += 1; continue
                    _, du, x = M.solve(); flow = {}
                    for j, (t, km, r) in enumerate(M.cols):
                        v = x[M.hidx[j]]
                        if v > 1e-6:
                            for e in arcs(r): flow[e] = flow.get(e, 0) + v
                    frac = [(min(f, 1 - f), e) for e, f in flow.items() if 1e-6 < f < 1 - 1e-6]
                    if not frac:
                        sol = [M.cols[j] for j in range(len(M.cols)) if x[M.hidx[j]] > 0.5]
                        R = CM.routes_to_vehicles(types, sol, I['V']); sc = CM.score(I, R); log('  ЦЕЛОЕ РЕШЕНИЕ В УЗЛЕ', sc)
                        if sc and sc[1] <= K - 1:
                            od = os.path.join(HERE, 'results', d, 'fleet_ks'); os.makedirs(od, exist_ok=True)
                            with open(os.path.join(od, name + '.out'), 'w') as fo:
                                fo.write('SOLVER fleet_ks\n' + ''.join(f"ROUTE {v} {' '.join(map(str, r))}\n" for v, r in enumerate(R)))
                            stack = [None]; break
                        continue
                    _, (i, j) = max(frac); orders = range(N)
                    no = forb + ((i, j),)
                    yes = list(forb)
                    if i >= 0: yes += [(i, k) for k in list(orders) + [-1] if k != j and k != i]
                    if j >= 0: yes += [(l, j) for l in list(orders) + [-1] if l != i and l != j]
                    stack += [no, tuple(yes)]
                    if nodes % 10 == 1: log(f'   узел {nodes}: ЛП {z:.4f}, ветвление по дуге {i}->{j} (поток {flow[(i, j)]:.3f}), открыто {len(stack)}, закрыто {closed}, {time.time()-tb:.0f} с')
                done = not stack
                log(f'RESULT B&P узла {m}: ' + ('ЗАКРЫТ — разбиения на %d маршрутов нет' % (K - 1) if done else 'не закончен') + f', узлов {nodes}, закрыто {closed}, {time.time()-tb:.0f} с')
                pr.close(); return
            z, du, x = M.solve(); pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            cuts = [(S[0], S[1], S[2], du[row], Mem) for S, row, Mem in zip(M.cuts, M.cutrow, M.mem)]
            alpha = [1.0 - mu[t] - lam for t in range(T)]
            # лагранжева граница узла при этих ценах (типы с нулевым числом бригад в узле не участвуют)
            gap = (K - 1) - z + 1e-6
            te = time.time(); routes, comp = pr.enum(gap, 0.0, a.enum_max, f'/tmp/fks_enum_{os.getpid()}.txt', alpha, pi, cuts)
            routes = [r for r in routes if m[r[0]] > 0]
            log(f'  перебор: запас {gap:.2e}, маршрутов {len(routes)} (в типах узла), полный {comp}, {time.time()-te:.0f} с')
            if comp:
                import sp_cpsat
                el = [(t, km, tuple(r)) for (t, km, r) in routes if len(set(r)) == len(r)]
                res = sp_cpsat.solve(N, types, el, 2, Pbest, K, [CM.PEN[o['pri']] for o in I['ords']], tlo=list(m), tup=list(m), tl=1800, ub=K - 1)
                log(f'  CP-SAT по полному перебору: {res["status"]}, маршрутов {res["obj"]}')
                if res['status'] == 'Infeasible': log(f'RESULT узел {m} закрыт: разбиения на {K - 1} маршрутов нет')
                elif res['chosen']:
                    R = CM.routes_to_vehicles(types, res['chosen'], I['V']); log('  НОВОЕ РЕШЕНИЕ', CM.score(I, R))
                    od = os.path.join(HERE, 'results', d, 'fleet_ks'); os.makedirs(od, exist_ok=True)
                    with open(os.path.join(od, name + '.out'), 'w') as fo:
                        fo.write('SOLVER fleet_ks\n' + ''.join(f"ROUTE {v} {' '.join(map(str, r))}\n" for v, r in enumerate(R)))
            pr.close(); return
        part, nparts = map(int, a.part.split('/')); my = combos[part::nparts]
        log(f'составов из {K - 1} бригад: {len(combos)}, мои: {len(my)} (часть {part}/{nparts})')
        pruned = 0; open_ = []
        for m in my:
            M.set_types(list(m), list(m), big)
            z, cv = cg_conv(a.combo_tl, 150)
            st = 'отпал' if cv and z > K - 1 + 1e-6 else ('НЕ ОТПАЛ' if cv else 'не сошлась')
            if st == 'отпал': pruned += 1
            else: open_.append(m)
            log(f'  {m}: LP {z:.4f} {st} (колонок {len(M.cols)}, SRC {len(M.cuts)})')
        log(f'RESULT typebranch part {part}/{nparts}: отпало {pruned} из {len(my)}; открытые {open_}')
        pr.close(); return
    level = 1; best_lb = L0.get('lb_used', 0); z = None; conv = False
    while time.time() - t0 < a.tl:
        z, du, x = M.solve()
        pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
        pr.fl = [(du[f['row']], f['S'], f['ty']) for f in M.fl]
        alpha = [1.0 - mu[t] - lam for t in range(T)]
        cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 0.0, alpha, pi)
        added = sum(M.add(t, km, r) for (t, km, rc, r) in cols)
        if added:
            if level == 2 and added < 5: level = 1
            continue
        if level == 1: level = 2; continue
        if not comp: log('   pricing incomplete — ЛП не сошлась, граница не засчитывается'); conv = False; break
        conv = True; lb = int(math.ceil(z - 1e-6)); best_lb = max(best_lb, lb)
        log(f'  LP {z:.6f} -> граница парка {lb} (лучшее решение {K}); колонок {len(M.cols)}, отсечений k(S) {len(M.fl)}')
        if lb >= K: break
        if a.mip > 0:
            import sp_cpsat
            el = [(t, km, tuple(r)) for (t, km, r) in M.cols if len(set(r)) == len(r)]
            log(f'  CP-SAT: разбиение на <= {K - 1} маршрутов по пулу из {len(el)} маршрутов, {sp_cpsat.workers()} потоков, {a.mip:.0f} с')
            res = sp_cpsat.solve(N, types, el, 2, Pbest, K, [CM.PEN[o['pri']] for o in I['ords']], tl=a.mip, ub=K - 1,
                                 hint={(vtype[v], tuple(r)) for v, r in enumerate(Rb) if r})
            log(f'  CP-SAT: {res["status"]}, маршрутов {res["obj"]}, граница по пулу {res["bound"]}')
            if res['chosen']:
                R = CM.routes_to_vehicles(types, res['chosen'], I['V']); sc = CM.score(I, R); log('  НОВОЕ РЕШЕНИЕ', sc)
                od = os.path.join(HERE, 'results', d, 'fleet_ks'); os.makedirs(od, exist_ok=True)
                with open(os.path.join(od, name + '.out'), 'w') as fo:
                    fo.write('SOLVER fleet_ks\n' + ''.join(f"ROUTE {v} {' '.join(map(str, r))}\n" for v, r in enumerate(R)))
            break
        new = separate(x)
        if not new: log('  нарушенных отсечений k(S) нет'); break
        log('  +' + ', '.join(f'{nm}(|S|={n})>={k} (нарушение {g})' for nm, n, k, g in new))
        level = 1
    log(f'RESULT fleet LB {best_lb} (было {L0.get("lb_used")}), best {K}, LP {z}, conv {conv}, k(S) rows {len(M.fl)}, {time.time()-t0:.0f}s')
    pr.close()
    if a.write and conv and best_lb > L0.get('lb_used', -1):
        L0.update(lb_used=best_lb, used_proof='fleet_ks (k(S) fleet cuts, fleet_ks.py)')
        if best_lb >= K: L0['used_conv'] = True
        json.dump(L0, open(lbf, 'w'), indent=1); log('записано в', lbf)


if __name__ == '__main__':
    main()
