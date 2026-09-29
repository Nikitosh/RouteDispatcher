"""Схемы шагов алгоритма для презентации (условные точки, не реальные данные).

Запуск из корня lct: research/.venv/bin/python presentation_build/figures_algo.py
"""
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fig')
os.makedirs(OUT, exist_ok=True)
FD = os.path.join(HERE, 'lo_profile/user/fonts')
for f in os.listdir(FD):
    font_manager.fontManager.addfont(os.path.join(FD, f))
plt.rcParams['font.family'] = 'Beeline Sans'

INK, MUTED = '#141414', '#5E5E5E'
# палитра «Билайна»: маршрут 1 — чёрный, маршрут 2 — жёлтый, убранный — серый, пул — красный
PINK, PURPLE, LAV, DARK = '#141414', '#FFC800', '#A1A1A1', '#141414'
POOL = '#E5093B'
O = (0, 0)
A = [(1.0, 2.2), (2.2, 3.0), (3.6, 3.1), (4.6, 2.3)]
B = [(1.2, -1.2), (2.5, -1.8), (3.8, -1.4), (4.8, -0.4)]
Cm = [(1.8, 0.6), (3.2, 0.9), (4.4, 0.9)]


def panel(ax):
    ax.set_xlim(-0.6, 5.4); ax.set_ylim(-2.5, 3.7); ax.set_aspect('equal'); ax.set_axis_off()


def route(ax, pts, c, ls='-', lw=2.6, start=True):
    p = ([O] if start else []) + pts
    ax.plot([x for x, _ in p], [y for _, y in p], ls, color=c, lw=lw, solid_capstyle='round', zorder=2)
    ax.scatter([x for x, _ in pts], [y for _, y in pts], s=110, color=c, edgecolor='white', lw=1.5, zorder=3)


def office(ax):
    ax.scatter([0], [0], s=190, marker='s', color=INK, edgecolor='white', lw=1.5, zorder=4)


def pool(ax, pts):
    ax.scatter([x for x, _ in pts], [y for _, y in pts], s=150, marker='s', facecolor='white', edgecolor=POOL, lw=2.2, zorder=3)


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), dpi=220, transparent=True)
    plt.close(fig); print('fig', name)


def fleet():
    """Сокращение бригад: 3 маршрута → убрали средний → его заявки разошлись по двум оставшимся."""
    C = [(0.9, 1.1), (3.2, -1.1), (5.0, 0.4)]
    for i in range(3):
        fig, ax = plt.subplots(figsize=(3.4, 3.5)); panel(ax)
        if i == 0:
            route(ax, A, PINK); route(ax, B, PURPLE); route(ax, C, LAV)
        elif i == 1:
            route(ax, A, PINK); route(ax, B, PURPLE); pool(ax, C)
        else:
            route(ax, [C[0]] + A, PINK)
            route(ax, B[:2] + [C[1]] + B[2:] + [C[2]], PURPLE)
            ax.scatter([x for x, _ in C], [y for _, y in C], s=330, facecolor='none', edgecolor=POOL, lw=2, zorder=5)
        office(ax); fig.subplots_adjust(0, 0, 1, 1); save(fig, f'algo_fleet_{i + 1}.png')


def sisr():
    """SISR: вырезаем цепочки из соседних маршрутов и вставляем дешевле."""
    bad_a = A[:2] + B[2:]; bad_b = B[:2] + A[2:]
    for i in range(3):
        fig, ax = plt.subplots(figsize=(3.4, 3.5)); panel(ax)
        if i == 0:
            route(ax, bad_a, PINK); route(ax, bad_b, PURPLE)
        elif i == 1:
            route(ax, A[:2], PINK); route(ax, B[:2], PURPLE)
            ax.plot([A[1][0], B[2][0]], [A[1][1], B[2][1]], ':', color=PINK, lw=1.6)
            ax.plot([B[1][0], A[2][0]], [B[1][1], A[2][1]], ':', color=PURPLE, lw=1.6)
            for s, c in [(B[2:], PINK), (A[2:], PURPLE)]:
                ax.plot([x for x, _ in s], [y for _, y in s], '--', color=c, lw=2, zorder=2)
                ax.scatter([x for x, _ in s], [y for _, y in s], s=150, marker='s', facecolor='white', edgecolor=c, lw=2.2, zorder=3)
        else:
            route(ax, A, PINK); route(ax, B, PURPLE)
        office(ax); fig.subplots_adjust(0, 0, 1, 1); save(fig, f'algo_sisr_{i + 1}.png')


