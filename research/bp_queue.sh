#!/bin/bash
# Очередь поиска ветвей и цен на задачах с умеренным разрывом (один процесс)
for i in vostok_s0 yugocentr_s2 yugocentr_s4 yugocentr_s5 vostok_s7 yugocentr_s3; do
  .venv/bin/python cg_bp2.py instances_road/$i.txt --tl 900 --node-tl 600 > runs/final/bp/$i.log 2>&1
  echo "$i: $(grep -E 'B&P done|NEW incumbent|lb json' runs/final/bp/$i.log | tail -3 | tr '\n' ' ')"
done
echo BPQ_DONE
