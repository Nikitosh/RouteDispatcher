#!/bin/sh
# Второй проход доказательств для модели продукта: cgc_master (ветвление с отсечениями по числу бригад, 3-SRC)
# по часу на каждую задачу, которую не закрыл cg_master. 4 задачи одновременно. Затем best.py, cg_finalize, сводка.
cd "$(dirname "$0")"
DIRS="instances_control_v2 instances_road_v2 instances_gen_v2"
python3 - $DIRS > v2_phase2.todo <<'PY'
import glob, json, os, sys
for d in sys.argv[1:]:
    for f in sorted(glob.glob(f'{d}/*.txt')):
        n = os.path.basename(f)[:-4]; p = f'lb/{d}/{n}.json'
        if not (os.path.exists(p) and json.load(open(p)).get('proven_optimal')): print(f)
PY
echo "к доказательству: $(wc -l < v2_phase2.todo) задач, $(date +%H:%M)"
xargs -P 4 -I{} sh -c 'n=$(basename {} .txt); d=$(dirname {}); mkdir -p runs/results/$d/cgc; .venv/bin/python cgc_master.py {} --tl 3600 > runs/results/$d/cgc/$n.log 2>&1' < v2_phase2.todo
python3 best.py $DIRS > /dev/null; .venv/bin/python cg_finalize.py $DIRS > /dev/null 2>&1
python3 v2_report.py > v2_report.md; echo "PHASE2 DONE $(date +%H:%M)"
