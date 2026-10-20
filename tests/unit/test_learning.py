"""План учится на выездах (model/learning.py): фактические длительности уточняют множители сложности."""
import random

import pytest

from dispatch.model import learning
from dispatch.model.complexity import estimate

OLD = {'year': 1950}
NEW = {'year': 2020}


def facts(bk, hd, true_factor, n, house=None, seed=1, noise=0.0):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        o = learning.make_order(i, bk, hd, f'Город Москва, ул. Тестовая, д. {i}')
        truth = learning.current()
        truth['work'][learning.key_of(bk, hd)] = true_factor
        out.append(learning.Fact(o, estimate(o, house, truth)[0] * rnd.lognormvariate(0, noise)))
    return out


class Houses(dict):
    def get(self, key, default=None):
        return dict.get(self, key.split(', кв')[0], default)


def test_factor_moves_towards_truth_with_more_visits():
    few = learning.fit(facts('Подключение', 'Конвергенция абонента', 1.5, 2), Houses())[0]
    many = learning.fit(facts('Подключение', 'Конвергенция абонента', 1.5, 200), Houses())[0]
    key = ('Подключение', 'Конвергенция')
    assert 1.10 < few['work'][key] < 1.2 < many['work'][key] == pytest.approx(1.5, abs=0.03)   # пока мало — почти не двигается


def test_unknown_subtype_learns_whole_type_and_rows():
    now, rows = learning.fit(facts('Глобальная проблема', 'Авария', 0.8, 100), Houses())
    assert now['work'][('Глобальная проблема', '')] == pytest.approx(0.8, abs=0.03)
    assert rows == [dict(what='Авария', bk='Глобальная проблема', was=1.0, now=now['work'][('Глобальная проблема', '')], n=100)]


def test_old_house_learned_after_work_types():
    houses = Houses({f'Город Москва, ул. Тестовая, д. {i}': OLD for i in range(300)})
    truth = learning.current()
    truth['old'] = 1.4
    data = []
    for i in range(300):
        o = learning.make_order(i, 'Локальная заявка', 'Разрывы', f'Город Москва, ул. Тестовая, д. {i}')
        data.append(learning.Fact(o, estimate(o, OLD, truth)[0]))
    now, rows = learning.fit(data, houses)
    # все дома старые — поправка сначала уходит в подтип; дом по остаткам почти не двигается: их не различить
    assert now['work'][('Локальная заявка', 'Разрывы')] * now['old'] == pytest.approx(1.2 * 1.4, rel=0.03)
    assert [r['what'] for r in rows] == ['Ремонт: Разрывы', 'старый дом']


def test_limits_and_mae():
    o = learning.make_order(1, 'Дозаказ', 'Заказ подключения', 'Город Москва, ул. А, д. 1')
    now, _ = learning.fit([learning.Fact(o, 1000)] * 500, Houses())
    assert now['work'][('Дозаказ', 'Заказ подключения')] == learning.LIMITS[1]
    data = facts('Подключение', 'Конвергенция абонента', 1.5, 50)
    assert learning.mae(data, Houses(), learning.fit(data, Houses())[0]) < learning.mae(data, Houses(), learning.current())


def test_csv_round_trip_and_events(tmp_path):
    rows = [{'Дата': '01.09.2026', 'Заявка': '7', 'Тип заявки BK': 'Подключение', 'Тип заявки HD': 'Конвергенция абонента',
             'Адрес': 'Город Москва, ул. А, д. 1, кв. 5', 'Гигабит': 'Да', 'План мин': 77, 'Факт мин': 90}]
    learning.write(tmp_path / 'f.csv', rows)
    got, raw = learning.read(tmp_path / 'f.csv')
    assert got[0].actual == 90 and got[0].order.gigabit and got[0].order.apt == 5 and raw[0]['Заявка'] == '7'
    doc = dict(event=dict(kind='finish', order=0, end=200),
               orders=[dict(id='7', bk='Подключение', hd='Конвергенция', addr='a', plan=dict(start=120))])
    assert [f.actual for f in learning.from_events([doc, dict(event=dict(kind='cancel'))])] == [80]


def test_estimate_uses_learned_factors():
    o = learning.make_order(1, 'Подключение', 'Конвергенция абонента', 'Город Москва, ул. А, д. 1')
    f = learning.current()
    f['work'][('Подключение', 'Конвергенция')] = 1.5
    f['old'] = 1.3
    service, _, detail = estimate(o, OLD, f)
    assert service == round(70 * 1.5 * 1.3)
    assert [p['value'] for p in detail['parts']][1:] == ['×1,50', '×1,30']


