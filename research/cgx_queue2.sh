#!/bin/bash
# single dispatcher: cgx_queue2.sh <listfile> <logdir> <argsfile>; lines "set inst"; args re-read per instance;
# keeps at most MAXP cgx_bp.py processes running (CPU budget)
cd /Users/nikitosh/Downloads/lct/research
LIST=$1; LD=$2; AF=$3; MAXP=${MAXP:-2}
mkdir -p $LD
while read d n; do
  [ -z "$n" ] && continue
  [ -d $LD/$n.claim ] && continue
  while [ $(pgrep -f "cgx_bp.py" | wc -l) -ge $MAXP ]; do sleep 5; done
  mkdir $LD/$n.claim
  ARGS=$(cat $AF)
  echo "$(date -u +%H:%M:%S) start $d $n $ARGS" >> $LD/queue.txt
  ( .venv/bin/python cgx_bp.py $d/$n.txt --write $ARGS > $LD/$n.log 2>&1; echo "$(date -u +%H:%M:%S) done $d $n $(grep RESULT $LD/$n.log)" >> $LD/queue.txt ) &
  sleep 3
done < $LIST
wait
