#!/bin/bash
# summary of cgm queue runs: instance, last node line LB/UB, result
SP=/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/d8f727be-c46e-4a0a-ac5e-5e8ed1e794e5/scratchpad
for f in $SP/q1/*.log $SP/cgmq*/*.log; do
  n=$(basename $f .log); r=$(grep RESULT $f | cut -c8-200)
  l=$(grep "  node " $f | tail -1 | grep -o "LB=[0-9.]* UB=[0-9.]*"); nn=$(grep -c "  node " $f)
  s=$(grep "stored lb_km" $f | grep -o "stored lb_km [0-9.]*")
  echo "$n | $s | nodes $nn $l | ${r:0:140}"
done
