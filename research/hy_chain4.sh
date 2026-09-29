#!/bin/bash
while pgrep -f "hy_chain[23].sh" >/dev/null; do sleep 5; done
export WORKERS=2 SPW=2
python3 hy_runs.py instances_road 3 1,2 s10_sa,s12_alns,s13_sisr
python3 hy_pipe.py instances_road 3 1,2 p1,p3
WORKERS=1 python3 hy_pipe.py instances_road 3 1,2 p5
WORKERS=1 python3 hy_pipe.py instances_control_road 3 1,2 p5
echo CHAIN4 DONE
