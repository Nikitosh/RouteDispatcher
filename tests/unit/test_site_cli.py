import json

import pytest

from dispatch.cli import main, mode_values
from dispatch.site.builder import build_site, event_label, slug


def fake_doc(name, event=None):
    d = dict(meta=dict(name=name), orders=[dict(id='7')], plan=dict(routes=[]))
    if event:
        d['event'] = event
    return d


def test_site_links_events_to_their_plan(tmp_path):
    plan_dir, ev_dir, other = tmp_path / 'plan', tmp_path / 'ev', tmp_path / 'other'
    for d, doc in ((plan_dir, fake_doc('Восток')), (other, fake_doc('Юго-восток')),
                   (ev_dir, fake_doc('Восток', dict(kind='cancel', order=0, at=180, previous=str(plan_dir))))):
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(doc, ensure_ascii=False))
    out = tmp_path / 'site'
    build_site([str(plan_dir), str(ev_dir), str(other)], str(out), log=lambda *_: None)
    man = json.loads((out / 'manifest.json').read_text())
    assert [d['slug'] for d in man['districts']] == ['vostok', 'yugo-vostok']
    assert man['districts'][0]['events'] == [dict(label='13:00 — отмена заявки 7', plan='data/vostok/event_0.json')]
    assert (out / 'index.html').exists() and (out / 'data/vostok/event_0.json').exists()


def test_slug_and_labels():
    assert slug('Северо-восток') == 'severo-vostok'
    assert event_label(dict(event=dict(kind='unavailable', name='Бригада 4', at=240), orders=[])) == '14:00 — Бригада 4 недоступна'


def test_mode_values_and_errors(capsys):
    assert mode_values('car=1.3,pt=1') == {'car': 1.3, 'pt': 1.0}
    with pytest.raises(Exception):
        mode_values('boat=2')
    assert main(['plan', 'нет-такого.csv', '-b', 'и-такого.xlsx']) == 1
    assert 'нет файла' in capsys.readouterr().err


def test_journeys_point_to_plans_and_events(tmp_path):
    plan_dir, ev_dir = tmp_path / 'plan', tmp_path / 'ev'
    for d, doc in ((plan_dir, fake_doc('Восток')),
                   (ev_dir, fake_doc('Восток', dict(kind='cancel', order=0, at=180, previous=str(plan_dir))))):
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(doc, ensure_ascii=False))
    journeys = [dict(slug='day', title='День', steps=[
        dict(slug='plan', title='План', text='утро', dir=str(plan_dir), order='7'),
        dict(slug='ev', title='Отмена', text='событие', dir=str(ev_dir), tab='event'),
        dict(slug='lost', title='Нет такого', text='', dir=str(tmp_path / 'нет')),
        dict(slug='eng', title='Инженер', text='телефон', link='engineer.html#d=vostok')]),
        dict(slug='empty', title='Пусто', steps=[dict(slug='x', title='x', text='', dir=str(tmp_path / 'нет'))])]
    logs = []
    build_site([str(plan_dir), str(ev_dir)], str(tmp_path / 'site'), log=logs.append, scenarios=journeys)
    man = json.loads((tmp_path / 'site' / 'manifest.json').read_text())
    assert [j['slug'] for j in man['journeys']] == ['day']                       # история без шагов пропущена
    steps = man['journeys'][0]['steps']
    assert [(s['slug'], s.get('d'), s.get('ev')) for s in steps] == [('plan', 'vostok', None), ('ev', 'vostok', 0), ('eng', None, None)]
    assert steps[0]['order'] == '7' and 'dir' not in steps[0] and steps[2]['link'] == 'engineer.html#d=vostok'
    assert any('Нет такого' in line for line in logs)
    event = json.loads((tmp_path / 'site' / 'data' / 'vostok' / 'event_0.json').read_text())
    assert '_dir' not in event


def test_traffic_flags_add_to_defaults():
    from dispatch.cli import build_parser, travel_settings
    a = build_parser().parse_args(['plan', 'o.xlsx', '-b', 'b.xlsx', '--traffic', 'bike=1.1', '--overhead', 'foot=4'])
    t = travel_settings(a)
    assert t.traffic == {'car': 1.3, 'bike': 1.1} and t.overhead['car'] == 5.0 and t.overhead['foot'] == 4.0
    assert travel_settings(build_parser().parse_args(['plan', 'o.xlsx', '-b', 'b.xlsx', '--flat-traffic'])).hourly is False


