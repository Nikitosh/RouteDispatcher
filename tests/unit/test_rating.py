from dispatch.model.rating import PRIOR, Review, compute, naive
from dispatch.planning.best import order_rating, ranked
from tests.helpers import brigade, line_problem, order


def test_bayes_pulls_few_reviews_to_mean():
    reviews = [Review(age=0, brigade='А', skill=1, work=5)] + [Review(age=0, brigade='Б', skill=1, work=4) for _ in range(40)]
    r = compute(reviews, {'А': [1], 'Б': [1]})
    a, b = r['А'].of(1), r['Б'].of(1)
    assert a.few and not b.few and a.reviews == 1
    assert a.value < 4.2                       # одна пятёрка почти не сдвигает от среднего
    assert abs(b.value - 4.0) < 0.05


def test_plan_late_does_not_hurt_engineer():
    base = [Review(age=0, brigade='А', skill=0, work=5, on_time=5) for _ in range(20)]
    late = [Review(age=0, brigade='А', skill=0, work=5, on_time=1, plan_late=True) for _ in range(10)]
    r = compute(base + late, {'А': [0]})['А']
    assert r.on_time == 5 and r.on_time_reviews == 20
    assert naive(base + late)['А', 0] < 4.7    # простое среднее наказало бы монтажника за чужое опоздание


def test_old_reviews_weigh_less():
    fresh = compute([Review(age=0, brigade='А', skill=0, work=3) for _ in range(PRIOR)]
                    + [Review(age=300, brigade='А', skill=0, work=5) for _ in range(PRIOR)], {'А': [0]})['А'].of(0)
    assert fresh.value < 4.0                   # старые пятёрки весят мало


def test_ranking_and_order_rating():
    p = line_problem([order(1, 'Ремонт', x=1), order(2, 'Подключение', x=2)],
                     [brigade('А', skills=(0, 1)), brigade('Б', skills=(0, 1))])
    reviews = [Review(age=0, brigade='А', skill=s, work=3) for s in (0, 1) for _ in range(30)] \
        + [Review(age=0, brigade='Б', skill=s, work=5) for s in (0, 1) for _ in range(30)]
    r = compute(reviews, {'А': [0, 1], 'Б': [0, 1]})
    assert ranked(p, r) == [1, 0]
    assert order_rating(p, [[], [0, 1]], r) == r['Б'].of(0).value
