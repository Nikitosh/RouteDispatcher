#!/bin/bash
while pgrep -f hy_master.sh >/dev/null; do sleep 5; done
export WORKERS=2
python3 hy_runs.py instances_road 1 1,2,3 s10_sa,s12_alns
WORKERS=1 python3 hy_pipe.py instances_road 1 1,2,3 p5@0.7,p5
echo POST DONE
