#!/bin/bash
WORKERS=4 python3 evalcfg.py fin_s33_3 s33_sasisr 3 2
WORKERS=4 python3 evalcfg.py fin_s71_3 s71_racesp 3 2 ADAPT=1 F0=0.5 FA=0.65
WORKERS=1 python3 evalcfg.py fin_s60_3 s60_coop 3 2
WORKERS=1 python3 evalcfg.py fin_s60_8t_3 s60_coop 3 2 ROLES=ARKZARKZ
echo FIN3_DONE
