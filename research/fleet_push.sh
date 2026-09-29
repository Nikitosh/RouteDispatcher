#!/bin/bash
# Длинные запуски на задачах, где граница по бригадам ниже лучшего решения
D=instances_gen_road; O=runs/results/$D/fleet
run(){ i=$1; s=$2; sd=$3; bin/$s $D/$i.txt 120 $sd > $O/${i}__${s}_$sd.tmp && mv $O/${i}__${s}_$sd.tmp $O/${i}__${s}_$sd.out.part; echo "$i $s $sd $(tail -1 $O/${i}__${s}_$sd.out.part)"; }
export -f run; export D O
for i in walk_yugovostok_5 walk_yugovostok_2 scarce_yugovostok_8 large_yugovostok_5 large_yugocentr_4; do
  for j in "s21_multi 11" "s33_sasisr 11" "s21_multi 12"; do echo "$i $j"; done
done | xargs -P 2 -L 1 bash -c 'run $0 $1 $2'
echo FLEET_DONE
