"""Обязательные слайды шаблона ЛЦТ (7–11) отдельным файлом: presentation_build/out/mandatory.pptx."""
import os
import sys

from pptx import Presentation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kit import drop_slides  # noqa: E402
from mandatory import fill_mandatory  # noqa: E402

prs = Presentation(os.path.join(HERE, '..', 'templates', 'шаблон ЛЦТ.pptx'))
fill_mandatory(prs)
drop_slides(prs, [6, 7, 8, 9, 10])
out = os.path.join(HERE, 'out', 'mandatory.pptx')
os.makedirs(os.path.dirname(out), exist_ok=True)
prs.save(out)
print('saved', out)
