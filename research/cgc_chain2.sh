#!/bin/bash
# cgc: after queue 2 -> validation runs on instances with proven optimum (must give LB <= optimum), write disabled
cd /Users/nikitosh/Downloads/lct/research
while kill -0 7060 2>/dev/null; do sleep 10; done
for x in instances_gen_road/scarce_yugovostok_5 instances_road/yugovostok_s5; do
  d=${x%%/*}; i=${x##*/}
  .venv/bin/python cgc_master.py $d/$i.txt --tl 300 --node-tl 60 --kstl 60 --write 0 --check > runs/results/cgc_tmp/logs/valid_${i}.log 2>&1
done
