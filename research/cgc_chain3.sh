#!/bin/bash
# cgc final phase, core 1: after queue 1 -> tight_yugovostok_5 re-run (integral-node fix), s2 re-run,
# then long B&P + node enumeration on walk_yugovostok_8
cd /Users/nikitosh/Downloads/lct/research
while kill -0 8288 2>/dev/null; do sleep 10; done
TL=900 TAG=_rerun EXTRA="--node-tl 150" ./cgc_queue2.sh instances_gen_road/tight_yugovostok_5
./cgc_queue.sh instances_road/yugovostok_s2
TL=2100 TAG=_long EXTRA="--nenum 0.006" ./cgc_queue2.sh instances_gen_road/walk_yugovostok_8
