#!/bin/bash
while pgrep -f "hy_master.sh|hy_post.sh" >/dev/null; do sleep 5; done
export WORKERS=2
python3 hy_polish.py instances_control_road 10 1 3 > runs/hy_runs/polish_ctrlroad_1.log 2>&1
python3 hy_polish.py instances_control_road 10 2 3 > runs/hy_runs/polish_ctrlroad_2.log 2>&1
python3 hy_polish.py instances_road 5 1 3 > runs/hy_runs/polish_road_1.log 2>&1
python3 hy_polish.py instances_control_road 20 3 3 > runs/hy_runs/polish_ctrlroad_3.log 2>&1
python3 hy_polish.py instances_road 10 2 3 > runs/hy_runs/polish_road_2.log 2>&1
python3 hy_polish.py instances_gen_road 10 2 3 > runs/hy_runs/polish_genroad_2.log 2>&1
python3 hy_polish.py instances 10 1 3 > runs/hy_runs/polish_inst_1.log 2>&1
python3 hy_polish.py instances_gen 10 2 3 > runs/hy_runs/polish_gen_2.log 2>&1
echo MASTER2 DONE
