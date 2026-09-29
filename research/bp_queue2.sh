#!/bin/bash
for i in yugocentr_s2 yugocentr_s4 yugocentr_s5 vostok_s7 yugocentr_s3; do
  .venv/bin/python cg_bp2.py instances_road/$i.txt --tl 900 --node-tl 150 --ng 8 > runs/final/bp/${i}_ng8.log 2>&1
  echo "$i: $(grep -E 'node 1 |B&P done|NEW incumbent|lb json' runs/final/bp/${i}_ng8.log | tail -4 | tr '\n' ' ')"
done
echo BPQ2_DONE
