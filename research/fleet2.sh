#!/bin/bash
for p in instances_gen_road/walk_yugovostok_2 instances_gen_road/large_yugocentr_4 instances_control_road/control_yugovostok_mix2_inf; do
  d=$(dirname $p); n=$(basename $p)
  for sd in 7 8; do
    ROLES=ARKZ bin/s60_coop $p.txt 120 $sd > runs/results/$d/fleet2/$n.s$sd.tmp
    mkdir -p runs/results/$d/fleet2/s$sd; mv runs/results/$d/fleet2/$n.s$sd.tmp runs/results/$d/fleet2/s$sd/$n.out
    echo "$n s$sd $(tail -1 runs/results/$d/fleet2/s$sd/$n.out)"
  done
done
echo FLEET2_DONE
