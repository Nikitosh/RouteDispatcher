"""Доказательство «на K бригадах нельзя» ветвлением по назначению заявок бригадам (29.09, для
control_yugovostok_mix1_inf: 129 из 130 составов из 9 бригад отпадают по ЛП, остаётся один — его задача с 9 бригадами
подаётся сюда).

Узел — {заявка k: множество бригад, которым её можно отдать}. Узел записывается как задача: у заявки k свой
«навык» (бит 6+i маски), он есть только у разрешённых бригад, которые и так умели её брать; прайсер и ЛП не меняются.
ЛП: min число маршрутов, каждая заявка ровно один раз (иначе искусственная колонка стоимостью big), бригад типа <= count,
точный ng-прайсинг (bin/cg_price) + 3-SRC. ЛП > K — в этом узле нельзя. Иначе ветвимся по заявке, сильнее всего
«размазанной» между типами: ветка на каждый тип с долей > 0 плюс ветка «остальные типы» (разбиение, ничего не теряется).
Целое ЛП без искусственных колонок — найдено решение на K бригадах.
Usage: fleet_bp.py <задача с нужными бригадами> <K> [--pool out ...] [--tl 3600] [--node-tl 600]"""
import sys, os, json, time, math, argparse, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import load_instance, check, parse_output
import cg_master as CM

HERE = os.path.dirname(os.path.abspath(__file__))


