"""Скриншоты страницы диспетчера и экрана инженера (site/, собирается scripts/demo.sh) для слайдов → shots/.

Запуск из корня lct: .pptx-venv/bin/python presentation/build/shots.py
Страница отдаётся локальным http.server. Кадры трёх видов: экран целиком (1440 × 900), отдельный блок страницы
(правая панель, диаграмма, карточка бригад) и экран инженера на телефоне (390 × 844). Тур по экрану в браузере
под управлением Playwright сам не открывается (navigator.webdriver), его открываем кнопкой.
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
URL = f'http://127.0.0.1:{PORT}/'
CHROME = os.path.expanduser('~/Library/Caches/ms-playwright/chromium_headless_shell-1224/'
                            'chrome-headless-shell-mac-arm64/chrome-headless-shell')


async def open_sc(pg, sc, wait=2200):
    await pg.goto(URL + 'index.html#sc=' + sc)
    await pg.reload()
    await pg.wait_for_selector('.glabel')
    await pg.wait_for_timeout(wait)                      # тайлы карты


async def save(pg, name, el=None, clip=None):
    path = os.path.join(OUT, name)
    if el == '#panel':                                   # панель — по содержимому, без пустого хвоста карточки
        await pg.evaluate("document.getElementById('panel').style.cssText = 'height:auto;max-height:none'")
    if el:
        await pg.locator(el).first.screenshot(path=path)
    else:
        await pg.screenshot(path=path, clip=clip)
    print('shot', name)


async def dispatcher(b):
    pg = await b.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=2)
    top = dict(x=0, y=0, width=1440, height=900)
    # общий вид, меню сценариев, тур
    await open_sc(pg, 'd-vostok')
    await save(pg, 'overview.png', clip=top)
    await pg.click('#brigades .bdet summary')            # транспорт, навыки и запас — в свёрнутом блоке
    await save(pg, 'brigades.png', '#brigades')
    await pg.click('#brigades .bdet summary')
    await save(pg, 'bottom.png', '.bottom')
    await pg.click('#scn-face'); await pg.wait_for_timeout(400)
    await save(pg, 'menu.png', clip=dict(x=380, y=0, width=860, height=760))
    await pg.click('#scn-face')
    await pg.click('#tour-btn'); await pg.wait_for_timeout(300)
    for _ in range(3):
        await pg.click('#tour-next')
    await pg.wait_for_timeout(500)
    await save(pg, 'tour.png', clip=top)
    await pg.keyboard.press('Escape')
    # проигрывание дня
    await pg.locator('#play-range').fill('240'); await pg.wait_for_timeout(1500)
    await save(pg, 'play.png', clip=dict(x=0, y=260, width=1440, height=640))
    # карточка заявки: длительность по дому и почему эта бригада
    await open_sc(pg, 'complexity-house')
    await save(pg, 'order.png', clip=dict(x=0, y=130, width=1440, height=770))
    await save(pg, 'order_card.png', '#panel')
    # разворот: момент события на шкале
    await open_sc(pg, 'home-turn')
    await pg.click('#tl-ev'); await pg.wait_for_timeout(1800)
    await save(pg, 'turn.png', clip=top)
    await save(pg, 'turn_panel.png', '#panel')
    # события дня — правая панель «что изменилось»
    for sc in ('home-accident', 'changes-urgent', 'changes-finish', 'changes-window', 'changes-breakdown',
               'changes-unavailable', 'changes-cancel'):
        await open_sc(pg, sc, 1500)
        await save(pg, f'ev_{sc.split("-")[1]}.png', '#panel')
        if sc == 'home-accident':
            await save(pg, 'accident.png', clip=top)
    # блоки второго этапа
    for sc, name in (('rating-stars', 'ratings'), ('rating-best', 'best'), ('complexity-lunch', 'lunch'),
                     ('complexity-learn', 'learned'), ('home-homes', 'homes'), ('staff-curve', 'curve'),
                     ('staff-price', 'price'), ('staff-deficit', 'deficit'), ('staff-shifts', 'shifts'),
                     ('staff-whatif', 'whatif')):
        await open_sc(pg, sc, 1500)
        await save(pg, f'{name}.png', '#panel .quality' if name == 'best' else '#panel')   # лучшие — сам график
        if name in ('homes', 'ratings', 'lunch', 'shifts'):
            await save(pg, f'{name}_full.png', clip=top)
    await open_sc(pg, 'complexity-lunch', 1200)
    await save(pg, 'gantt.png', '#gantt-card')
    await open_sc(pg, 'staff-shifts', 1200)
    await save(pg, 'shortage.png', '#unassigned')
    await open_sc(pg, 'staff-coverage', 2500)
    await save(pg, 'coverage.png', clip=dict(x=0, y=260, width=1440, height=640))
    await pg.close()


async def engineer(b):
    pg = await b.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=3)
    base = URL + 'engineer.html#d=vostok&b=' + 'Бригада%202'
    await pg.goto(base); await pg.wait_for_selector('.stop'); await pg.wait_for_timeout(1500)
    await save(pg, 'eng_top.png', clip=dict(x=0, y=0, width=390, height=844))
    await pg.click('#stop-1 [data-mark="arrived"]'); await pg.wait_for_timeout(300)
    await pg.locator('#stop-1').scroll_into_view_if_needed()
    await save(pg, 'eng_stop.png', '#stop-1')
    await save(pg, 'eng_marks.png', '#fact')
    await save(pg, 'eng_kit.png', '.kit')
    await pg.evaluate('localStorage.clear()')
    await pg.set_viewport_size({'width': 1280, 'height': 900})
    await pg.emulate_media(media='print'); await pg.wait_for_timeout(500)
    await save(pg, 'eng_sheet.png', '#sheet')
    await pg.emulate_media(media='screen')
    await pg.close()


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
        await dispatcher(b)
        await engineer(b)
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
