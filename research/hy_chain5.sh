#!/bin/bash
while pgrep -f "hy_chain[234].sh" >/dev/null; do sleep 5; done
export WORKERS=2
python3 hy_pipe.py instances_road 3 1,2 p1n
echo CHAIN5 DONE
