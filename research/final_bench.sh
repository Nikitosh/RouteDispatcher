#!/bin/bash
# Итоговое сравнение решателей для продукта (1 с), чистые прогоны
D=$1; W=${2:-2}
for s in s10_sa s12_alns s23_sa3 s31_sisr2 s33_sasisr s40_hy pyvrp; do
  WORKERS=$W INST=$D .venv/bin/python bench.py 1 1 $s 2>&1 | grep -v -e Warning -e "ld: warning" | tail -1
  mv bench_${D}_tl1.0_${s}.json runs/final/ 2>/dev/null
done
echo FINAL_DONE $D
