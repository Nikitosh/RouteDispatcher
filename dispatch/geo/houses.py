"""Данные о домах заявок: cache/houses.json, ключ — адрес дома без квартиры, как в файлах заявок.

Собирает файл python -m dispatch houses (tools/houses.py). Если файла нет или дома в нём нет, длительность считается
по нормативу и подтипу работы, без поправок на дом.
"""
import os

from ..config import CACHE_DIR
from ..model.domain import house_address
from .store import load_json

PATH = os.path.join(CACHE_DIR, 'houses.json')


class HouseIndex:
    def __init__(self, path=PATH):
        self.path = path
        self.data = load_json(path) if os.path.exists(path) else {}

    def get(self, address):
        return self.data.get(house_address(address)) if address else None

    def __len__(self):
        return len(self.data)
