"""Заявки, бригады и остановки маршрута."""
from dataclasses import dataclass, field
from typing import Optional

from ..config import MODE_NAMES, MODES, SKILL_NAMES


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

    @property
    def earliest(self):
        return max(0, self.window[0])

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
