#!/bin/bash
# Исходная точка: существующие решатели на задачах с реальными дорогами, лимит 1 с
for d in instances_road instances_gen_road; do
  for s in s03 s10 s12 s13 s14 pyvrp; do
    WORKERS=2 INST=$d .venv/bin/python bench.py 1 1 $s 2>&1 | grep -v -e Warning -e "ld: warning" | tail -1 | sed "s/^/$d /"
  done
done
echo BASELINE_DONE
