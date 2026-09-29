#!/bin/bash
# cgc: run cgc_master.py (B&P with fleet cuts) sequentially over "dir/inst" arguments. TL env var = per-instance seconds.
cd /Users/nikitosh/Downloads/lct/research
TL=${TL:-720}
for x in "$@"; do
  d=${x%%/*}; i=${x##*/}
  .venv/bin/python cgc_master.py $d/$i.txt --tl $TL --node-tl 90 --kstl ${KSTL:-200} > runs/results/cgc_tmp/logs/${i}_bp.log 2>&1
done
