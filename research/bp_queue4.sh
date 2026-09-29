#!/bin/bash
# Очередь B&P по открытым исходным задачам (разрыв < 7%), два потока
run(){ i=$1; .venv/bin/python cg_bp2.py instances_gen_road/$i.txt --tl 900 --node-tl 150 > runs/final/bp/q4_$i.log 2>&1; echo "$i $(grep -E 'B&P done|NEW|lb json' runs/final/bp/q4_$i.log | tail -3 | tr '\n' ' ')"; }
export -f run
cat /tmp/bpq_gen.txt | xargs -P 2 -I{} bash -c 'run {}'
echo BPQ4_DONE
