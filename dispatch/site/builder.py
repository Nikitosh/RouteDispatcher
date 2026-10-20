"""Статическая страница диспетчера. Планы становятся участками, результаты replan — событиями своего плана
(по полю event.previous). Пишет index.html, app.js, style.css, manifest.json и data/<участок>/*.json; папку можно
открыть через `python -m dispatch serve` или выложить на любой статический хостинг."""
import json
import os
import re
import shutil
import zlib

from ..formatting import hhmm

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
ASSETS = ('index.html', 'app.js', 'style.css', 'engineer.html', 'engineer.js', 'engineer.css', 'status.js', 'favicon.svg',
          'BeelineSans-Regular.woff2', 'BeelineSans-Medium.woff2', 'BeelineSans-Bold.woff2')
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
    elif e['kind'] == 'window':
        what = f"клиент перенёс окно заявки {orders[e['order']]['id']} на {hhmm(e['now'][0])}–{hhmm(e['now'][1])}"
    elif e['kind'] == 'breakdown':
        what = f"{e['name']}: сломалась машина"
    elif e['kind'] == 'finish':
        done = 'закончится' if e['end'] > e['at'] else 'закончена'      # бригада сообщает заранее или по факту
        what = f"заявка {orders[e['order']]['id']} {done} в {hhmm(e['end'])} ({e['delta']:+d} мин)"
    elif e['kind'] == 'move':
        what = f"заявка {orders[e['order']]['id']} передана: {e['name']}"
    else:
        what = f"новая заявка: {(e.get('type') or '').lower()}"
    return f"{hhmm(e['at'])} — {what}"


def demo_phones(doc):
    """Телефон клиента для кнопки «Позвонить» на экране инженера. В выгрузке организаторов телефонов нет — ставим
    демонстрационный номер с несуществующим кодом +7 000, одинаковый для заявки во всех версиях плана."""
    for o in doc.get('orders', []):
        if not o.get('phone'):
            h = zlib.crc32(str(o.get('id')).encode())
            o['phone'] = f'+7 000 {h % 1000:03d}-{h // 1000 % 100:02d}-{h // 100000 % 100:02d}'
            doc.setdefault('meta', {})['demo_phones'] = True
    return doc


def _dump(doc, path):
    json.dump(demo_phones(doc), open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))


def build_site(result_dirs, out, api=False, log=print, scenarios=None, event_button=False):
    """scenarios — сценарии страницы по группам: [{slug, title, text, steps: [{slug, title, text, dir, order?, show?}]}],
    dir — папка результата (план или событие), order — открыть карточку заявки, show — блоки панели сверху (через
    запятую: curve, price, deficit, quality, learned, exact, early, coverage). Событие относится к плану, от которого
    оно посчитано (event.previous; цепочка событий — к плану в её начале); если папки переносили — к плану участка
    с тем же названием."""
    plans, events, where = {}, [], {}
    for d in result_dirs:
        path = os.path.join(d, 'plan.json')
        if not os.path.exists(path):
            continue
        doc = json.load(open(path, encoding='utf-8'))
        if doc.get('event'):
            doc['_dir'] = os.path.abspath(d)             # папка события — для демо-страниц, в файл не идёт
            events.append(doc)
        else:
            plans[os.path.abspath(d)] = doc
    by_dir = {e['_dir']: e for e in events}

    def root(e, seen=()):
        prev = e['event'].get('previous')
        prev = prev and os.path.abspath(prev)          # относительный путь — от текущей папки, как писал replan
        if prev in plans:
            return prev
        if prev in by_dir and prev not in seen:
            return root(by_dir[prev], seen + (prev,))
        same = [d for d, doc in plans.items() if doc['meta']['name'] == e['meta']['name']]
        return same[0] if len(same) == 1 else None
    owner = {e['_dir']: root(e) for e in events}
    for e in events:
        if owner[e['_dir']] is None:
            log(f"Событие {e['_dir']}: не найден план, от которого оно посчитано, пропускаю")
    shutil.rmtree(os.path.join(out, 'data'), ignore_errors=True)    # данные прошлой сборки (лишние события) не нужны
    os.makedirs(os.path.join(out, 'data'), exist_ok=True)
    for f in ASSETS:
        shutil.copy(os.path.join(WEB, f), os.path.join(out, f))
    districts = []
    for path, doc in sorted(plans.items(), key=lambda x: x[1]['meta']['name']):
        s = base = slug(doc['meta']['name']) or 'uchastok'
        n = 1
        while any(d['slug'] == s for d in districts):  # «Восток 5 2» и «Восток — 5/2» — разные папки
            n += 1
            s = f'{base}-{n}'
        folder = os.path.join(out, 'data', s)
        os.makedirs(folder, exist_ok=True)
        _dump(doc, os.path.join(folder, 'plan.json'))
        where[path] = (len(districts), None)
        mine = sorted((e for e in events if owner.get(e.get('_dir')) == path), key=lambda e: e['event']['at'])
        entries = []
        for i, e in enumerate(mine):
            where[e.pop('_dir')] = (len(districts), i)
            e['event'].pop('previous', None)              # путь на диске странице не нужен
            _dump(e, os.path.join(folder, f'event_{i}.json'))
            entries.append(dict(label=event_label(e), plan=f'data/{s}/event_{i}.json'))
        districts.append(dict(name=doc['meta']['name'], slug=s, plan=f'data/{s}/plan.json', events=entries))
        log(f"{doc['meta']['name']}: план и событий {len(entries)}")
        if api:
            from .bundle import build_bundle
            build_bundle(doc, mine, out, log=log, name=s)
    journeys = []
    for j in scenarios or []:
        steps = []
        for st in j['steps']:
            if st.get('link'):
                steps.append(dict(st))
                continue
            at = where.get(os.path.abspath(st['dir']))
            if at is None:
                log(f"Шаг «{st['title']}» истории «{j['title']}»: нет результата {st['dir']}, пропускаю")
                continue
            steps.append(dict({k: v for k, v in st.items() if k != 'dir'}, d=districts[at[0]]['slug'], ev=at[1]))
        if steps:
            journeys.append(dict(slug=j['slug'], title=j['title'], text=j.get('text', ''), steps=steps))
    json.dump(dict(districts=districts, api=api, events=event_button, journeys=journeys), open(os.path.join(out, 'manifest.json'), 'w'),
              ensure_ascii=False, indent=1)
    return out
