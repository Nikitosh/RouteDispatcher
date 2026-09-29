"""Задачи, снятые по таймауту в v3_run и оставшиеся без lb/*.json (стартовали до промежуточной записи в cg_master),
пересчитываются облегчённо: cg_master --no-enum --tl 600, по одной. Работает, пока не начнётся второй проход."""
import os, re, subprocess, time
os.chdir(os.path.dirname(os.path.abspath(__file__)))
seen = {l.split()[1] for l in open('v3_lost.log') if len(l.split()) > 1} | {'instances_gen_v2/large_yugovostok_5.txt'} | {l.strip() for l in open('v3_lost.txt')}
while True:
    log = open('v3_run.log').read()
    if 'phase2' in log: break
    for f in re.findall(r'(\S+\.txt): снят по таймауту', log):
        d, n = os.path.dirname(f), os.path.basename(f)[:-4]
        if f in seen or os.path.exists(f'lb/{d}/{n}.json'): continue
        seen.add(f)
        with open(f'runs/results/{d}/cg/{n}.noenum.log', 'w') as fh:
            subprocess.run(['nice', '-n', '5', '.venv/bin/python', 'cg_master.py', f, '--tl', '600', '--no-enum'], stdout=fh, stderr=subprocess.STDOUT)
        with open('v3_lost.log', 'a') as fh: fh.write(f"{time.strftime('%H:%M')} {f} {'lb есть' if os.path.exists(f'lb/{d}/{n}.json') else 'lb нет'}\n")
    time.sleep(60)
