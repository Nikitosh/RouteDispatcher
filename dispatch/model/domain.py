"""Заявки, бригады и остановки маршрута."""
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import EQUIPMENT, MODE_NAMES, MODES, SHIFT, SKILL_NAMES, equipment_for

_APT = re.compile(r',?\s*(?:кв|квартира)\.?\s*(\d+)\s*$', re.I)


def work_minutes(service, speed):
    """Длительность работы бригады с темпом speed: ⌊service·speed + 0,5⌋ минут — то же в решателе
    (core/problem.hpp: Instance::computeService)."""
    return service if speed == 1 else int(math.floor(service * speed + 0.5))


def house_address(address):
    return _APT.sub('', address.strip())


def apartment(address):
    m = _APT.search(address.strip())
    return int(m[1]) if m else None


@dataclass
class Order:
    id: str
    bk: str                          # тип в BeeKeeper: «Подключение», «Глобальная проблема», ...
    type: str                        # тип для диспетчера: Подключение, Ремонт, Дозаказ, Авария
    window: tuple                    # окно начала работ из файла, минуты от 10:00 (может выходить за смену)
    service: int                     # работа на месте, мин
    priority: int
    skill: int
    district: str = ''
    address: str = ''
    hd: str = ''
    need: Optional[frozenset] = None  # допустимые виды транспорта; None — любой
    lat: float = 0.0
    lon: float = 0.0
    apt: Optional[int] = None        # номер квартиры из адреса
    gigabit: bool = False
    car_access: float = 0.0          # надбавка к поездке на машине: шлагбаум, закрытый двор
    detail: Optional[dict] = None    # разбор длительности и место в доме (model/complexity.py)
    equipment: Optional[dict] = None  # нужное оборудование {вид: штук}; None — по типу и подтипу (config.equipment_for)

    @property
    def house(self):
        """Адрес дома без квартиры: по нему ищутся координаты и данные о доме."""
        return house_address(self.address)

    @property
    def earliest(self):
        return max(0, self.window[0])

    @property
    def demand(self):
        """Нужное оборудование: {вид: штук}, только ненулевые."""
        e = equipment_for(self.bk, self.hd, self.id) if self.equipment is None else self.equipment
        return {i: e[i] for i in EQUIPMENT if e.get(i)}

    def need_text(self):
        return ' или '.join(MODE_NAMES[m] for m in MODES if m in self.need) if self.need else ''


@dataclass
class Brigade:
    name: str
    mode: str
    skills: list
    start_address: str = ''          # пусто — офис
    start: int = 0                   # индекс стартовой точки в задаче
    lat: float = 0.0
    lon: float = 0.0
    schedule: str = ''               # график: «2/2», «5/2» или пусто
    shift: tuple = (0, SHIFT)        # смена, минуты от 10:00
    lunch: bool = True               # обед, если смена от 6 часов (Problem.lunch_window); False — без обеда
    carry: Optional[int] = None      # сколько единиц оборудования унесёт (config.CARRY по транспорту); None — без предела
    stock: Optional[dict] = None     # выданное оборудование {вид: штук}; None — не задано, ограничен только carry
    level: str = ''                  # уровень из файла бригад: стажёр, инженер, старший; пусто — не указан (инженер)
    speed: float = 1.0               # темп: множитель длительности работ (config.LEVELS, «Темп», обучение по выездам)

    @property
    def shift_text(self):
        f = lambda m: f'{(600 + m) // 60}:{(600 + m) % 60:02d}'
        return f'{f(self.shift[0])}–{f(self.shift[1])}'

    @property
    def mode_name(self):
        return MODE_NAMES[self.mode]

    @property
    def skill_names(self):
        return [SKILL_NAMES[s] for s in self.skills]


@dataclass
class Stop:
    order: int                       # индекс заявки
    arrive: float
    start: float
    end: float
    km: float                        # от предыдущей точки


@dataclass
class Schedule:
    feasible: bool
    km: float
    stops: list = field(default_factory=list)
    lunch: Optional[tuple] = None    # обед: (начало, после скольких остановок); None — не нужен (в т. ч. работа кончается до 13:00)
    short: dict = field(default_factory=dict)   # чего из оборудования не хватает маршруту: {вид: штук}
    on_time: bool = True             # по времени (окна, смена, обед) и навыкам допустим, оборудование не в счёт
