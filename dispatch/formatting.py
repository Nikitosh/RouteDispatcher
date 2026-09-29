"""Время и расстояния в тексте для диспетчера."""
from .config import DAY_START


def hhmm(minutes):
    """Минуты от 10:00 → «ЧЧ:ММ»."""
    m = round(minutes) + DAY_START
    return f'{m // 60:02d}:{m % 60:02d}'


def km_text(x):
    return f'{x:.1f}'.replace('.', ',')
