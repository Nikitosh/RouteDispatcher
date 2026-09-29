#!/bin/sh
# Бенчмарк модели продукта на 124 задачах: продукт (s76, 3 с, 3 сида) и эталон (s76, 20 с) → лучшие известные →
# точные границы (cg_master) → сводка (v2_report.py). Прогоны s60 из первого запуска лежат в runs/results/*/s60_*.
cd "$(dirname "$0")"
DIRS="instances_control_v2 instances_road_v2 instances_gen_v2"
make -s bin/s76_coopG2
for d in $DIRS; do
  for f in $d/*.txt; do n=$(basename $f .txt)
    for s in 1 2 3; do o=runs/results/$d/prod3_s$s/$n.out; mkdir -p $(dirname $o); [ -s $o ] || bin/s76_coopG2 $f 3 $s > $o; done
    o=runs/results/$d/s76_20s/$n.out; mkdir -p $(dirname $o); [ -s $o ] || bin/s76_coopG2 $f 20 11 > $o
  done; echo "solvers done: $d $(date +%H:%M)"
done
python3 best.py $DIRS > /dev/null; echo "best done $(date +%H:%M)"
for d in $DIRS; do CG_SKIP_DONE=1 .venv/bin/python cg_batch.py $d 4 --tl 600 > runs/results/$d/cg_batch.log 2>&1; echo "cg done: $d $(date +%H:%M)"; done
python3 best.py $DIRS > /dev/null; .venv/bin/python cg_finalize.py $DIRS > /dev/null 2>&1
python3 v2_report.py > v2_report.md; echo "ALL DONE $(date +%H:%M)"
