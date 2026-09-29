"""Выгрузка задач для C++ решателей.
Формат (пробелы/переводы строк):
name
N V S                      # заявок, бригад, стартовых точек; узлы 0..S-1 старты, S..S+N-1 заявки
N строк: id svc a b pri skill     # a,b: допустимое начало работ, минуты от 10:00 (b уже урезано до 720-svc); skill 0=L 1=C 2=A
V строк: start_node mode skillmask   # mode 0=car 1=pt 2=bike 3=foot
для mode 0..3: матрица времени (S+N)^2 в минутах, затем матрица расстояний в км
"""
import sys, solve2
from data import load
MODES=['car','pt','bike','foot']; SKI={'L':0,'C':1,'A':2}
def export(region, n, seed, path, far=3):
    ros=solve2.roster(n,seed,region)
    if region=='Юго-восток' and far>=3: ros[2].update(start=1,mode='car',skills={'L','C','A'})
    orders,S,T,D=solve2.matrices(region)
    N=len(orders); M=S+N
    with open(path,'w') as f:
        f.write(f"{region}_s{seed}\n{N} {n} {S}\n")
        for o in orders:
            b=min(o['b'],720-o['svc'])
            f.write(f"{o['id']} {o['svc']} {o['a']} {b} {o['pri']} {SKI[solve2.SK[o['bk']]]}\n")
        for b in ros:
            f.write(f"{b['start']} {MODES.index(b['mode'])} {sum(1<<SKI[s] for s in b['skills'])}\n")
        for m in MODES:
            for i in range(M): f.write(' '.join(f"{T[m][i][j]:.3f}" for j in range(M))+'\n')
            for i in range(M): f.write(' '.join(f"{D[m][i][j]:.4f}" for j in range(M))+'\n')
if __name__=='__main__':
    tr={'Восток':'vostok','Югоцентр':'yugocentr','Юго-восток':'yugovostok'}
    for region,n in [('Восток',12),('Югоцентр',11),('Юго-восток',12)]:
        for sd in range(10):
            export(region,n,sd,f"instances/{tr[region]}_s{sd}.txt")
    print("ok")
