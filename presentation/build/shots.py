"""Скриншоты страницы диспетчера (site/, собирается scripts/demo.sh) для демо-слайдов → shots/.

Запуск из корня lct: .pptx-venv/bin/python presentation/build/shots.py
Страница отдаётся локальным http.server; кадры в пропорции места под картинку на слайде (9,1 × 5,35).
"""
import asyncio
import os
import subprocess
import sys
import time

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, '..', '..', 'site')
OUT = os.path.join(HERE, 'shots')
PORT = 8790
RATIO = 9.1 / 5.35
WIDTH = 1320
COMPARE_WIDTH = 1400
# Браузер из кэша ms-playwright, если он там есть (на машине автора версия пакета новее кэша); иначе — установленный
# командой playwright install chromium.
CHROME = os.path.expanduser('~/Library/Caches/ms-playwright/chromium_headless_shell-1224/'
                            'chrome-headless-shell-mac-arm64/chrome-headless-shell')
EXPLAIN_ORDER = '74198'          # подключение на Востоке: объяснение с тремя группами других бригад
EVENT = 'Бригада 4 недоступна'


async def top_of(pg, selector):
    return await pg.evaluate(f"document.querySelector('{selector}').getBoundingClientRect().top + scrollY")


async def shot(pg, name, y0, width=WIDTH):
    await pg.wait_for_timeout(2500)                 # тайлы карты
    await pg.screenshot(path=os.path.join(OUT, name), clip=dict(x=0, y=y0, width=width, height=width / RATIO), full_page=True)
    print('shot', name)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
        pg = await b.new_page(viewport={'width': WIDTH, 'height': 900}, device_scale_factor=2)
        await pg.goto(f'http://127.0.0.1:{PORT}/#d=vostok&tab=plan', wait_until='networkidle')
        await shot(pg, 'plan.png', 0)
        await pg.set_viewport_size({'width': COMPARE_WIDTH, 'height': 900})   # шкала и нижние карточки целиком
        await pg.evaluate("""() => {                        // список базового варианта раскрыт и обрезан по соседней карточке
            document.querySelector('#unassigned details').open = true;
            const u = document.getElementById('unassigned');
            u.style.maxHeight = document.getElementById('compare').offsetHeight + 'px'; u.style.overflow = 'hidden';
        }""")
        await shot(pg, 'compare.png', await top_of(pg, '#gantt-card') - 10, COMPARE_WIDTH)
        await pg.set_viewport_size({'width': WIDTH, 'height': 900})
        k = await pg.evaluate(f"D.orders.findIndex(o => o.id === '{EXPLAIN_ORDER}')")
        await pg.evaluate(f'select({k})')
        await shot(pg, 'explain.png', 0)
        j = await pg.evaluate(f"MAN.districts[0].events.findIndex(e => e.label.includes('{EVENT}'))")
        await pg.evaluate(f"document.getElementById('event').value = {j}; loadEvent({j}).then(fitAll)")
        await shot(pg, 'event.png', 0)
        await b.close()


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    server = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT), '-d', SITE],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        asyncio.run(main())
    finally:
        server.terminate()
