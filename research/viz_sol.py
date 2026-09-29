"""Картинка решения для задач из instances*_road/instances*: точки заявок и маршруты бригад.
python viz_sol.py <папка> <задача> [файл решения] -> viz/<задача>.png"""
import sys, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
from validate import load_instance, parse_output
from data import load
import solve2
d,inst=sys.argv[1],sys.argv[2]; sol=sys.argv[3] if len(sys.argv)>3 else f'best/{d}/{inst}.out'
I=load_instance(f'{d}/{inst}.txt'); R,_=parse_output(open(sol).read())
if 'gen' in d:
    import gen
    fam,rk,sd=inst.split('_')[0],inst.split('_')[1],int(inst.split('_')[2])
    P=gen.gen(fam,rk,sd,None,points_only=True); S=I['S']; starts=P[:S]
    orders=[{'lat':p[0],'lon':p[1]} for p in P[S:]]
else:
    reg={'vostok':'Восток','yugocentr':'Югоцентр','yugovostok':'Юго-восток'}[inst.split('_')[0]]
    office,orders=load(reg)
    starts=[(office['lat'],office['lon'])]+solve2.EXTRA.get(reg,[])
    P=starts+[(o['lat'],o['lon']) for o in orders]
MODES=['car','pt','bike','foot']; mk={0:'s',1:'o',2:'^',3:'v'}
fig,ax=plt.subplots(figsize=(11,11)); cm=plt.get_cmap('tab20')
for k,o in enumerate(orders):
    ax.scatter(o['lon'],o['lat'],s=18,c='lightgray',zorder=1)
for v,r in enumerate(R):
    if not r: continue
    ve=I['veh'][v]; seq=[ve['start']]+[I['S']+k for k in r]
    xs=[P[i][1] for i in seq]; ys=[P[i][0] for i in seq]
    km=sum(I['D'][ve['mode']][a][b] for a,b in zip(seq,seq[1:]))
    ax.plot(xs,ys,'-',color=cm(v%20),lw=1.8,marker=mk[ve['mode']],ms=5,label=f"бр{v} {MODES[ve['mode']]} старт{ve['start']} {len(r)} зак {km:.0f} км",zorder=2)
for i,s in enumerate(starts): ax.scatter(s[1],s[0],s=200,marker='*',c='k',zorder=3); ax.annotate(f'старт {i}',(s[1],s[0]))
ax.legend(fontsize=8,loc='upper left'); ax.set_title(f'{inst}: {sol}')
out=sys.argv[4] if len(sys.argv)>4 else f'viz/{inst}.png'
plt.savefig(out,dpi=80,bbox_inches='tight'); print(out)
