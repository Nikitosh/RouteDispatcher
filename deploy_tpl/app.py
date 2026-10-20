"""Точка входа Vercel: страница диспетчера и пересчёт плана (dispatch/api.py). Собирает scripts/build_vercel.sh."""
import os
import shutil
import stat

ROOT = os.path.dirname(os.path.abspath(__file__))
BIN = '/tmp/dispatch-bin'                     # бинарники решателя — в /tmp с правом запуска (файлы функции только для чтения)
if not os.path.isdir(BIN):
    shutil.copytree(os.path.join(ROOT, 'bin'), BIN)
    for f in os.listdir(BIN):
        os.chmod(os.path.join(BIN, f), stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
os.environ.setdefault('DISPATCH_SOLVER_BIN', BIN)
os.environ.setdefault('DISPATCH_READONLY', '1')
os.environ.setdefault('DISPATCH_CACHE', '/tmp/dispatch-cache')

from dispatch.api import create_app  # noqa: E402

app = create_app(os.path.join(ROOT, 'public'))
