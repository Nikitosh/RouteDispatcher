"""Статическая страница диспетчера. Планы становятся участками, результаты replan — событиями своего плана
(по полю event.previous). Пишет index.html, app.js, style.css, manifest.json и data/<участок>/*.json; папку можно
открыть через `python -m dispatch serve` или выложить на любой статический хостинг."""
import json
import os
import re
import shutil

from ..formatting import hhmm

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
ASSETS = ('index.html', 'app.js', 'style.css')
_TRANSLIT = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                     ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u',
                      'f', 'h', 'c', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))


def slug(text):
    latin = ''.join(_TRANSLIT.get(c, c) for c in text.lower())
    return re.sub(r'[^a-z0-9]+', '-', latin).strip('-')


def event_label(doc):
    e, orders = doc['event'], doc['orders']
    if e['kind'] == 'unavailable':
        what = f"{e['name']} недоступна"
    elif e['kind'] == 'cancel':
        what = f"отмена заявки {orders[e['order']]['id']}"
    else:
        what = f"новая заявка: {(e.get('type') or '').lower()}"
    return f"{hhmm(e['at'])} — {what}"


def _dump(doc, path):
    json.dump(doc, open(path, 'w'), ensure_ascii=False, separators=(',', ':'))


def build_site(result_dirs, out, log=print):
    plans, events = {}, []
    for d in result_dirs:
        path = os.path.join(d, 'plan.json')
        if not os.path.exists(path):
            continue
        doc = json.load(open(path))
        if doc.get('event'):
            events.append(doc)
        else:
            plans[os.path.abspath(d)] = doc
    os.makedirs(os.path.join(out, 'data'), exist_ok=True)
    for f in ASSETS:
        shutil.copy(os.path.join(WEB, f), os.path.join(out, f))
    districts = []
    for path, doc in sorted(plans.items(), key=lambda x: x[1]['meta']['name']):
        s = slug(doc['meta']['name'])
        folder = os.path.join(out, 'data', s)
        os.makedirs(folder, exist_ok=True)
        _dump(doc, os.path.join(folder, 'plan.json'))
        mine = sorted((e for e in events if e['event'].get('previous') == path), key=lambda e: e['event']['at'])
        entries = []
        for i, e in enumerate(mine):
            _dump(e, os.path.join(folder, f'event_{i}.json'))
            entries.append(dict(label=event_label(e), plan=f'data/{s}/event_{i}.json'))
        districts.append(dict(name=doc['meta']['name'], slug=s, plan=f'data/{s}/plan.json', events=entries))
        log(f"{doc['meta']['name']}: план и событий {len(entries)}")
    json.dump(dict(districts=districts), open(os.path.join(out, 'manifest.json'), 'w'), ensure_ascii=False, indent=1)
    return out