def test_chained_and_moved_events_find_their_plan(tmp_path):
    plan_dir, ev1, ev2, lost = tmp_path / 'plan', tmp_path / 'ev1', tmp_path / 'ev2', tmp_path / 'lost'
    docs = ((plan_dir, fake_doc('Восток')),
            (ev1, fake_doc('Восток', dict(kind='cancel', order=0, at=180, previous=str(plan_dir)))),
            (ev2, fake_doc('Восток', dict(kind='cancel', order=0, at=240, previous=str(ev1)))),          # цепочка событий
            (lost, fake_doc('Нигде', dict(kind='cancel', order=0, at=60, previous='/нет/такой/папки'))))
    for d, doc in docs:
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(doc, ensure_ascii=False))
    moved = fake_doc('Восток', dict(kind='cancel', order=0, at=300, previous='/старый/путь/plan'))   # папки переносили
    (tmp_path / 'moved').mkdir()
    (tmp_path / 'moved' / 'plan.json').write_text(json.dumps(moved, ensure_ascii=False))
    logs = []
    build_site([str(d) for d, _ in docs] + [str(tmp_path / 'moved')], str(tmp_path / 'site'), log=logs.append)
    man = json.loads((tmp_path / 'site' / 'manifest.json').read_text())
    assert [d['slug'] for d in man['districts']] == ['vostok'] and len(man['districts'][0]['events']) == 3
    assert any('lost' in line and 'не найден план' in line for line in logs)


def test_slug_collisions_get_own_folders(tmp_path):
    dirs = []
    for i, name in enumerate(('Восток 5 2', 'Восток — 5/2', '!!!')):
        d = tmp_path / f'p{i}'
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(fake_doc(name), ensure_ascii=False))
        dirs.append(str(d))
    build_site(dirs, str(tmp_path / 'site'), log=lambda *_: None)
    man = json.loads((tmp_path / 'site' / 'manifest.json').read_text())
    slugs = [d['slug'] for d in man['districts']]
    assert len(set(slugs)) == 3 and 'uchastok' in slugs and all(s for s in slugs)


def test_slug_dedupe_does_not_loop(tmp_path):
    dirs = []
    for i, name in enumerate(('Восток', 'Восток 2', 'Восток!', 'Восток?')):
        d = tmp_path / f'p{i}'
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(fake_doc(name), ensure_ascii=False))
        dirs.append(str(d))
    build_site(dirs, str(tmp_path / 'site'), log=lambda *_: None)
    slugs = [d['slug'] for d in json.loads((tmp_path / 'site' / 'manifest.json').read_text())['districts']]
    assert len(set(slugs)) == 4


def test_site_has_no_disk_paths_and_no_stale_files(tmp_path):
    plan_dir, ev_dir = tmp_path / 'plan', tmp_path / 'ev'
    for d, doc in ((plan_dir, fake_doc('Восток')), (ev_dir, fake_doc('Восток', dict(kind='cancel', order=0, at=180, previous=str(plan_dir))))):
        d.mkdir()
        (d / 'plan.json').write_text(json.dumps(doc, ensure_ascii=False))
    site = tmp_path / 'site'
    build_site([str(plan_dir), str(ev_dir)], str(site), log=lambda *_: None)
    event = json.loads((site / 'data' / 'vostok' / 'event_0.json').read_text())
    assert 'previous' not in event['event'] and str(tmp_path) not in (site / 'data' / 'vostok' / 'event_0.json').read_text()
    build_site([str(plan_dir)], str(site), log=lambda *_: None)          # без события: старый файл не остаётся
    assert not (site / 'data' / 'vostok' / 'event_0.json').exists()


def test_demo_events_pick_from_plan():
    import importlib.util
    spec = importlib.util.spec_from_file_location('demo_events', 'scripts/demo_events.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    stop = lambda k, beg, end: dict(k=k, beg=beg, end=end)
    doc = dict(orders=[dict(id=str(i), window=[0, 600]) for i in range(6)],
               brigades=[dict(name='Бригада 1', mode='car'), dict(name='Бригада 2', mode='foot')],
               plan=dict(routes=[dict(v=0, stops=[stop(0, 100, 160), stop(1, 200, 260), stop(2, 300, 330)]),
                                 dict(v=1, stops=[stop(3, 140, 170), stop(4, 260, 300), stop(5, 400, 430)])]))
    ev = m.pick(doc)
    assert ev['unavailable'] == ['--unavailable', 'Бригада 2']               # «Бригады 4» нет — у кого больше неначатых
    assert ev['cancel'][1] in {'1', '2', '4', '5'} and ev['window'][1] in {'1', '2', '4', '5'}   # не начаты к 13:00
    assert ev['finish'][:2] == ['--finish', '0'] or ev['finish'][:2] == ['--finish', '3']
    assert ev['breakdown'] == ['--breakdown', 'Бригада 1', '--to', 'pt']


def test_demo_phones_are_stable_and_fake():
    """Телефона в выгрузке нет — демо-номер с несуществующим кодом +7 000, одинаковый для заявки; свой не трогаем."""
    from dispatch.site.builder import demo_phones
    a = demo_phones({'orders': [{'id': '46393'}, {'id': '1', 'phone': '8 900 000-00-00'}], 'meta': {}})
    b = demo_phones({'orders': [{'id': '46393'}], 'meta': {}})
    assert a['orders'][0]['phone'] == b['orders'][0]['phone'] and a['orders'][0]['phone'].startswith('+7 000 ')
    assert a['orders'][1]['phone'] == '8 900 000-00-00' and a['meta']['demo_phones'] is True
