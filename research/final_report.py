"""Итоговая таблица по решателям для набора: сравнение с лучшим известным решением и нижней границей.
python3 final_report.py <папка_задач> [файлы runs/final/bench_*.json]"""
import glob, json, os, sys, statistics as st
d=sys.argv[1]
meta=json.load(open(f'best/{d}/_meta.json'))
lbs={os.path.basename(f)[:-5]:json.load(open(f)) for f in glob.glob(f'lb/{d}/*.json') if not os.path.basename(f).startswith('_')}
print(f"\n== {d}: {len(meta)} задач. Лучшее известное: штраф {sum(m['pen'] for m in meta.values())}, бригад {sum(m['used'] for m in meta.values())}, км {sum(m['km'] for m in meta.values()):.1f}")
opt=[i for i,m in meta.items() if i in lbs and lbs[i].get('lb_used')==m['used'] and lbs[i].get('lb_km') and m['km']<=lbs[i]['lb_km']*1.0005]
fleet_opt=[i for i,m in meta.items() if i in lbs and lbs[i].get('lb_used')==m['used']]
print(f"   нижние границы есть для {len(lbs)}; число бригад доказано оптимальным в {len(fleet_opt)}; решение доказано оптимальным (разрыв <0,05%) в {len(opt)}")
gaps=[(m['km']/lbs[i]['lb_km']-1)*100 for i,m in meta.items() if i in lbs and lbs[i].get('lb_used')==m['used'] and lbs[i].get('lb_km')]
if gaps: print(f"   разрыв лучшего известного пробега до нижней границы при оптимальном числе бригад: средний {st.mean(gaps):.2f}%, максимум {max(gaps):.2f}%")
print(f"{'решатель (1 с)':16} {'штраф':>6} {'бригад':>7} {'км':>8} | {'= лучшему':>9} {'лишние бр':>9} {'разрыв км':>9}")
for f in sorted(glob.glob(f'runs/final/bench_{d}_tl1.0_*.json')):
    rows=[r for r in json.load(open(f)) if r['ok']]
    if not rows: continue
    s=rows[0]['solver']
    eq=sum(1 for r in rows if (r['pen'],r['used'])==(meta[r['inst']]['pen'],meta[r['inst']]['used']) and r['km']<=meta[r['inst']]['km']*1.005)
    du=sum(r['used']-meta[r['inst']]['used'] for r in rows if r['pen']==meta[r['inst']]['pen'])
    g=[r['km']/meta[r['inst']]['km']-1 for r in rows if (r['pen'],r['used'])==(meta[r['inst']]['pen'],meta[r['inst']]['used'])]
    print(f"{s:16} {sum(r['pen'] for r in rows):6d} {sum(r['used'] for r in rows):7d} {sum(r['km'] for r in rows):8.1f} | {eq:4d}/{len(rows):<4d} {du:+9d} {st.mean(g)*100:8.2f}%")
