"""Сводный разбор прогонов: python3 analyze.py <папка_задач>
Лучший известный результат по задаче берётся по всем решателям и всем лимитам времени."""
import glob, json, sys, statistics as st
from collections import defaultdict
inst_dir=sys.argv[1] if len(sys.argv)>1 else 'instances_gen'
rows=[]
for f in glob.glob(f'bench_{inst_dir}_tl*.json'):
    tl=f.split('_tl')[1].split('_')[0]
    for r in json.load(open(f)):
        if r['ok']: r['tl']=tl; rows.append(r)
best={}
for r in rows:
    k=(r['pen'],r['used'],r['km']); best[r['inst']]=min(best.get(r['inst'],k),k)
def stats(rs):
    win=sum(1 for r in rs if (r['pen'],r['used'])==best[r['inst']][:2] and r['km']<=best[r['inst']][2]*1.01)
    dpen=st.mean(r['pen']-best[r['inst']][0] for r in rs)
    dused=st.mean(r['used']-best[r['inst']][1] for r in rs if r['pen']==best[r['inst']][0]) if any(r['pen']==best[r['inst']][0] for r in rs) else float('nan')
    g=[r['km']/best[r['inst']][2]-1 for r in rs if (r['pen'],r['used'])==best[r['inst']][:2]]
    return win,dpen,dused,(st.mean(g)*100 if g else float('nan')),len(rs)
fam=lambda i:i.split('_')[0]
fams=sorted({fam(i) for i in best})
for tl in sorted({r['tl'] for r in rows},key=float):
    print(f"\n=== лимит {tl} с. win = лучший результат (пробег в пределах 1%), Δшт = лишний штраф за невыполненные, Δбр = лишние бригады, gap = лишний пробег при равных бригадах")
    print(f"{'решатель':22} {'win':>7} {'Δшт':>6} {'Δбр':>6} {'gap':>6} | "+' '.join(f"{f[:6]:>11}" for f in fams))
    sol=sorted({r['solver'] for r in rows if r['tl']==tl})
    res=[]
    for s in sol:
        rs=[r for r in rows if r['tl']==tl and r['solver']==s]
        w,dp,du,g,n=stats(rs)
        per=[]
        for f in fams:
            fr=[r for r in rs if fam(r['inst'])==f]; w2,dp2,du2,g2,n2=stats(fr); per.append(f"{w2:2d}/{n2:<2d} {du2:+4.1f}")
        res.append((-w,s,f"{s:22} {w:3d}/{n:<3d} {dp:6.1f} {du:+6.2f} {g:5.1f}% | "+' '.join(f"{p:>11}" for p in per)))
    for _,_,line in sorted(res): print(line)
print("\nв колонках семейств: побед/задач и лишние бригады в среднем")
