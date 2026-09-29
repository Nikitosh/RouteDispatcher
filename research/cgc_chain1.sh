#!/bin/bash
cd /Users/nikitosh/Downloads/lct/research
while kill -0  2>/dev/null; do sleep 5; done
.venv/bin/python cgc_master.py instances_road/yugovostok_s4.txt --nobranch --write 0 --kstl 30 --lm 1 --check --maxsrc 300 --slowprice 25 > runs/results/cgc_tmp/logs/s4_lm2.log 2>&1
./cgc_queue.sh instances_road/yugovostok_s8 instances_road/yugovostok_s9 instances_gen_road/walk_yugovostok_8 instances_gen_road/tight_yugovostok_5 instances_gen_road/real_yugovostok_5 instances_gen_road/peak_yugovostok_2
.venv/bin/python cgc_master.py instances_road/yugovostok_s1.txt --tl 240 --node-tl 60 --kstl 50 --write 0 --check > runs/results/cgc_tmp/logs/s1_check.log 2>&1
./cgc_queue.sh instances_road/yugovostok_s2
