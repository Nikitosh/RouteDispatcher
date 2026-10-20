from dispatch.planning.whatif import early_start, exact_time, narrow
from tests.helpers import brigade, line_problem, order


class FixedSolver:
    """Подмена решателя: всегда один и тот же маршрут, запоминает задачи, которые ему дали."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def plan_only(self, problem, seconds, seed, workdir):
        self.seen.append(problem)
        return self.routes


def test_narrow_keeps_accidents_and_is_stable():
    o, acc = order('101', 'Подключение', (120, 240)), order('102', 'Авария', (120, 240))
    assert narrow(o, 0.0) is o and narrow(acc, 1.0) is acc
    w = narrow(o, 1.0).window
    assert w in ((120, 180), (180, 240)) and narrow(o, 1.0).window == w


def test_early_start_lists_calls():
    p = line_problem([order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (120, 300), x=20)], [brigade('Б1')])
    res = early_start(FixedSolver([[0, 1]]), p, 1, 1, '/tmp')
    assert res['brigades'] == 1
    assert res['calls'] == [dict(k=1, id='B', brigade='Б1', window=[120, 300], start=60, earlier=60)]   # B с 120 → 60


def test_exact_time_points():
    p = line_problem([order(str(i), 'Ремонт', (0, 120), x=i) for i in range(1, 5)], [brigade('Б1')])
    s = FixedSolver([[0, 1, 2, 3]])
    pts = exact_time(s, p, 1, 1, '/tmp', shares=(0.0, 1.0))
    assert [x['narrowed'] for x in pts] == [0, 4]
    assert all(o.window[1] - o.window[0] == 60 for o in s.seen[1].orders)


class BySolver:
    """Подмена решателя: маршруты по номеру вызова (варианты по очереди)."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def plan_only(self, problem, seconds, seed, workdir):
        return self.answers.pop(0)


def test_early_start_keeps_current_plan_when_run_is_worse():
    """Прогон раннего старта хуже основного плана (две бригады вместо одной): берётся основной, выигрыша нет."""
    p = line_problem([order('A', 'Ремонт', (0, 300), x=10), order('B', 'Ремонт', (0, 300), x=20)], [brigade('Б1'), brigade('Б2')])
    res = early_start(BySolver([[0], [1]]), p, 1, 1, '/tmp', current=[[0, 1], []])
    assert res['brigades'] == 1 and res['useful'] is False and res['calls'] == []


def test_early_start_useful_when_fewer_brigades():
    """Только ранний старт даёт одной бригаде успеть обе заявки: выигрыш есть, кому звонить — B."""
    p = line_problem([order('A', 'Ремонт', (0, 60), x=10), order('B', 'Ремонт', (100, 140), x=10)], [brigade('Б1'), brigade('Б2')])
    res = early_start(BySolver([[0, 1], []]), p, 1, 1, '/tmp', current=[[0], [1]])
    assert res['useful'] is True and res['brigades'] == 1 and [c['id'] for c in res['calls']] == ['B']


def test_exact_time_zero_share_is_current_plan_and_monotone():
    """0% — основной план (решатель дал хуже); 50% не хуже 100%: план для узких окон годится и для широких."""
    p = line_problem([order(str(i), 'Ремонт', (0, 120), x=i) for i in (1, 2)], [brigade('Б1'), brigade('Б2')])
    one, two = [[0, 1], []], [[0], [1]]
    pts = exact_time(BySolver(two, two, one), p, 1, 1, '/tmp', shares=(0.0, 0.5, 1.0), current=one)
    assert [x['brigades'] for x in pts] == [1, 1, 1]       # 100% решатель дал одну бригаду — она годится и для 50%
    assert pts[0]['km'] == round(p.km(0, one[0]), 1)
    pts = exact_time(BySolver(two, two, two), p, 1, 1, '/tmp', shares=(0.0, 1.0), current=one)
    assert [x['brigades'] for x in pts] == [1, 2]          # основной план — только для 0%
