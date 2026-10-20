"""Графики смен: 2/2 — 10:00–22:00, 5/2 — 10:00–19:00 или 13:00–22:00 (Brigade.shift, минуты от 10:00)."""
import pytest

from dispatch.files import document as docs
from dispatch.files.readers import InputError, parse_shift
from dispatch.geo.geocoder import Geocoder
from dispatch.geo.houses import HouseIndex
from dispatch.planning.planner import Planner
from dispatch.planning.replan import Replanner, Unavailable
from dispatch.planning.validate import Check, check_plan
from tests.helpers import LineMatrices, brigade, line_problem, order


def shifted(name, shift, **kw):
    b = brigade(name, **kw)
    b.shift = shift
    return b


def test_parse_shift_and_text():
    assert parse_shift('13-22', 'x') == (180, 720) and parse_shift('10:00–19:00', 'x') == (0, 540)
    assert parse_shift('12:30-21', 'x') == (150, 660)
    for bad in ('8-17', '13-23', '19-13', 'утро'):
        with pytest.raises(InputError):
            parse_shift(bad, 'строка 2')
    assert shifted('Б', (150, 660)).shift_text == '12:30–21:00' and brigade('Б').shift_text == '10:00–22:00'


def test_write_with_late_shift_gives_each_brigade_own_start(tmp_path):
    """Кто-то начинает позже 10:00: у каждой бригады своя стартовая строка, начало смены прибавлено к выездам из неё."""
    late, early = shifted('Поздняя', (180, 720)), shifted('Ранняя', (0, 540), start=1, x=5)
    p = line_problem([order(1, x=10), order(2, x=20)], [late, early], starts=((0.0, 0.0), (5.0, 0.0)))
    path = tmp_path / 'task.txt'
    p.write(str(path))
    lines = path.read_text().splitlines()
    assert lines[1] == '2 2 2'                                        # стартовых точек = бригад
    assert lines[4].split()[:4] == ['0', '0', lines[4].split()[2], '720'] and lines[5].split()[:4:3] == ['1', '540']
    car = [list(map(float, lines[6 + r].split())) for r in range(4)]   # первая матрица — время на машине
    assert car[0][2:] == [180 + 10, 180 + 20]                        # поздняя: начало смены + дорога
    assert car[1][2:] == [5, 15]                                      # ранняя: только дорога от своего старта
    assert car[2][3] == 10 and car[0][1] == 0 + 5                     # между заявками и между стартами — без сдвига


def test_write_without_late_shift_keeps_shared_starts(tmp_path):
    p = line_problem([order(1, x=10)], [shifted('А', (0, 540)), brigade('Б')])
    path = tmp_path / 'task.txt'
    p.write(str(path))
    assert path.read_text().splitlines()[1] == '1 2 1'


def test_validator_uses_shift_of_each_brigade():
    p = line_problem([order(1, 'Ремонт', (0, 300), x=10)], [shifted('Поздняя', (180, 720)), shifted('Ранняя', (0, 30))])
    assert p.schedule(0, [0]).stops[0].start == 190
    assert check_plan(p, [[0], []]).ok
    errs = check_plan(p, [[], [0]]).errors
    assert errs == ['Ранняя: работа после конца смены 10:00–10:30 (заявка 1)']


def test_document_round_trip_keeps_shift():
    p = line_problem([order(1, x=10)], [shifted('Б1', (180, 720)), brigade('Б2')])
    b1 = docs.brigade_dict(0, p.brigades[0])
    assert (b1['schedule'], b1['shift'], b1['shift_text']) == ('', [180, 720], '13:00–22:00')
    doc = dict(brigades=[b1, docs.brigade_dict(1, p.brigades[1])], office=dict(lat=0.0, lon=0.0, addr='офис'))
    brigades, _ = docs.brigades_from(doc)
    assert [b.shift for b in brigades] == [(180, 720), (0, 720)]
    del doc['brigades'][0]['shift']                                    # документ до графиков — весь день
    assert docs.brigades_from(doc)[0][0].shift == (0, 720)


def test_insert_does_not_send_late_brigade_before_its_shift(tmp_path):
    """Б1 недоступна в 10:30; резерв Б3 выходит в 13:00, поэтому её заявки начинаются не раньше 13:00 + дорога."""
    orders = [order('A', 'Ремонт', (0, 600), x=10), order('B', 'Ремонт', (0, 600), x=20)]
    brigades = [brigade('Б1'), shifted('Б3', (180, 720))]
    p = line_problem(orders, brigades)
    planner = Planner(geocoder=object(), matrices=LineMatrices(), solver=object(), geometry=object(), log=lambda *_: None)
    doc = planner.document(p, dict(name='тест', office='офис', traffic={}, overhead={}), [[0, 1], []], [[0, 1], []], 1,
                           Check(True))
    r = Replanner(doc, matrices=LineMatrices(), geocoder=Geocoder(path=str(tmp_path / 'g.json'), online=False),
                  geometry=type('G', (), {'leg': lambda *a: None})())
    res = r.apply(5, Unavailable('Б1'))
    assert res['plan']['check']['ok'], res['plan']['check']['errors']
    starts = [o['plan']['start'] for o in res['orders'] if o['plan'] and o['plan']['brigade'] == 1]
    assert starts and min(starts) >= 190


def test_planner_load_applies_schedule(tmp_path):
    orders = tmp_path / 'o.csv'
    orders.write_text('Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес\n'
                      '1;Локальная заявка;Ремонт;17.08.2026 10:00;17.08.2026 12:00;Р;Город Москва, ул. А, д. 1\n'
                      'Адрес офиса;г. Москва, ул. Б, д. 2\n', encoding='cp1251')
    brigades = tmp_path / 'b.csv'
    brigades.write_text('Бригада;Транспорт;Навыки;График\nБ1;авто;ремонт;\nБ2;авто;ремонт;2/2\nБ3;авто;ремонт;\n',
                        encoding='cp1251')

    class Here:
        def locate(self, addrs, log=None):
            return {a: (55.0 + i * 0.01, 37.0) for i, a in enumerate(addrs)}
    logs = []
    planner = Planner(geocoder=Here(), matrices=LineMatrices(), solver=object(), geometry=object(),
                      houses=HouseIndex(str(tmp_path / 'нет.json')), log=logs.append)
    problem, _ = planner.load(str(orders), str(brigades), 'тест', schedule='5/2')
    assert [b.shift for b in problem.brigades] == [(0, 540), (0, 720), (180, 720)]
    assert any(line.startswith('Смены: ') and '13:00–22:00 — 1' in line for line in logs)


def test_unassigned_reason_knows_about_shifts():
    """Вечером «Утро» уже не работает: она не «занята», у неё нет смены."""
    from dispatch.planning.explain import Explainer
    orders = [order('A', 'Ремонт', (600, 610), x=10), order('B', 'Ремонт', (600, 610), x=10)]
    p = line_problem(orders, [shifted('Утро', (0, 540)), brigade('Вечер')])
    r = Explainer(p).unassigned([[], [1]], 0)
    assert r.code == 'busy' and '(Вечер) заняты' in r.text and 'не на смене в это время: Утро' in r.text
