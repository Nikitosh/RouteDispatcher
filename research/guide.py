"""Направленная подсказка: копия задачи, где бригада с домом вне офиса платит PEN× км за заявки вне своего кластера
(кластер = ближайшая стартовая точка по машине). Время не меняется. python guide.py <задача> <выход.txt> [PEN=3]"""
import sys
src,dst=sys.argv[1],sys.argv[2]; PEN=float(sys.argv[3]) if len(sys.argv)>3 else 3
L=open(src).read().split('\n'); N,V,S=map(int,L[1].split()); M=S+N
veh=[list(map(int,L[2+N+v].split())) for v in range(V)]
off=2+N+V; mats=[]   # для каждого вида: T (M строк), D (M строк)
blocks=[L[off+i*M:off+(i+1)*M] for i in range(8)]
Tcar=[list(map(float,r.split())) for r in blocks[0]]
cl=[min(range(S),key=lambda s:Tcar[s][S+k]) for k in range(N)]
for m in range(4):
    D=[list(map(float,r.split())) for r in blocks[2*m+1]]
    for v in range(V):
        pass
    blocks[2*m+1]=D
# штраф зависит от бригады, а матрицы общие на вид транспорта -> делаем отдельный вид для каждой дальней бригады невозможно;
# поэтому штрафуем въезд в заявку вне кластера для всех бригад этого вида, стартующих не из офиса, через дублирование:
# проще — штрафуем в матрице расстояний вида 'car' въезд в заявку k из точки другого кластера (переезды между кластерами).
cls=lambda i: (i if i<S else cl[i-S])
for m in range(4):
    D=blocks[2*m+1]
    for i in range(M):
        for j in range(S,M):
            if cls(i)!=cls(j) and not (cls(i)==0 and i<S): D[i][j]*=PEN
    blocks[2*m+1]=[' '.join(f"{x:.4f}" for x in row) for row in D]
out=L[:off]+[r for b in blocks for r in b]
open(dst,'w').write('\n'.join(out)+'\n')
