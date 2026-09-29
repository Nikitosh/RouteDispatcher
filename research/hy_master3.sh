#!/bin/bash
export WORKERS=2
python3 hy_polish.py instances_gen_road 20 4 3 > runs/hy_runs/polish_genroad_4.log 2>&1
python3 hy_polish.py instances_road 20 3 3 > runs/hy_runs/polish_road_3.log 2>&1
echo MASTER3 DONE
