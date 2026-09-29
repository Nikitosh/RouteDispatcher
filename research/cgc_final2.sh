#!/bin/bash
# cgc final schedule core 2 (all runs with the integral-node fix)
cd /Users/nikitosh/Downloads/lct/research
TAG=_fix ./cgc_queue2.sh instances_gen_road/large_yugovostok_8 instances_gen_road/scarce_yugovostok_8
TL=900 TAG=_fix EXTRA="--nenum 0.005" ./cgc_queue2.sh instances_road/yugovostok_s9
TAG=_fix ./cgc_queue2.sh instances_road/yugovostok_s1 instances_control_road/control_yugovostok_mix1_inf instances_gen_road/real_yugovostok_8 instances_road/yugovostok_s2
TL=420 TAG=_fix ./cgc_queue2.sh instances_gen_road/large_yugovostok_2
