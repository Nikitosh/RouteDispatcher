"""Синтетика для рейтинга (python -m dispatch reviews <участок>): парк монтажников и история отзывов клиентов.

Парк — бригады участка плюс новые до POOL человек (жюри: в режиме «лучшие» число монтажников условно неограниченно):
навыки — частоты контрольного дня, транспорт — mix2, дом — жилой дом OSM рядом с одной из заявок участка.
У каждого монтажника скрытое «истинное» качество по видам работ. За HISTORY дней он работает через день (2/2), около
6 выездов в смену; оценку оставляет примерно каждый третий клиент, с разбросом. NEWCOMERS новичков пришли недавно и
отзывов у них мало. Часть визитов опоздала по вине плана (авария, перестройка дня) — клиент ставит низкую оценку за
пунктуальность, но работа от этого хуже не стала. Повторный выезд в течение 30 дней тем вероятнее, чем хуже качество.
Пишет data/demo/<участок> монтажники.xlsx и <участок> отзывы.csv.
"""
import os
import random

from ..files.readers import read_brigades, read_orders
from ..model import rating
from .demo_data import DEMO_DIR, mode_of, write_brigades
from .districts import ROSTER, pick
from .homes import RADIUS, _dist, residential

POOL = 30
HISTORY = 60
NEWCOMERS = 4
RESPONSE = 0.30          # доля клиентов, оставивших оценку
PLAN_LATE = 0.08         # доля визитов с опозданием по вине плана


def pool(region, rnd):
    """[(имя, транспорт, навыки, старт)]: бригады участка и новые монтажники с домами."""
    brigades = read_brigades(os.path.join(DEMO_DIR, f'{region} бригады.xlsx'))
    orders = read_orders(os.path.join(DEMO_DIR, f'{region} заявки.xlsx')).orders
    from ..geo.geocoder import Geocoder
    geocoder = Geocoder(online=False)
    coords = geocoder.locate([o.house for o in orders], log=lambda *_: None)
    rows = [(b.name, b.mode, b.skills, b.start_address) for b in brigades]
    anchors = [coords[rnd.choice(orders).house] for _ in range(POOL - len(rows))]
    houses = residential(anchors)
    for i, anchor in enumerate(anchors, start=len(rows) + 1):
        name = f'Бригада {i}'
        near = sorted((h for h in houses if _dist(h, anchor) < RADIUS), key=lambda h: h[2])
        lat, lon, street_house, city = rnd.choice(near)
        address = f"{'Город Москва' if not city or city == 'Москва' else city}, {street_house}"
        geocoder.remember(address, lat, lon, 'osm')
        rows.append((name, mode_of(f'{region} {name}', False, 'mix2'), sorted(pick(rnd, ROSTER)), address))
    geocoder.save()
    return rows, orders


def history(rows, orders, rnd):
    """[(Review, номер заявки)] и истинное качество {(монтажник, навык): оценка}."""
    from ..config import SKILL
    mix = [SKILL[o.bk] for o in orders]
    truth, out, n = {}, [], 0
    newcomers = {name for name, *_ in rnd.sample(rows[len(rows) // 2:], NEWCOMERS)}
    for name, _, skills, _ in rows:
        for s in skills:
            truth[name, s] = min(4.95, max(3.2, rnd.gauss(4.45, 0.35)))
        days = rnd.randint(5, 15) if name in newcomers else HISTORY
        first = rnd.randint(0, 1)
        for age in range(days - 1, -1, -1):
            if (age + first) % 4 >= 2:                 # 2/2: два дня работы, два выходных
                continue
            for _ in range(rnd.randint(5, 7)):
                s = rnd.choice([x for x in mix if x in skills] or skills)
                q = truth[name, s]
                n += 1
                late = rnd.random() < PLAN_LATE
                r = rating.Review(age=age, brigade=name, skill=s, plan_late=late)
                if rnd.random() < RESPONSE:
                    r.work = min(5, max(1, round(rnd.gauss(q, 0.6))))
                    r.on_time = rnd.choice((1, 2)) if late else min(5, max(1, round(rnd.gauss(4.7, 0.5))))
                out.append((r, f'H{n:05d}'))
    return out, truth


def expected(q, sd=0.6):
    """Средняя оценка 1–5, которую ставят монтажнику с качеством q (с округлением и обрезкой)."""
    import math
    cdf = lambda x: 0.5 * (1 + math.erf((x - q) / (sd * math.sqrt(2))))
    return sum(k * (cdf(k + .5 if k < 5 else 99) - cdf(k - .5 if k > 1 else -99)) for k in range(1, 6))


def spearman(a, b):
    ra = {k: i for i, k in enumerate(sorted(a, key=a.get))}
    rb = {k: i for i, k in enumerate(sorted(b, key=b.get))}
    n = len(a)
    return 1 - 6 * sum((ra[k] - rb[k]) ** 2 for k in a) / (n * (n * n - 1))


def write(region, seed=1):
    rnd = random.Random(f'{region}|{seed}|reviews')
    rows, orders = pool(region, rnd)
    pool_path = os.path.join(DEMO_DIR, f'{region} монтажники.xlsx')
    write_brigades(pool_path, rows)
    reviews, truth = history(rows, orders, rnd)
    rev_path = os.path.join(DEMO_DIR, f'{region} отзывы.csv')
    rating.write(rev_path, reviews)
    plain = [r for r, _ in reviews]
    bayes = rating.compute(plain, {name: skills for name, _, skills, _ in rows})
    naive = rating.naive(plain)
    keys = [k for k in truth if k in naive]
    true = {k: expected(truth[k]) for k in keys}
    b = {k: bayes[k[0]].of(k[1]).value for k in keys}
    nv = {k: naive[k] for k in keys}
    mae = lambda est: sum(abs(est[k] - true[k]) for k in keys) / len(keys)
    return dict(pool=pool_path, reviews=rev_path, n=len(rows), visits=len(reviews),
                rated=sum(1 for r in plain if r.work is not None), mae_bayes=mae(b), mae_naive=mae(nv),
                rank_bayes=spearman(b, true), rank_naive=spearman(nv, true))
