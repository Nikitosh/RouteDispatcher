"""Где лежат файлы организаторов: data/organizers/, а для старой раскладки — корень проекта."""
import glob
import os

from ..config import DATA_DIR, ROOT


def organizers_file(region, kind):
    """kind: «Синтетические» или «Контрольное»."""
    for base in (os.path.join(DATA_DIR, 'organizers'), ROOT):
        hits = sorted(glob.glob(os.path.join(base, f'{region} {kind}*.csv')))
        if hits:
            return hits[0]
    raise FileNotFoundError(f'нет файла организаторов «{region} {kind}…csv» в data/organizers')