def brigade_facts(speeds, n, seed=2, noise=0.1):
    """Ремонты «Разрывы» разных бригад двух участков с «истинным» темпом speeds[(участок, бригада)]."""
    rnd = random.Random(seed)
    out = []
    for (region, name), speed in speeds.items():
        for i in range(n):
            o = learning.make_order(f'{name}{i}', 'Локальная заявка', 'Разрывы', f'Город Москва, ул. Тестовая, д. {i}')
            out.append(learning.Fact(o, estimate(o, None, learning.current())[0] * speed * rnd.lognormvariate(0, noise),
                                     name, region))
    return out


def test_brigade_speed_learned_after_work_and_house():
    speeds = {('Восток', 'Бригада 1'): 0.8, ('Восток', 'Бригада 2'): 1.0, ('Запад', 'Бригада 1'): 1.3}
    now, rows = learning.fit(brigade_facts(speeds, 300), Houses())
    assert now['brigades']['Восток: Бригада 1'] == pytest.approx(0.8 / 1.03, rel=0.06)
    assert now['brigades']['Запад: Бригада 1'] == pytest.approx(1.3 / 1.03, rel=0.06)   # тёзка на другом участке — свой темп
    # подтип забрал средний сдвиг (0,8 + 1 + 1,3)/3 ≈ 1,03; произведение — истина
    assert now['work'][('Локальная заявка', 'Разрывы')] * now['brigades']['Восток: Бригада 2'] == pytest.approx(1.2, rel=0.03)
    brig = [r for r in rows if 'brigade' in r]
    assert [(r['region'], r['what'], r['n'], r['was']) for r in brig] == [
        ('Восток', 'Бригада 1: темп', 300, 1.0), ('Восток', 'Бригада 2: темп', 300, 1.0), ('Запад', 'Бригада 1: темп', 300, 1.0)]


def test_brigade_speed_starts_from_level_and_shrinks():
    data = brigade_facts({('Восток', 'Стажёр'): 1.15}, 3, noise=0.0)
    now, rows = learning.fit(data, Houses(), speeds={'Восток: Стажёр': 1.15})
    assert now['brigades']['Восток: Стажёр'] == pytest.approx(1.15, abs=0.01)     # уровень угадан — темп на месте
    now, _ = learning.fit(data, Houses())
    assert 1.0 < now['brigades']['Восток: Стажёр'] < 1.07                          # 3 выезда против PRIOR = 8 — сдвиг мал
    assert learning.mae(data, Houses(), learning.fit(data, Houses(), speeds={'Восток: Стажёр': 1.15})[0]) < 1


def test_fit_for_applies_speed_by_name_within_region():
    from tests.helpers import brigade
    data = brigade_facts({('Восток', 'Бригада 1'): 0.7, ('Запад', 'Бригада 2'): 1.4}, 200)
    mine = [brigade('Бригада 1'), brigade('Бригада 2'), brigade('Бригада 3')]
    mine[1].speed = 1.15                                                        # стажёр по файлу бригад
    factors, rows = learning.fit_for(data, Houses(), mine, 'Восток')
    assert mine[0].speed < 0.8 and mine[1].speed == 1.15 and mine[2].speed == 1.0   # Бригада 2 Запада — не наша
    assert [r['what'] for r in rows if 'brigade' in r] == ['Бригада 1: темп']
    assert 'Восток: Бригада 1' in factors['brigades']


def test_csv_with_brigade_round_trip(tmp_path):
    rows = [{'Дата': '01.09.2026', 'Участок': 'Восток', 'Бригада': 'Бригада 3', 'Заявка': '7', 'Тип заявки BK': 'Подключение',
             'Тип заявки HD': 'Конвергенция абонента', 'Адрес': 'Город Москва, ул. А, д. 1', 'Гигабит': 'Нет',
             'План мин': 77, 'Факт мин': 66}]
    learning.write(tmp_path / 'f.csv', rows)
    assert (tmp_path / 'f.csv').read_text(encoding='utf-8-sig').startswith('Дата;Участок;Бригада;Заявка')
    got, _ = learning.read(tmp_path / 'f.csv')
    assert (got[0].brigade, got[0].region, got[0].key, got[0].actual) == ('Бригада 3', 'Восток', 'Восток: Бригада 3', 66)
    doc = dict(meta=dict(name='Восток'), brigades=[dict(name='Бригада 1'), dict(name='Бригада 4')],
               event=dict(kind='finish', order=0, end=200),
               orders=[dict(id='7', bk='Ремонт', hd='', addr='a', plan=dict(brigade=1, start=150))])
    doc['orders'][0]['bk'] = 'Локальная заявка'
    (f,) = learning.from_events([doc])
    assert (f.actual, f.brigade, f.region) == (50, 'Бригада 4', 'Восток')
