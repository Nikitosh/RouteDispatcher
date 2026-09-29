"""Собрать финальный PDF: обязательные слайды ЛЦТ + основная часть в стиле «Билайна».

Полный цикл из корня lct:
    scripts/demo.sh --no-serve                                       # планы, события и страница site/
    .pptx-venv/bin/python presentation/build/shots.py                # скриншоты страницы → shots/ (сервер поднимает сам)
    research/.venv/bin/python presentation/build/figures.py       # карты и шкала → fig/
    research/.venv/bin/python presentation/build/figures_algo.py  # схемы алгоритма → fig/
    .pptx-venv/bin/python presentation/build/build_mandatory.py      # слайды 7–11 шаблона ЛЦТ → out/mandatory.pptx
    .pptx-venv/bin/python presentation/build/build_bee.py            # основная часть → out/main.pptx
    .pptx-venv/bin/python presentation/build/assemble.py             # → presentation/presentation_trdelnik.pdf
Шаблон ЛЦТ — 13,33 × 7,5 дюйма, шаблон «Билайна» — 26,67 × 15; страницы ЛЦТ масштабируем ×2.
"""
import os
import shutil
import subprocess

from pypdf import PdfReader, PdfWriter, Transformation

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
PROFILE = 'file://' + os.path.join(HERE, 'lo_profile')   # в профиле лежат Montserrat и Beeline Sans


def render(pptx):
    subprocess.run(['soffice', f'-env:UserInstallation={PROFILE}', '--headless', '--convert-to', 'pdf',
                    '--outdir', OUT, pptx], check=True, capture_output=True)
    return pptx[:-5] + '.pdf'


w = PdfWriter()
for pdf in [render(os.path.join(OUT, 'mandatory.pptx')), render(os.path.join(OUT, 'main.pptx'))]:
    for page in PdfReader(pdf).pages:
        k = 1920 / float(page.mediabox.width)
        if abs(k - 1) > 1e-3:
            page.add_transformation(Transformation().scale(k, k))
            page.mediabox.upper_right = (float(page.mediabox.width) * k, float(page.mediabox.height) * k)
        w.add_page(page)
dst = os.path.join(HERE, '..', 'presentation_trdelnik.pdf')
with open(dst, 'wb') as f:
    w.write(f)
print('saved', os.path.abspath(dst), len(w.pages), 'pages')
for src, name in [('mandatory.pptx', 'presentation_trdelnik_1_обязательные.pptx'),
                  ('main.pptx', 'presentation_trdelnik_2_основная.pptx')]:
    shutil.copy(os.path.join(OUT, src), os.path.join(HERE, '..', name))