def regret():
    """Упущенная выгода: сначала ставим заявку, у которой второй вариант намного хуже лучшего.
    Числа — настоящая стоимость вставки на рисунке (1 клетка = 1 км): сколько добавится к маршруту."""
    import math
    d = lambda p, q: math.dist(p, q)
    RA, RB = A[:3], B[:3]

    def best(r, p):
        opts = []
        for k in range(len(r) + 1):
            pr = O if k == 0 else r[k - 1]; nx = r[k] if k < len(r) else None
            opts.append((d(pr, p) + ((d(p, nx) - d(pr, nx)) if nx else 0), pr, nx))
        return min(opts, key=lambda t: t[0])
    fig, ax = plt.subplots(figsize=(5.6, 4.6)); panel(ax); ax.set_xlim(-0.6, 7.4); ax.set_ylim(-2.3, 4.4)
    route(ax, RA, PINK); route(ax, RB, PURPLE); office(ax)
    # заявка, её цвет, места подписей стоимости (для маршрутов 1 и 2) и подписи упущенной выгоды
    cases = [((2.0, 0.4), '#8C8C8C', [(1.6, 1.25), (1.75, -0.55)], (2.3, 0.3), False),
             ((4.3, 3.6), DARK, [(3.2, 3.75), (4.25, 1.0)], (4.6, 3.45), True)]
    for p, c, lab, lost_at, first in cases:
        costs = []
        for (r, rc), (lx, ly) in zip([(RA, PINK), (RB, PURPLE)], lab):
            cost, pr, nx = best(r, p); costs.append(cost)
            pts = [pr, p] + ([nx] if nx else [])
            ax.plot([q[0] for q in pts], [q[1] for q in pts], '--', color=c, lw=1.5, zorder=1)
            ax.text(lx, ly, f'+{cost:.1f} км'.replace('.', ','), fontsize=12, color=c, fontweight='bold')
        ax.scatter([p[0]], [p[1]], s=190, color='white', edgecolor=c, lw=2.6, zorder=5)
        lost = f'упущенная выгода {abs(costs[0] - costs[1]):.1f}'.replace('.', ',')
        ax.text(*lost_at, lost + ('\n→ ставим первой' if first else ''), fontsize=11, color=c,
                fontweight='bold' if first else 'normal', va='top')
    fig.subplots_adjust(0, 0, 1, 1); save(fig, 'algo_regret.png')


def columns():
    """Генерация столбцов: заявки × маршруты, выбранные маршруты покрывают каждую заявку ровно раз."""
    import random
    random.seed(4)
    n, m = 9, 14
    chosen = {1: [0, 1, 2], 5: [3, 4], 8: [5, 6, 7, 8]}
    cols = []
    for j in range(m):
        if j in chosen: cols.append(set(chosen[j]))
        else: cols.append(set(random.sample(range(n), random.randint(2, 4))))
    fig, ax = plt.subplots(figsize=(5.6, 3.9))
    for j, cset in enumerate(cols):
        sel = j in chosen
        if sel:
            ax.add_patch(plt.Rectangle((j - .45, -.6), .9, n + .2, color='#FFF1C2', zorder=0))
        for i in range(n):
            if i in cset:
                ax.add_patch(plt.Rectangle((j - .32, n - 1 - i - .32), .64, .64, color=PURPLE if sel else '#D9D9D9', zorder=2))
            else:
                ax.add_patch(plt.Rectangle((j - .32, n - 1 - i - .32), .64, .64, fill=False, edgecolor='#E6E6E6', lw=.8, zorder=1))
    for i in range(n):
        ax.text(-.9, n - 1 - i, f'заявка {i + 1}', ha='right', va='center', fontsize=8.5, color=MUTED)
    ax.text(m / 2 - .5, n + .1, 'маршруты-кандидаты (столбцы)', ha='center', fontsize=9.5, color=INK, fontweight='bold')
    ax.set_xlim(-3.4, m - .4); ax.set_ylim(-.8, n + .7); ax.set_aspect('equal'); ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1); save(fig, 'algo_columns.png')


def swap():
    """Обмен начал маршрутов: до 13:00 бригады меняются заявками, после — оставляют свои."""
    from matplotlib.patches import FancyBboxPatch
    one = [(0.3, 1.3), (1.6, 2.7), (3.6, 4.9), (5.4, 6.7), (7.2, 8.5), (9.0, 10.3)]
    two = [(0.2, 1.6), (1.9, 2.8), (3.3, 4.6), (5.1, 6.2), (6.8, 8.2), (8.7, 9.9)]
    cut = 3.0
    rows = [('до', 'бригада 1', one, PINK, one, PINK), ('до', 'бригада 2', two, PURPLE, two, PURPLE),
            ('после', 'бригада 1', two, PURPLE, one, PINK), ('после', 'бригада 2', one, PINK, two, PURPLE)]
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ys = [4.2, 3.4, 1.4, 0.6]
    for y, (stage, name, head, hc, tail, tc) in zip(ys, rows):
        for a_, b_ in head:
            if b_ <= cut:
                ax.add_patch(FancyBboxPatch((a_, y - .22), b_ - a_, .44, boxstyle='round,pad=0,rounding_size=.08', color=hc, lw=0))
        for a_, b_ in tail:
            if a_ >= cut:
                ax.add_patch(FancyBboxPatch((a_, y - .22), b_ - a_, .44, boxstyle='round,pad=0,rounding_size=.08', color=tc, lw=0))
        ax.plot([0, 12], [y, y], color='#E6E6E6', lw=1, zorder=0)
        ax.text(-0.2, y, name, ha='right', va='center', fontsize=11, color=INK)
    ax.text(-2.2, 4.85, 'до обмена', ha='left', va='center', fontsize=12, fontweight='bold', color=INK)
    ax.text(-2.2, 2.05, 'после обмена', ha='left', va='center', fontsize=12, fontweight='bold', color=INK)
    ax.plot([cut, cut], [0.1, 4.75], '--', color=POOL, lw=1.6)
    ax.text(cut, 4.95, '13:00', ha='center', fontsize=11, color=POOL, fontweight='bold')
    for h in range(0, 13, 2):
        ax.text(h, -0.15, f'{10 + h}:00', ha='center', fontsize=9, color=MUTED)
    ax.set_xlim(-2.3, 12.6); ax.set_ylim(-0.4, 5.3); ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1); save(fig, 'algo_swap.png')


if __name__ == '__main__':
    fleet(); sisr(); regret(); columns(); swap()
