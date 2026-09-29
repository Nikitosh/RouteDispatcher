#!/bin/bash
# ждать окончания базовых прогонов
while pgrep -f "hy_runs.py instances_control 10" >/dev/null; do sleep 5; done
export WORKERS=2
python3 hy_runs.py instances_control 3 1,2 s10_sa,s12_alns,s13_sisr
python3 hy_pipe.py instances_control 3 1,2 p1,p2,p3,s40
python3 hy_runs.py instances 3 1,2 s10_sa,s12_alns,s13_sisr
python3 hy_pipe.py instances 3 1,2 p1,p2,p3,s40
echo CHAIN1 DONE
