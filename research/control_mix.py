"""Контрольные задачи со смешанным транспортом (реальные дороги). Бригады с домом в области — машина; московские —
случайно, воспроизводимо по имени бригады и варианту: 60% общ. транспорт, 25% машина, 10% велосипед, 5% пешком.
Пишет instances_control_road/control_<участок>_mix<вариант>_<inf|all>.txt"""
import os, zlib, random
os.environ['ROADS']='1'
import control
MODES=['car','pt','bike','foot']; SKI={'L':0,'C':1,'A':2}
def mode_of(b,far,var):
    if far: return 'car'
    r=random.Random(zlib.crc32(f'{b}|{var}'.encode())).random()
    return 'pt' if r<0.60 else 'car' if r<0.85 else 'bike' if r<0.95 else 'foot'
for region,tr in control.TR.items():
    C=control.build(region); S=C['S']; orders=C['orders']; M=S+len(orders)
    for var in (1,2):
        modes={b:mode_of(b,C['bstart'][b]!=0,var) for b in C['names']}
        for sv in ('inf','all'):
            path=f"instances_control_road/control_{tr}_mix{var}_{sv}.txt"
            with open(path,'w') as f:
                f.write(f"control_{tr}_mix{var}_{sv}\n{len(orders)} {len(C['names'])} {S}\n")
                for o in orders: f.write(f"{o['id']} {o['svc']} {o['a']} {min(o['b'],720-o['svc'])} {o['pri']} {SKI[control.solve2.SK[o['bk']]]}\n")
                for b in C['names']:
                    sk=C['skills'][b] if sv=='inf' else {'L','C','A'}
                    f.write(f"{C['bstart'][b]} {MODES.index(modes[b])} {sum(1<<SKI[s] for s in sk)}\n")
                for m in MODES:
                    for i in range(M): f.write(' '.join(f"{C['T'][m][i][j]:.3f}" for j in range(M))+'\n')
                    for i in range(M): f.write(' '.join(f"{C['D'][m][i][j]:.4f}" for j in range(M))+'\n')
        from collections import Counter
        print(region, var, dict(Counter(modes.values())))
