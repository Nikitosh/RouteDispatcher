#!/bin/bash
# cgc final schedule core 1 (all runs with the integral-node fix)
cd /Users/nikitosh/Downloads/lct/research
TAG=_fix ./cgc_queue2.sh instances_gen_road/peak_yugovostok_2
TL=900 TAG=_fix EXTRA="--node-tl 150" ./cgc_queue2.sh instances_gen_road/tight_yugovostok_5
TL=1200 TAG=_fix EXTRA="--nenum 0.006" ./cgc_queue2.sh instances_gen_road/walk_yugovostok_8
TAG=_fix ./cgc_queue2.sh instances_road/yugovostok_s6 instances_gen_road/tight_yugovostok_2 instances_road/yugovostok_s8
TL=420 TAG=_fix ./cgc_queue2.sh instances_gen_road/real_yugovostok_5
