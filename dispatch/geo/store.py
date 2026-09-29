"""JSON-кэш на диске с атомарной записью."""
import json
import os


class JsonStore:
    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path)) if os.path.exists(path) else {}
        self.dirty = False

    def __contains__(self, key):
        return key in self.data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def put(self, key, value):
        self.data[key] = value
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = self.path + '.tmp'
        json.dump(self.data, open(tmp, 'w'), ensure_ascii=False)
        os.replace(tmp, self.path)
        self.dirty = False
