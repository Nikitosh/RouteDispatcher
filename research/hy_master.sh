#!/bin/bash
# последовательная очередь (не более 2 процессов решателей одновременно)
while pgrep -f hy_chain6b >/dev/null; do sleep 5; done
export WORKERS=2
python3 hy_runs.py instances_control_road 30 1,2,3,4 s12_alns,s14_hgs_lite,s10_sa yugovostok
echo step7a
python3 hy_polish.py instances_control_road 10 1 3 > runs/hy_runs/polish_ctrlroad_1.log 2>&1
python3 hy_polish.py instances_control_road 10 2 3 > runs/hy_runs/polish_ctrlroad_2.log 2>&1
echo step7
python3 hy_polish.py instances_gen_road 5 1 3 > runs/hy_runs/polish_genroad_1.log 2>&1
python3 hy_polish.py instances_road 5 1 3 > runs/hy_runs/polish_road_1.log 2>&1
echo step8
python3 hy_polish.py instances_control_road 20 3 3 > runs/hy_runs/polish_ctrlroad_3.log 2>&1
python3 hy_polish.py instances_road 10 2 3 > runs/hy_runs/polish_road_2.log 2>&1
python3 hy_polish.py instances_gen_road 10 2 3 > runs/hy_runs/polish_genroad_2.log 2>&1
python3 hy_polish.py instances 10 1 3 > runs/hy_runs/polish_inst_1.log 2>&1
python3 hy_polish.py instances_gen 10 2 3 > runs/hy_runs/polish_gen_2.log 2>&1
echo step9
WORKERS=1 python3 hy_pipe.py instances_gen_road 10 1 p3@0.7
python3 hy_polish.py instances_gen_road 10 3 3 > runs/hy_runs/polish_genroad_3.log 2>&1
echo MASTER DONE
