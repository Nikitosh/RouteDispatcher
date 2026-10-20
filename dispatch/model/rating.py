"""Рейтинг монтажника по отзывам клиентов.

Отзыв — две оценки 1–5: «качество работы» и «приехали вовремя». В рейтинг монтажника идёт только качество работы,
и только его собственной: оценку получает тот, кто фактически выполнил заявку. Пунктуальность считается отдельно и
без визитов, где опоздание вызвал план (авария, перестройка дня), — за это отвечает диспетчер, а не монтажник.

Рейтинг по виду работ — байесовское среднее с затуханием:
    R = (PRIOR · μ + Σ wᵢ·rᵢ) / (PRIOR + Σ wᵢ),   wᵢ = 0,5^(возраст / HALF_LIFE),
μ — среднее по всем монтажникам участка. Новичок с одной пятёркой не обгоняет опытного, одна случайная двойка не
топит хорошего, свежие оценки весят больше старых.
"""
import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field

PRIOR = 10            # сила априорного среднего, в отзывах
HALF_LIFE = 60        # дней: вес оценки падает вдвое
FEW = 8               # меньше отзывов — «мало данных»
KINDS = ('Ремонт', 'Подключение', 'Авария')          # по навыкам: 0, 1, 2 (дозаказ — вместе с подключением)


@dataclass
class Review:
    age: int                      # дней назад
    brigade: str                  # кто фактически выполнил заявку
    skill: int                    # 0 ремонт, 1 подключение и дозаказ, 2 авария
    work: int = None              # «качество работы», None — клиент не оценил
    on_time: int = None           # «приехали вовремя»
    plan_late: bool = False       # опоздание вызвано планом: в пунктуальность монтажника не идёт


@dataclass
class Rating:
    value: float
    reviews: int
    few: bool


@dataclass
class EngineerRating:
    by_skill: dict = field(default_factory=dict)      # навык → Rating
    on_time: float = None
    on_time_reviews: int = 0

    def of(self, skill):
        return self.by_skill.get(skill)

    def overall(self, mix):
        """Средний рейтинг по видам работ дня с весами mix {навык: доля заявок}; только навыки монтажника."""
        parts = [(w, self.by_skill[s].value) for s, w in mix.items() if s in self.by_skill and w > 0]
        return sum(w * r for w, r in parts) / sum(w for w, _ in parts) if parts else 0.0


def _weight(age):
    return 0.5 ** (age / HALF_LIFE)


def compute(reviews, skills):
    """{монтажник: EngineerRating}; skills — {монтажник: его навыки} (рейтинг только по ним)."""
    scores = defaultdict(list)                        # (монтажник, навык) → [(вес, оценка)]
    times = defaultdict(list)
    for r in reviews:
        w = _weight(r.age)
        if r.work is not None:
            scores[r.brigade, r.skill].append((w, r.work))
        if r.on_time is not None and not r.plan_late:
            times[r.brigade].append((w, r.on_time))
    prior = {}
    for s in range(len(KINDS)):
        xs = [x for (b, sk), v in scores.items() if sk == s for _, x in v]
        prior[s] = sum(xs) / len(xs) if xs else 4.5
    out = {}
    for b, own in skills.items():
        e = EngineerRating()
        for s in own:
            v = scores.get((b, s), [])
            sw = sum(w for w, _ in v)
            e.by_skill[s] = Rating(round((PRIOR * prior[s] + sum(w * x for w, x in v)) / (PRIOR + sw), 2), len(v), len(v) < FEW)
        t = times.get(b, [])
        if t:
            e.on_time = round(sum(w * x for w, x in t) / sum(w for w, _ in t), 2)
            e.on_time_reviews = len(t)
        out[b] = e
    return out


def naive(reviews):
    """Простое среднее всех оценок монтажника по навыку, включая «вовремя», — для сравнения с compute."""
    acc = defaultdict(list)
    for r in reviews:
        acc[r.brigade, r.skill] += [x for x in (r.work, r.on_time) if x is not None]
    return {k: sum(v) / len(v) for k, v in acc.items() if v}


HEADER = ['Дней назад', 'Заявка', 'Монтажник', 'Вид работ', 'Оценка работы', 'Оценка «вовремя»', 'Опоздание по вине плана']


def read(path):
    rows = list(csv.reader(io.StringIO(open(path, encoding='utf-8').read()), delimiter=';'))
    out = []
    for r in rows[1:]:
        num = lambda x: int(x) if x.strip() else None
        out.append(Review(age=int(r[0]), brigade=r[2], skill=KINDS.index(r[3]), work=num(r[4]), on_time=num(r[5]),
                          plan_late=r[6].strip().lower() == 'да'))
    return out


def write(path, rows):
    """rows — [(Review, номер заявки)]."""
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(HEADER)
        for r, oid in rows:
            w.writerow([r.age, oid, r.brigade, KINDS[r.skill], '' if r.work is None else r.work,
                        '' if r.on_time is None else r.on_time, 'да' if r.plan_late else ''])
