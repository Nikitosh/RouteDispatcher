import itertools

from dispatch.config import MODES
from dispatch.planning.validate import check_plan
from tests.helpers import brigade, line_problem, order


def test_transport_encoded_in_skill_bits():
    """Бит (маска >> код навыка) & 1, который видит решатель, должен совпадать с can() для любых сочетаний."""
    needs = [None, {'car'}, {'car', 'bike'}, {'foot'}]
    orders = [order(i, kind, x=i, need=n) for i, (kind, n) in enumerate(itertools.product(['Ремонт', 'Подключение', 'Авария'], needs))]
    brigades = [brigade(f'Б{i}', m, s) for i, (m, s) in enumerate(itertools.product(MODES, [(0,), (1, 2), (0, 1, 2)]))]
    p = line_problem(orders, brigades)
    assert len(p.classes) == 4
    for v, k in itertools.product(range(len(brigades)), range(len(orders))):
        assert bool((p.skill_mask(v) >> p.skill_code(k)) & 1) == p.can(v, k)


def test_latest_start_respects_window_and_shift():
    p = line_problem([order(1, 'Ремонт', (0, 120)), order(2, 'Авария', (600, 720))], [brigade('Б1')])
    assert p.latest(0) == 120
    assert p.latest(1) == 720 - 80           # работу нужно закончить до 22:00


def test_schedule_waits_for_window():
    p = line_problem([order(1, 'Ремонт', (60, 120), x=10), order(2, 'Ремонт', (0, 200), x=15)], [brigade('Б1')])
    s = p.schedule(0, [0, 1])
    assert s.feasible and s.km == 15
    assert (s.stops[0].arrive, s.stops[0].start, s.stops[0].end) == (10, 60, 90)
    assert s.stops[1].start == 95
    assert not p.schedule(0, [1, 0]).feasible or p.schedule(0, [1, 0]).stops[1].start <= 120


def test_release_delays_only_changed_part():
    p = line_problem([order(1, 'Ремонт', (0, 300), x=5), order(2, 'Ремонт', (0, 300), x=6)], [brigade('Б1')])
    assert p.schedule(0, [0, 1], release=100, keep=1).stops[1].start == 101     # к первой заявке — по старому плану
    assert p.schedule(0, [0, 1], release=100, keep=0).stops[0].start == 105


def test_write_instance(tmp_path):
    p = line_problem([order(1, 'Подключение', (0, 120), x=3, need={'car'})], [brigade('Б1', 'car', (1,)), brigade('Б2', 'foot', (1,))])
    path = tmp_path / 'task.txt'
    p.write(str(path))
    lines = path.read_text().splitlines()
    assert lines[:2] == ['тест', '1 2 1']
    assert lines[2].split() == ['1', '70', '0', '120', '2', str(1 + 3 * 1), '1', '0']     # подключению нужен роутер
    # машина: и «любой», и «только машина»; свободна с 0, обед 13:00–15:15 на 45 мин, оборудование без предела; темп у
    # всех 1 — поля темпа нет
    assert lines[3].split() == ['0', '0', str((1 << 1) | (1 << 4)), '720', '0.0', '180', '315', '45', '-1', '-1', '-1']
    assert lines[4].split() == ['0', '3', str(1 << 1), '720', '0.0', '180', '315', '45', '-1', '-1', '-1']
    assert len(lines) == 2 + 1 + 2 + 4 * 2 * 2


def test_validator_catches_violations():
    orders = [order(1, 'Ремонт', (0, 20), x=30), order(2, 'Авария', (0, 300), x=1), order(3, 'Ремонт', (0, 300), x=2, need={'car'})]
    p = line_problem(orders, [brigade('Б1', 'foot', (0,)), brigade('Б2', 'car', (0, 2))])
    assert check_plan(p, [[], [1, 2]]).ok
    errs = check_plan(p, [[0, 1, 2], [2]]).errors
    assert any('позже окна' in e for e in errs)
    assert any('нет навыка' in e for e in errs)
    assert any('транспорт не подходит' in e for e in errs)
    assert any('назначена 2 раз' in e for e in errs)
    frozen = [[], [1]]
    assert any('выполненная часть' in e for e in check_plan(p, [[], [2]], frozen=frozen).errors)
    assert any('недоступна' in e for e in check_plan(p, [[], [1, 2]], frozen=frozen, removed={1}).errors)


