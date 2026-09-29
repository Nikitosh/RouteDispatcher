#!/bin/bash
while pgrep -f "hy_runs.py instances_control_road 2" >/dev/null; do sleep 5; done
export WORKERS=2
for D in instances_control_road instances_road; do
python3 hy_runs.py $D 2 1,2,3 s10_sa,s12_alns
python3 hy_pipe.py $D 2 1,2,3 p1@0.7
WORKERS=1 python3 hy_pipe.py $D 2 1,2,3 p3@0.7,p5@0.7
done
echo CHAIN6 DONE
