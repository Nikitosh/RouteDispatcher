"""Исходные 30 задач (instances/) заново, но с реальными дорогами -> instances_road/."""
import os, solve2, export, roads
from data import load
def mats(region):
    office,orders=load(region); starts=[(office['lat'],office['lon'])]+solve2.EXTRA.get(region,[])
    T,D=roads.all_modes(starts+[(o['lat'],o['lon']) for o in orders]); return orders,len(starts),T,D
solve2.matrices=mats
os.makedirs('instances_road',exist_ok=True)
tr={'Восток':'vostok','Югоцентр':'yugocentr','Юго-восток':'yugovostok'}
for region,n in [('Восток',12),('Югоцентр',11),('Юго-восток',12)]:
    for sd in range(10): export.export(region,n,sd,f"instances_road/{tr[region]}_s{sd}.txt")
print('instances_road ok')
