#!/bin/bash
# usage: cgx_queue.sh <listfile> <logdir> <extra args...>   (lines "set inst"); claim via mkdir
cd /Users/nikitosh/Downloads/lct/research
LIST=$1; LD=$2; shift 2
mkdir -p $LD
while read d n; do
  [ -z "$n" ] && continue
  mkdir $LD/$n.claim 2>/dev/null || continue
  echo "$(date -u +%H:%M:%S) start $d $n" >> $LD/queue.txt
  .venv/bin/python cgx_bp.py $d/$n.txt --write "$@" > $LD/$n.log 2>&1
  echo "$(date -u +%H:%M:%S) done $d $n $(grep RESULT $LD/$n.log)" >> $LD/queue.txt
done < $LIST
