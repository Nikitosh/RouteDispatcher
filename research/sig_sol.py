"""Сигнатура «территорий» решения: для каждой бригады, покидающей свой кластер, — сжатая цепочка кластеров (A — офис).
python3 sig_sol.py <задача.txt> <решение.out | cfg.json:сид>"""
import sys, json
from validate import load_instance, parse_output
def sig(I,R):
    S=I['S']; T=I['T'][0]; rep=list(range(S))
    for a in range(S):
        for q in range(a):
            if T[q][a]<3 and T[a][q]<3: rep[a]=rep[q]; break
    cl=[rep[min(range(S),key=lambda s:T[s][S+k])] for k in range(I['N'])]
    out=[]
    for v,r in enumerate(R):
        if not r: continue
        h=rep[I['veh'][v]['start']]; q=chr(65+h)
        for k in r:
            c=chr(65+cl[k])
            if c!=q[-1]: q+=c
        if any(cl[k]!=h for k in r): out.append(f"{v}:{q}")
    return ' '.join(out)
if __name__=='__main__':
    I=load_instance(sys.argv[1]); src=sys.argv[2]
    if '.json:' in src:
        f,sd=src.split(':'); D=json.load(open(f)); nm=sys.argv[1].split('/')[-1][:-4]
        R=[r for r in D['rows'] if r['i']==nm and r['sd']==int(sd)][0]['routes']
    else: R,_=parse_output(open(src).read())
    print(sig(I,R))
