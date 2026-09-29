from dispatch.planning.explain import Explainer
from tests.helpers import brigade, line_problem, order


def reason(p, routes, k):
    return Explainer(p).unassigned(routes, k).code


def test_reasons_follow_tz_order():
    orders = [
        order(1, 'Авария', (0, 100), x=1),                       # 0: ни у кого нет навыка аварий
        order(2, 'Ремонт', (0, 100), x=1, need={'bike'}),         # 1: нет велосипеда
        order(3, 'Ремонт', (700, 720), x=1),                      # 2: 30 мин работы не помещаются в смену
        order(4, 'Ремонт', (0, 10), x=50),                        # 3: не доехать к окну
        order(5, 'Ремонт', (0, 100), x=2),                        # 4: бригада занята
        order(6, 'Ремонт', (0, 100), x=3),                        # 5: занимает бригаду
    ]
    p = line_problem(orders, [brigade('Б1', 'car', (0,))])
    codes = [reason(p, [[5]], k) for k in range(5)]
    assert codes == ['skill', 'transport', 'shift', 'window', 'free']
    p2 = line_problem([order(5, 'Ремонт', (0, 30), x=2), order(6, 'Ремонт', (0, 30), x=3)], [brigade('Б1', 'car', (0,))])
    assert reason(p2, [[1]], 0) == 'busy'
    assert Explainer(p2).greedy_unassigned([[1]], 0).code == 'busy'


def test_assigned_explanation_groups():
    orders = [order(1, 'Ремонт', (0, 300), x=5), order(2, 'Ремонт', (0, 300), x=6), order(3, 'Ремонт', (0, 300), x=-40)]
    brigades = [brigade('Б1', 'car', (0,)), brigade('Б2', 'car', (0,)), brigade('Б3', 'car', (1,)), brigade('Б4', 'car', (0,))]
    p = line_problem(orders, brigades)
    e = Explainer(p).assigned([[0, 1], [2], [], []], 1)
    assert e.brigade == 0 and e.added_km == 1
    groups = {g['code']: g['names'] for g in e.others}
    assert groups == {'ok': 'Б2', 'idle': 'Б4', 'skill': 'Б3'}
    assert e.summary.startswith('Б1: добавляет к маршруту 1,0 км')
    assert 'Б2 — успевает, но +' not in e.summary and e.others[0]['text'] == 'успевает, но +12,0 км'
