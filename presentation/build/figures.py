"""Картинки для презентации по реальным данным: карты планов и временная шкала.

Запуск из корня lct: research/.venv/bin/python presentation/build/figures.py. Планы — лучшие известные решения в модели
продукта (research/best/instances_control_v2).
Подложка — тайлы OpenStreetMap, обесцвеченные (кэш в presentation/build/tiles/).
"""
import math
import os
import sys
import urllib.request

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(HERE, '..', '..')
FEAS = os.path.join(PROJECT, 'research')
sys.path.insert(0, FEAS)
os.chdir(FEAS)
import control  # noqa: E402  (реальный план диспетчера и стартовые точки)
sys.path.insert(0, PROJECT)
from dispatch.geo.geometry import RouteGeometry  # noqa: E402  (линии маршрутов по улицам: кэш и OSRM)
GEOMETRY = RouteGeometry()

OUT = os.path.join(HERE, 'fig')
TILES = os.path.join(HERE, 'tiles')
os.makedirs(OUT, exist_ok=True)
os.makedirs(TILES, exist_ok=True)

for f in os.listdir(os.path.join(HERE, 'lo_profile/user/fonts')):
    font_manager.fontManager.addfont(os.path.join(HERE, 'lo_profile/user/fonts', f))
plt.rcParams['font.family'] = 'Beeline Sans'

INK = '#141414'
MUTED = '#5E5E5E'
# 12 различимых цветов бригад: основные и дополнительные цвета темы «Билайна»
PAL = ['#141414', '#FFC800', '#F47300', '#00C2AB', '#EC008C', '#6332C8',
       '#E5093B', '#0096D6', '#8DC63F', '#A1A1A1', '#B07CC6', '#8A5A00']
REG = {'vostok': 'Восток', 'yugocentr': 'Югоцентр', 'yugovostok': 'Юго-восток'}


