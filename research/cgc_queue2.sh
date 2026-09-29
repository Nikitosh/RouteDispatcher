#!/bin/bash
# cgc: like cgc_queue.sh, plus EXTRA env var with additional cgc_master.py flags
cd /Users/nikitosh/Downloads/lct/research
TL=${TL:-720}
for x in "$@"; do
  d=${x%%/*}; i=${x##*/}
  .venv/bin/python cgc_master.py $d/$i.txt --tl $TL --node-tl 90 --kstl ${KSTL:-200} $EXTRA > runs/results/cgc_tmp/logs/${i}_bp${TAG}.log 2>&1
done
