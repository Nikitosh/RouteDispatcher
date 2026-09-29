#!/bin/sh
# Ночной расчёт 29.09: решатели на новых участках → лучшие известные → границы cg_master (лимит 15 мин/задачу, 6 потоков)
# → сводка → второй проход cgc_master по незакрытым (лимит 30 мин/задачу). Лог: v3_run.log.
cd "$(dirname "$0")"
OLD="instances_control_v2 instances_road_v2 instances_gen_v2"; NEW="instances_newctl_v2 instances_newroad_v2"
for d in $NEW; do
  for f in $d/*.txt; do n=$(basename $f .txt)
    for s in 1 2 3; do o=runs/results/$d/prod3_s$s/$n.out; mkdir -p $(dirname $o); [ -s $o ] || bin/s76_coopG2 $f 3 $s > $o; done
    o=runs/results/$d/s76_20s/$n.out; mkdir -p $(dirname $o); [ -s $o ] || bin/s76_coopG2 $f 20 11 > $o
  done; echo "solvers done: $d $(date +%H:%M)"
done
python3 best.py $OLD $NEW > /dev/null; echo "best done $(date +%H:%M)"
python3 v3_cg.py cg 6 900 instances_road_v2 instances_gen_v2 $NEW
python3 best.py $OLD $NEW > /dev/null; .venv/bin/python cg_finalize.py $OLD $NEW > /dev/null 2>&1
python3 v2_report.py > v2_report.md; echo "stage1 report $(date +%H:%M)"
python3 - $OLD $NEW > v3_todo.txt <<'PY'
import glob, json, os, sys
rows = []
for d in sys.argv[1:]:
    for f in sorted(glob.glob(f'{d}/*.txt')):
        n = os.path.basename(f)[:-4]; p = f'lb/{d}/{n}.json'
        j = json.load(open(p)) if os.path.exists(p) else {}
        if j.get('proven_optimal'): continue
        gap = (j['best_km'] / j['lb_km'] - 1) if j.get('lb_km') and j.get('best_used') == j.get('lb_used') else 9
        rows.append((gap, f))
for g, f in sorted(rows): print(f)
PY
echo "phase2: $(wc -l < v3_todo.txt) задач $(date +%H:%M)"
python3 v3_cg.py cgc 6 1800 v3_todo.txt
python3 best.py $OLD $NEW > /dev/null; .venv/bin/python cg_finalize.py $OLD $NEW > /dev/null 2>&1
python3 v2_report.py > v2_report.md; echo "ALL DONE $(date +%H:%M)"
