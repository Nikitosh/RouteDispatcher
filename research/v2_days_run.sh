#!/bin/sh
# Лучшие известные и точные границы для новых реальных дней (instances_days_v2), как v2_run.sh:
# продукт (s76, 3 с, 3 сида) и эталон (s76, 20 с) → best.py → cg_batch (600 с на задачу, 4 потока) → best.py + cg_finalize.
cd "$(dirname "$0")"
d=instances_days_v2
make -s bin/s76_coopG2
for f in $d/*.txt; do n=$(basename $f .txt)
  for s in 1 2 3; do o=runs/results/$d/prod3_s$s/$n.out; mkdir -p $(dirname $o); [ -s $o ] || bin/s76_coopG2 $f 3 $s > $o; done
  o=runs/results/$d/s76_20s/$n.out; mkdir -p $(dirname $o); [ -s $o ] || bin/s76_coopG2 $f 20 11 > $o
done; echo "solvers done $(date +%H:%M)"
python3 best.py $d > /dev/null; echo "best done $(date +%H:%M)"
CG_SKIP_DONE=1 .venv/bin/python cg_batch.py $d 4 --tl 600 > runs/results/$d/cg_batch.log 2>&1; echo "cg done $(date +%H:%M)"
python3 best.py $d > /dev/null; .venv/bin/python cg_finalize.py $d > /dev/null 2>&1; echo "ALL DONE $(date +%H:%M)"
