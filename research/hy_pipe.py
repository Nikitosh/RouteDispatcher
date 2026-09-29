"""Конвейеры решателей. python3 hy_pipe.py <instdir> <T> <seeds 1,2> <pipes: p1,p2,p3,...> [filter]; WORKERS=2
Результат: runs/hy_runs/<instdir>/<pipe>_t<T>_s<seed>/<inst>.out
  p1  = s12_alns 0.5T -> s40_hy (INIT) 0.5T
  p2  = s10_sa  0.5T -> s40_hy 0.5T
  p3  = s10_sa || s12_alns по 0.5T (2 ядра) -> s40_hy 0.5T с INIT=оба
  p4  = s12_alns 0.5T -> s13_sisr на подзадаче с бригадами решения 0.5T (без правки решателей)
  s40 = s40_hy самостоятельно T"""
import glob, os, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from validate import load_instance, check, parse_output
d, T, seeds, pipes = sys.argv[1], float(sys.argv[2]), sys.argv[3].split(','), sys.argv[4].split(',')
flt = sys.argv[5] if len(sys.argv) > 5 else ''
W = int(os.environ.get('WORKERS', 2)); D = os.path.basename(d.rstrip('/'))
SCR = os.environ.get('HYTMP', '/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/d8f727be-c46e-4a0a-ac5e-5e8ed1e794e5/scratchpad/pipe')
os.makedirs(SCR, exist_ok=True)
def run(cmd, env=None, tl=10):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=e, timeout=tl * 5 + 60).stdout
def tmpf(txt):
    fd, p = tempfile.mkstemp(dir=SCR, suffix='.out'); os.write(fd, txt.encode()); os.close(fd); return p
def job(j):
    pipe, p, sd, out = j; h = T / 2
    if pipe.endswith('n'): os.environ['NODP'] = '1'; os.environ['ELIM'] = '0'; pipe = pipe[:-1]   # абляция: без ДП
    h2 = h
    if '@' in pipe: pipe, fr = pipe.split('@'); h = T * float(fr); h2 = T - h   # p3@0.7: 70% времени на первый этап
    if pipe == 'p1':
        a = tmpf(run(['bin/s12_alns', p, str(h), sd], tl=h)); r = run(['bin/s40_hy', p, str(h2), sd], {'INIT': a}, h2); os.remove(a)
    elif pipe == 'p2':
        a = tmpf(run(['bin/s10_sa', p, str(h), sd], tl=h)); r = run(['bin/s40_hy', p, str(h2), sd], {'INIT': a}, h2); os.remove(a)
    elif pipe == 'p3':
        with ThreadPoolExecutor(2) as ex:
            f1 = ex.submit(run, ['bin/s10_sa', p, str(h), sd], None, h); f2 = ex.submit(run, ['bin/s12_alns', p, str(h), sd], None, h)
            a, b = tmpf(f1.result()), tmpf(f2.result())
        r = run(['bin/s40_hy', p, str(h2), sd], {'INIT': a + ',' + b}, h2); os.remove(a); os.remove(b)
    elif pipe in ('p5', 'p6'):   # s10 || s12 (p6: s10 || s13) по 0.5T -> два s40 (разные сиды) по 0.5T параллельно, лучший
        second = 'bin/s12_alns' if pipe == 'p5' else 'bin/s13_sisr'
        with ThreadPoolExecutor(2) as ex:
            f1 = ex.submit(run, ['bin/s10_sa', p, str(h), sd], None, h); f2 = ex.submit(run, [second, p, str(h), sd], None, h)
            a, b = tmpf(f1.result()), tmpf(f2.result())
        with ThreadPoolExecutor(2) as ex:
            g = [ex.submit(run, ['bin/s40_hy', p, str(h2), str(int(sd) * 100 + q)], {'INIT': a + ',' + b}, h2) for q in (1, 2)]
            outs = [x.result() for x in g]
        os.remove(a); os.remove(b)
        I = load_instance(p); PEN = {1: 100, 2: 50, 3: 20}
        def sc(o):
            rr, _ = parse_output(o); rr = (rr + [[]] * I['V'])[:I['V']]; c = check(I, rr)
            if not c['ok']: return (1e18,)
            sv = {k for x in rr for k in x}; return (sum(PEN[I['ords'][k]['pri']] for k in range(I['N']) if k not in sv), c['used'], c['km'])
        r = min(outs, key=sc)
    elif pipe == 's40':
        r = run(['bin/s40_hy', p, str(T), sd], tl=T)
    else: raise SystemExit('unknown pipe ' + pipe)
    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, 'w').write(r)
insts = [p for p in sorted(glob.glob(f'{d}/*.txt')) if flt in p]
jobs = [(pp, p, sd, f'runs/hy_runs/{D}/{pp}_t{T:g}_s{sd}/{os.path.basename(p)[:-4]}.out') for pp in pipes for sd in seeds for p in insts]
jobs = [j for j in jobs if not os.path.exists(j[3])]
with ThreadPoolExecutor(W) as ex: list(ex.map(job, jobs))
print('done', len(jobs))