# ---------- подложка ----------
def merc(lat, lon, z):
    n = 256 * 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def tile(z, x, y):
    p = os.path.join(TILES, f'{z}_{x}_{y}.png')
    if not os.path.exists(p):
        req = urllib.request.Request(f'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                                     headers={'User-Agent': 'Trdelnik-hackathon-slides/1.0'})
        open(p, 'wb').write(urllib.request.urlopen(req, timeout=30).read())
    im = Image.open(p).convert('L').convert('RGB')          # серая подложка, как в интерфейсе
    return Image.blend(im, Image.new('RGB', im.size, 'white'), .45)


def basemap(ax, pts, z, pad=0.08, aspect=None):
    xs, ys = zip(*[merc(la, lo, z) for la, lo in pts])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dx, dy = (x1 - x0) * pad + 10, (y1 - y0) * pad + 10
    x0, x1, y0, y1 = x0 - dx, x1 + dx, y0 - dy, y1 + dy
    if aspect:  # ширина / высота
        cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
        if w / h < aspect: w = h * aspect
        else: h = w / aspect
        x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    tx0, tx1, ty0, ty1 = int(x0 // 256), int(x1 // 256), int(y0 // 256), int(y1 // 256)
    img = Image.new('RGB', ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            img.paste(tile(z, tx, ty), ((tx - tx0) * 256, (ty - ty0) * 256))
    ax.imshow(img, extent=(tx0 * 256, (tx1 + 1) * 256, (ty1 + 1) * 256, ty0 * 256), interpolation='lanczos')
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.set_axis_off()
    return lambda la, lo: merc(la, lo, z)


# ---------- данные ----------
def read_inst(path):
    L = open(path).read().split('\n')
    n, v, S = map(int, L[1].split()); M = S + n
    ords = [list(map(float, l.split()[1:])) for l in L[2:2 + n]]       # svc a b pri skill
    br = [list(map(int, l.split())) for l in L[2 + n:2 + n + v]]       # start mode mask
    mats, p = {}, 2 + n + v
    for m in ['car', 'pt', 'bike', 'foot']:
        T = [list(map(float, L[p + i].split())) for i in range(M)]; p += M
        D = [list(map(float, L[p + i].split())) for i in range(M)]; p += M
        mats[m] = (T, D)
    return dict(n=n, v=v, S=S, ords=ords, br=br, mats=mats)


def read_routes(path):
    r = {}
    for l in open(path):
        if l.startswith('ROUTE'):
            a = list(map(int, l.split()[1:]))
            if len(a) > 1: r[a[0]] = a[1:]
    return r


MODES = ['car', 'pt', 'bike', 'foot']


def simulate(I, v, route):
    st, mode, _ = I['br'][v]; T, D = I['mats'][MODES[mode]]
    t, prev, km, stops = 0.0, st, 0.0, []
    for k in route:
        nd = I['S'] + k; svc, a, b = I['ords'][k][:3]
        arr = t + T[prev][nd]; beg = max(arr, a); km += D[prev][nd]
        stops.append(dict(k=k, dep=t, arr=arr, beg=beg, end=beg + svc, late=beg > b + 1e-6))
        t, prev = beg + svc, nd
    return stops, km


def real_routes(C, I, mode):
    """План диспетчера: заявки каждой бригады по началу окна (как в control.eval_real, упрощённо)."""
    res = {}
    for vi, b in enumerate(C['names']):
        ks = sorted(C['plan'][b], key=lambda k: (C['orders'][k]['a'], C['orders'][k]['b']))
        if ks: res[vi] = ks
    return res


def draw_plan(ax, C, routes, proj, lw=2.0, ms=5, starts=True, I=None, mode=None):
    """Маршруты по улицам (dispatch RouteGeometry: кэш линий, при необходимости OSRM); вид транспорта бригады — из задачи I
    или общий mode. Если линии нет, участок рисуется прямым отрезком."""
    O = C['orders']
    for i, (v, r) in enumerate(sorted(routes.items())):
        c = PAL[i % len(PAL)]
        s = C['starts'][C['bstart'][C['names'][v]]]
        pts = [s] + [(O[k]['lat'], O[k]['lon']) for k in r]
        m = MODES[I['br'][v][1]] if I else (mode or 'car')
        line = []
        for a, b in zip(pts, pts[1:]):
            g = GEOMETRY.leg(m, a, b)
            line += g if g else [list(a), list(b)]
        xy = [proj(*p) for p in line]
        ax.plot([p[0] for p in xy], [p[1] for p in xy], '-', color=c, lw=lw, alpha=.9, solid_capstyle='round', solid_joinstyle='round', zorder=3)
        xy = [proj(*p) for p in pts]
        ax.scatter([p[0] for p in xy[1:]], [p[1] for p in xy[1:]], s=ms ** 2, color=c, edgecolor='white', lw=.8, zorder=4)
    un = [k for k in range(len(O)) if not any(k in r for r in routes.values())]
    for k in un:
        x, y = proj(O[k]['lat'], O[k]['lon'])
        ax.scatter([x], [y], s=(ms + 3) ** 2, marker='s', facecolor='none', edgecolor='#E5093B', lw=2, zorder=5)
    if starts:
        for s in set(C['starts']):
            x, y = proj(*s)
            ax.scatter([x], [y], s=110, marker='s', color=INK, edgecolor='white', lw=1.5, zorder=6)


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), dpi=200, transparent=False, facecolor='white')
    plt.close(fig)
    print('fig', name)


# ---------- 1. Восток: реальный день против нашего плана ----------
def real_vs_ours(reg='vostok', mode='pt'):
    C = control.build(REG[reg])
    I = read_inst(f'instances_control_v2/control_{reg}_{mode}_inf.txt')
    ours = read_routes(f'best/instances_control_v2/control_{reg}_{mode}_inf.out')
    real = real_routes(C, I, mode)
    pts = [(o['lat'], o['lon']) for o in C['orders']] + C['starts']
    for name, routes in [('real', real), ('ours', ours)]:
        fig = plt.figure(figsize=(6.15, 4.25)); ax = fig.add_axes([0, 0, 1, 1])
        proj = basemap(ax, pts, 13, aspect=6.15 / 4.25)
        draw_plan(ax, C, routes, proj, I=I)
        save(fig, f'map_{reg}_{mode}_{name}.png')
    stats = {}
    for name, routes in [('real', real), ('ours', ours)]:
        km = late = 0
        for v, r in routes.items():
            st, k_ = simulate(I, v, r); km += k_; late += sum(s['late'] for s in st)
        stats[name] = dict(brig=len(routes), km=round(km, 1), late=late)
    print(reg, mode, stats)
    return stats


# ---------- 2. Наш план на трёх участках ----------
def three_regions(mode='pt'):
    for reg in REG:
        C = control.build(REG[reg])
        ours = read_routes(f'best/instances_control_v2/control_{reg}_{mode}_inf.out')
        pts = [(o['lat'], o['lon']) for o in C['orders']] + C['starts']
        if reg == 'yugovostok':  # московская часть; бригады из Каширы, Ступино и Домодедово — за краем карты
            pts = [p for p in pts if p[0] > 55.55]
        fig = plt.figure(figsize=(4.0, 4.4)); ax = fig.add_axes([0, 0, 1, 1])
        proj = basemap(ax, pts, 12, aspect=4.0 / 4.4)
        draw_plan(ax, C, ours, proj, lw=1.6, ms=4, mode=mode)
        save(fig, f'map3_{reg}_{mode}.png')


# ---------- 3. Временная шкала бригад (наш план) ----------
TYPE_COL = {1: '#141414', 2: '#FFC800', 3: '#A1A1A1'}   # приоритет: авария, подключение, ремонт/дозаказ


def gantt(reg='vostok', mode='pt'):
    I = read_inst(f'instances_control_v2/control_{reg}_{mode}_inf.txt')
    ours = read_routes(f'best/instances_control_v2/control_{reg}_{mode}_inf.out')
    rows = sorted(ours.items())
    fig, ax = plt.subplots(figsize=(12.2, 4.15))
    for i, (v, r) in enumerate(rows):
        y = len(rows) - 1 - i
        st, km = simulate(I, v, r)
        for s in st:
            ax.barh(y, s['arr'] - s['dep'], left=s['dep'], height=.22, color='#E3E3E3', lw=0)
            if s['beg'] > s['arr'] + .5:
                ax.plot([s['arr'], s['beg']], [y, y], ':', color='#8C8C8C', lw=1.4)
            svc, a, b, pri = I['ords'][s['k']][:4]
            ax.barh(y, s['end'] - s['beg'], left=s['beg'], height=.62, color=TYPE_COL[int(pri)], lw=.8, edgecolor='white')
        ax.text(-8, y, f'Б{i + 1}', ha='right', va='center', fontsize=10, fontweight='bold', color=INK)
        ax.text(726, y, f'{len(r)} заявок, {km:.0f} км', ha='left', va='center', fontsize=8.5, color=MUTED)
    ax.set_xlim(-2, 720); ax.set_ylim(-.7, len(rows) - .3)
    ax.set_xticks(range(0, 721, 60)); ax.set_xticklabels([f'{10 + h}:00' for h in range(13)], fontsize=8.5, color=MUTED)
    ax.set_yticks([])
    for s in ['top', 'right', 'left']: ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color('#D9D9D9')
    ax.grid(axis='x', color='#EDEDED', lw=.8); ax.set_axisbelow(True)
    fig.subplots_adjust(left=.04, right=.87, top=.97, bottom=.12)
    save(fig, f'gantt_{reg}_{mode}.png')


if __name__ == '__main__':
    real_vs_ours('vostok', 'pt')
    three_regions('pt')
    gantt('vostok', 'mix2')
    GEOMETRY.save()
