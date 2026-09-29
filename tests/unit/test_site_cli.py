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
