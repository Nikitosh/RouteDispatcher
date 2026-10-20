"""JSON-кэш на диске с атомарной записью. DISPATCH_READONLY=1 — не записывать (сервер на Vercel: диск только для чтения).

Перед записью файл перечитывается и к нему добавляются только свои новые ключи: другой процесс или другой экземпляр в
этом же процессе мог дописать кэш, пока этот работал, и его пары не должны пропасть. Между перечитыванием и заменой
файла остаётся короткое окно: два процесса, записавшие в один момент, могут потерять пары друг друга (их посчитают
заново при следующем запуске). Временный файл свой у каждого процесса и потока."""
import gzip
import json
import os
import threading

READONLY = os.environ.get('DISPATCH_READONLY') == '1'


class JsonStore:
    def __init__(self, path, readonly=False):
        self.path = path
        self.readonly = readonly                 # только читать: например, пакеты данных сервера пересчёта
        self.data = load_json(path) if os.path.exists(path) else {}
        self.added = {}

    def __contains__(self, key):
        return key in self.data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def put(self, key, value):
        self.data[key] = value
        self.added[key] = value

    @property
    def dirty(self):
        return bool(self.added)

    def save(self):
        if not self.added or READONLY or self.readonly:
            return
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        if os.path.exists(self.path):
            self.data = {**self.data, **load_json(self.path), **self.added}
        tmp = f'{self.path}.{os.getpid()}.{threading.get_ident()}.tmp'
        with (gzip.open(tmp, 'wt', encoding='utf-8') if self.path.endswith('.gz') else open(tmp, 'w', encoding='utf-8')) as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self.added = {}


def load_json(path):
    """JSON с диска; .gz — сжатый (пакеты данных сервера, site/bundle.py)."""
    with (gzip.open(path, 'rt', encoding='utf-8') if path.endswith('.gz') else open(path, encoding='utf-8')) as f:
        return json.load(f)