def write_node(base_lines, N, V, forced, fn):
    """forced: {k: set(v)} -> задача, где заявку k берут только бригады из forced[k] (и только если умели)"""
    L = list(base_lines); ords = [L[2 + k].split() for k in range(N)]; veh = [L[2 + N + v].split() for v in range(V)]
    for i, (k, allowed) in enumerate(sorted(forced.items())):
        s0 = int(ords[k][5]); b = 6 + i; assert b < 31, 'слишком глубоко'
        ords[k][5] = str(b)
        for v in range(V):
            if v in allowed and (int(veh[v][2]) >> s0) & 1: veh[v][2] = str(int(veh[v][2]) | (1 << b))
    for k in range(N): L[2 + k] = ' '.join(ords[k])
    for v in range(V): L[2 + N + v] = ' '.join(veh[v])
    open(fn, 'w').write('\n'.join(L))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('inst'); ap.add_argument('K', type=int)
    ap.add_argument('--pool', nargs='*', default=[]); ap.add_argument('--tl', type=float, default=3600)
    ap.add_argument('--node-tl', type=float, default=600); ap.add_argument('--maxsrc', type=int, default=150); ap.add_argument('--ng', type=int, default=10)
    a = ap.parse_args(); t0 = time.time(); K = a.K
    log = lambda *x: print(f'[{time.time()-t0:7.1f}s]', *x, flush=True)
    base = open(a.inst).read().split('\n'); I0 = load_instance(a.inst); N, V = I0['N'], I0['V']
    pool = set()   # (бригада, маршрут) — годятся в любом узле, где все заявки маршрута разрешены этой бригаде
    for f in a.pool:
        try: R, _ = parse_output(open(f).read())
        except Exception: continue
        for v, r in enumerate(R[:V]):
            if r and check(I0, [[]] * v + [r])['ok']: pool.add((v, tuple(r)))
    log(f'{os.path.basename(a.inst)}: N={N}, бригад {V}, цель — доказать, что {K} маршрутов мало; пул {len(pool)} маршрутов')
    tmpd = tempfile.mkdtemp(prefix='fleetbp_'); nodes = 0; closed = 0; big = 10.0 * V

    def solve_node(forced):
        fn = os.path.join(tmpd, 'node.txt'); write_node(base, N, V, forced, fn)
        I = load_instance(fn); pr = CM.Pricer(fn, a.ng); types = pr.types; T = len(types)
        vt = {v: t for t, ty in enumerate(types) for v in ty['vehs']}
        M = CM.Master(I, types); M.stage = 2
        ok = lambda v, r: all((I['veh'][v]['mask'] >> I['ords'][k]['skill']) & 1 for k in r)
        for (v, r) in pool:
            if ok(v, r): M.add(vt[v], 0.0, r)
        for t in range(T):
            for k in range(N):
                if ok(types[t]['vehs'][0], (k,)): M.add(t, 0.0, (k,))
        M.set_stage(2, 0, K + 1); M.set_types([0] * T, [ty['count'] for ty in types], big)
        level = 1; ts = time.time()
        while True:
            z, du, x = M.solve()
            pi = du[:N]; mu = du[N:N + T]; lam = du[N + T]
            cuts = [(S[0], S[1], S[2], du[row]) for S, row in zip(M.cuts, M.cutrow)]
            alpha = [1.0 - mu[t] - lam for t in range(T)]
            cols, mins, comp = pr.price(level, 60 if level == 1 else 200, 0.0, alpha, pi, cuts=cuts)
            added = 0
            for (t, km, rc, r) in cols:
                if M.add(t, 0.0, r): added += 1; pool.add((types[t]['vehs'][0], r))
            if time.time() - ts > a.node_tl: pr.close(); return None, None, None, types
            if added:
                if level == 2 and added < 5: level = 1
                continue
            if level == 1: level = 2; continue
            if not comp: pr.close(); return None, None, None, types
            if z > K + 1e-6 or len(M.cuts) >= a.maxsrc: break
            new = M.separate(x, maxcuts=min(30, a.maxsrc - len(M.cuts)))
            if not new: break
            for S in new: M.add_cut(S)
            level = 1
        pr.close()
        act = [(M.cols[j], x[M.hidx[j]]) for j in range(len(M.cols)) if x[M.hidx[j]] > 1e-7]
        art = sum(x[c] for c in M.art) if M.art else 0.0
        return z, act, art, types

    stack = [{}]; found = None
    while stack and time.time() - t0 < a.tl:
        forced = stack.pop(); nodes += 1
        z, act, art, types = solve_node(forced)
        tag = ', '.join(f'{k}->{sorted(s)}' for k, s in sorted(forced.items())) or 'корень'
        if z is None: log(f'  узел {nodes} [{tag}]: ЛП не сошлась — дерево не закрыто'); stack = [None]; break
        if z > K + 1e-6: closed += 1; log(f'  узел {nodes} [{tag}]: ЛП {z:.4f} > {K} — закрыт'); continue
        # доля каждой заявки по типам
        share = {}
        for (t, km, r), v in act:
            for k in r: share.setdefault(k, {}); share[k][t] = share[k].get(t, 0) + v
        split = [(1 - max(d.values()), k) for k, d in share.items() if k not in forced]
        if art < 1e-6 and all(abs(v - round(v)) < 1e-6 for _, v in act):
            sol = [c for c, v in act if v > 0.5]; R = CM.routes_to_vehicles(types, sol, V); found = (R, CM.score(I0, R))
            log(f'  узел {nodes} [{tag}]: ЦЕЛОЕ РЕШЕНИЕ {found[1]}'); break
        if not split or max(split)[0] < 1e-6:
            log(f'  узел {nodes} [{tag}]: ЛП {z:.4f}, все заявки целиком у одного типа, но решение дробное — нужно другое ветвление'); stack = [None]; break
        _, k = max(split); d = share[k]
        vehs_of = {t: set(types[t]['vehs']) for t in range(len(types))}
        allowed_now = forced.get(k, set(range(V)))
        kids = []
        for t in sorted(d, key=lambda t: d[t]):   # на стек в обратном порядке: сначала самый вероятный тип
            kids.append(({**forced, k: vehs_of[t] & allowed_now}, d[t]))
        rest = allowed_now - set().union(*[vehs_of[t] for t in d])
        if rest: kids.insert(0, ({**forced, k: rest}, 0.0))
        stack += [c for c, _ in kids if c[k]]
        log(f'  узел {nodes} [{tag}]: ЛП {z:.4f} (иск. {art:.3f}), ветвление по заявке {k}: ' +
            ', '.join(f'тип{t}({sorted(vehs_of[t])})={v:.2f}' for t, v in sorted(d.items(), key=lambda kv: -kv[1])) + (f', остальные {sorted(rest)}' if rest else '') + f'; открыто {len(stack)}')
    if found: log('RESULT найдено решение на', K, 'бригадах:', found[1]); print('ROUTES', json.dumps(found[0]))
    elif not stack: log(f'RESULT ДОКАЗАНО: на {K} бригадах нельзя (узлов {nodes}, закрыто {closed}, {time.time()-t0:.0f} с)')
    else: log(f'RESULT не закончено: узлов {nodes}, закрыто {closed}')


if __name__ == '__main__':
    main()
