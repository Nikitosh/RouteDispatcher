"""Синтетика для «план учится» (python -m dispatch facts): история фактических длительностей выездов.

Берём заявки и бригады шести демо-участков (адреса с домами из cache/houses.json, бригады — data/demo/<участок>
бригады.xlsx) и разыгрываем DAYS дней по PER_DAY выездов на участок. Выезд выполняет случайная бригада того же участка с
нужным навыком. Факт = длительность по «истинным» множителям TRUE × «истинный» темп бригады (TRUE_SPEED, остальные 1) ×
логнормальный разброс SIGMA. Истина отличается от наших коэффициентов в нескольких местах, а несколько бригад работают
заметно быстрее или медленнее норматива — обучение (model/learning.py) должно это найти. Имена бригад повторяются на
разных участках, поэтому в истории есть колонка «Участок»: темп учится по паре «участок, бригада», и `plan --facts`
применяет его к бригадам планируемого участка с тем же именем. Пишет data/demo/выезды.csv; проверку — обучение на
первых днях и ошибку на последних — печатает.
"""
import glob
import os
import random

from ..files.readers import read_brigades, read_orders
from ..geo.houses import HouseIndex
from ..model import learning
from ..model.complexity import estimate
from .demo_data import DEMO_DIR

DAYS, PER_DAY, HOLDOUT = 30, 20, 6           # PER_DAY — выездов в день на участок
SIGMA = 0.18
TRUE = {('Подключение', 'Конвергенция'): 1.25,              # у нас 1,10
        ('Локальная заявка', 'Переключение на Гбит'): 1.45,   # у нас 1,20
        ('Локальная заявка', 'Нет линка'): 1.15,              # у нас 1,30
        'old': 1.25}                                          # у нас 1,15
TRUE_SPEED = {('Восток', 'Бригада 3'): 0.85,                # опытные — быстрее норматива
              ('Восток', 'Бригада 8'): 1.25,                # новичок
              ('Запад', 'Бригада 2'): 0.9,
              ('Север', 'Бригада 5'): 1.2,
              ('Северо-восток', 'Бригада 1'): 0.85,
              ('Юго-восток', 'Бригада 9'): 1.3,
              ('Югоцентр', 'Бригада 4'): 0.9}


def truth():
    t = learning.current()
    t['work'].update({k: v for k, v in TRUE.items() if k != 'old'})
    t['old'] = TRUE['old']
    return t


def regions():
    """{участок: (заявки, бригады)} демо-участков, у которых есть оба файла."""
    out = {}
    for path in sorted(glob.glob(os.path.join(DEMO_DIR, '* заявки.xlsx'))):
        name = os.path.basename(path)[:-len(' заявки.xlsx')]
        brigades = os.path.join(DEMO_DIR, f'{name} бригады.xlsx')
        if os.path.exists(brigades):
            out[name] = (read_orders(path).orders, read_brigades(brigades))
    return out


def build(seed=1, log=print):
    rnd = random.Random(seed)
    houses = HouseIndex()
    data = regions()
    real, ours = truth(), learning.current()
    rows, facts = [], []
    for day in range(DAYS):
        for region, (orders, brigades) in data.items():
            for o in rnd.sample(orders, min(PER_DAY, len(orders))):
                b = rnd.choice([b for b in brigades if o.skill in b.skills] or brigades)
                h = houses.get(o.house)
                pace = TRUE_SPEED.get((region, b.name), 1.0)
                actual = max(5, round(estimate(o, h, real)[0] * pace * rnd.lognormvariate(0, SIGMA)))
                rows.append({'Дата': f'{day + 1:02d}.09.2026', 'Участок': region, 'Бригада': b.name, 'Заявка': o.id,
                             'Тип заявки BK': o.bk, 'Тип заявки HD': o.hd, 'Адрес': o.address,
                             'Гигабит': 'Да' if o.gigabit else 'Нет', 'План мин': estimate(o, h, ours)[0], 'Факт мин': actual})
                facts.append((day, learning.Fact(o, actual, b.name, region)))
    path = os.path.join(DEMO_DIR, 'выезды.csv')
    learning.write(path, rows)
    train = [f for d, f in facts if d < DAYS - HOLDOUT]
    test = [f for d, f in facts if d >= DAYS - HOLDOUT]
    learned, report = learning.fit(train, houses)
    log(f'{path}: {len(rows)} выездов за {DAYS} дней, {len(data)} участков, '
        f'{len({(f.region, f.brigade) for _, f in facts})} бригад')
    for r in report:
        if 'brigade' not in r:
            log(f"  {r['what']}: ×{r['was']:.2f} → ×{r['now']:.2f} по {r['n']} выездам")
    speeds = [r for r in report if 'brigade' in r]
    log('  Темп бригад с «истинным» темпом не 1:')
    for r in speeds:
        true = TRUE_SPEED.get((r['region'], r['brigade']))
        if true:
            log(f"    {r['region']}, {r['what']} ×{r['was']:.2f} → ×{r['now']:.2f} по {r['n']} выездам (истина ×{true:.2f})")
    rest = [abs(r['now'] - 1) for r in speeds if (r['region'], r['brigade']) not in TRUE_SPEED]
    log(f'  Остальные {len(rest)} бригад (истина ×1,00): в среднем отклонение {sum(rest) / max(1, len(rest)):.3f}, '
        f'наибольшее {max(rest, default=0):.3f}')
    no_speed = dict(learned, brigades={})
    before, middle, after = (learning.mae(test, houses, x) for x in (ours, no_speed, learned))
    log(f'Ошибка прогноза на последних {HOLDOUT} днях: {before:.1f} → {middle:.1f} (подтипы и дом) → {after:.1f} мин (и темп бригад)')
    return dict(path=path, before=before, after=after, report=report)