def test_shift_bounds_schedule():
    late = brigade('Поздняя')
    late.shift = (180, 720)                  # 13:00–22:00
    early = brigade('Ранняя')
    early.shift = (0, 540)                   # 10:00–19:00
    p = line_problem([order(1, 'Ремонт', (0, 120), x=10), order(2, 'Ремонт', (520, 600), x=10)], [late, early])
    s = p.schedule(0, [1])
    assert s.feasible and s.lunch == (180, 0) and s.stops[0].arrive == 180 + 45 + 10   # обед с начала смены, потом дорога
    assert not p.schedule(0, [0]).feasible   # окно до 12:00, а смена с 13:00
    assert not p.schedule(1, [1]).feasible   # работа закончится после 19:00
    errs = check_plan(p, [[0], [1]]).errors
    assert any('позже окна' in e for e in errs) and any('конца смены 10:00–19:00' in e for e in errs)



def test_speed_scales_service_per_brigade(tmp_path):
    """Темп бригады: длительность ⌊норматив·темп + 0,5⌋ у неё в расписании, в самом позднем начале и в проверке;
    в задаче решателя — последнее поле бригады, а конец окна — по самой быстрой."""
    fast, slow = brigade('Быстрая', x=0), brigade('Медленная', x=0)
    fast.speed, slow.speed = 0.85, 1.25
    orders = [order(1, 'Подключение', (0, 600), x=10), order(2, 'Ремонт', (0, 600), x=10), order(3, 'Авария', (600, 720), x=1)]
    p = line_problem(orders, [fast, slow])
    assert [p.service(0, k) for k in range(3)] == [60, 26, 68]           # 59,5 → 60; 25,5 → 26; 68
    assert [p.service(1, k) for k in range(3)] == [88, 38, 100]          # 87,5 → 88; 37,5 → 38
    s = p.schedule(0, [0, 1])
    assert [(st.start, st.end) for st in s.stops] == [(10, 70), (70, 96)]
    s = p.schedule(1, [0, 1])
    assert [(st.start, st.end) for st in s.stops] == [(10, 98), (98, 136)]
    assert p.latest(2, 0) == 720 - 68 and p.latest(2, 1) == 720 - 100 and p.latest(2) == 720 - 68
    # 22:00 − 80 мин аварии по нормативу = 640: быстрая успевает начать в 651, медленная — нет
    assert p.in_time(0, 2, 651) and not p.in_time(1, 2, 651)
    assert check_plan(p, [[2], []]).ok
    path = tmp_path / 'task.txt'
    p.write(str(path))
    lines = path.read_text().splitlines()
    assert lines[4].split()[3] == str(720 - 68)                           # конец окна аварии — по самой быстрой
    assert lines[5].split()[-1] == '0.85' and lines[6].split()[-1] == '1.25'


def test_validator_uses_brigade_speed():
    b = brigade('Медленная')
    b.speed = 1.5
    orders = [order(1, 'Подключение', (0, 0), x=0), order(2, 'Ремонт', (75, 75), x=0)]
    b.lunch = False
    p = line_problem(orders, [b])
    assert not p.schedule(0, [0, 1]).feasible                  # 70·1,5 = 105 мин: к 75 не успевает
    assert any('позже окна' in e for e in check_plan(p, [[0, 1]]).errors)
    b.speed = 1.0
    assert p.schedule(0, [0, 1]).feasible and check_plan(p, [[0, 1]]).ok
