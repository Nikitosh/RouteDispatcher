"""Разбор решения по кластерам дальних домов: python3 clus_sol.py <задача.txt> <решение.out | cfg.json:сид>
Для каждой бригады: старт, транспорт, навыки, км, последовательность кластеров заявок (кластер = ближайший старт)."""
import sys, json
from validate import load_instance, parse_output
MODES=['car','pt','bike','foot']; SK='LCA'
I=load_instance(sys.argv[1]); src=sys.argv[2]
if ':' in src and src.split(':')[0].endswith('.json'):
    f,sd=src.split(':'); D=json.load(open(f)); nm=sys.argv[1].split('/')[-1][:-4]
    R=[r for r in D['rows'] if r['i']==nm and r['sd']==int(sd)][0]['routes']
else: R,_=parse_output(open(src).read())
S=I['S']; T=I['T'][0]
rep=list(range(S))
for s in range(S):
    for q in range(s):
        if T[q][s]<3 and T[s][q]<3: rep[s]=rep[q]; break
names={}; 
for s in range(S):
    if rep[s]==s: names[s]=chr(ord('O')+len(names)) if s==0 else 'ABCDEFGH'[len(names)-1]
cl=[]
for k in range(I['N']):
    b=min(range(S),key=lambda s:T[s][S+k]); cl.append(names[rep[b]])
from collections import Counter
print('кластеры заявок:',dict(Counter(cl)),' старты:',{names[rep[s]]:s for s in range(S) if rep[s]==s})
cnt=Counter((names[rep[v['start']]],MODES[v['mode']]) for v in I['veh']); print('парк:',dict(cnt))
tot=0;used=0
for v,ve in enumerate(I['veh']):
    r=R[v] if v<len(R) else []
    if not r: continue
    used+=1; m=ve['mode']; prev=ve['start']; km=0; t=0; seq=[]
    for k in r:
        o=I['ords'][k]; n=S+k; beg=max(t+I['T'][m][prev][n],o['a']); km+=I['D'][m][prev][n]; t=beg+o['svc']; prev=n
        seq.append(f"{cl[k]}{int(beg//60)+10:02d}{SK[o['skill']]}")
    tot+=km; mask=''.join(SK[s] for s in range(3) if (ve['mask']>>s)&1)
    print(f"бр{v:2d} {names[rep[ve['start']]]} {MODES[m]:4} {mask:3} {len(r):2d}з {km:6.1f}км кон{int(t//60)+10:02d}:{int(t%60):02d} "+' '.join(seq))
print('бригад',used,'км',round(tot,1))
