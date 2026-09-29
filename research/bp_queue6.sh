#!/bin/bash
run(){ d=$1; i=$2; .venv/bin/python cg_bp2.py $d/$i.txt --tl 3600 --node-tl 150 > runs/final/bp/q6_$i.log 2>&1; echo "$i $(grep -E 'B&P done|NEW|lb json' runs/final/bp/q6_$i.log | tail -3 | tr '\n' ' ')"; }
export -f run
cat /tmp/bpq6.txt | xargs -P 3 -L 1 bash -c 'run $0 $1'
echo BPQ6_DONE
