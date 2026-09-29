"""Константы модели и пути. Время везде — минуты от начала смены (10:00)."""
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get('DISPATCH_CACHE', os.path.join(ROOT, 'cache'))
SOLVER_BIN = os.environ.get('DISPATCH_SOLVER_BIN', os.path.join(ROOT, 'solver', 'bin'))
DATA_DIR = os.path.join(ROOT, 'data')

DAY_START = 600          # 10:00 в минутах от полуночи
SHIFT = 720              # смена 10:00–22:00

# Работа на месте по нормативам без 20 минут «дороги» — дорогу считаем по картам.
SERVICE = {'Подключение': 70, 'Глобальная проблема': 80, 'Локальная заявка': 30, 'Дозаказ': 20}
PRIORITY = {'Глобальная проблема': 1, 'Подключение': 2, 'Локальная заявка': 3, 'Дозаказ': 3}
PENALTY = {1: 100, 2: 50, 3: 20}                     # штраф за невыполненную заявку по приоритету
SKILL = {'Локальная заявка': 0, 'Подключение': 1, 'Дозаказ': 1, 'Глобальная проблема': 2}
SKILL_NAMES = ['Ремонт', 'Подключение и дозаказ', 'Аварии']
TYPE_NAMES = {'Локальная заявка': 'Ремонт', 'Подключение': 'Подключение', 'Дозаказ': 'Дозаказ', 'Глобальная проблема': 'Авария'}

MODES = ['car', 'pt', 'bike', 'foot']                 # порядок совпадает с форматом задачи решателя
MODE_NAMES = {'car': 'Авто', 'pt': 'Общ. транспорт', 'bike': 'Велосипед', 'foot': 'Пешком'}


@dataclass
class TravelSettings:
    """Время в пути = OSRM × коэффициент пробок + подход к заявке (парковка, дверь, домофон, лифт).
    Коэффициент 1,3 для машины сверен вручную по Яндекс Картам."""
    traffic: dict = field(default_factory=lambda: {'car': 1.3})
    overhead: dict = field(default_factory=lambda: {'car': 5.0, 'pt': 3.0, 'bike': 3.0, 'foot': 3.0})


@dataclass
class ReplanSettings:
    """Цены при перестройке плана после аварии, в «км-эквиваленте»."""
    react: float = 120.0         # авария должна начаться не позже чем через 2 часа после появления
    per_minute: float = 1.0      # минута ожидания аварии
    per_change: float = 15.0     # перенос одной заявки к другой бригаде
    open_reserve: float = 60.0   # вывод резервной бригады
