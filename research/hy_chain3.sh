#!/bin/bash
while pgrep -f hy_chain2.sh >/dev/null; do sleep 5; done
export WORKERS=2 SPW=2
.venv/bin/python hy_spall.py instances_control 20 30 20 > runs/hy_runs/spall_control.log 2>&1
.venv/bin/python hy_spall.py instances_control_road 20 30 20 > runs/hy_runs/spall_control_road.log 2>&1
.venv/bin/python hy_polish.py instances_gen 5 1 3 > runs/hy_runs/polish_gen.log 2>&1
echo CHAIN3 DONE
