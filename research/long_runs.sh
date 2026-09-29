#!/bin/bash
# Длинные запуски (30 с) лучших решателей для улучшения лучших известных решений
for d in instances_road instances_gen_road; do
  for s in s33_sasisr s21_multi; do
    WORKERS=3 INST=$d .venv/bin/python bench.py 30 1 $s 2>&1 | grep -v -e Warning -e "ld: warning" | tail -1 | sed "s/^/$d /"
  done
done
echo LONG_DONE
