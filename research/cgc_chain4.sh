#!/bin/bash
# cgc final phase, core 2: after queue 2 -> validation on proven-optimal instances (no write; node enumeration on),
# then long B&P on s9
cd /Users/nikitosh/Downloads/lct/research
while kill -0 7060 2>/dev/null; do sleep 10; done
for x in instances_road/yugovostok_s5; do
  d=${x%%/*}; i=${x##*/}
  .venv/bin/python cgc_master.py $d/$i.txt --tl 300 --node-tl 60 --kstl 60 --write 0 --check --nenum 0.01 > runs/results/cgc_tmp/logs/valid_${i}.log 2>&1
done
TL=2100 TAG=_long EXTRA="--nenum 0.005" ./cgc_queue2.sh instances_road/yugovostok_s9
