#!/bin/bash
# Очередь длинных запусков: longq.sh <папка> <решатель> <время> <сид> <задача...>; пишет runs/results/<папка>/long/<решатель>_<сид>_<время>/<задача>.out
D=$1; S=$2; T=$3; SD=$4; shift 4
O=runs/results/$D/long/${S}_${SD}_${T}; mkdir -p $O
for i in "$@"; do bin/$S $D/$i.txt $T $SD > $O/$i.tmp && mv $O/$i.tmp $O/$i.out; echo "$D $i $S $SD $T $(tail -1 $O/$i.out)"; done
