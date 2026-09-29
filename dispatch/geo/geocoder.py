"""Адреса → координаты: кэш cache/geocode.json, новые адреса — Nominatim (не чаще раза в секунду)."""
import json
import os
import re
import time
import urllib.parse
import urllib.request

from ..config import CACHE_DIR
from .store import JsonStore

_COORDS = re.compile(r'\s*(-?\d+\.\d+)\s*[,; ]\s*(-?\d+\.\d+)\s*')


class GeocodeError(Exception):
    pass


def _query(address):
    """Адрес в формате организаторов → строка для Nominatim."""
    q = re.sub(r'^(г\.)?Город Москва', 'Москва', address)
    for short, full in (('ул.', 'улица '), ('пр-кт.', 'проспект '), ('б-р.', 'бульвар '), ('д. ', '')):
        q = q.replace(short, full)
    return q


class Geocoder:
    def __init__(self, path=None, online=True):
        self.store = JsonStore(path or os.path.join(CACHE_DIR, 'geocode.json'))
        self.online = online

    def locate(self, addresses, log=print):
        """{адрес: (lat, lon)}; строки «55.7, 37.6» считаются готовыми координатами."""
        out, missing = {}, []
        for a in dict.fromkeys(addresses):
            m = _COORDS.fullmatch(a)
            if m:
                out[a] = (float(m[1]), float(m[2]))
            elif a in self.store:
                out[a] = (self.store.get(a)['lat'], self.store.get(a)['lon'])
            else:
                missing.append(a)
        if missing and not self.online:
            raise GeocodeError('Нет координат (геокодинг выключен):\n  ' + '\n  '.join(missing))
        failed = []
        if missing:
            log(f'Геокодинг: {len(missing)} новых адресов через Nominatim')
        for a in missing:
            hit = self._nominatim(a)
            if hit:
                self.store.put(a, dict(lat=float(hit['lat']), lon=float(hit['lon']), name=hit['display_name'], level='nominatim'))
                out[a] = (float(hit['lat']), float(hit['lon']))
            else:
                failed.append(a)
        self.store.save()
        if failed:
            raise GeocodeError('Не нашёл координаты (укажите их как «широта, долгота»):\n  ' + '\n  '.join(failed))
        return out

    def remember(self, address, lat, lon, source):
        self.store.put(address, dict(lat=lat, lon=lon, name=f'{source} {address}', level=source))

    def save(self):
        self.store.save()

    @staticmethod
    def _nominatim(address):
        url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(
            {'q': _query(address), 'format': 'json', 'limit': 1, 'countrycodes': 'ru'})
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'dispatch/1.0'}), timeout=20))
        except OSError:
            r = []
        time.sleep(1.1)
        return r[0] if r else None
