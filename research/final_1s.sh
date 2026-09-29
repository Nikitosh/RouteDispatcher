#!/bin/bash
T=$1
WORKERS=4 python3 evalcfg.py fin_s33_$T s33_sasisr $T 2
WORKERS=4 python3 evalcfg.py fin_s51_$T s51_race $T 2 ADAPT=1 F0=0.5 FA=0.65
WORKERS=4 python3 evalcfg.py fin_s71_$T s71_racesp $T 2 ADAPT=1 F0=0.5 FA=0.65
WORKERS=4 python3 evalcfg.py fin_s80_$T s80_lagcg $T 2
WORKERS=2 python3 evalcfg.py fin_s60zr_$T s60_coop $T 2 ROLES=ZR
WORKERS=1 python3 evalcfg.py fin_s60_$T s60_coop $T 2
echo FIN_DONE $T
