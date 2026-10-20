"""Общее для тестов: сеть общественного транспорта и ответы Яндекс Расписаний — только из папок теста; к API Яндекса
тесты не обращаются никогда (квота ключа 500 запросов в сутки)."""
import pytest

from dispatch.tools import rasp, transit


@pytest.fixture(autouse=True)
def isolated_transit(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp('transit')
    monkeypatch.setattr(transit, 'DIR', str(root))
    monkeypatch.setattr(rasp, 'DIR', str(root / 'yandex'))

    def no_network(*a, **k):
        raise AssertionError('тест обратился к API Яндекс Расписаний')
    monkeypatch.setattr(rasp.urllib.request, 'urlopen', no_network)
    monkeypatch.setenv('DISPATCH_HERE_GRID', 'off')             # в тестах — районы: цифры не зависят от файла сетки HERE
    return root
