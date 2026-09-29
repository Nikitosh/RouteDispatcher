#!/bin/bash
while pgrep -f hy_chain1.sh >/dev/null; do sleep 5; done
export WORKERS=2
python3 hy_runs.py instances_control_road 3 1,2 s10_sa,s12_alns,s13_sisr
python3 hy_pipe.py instances_control_road 3 1,2 p1,p2,p3,s40
python3 hy_runs.py instances_control_road 10 1,2,3 s10_sa,s12_alns,s13_sisr
echo CHAIN2 DONE
