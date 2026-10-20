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
if not os.path.exists(CHROME):                           # любая установленная версия headless shell
    import glob
    CHROME = (sorted(glob.glob(os.path.expanduser('~/Library/Caches/ms-playwright/chromium_headless_shell-*/'
                                                  'chrome-headless-shell-mac-arm64/chrome-headless-shell'))) or [CHROME])[-1]


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


# ── экран инженера в рамке телефона и маршрутный лист на листе бумаги (eng_phone_N.png, eng_sheet_paper.png) ──
FONT = os.path.join(HERE, '..', 'templates', 'beeline', 'Шрифт', 'beeline sans-2', 'BeelineSans-Bold.ttf')
SS = 3                                                    # пиксели на CSS px (deviceScaleFactor)
STATUS_H, HOME_H = 54, 26                                 # строка состояния и полоска «домой», CSS px
PHONE_W, PHONE_H = 390, 844


def _mask(size, box, r, ss=4):
    """Сглаженная маска скруглённого прямоугольника."""
    from PIL import Image, ImageDraw
    m = Image.new('L', (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([v * ss for v in box], r * ss, fill=255)
    return m.resize(size, Image.LANCZOS)


def _shadow(alpha, blur, opacity, dy):
    from PIL import Image, ImageFilter
    sh = Image.new('RGBA', alpha.size, (20, 20, 20, 0))
    a = alpha.point(lambda v: int(v * opacity)).filter(ImageFilter.GaussianBlur(blur))
    sh.putalpha(Image.new('L', alpha.size, 0)); sh.putalpha(a)
    out = Image.new('RGBA', alpha.size, (0, 0, 0, 0))
    out.paste(sh, (0, dy), sh)
    return out


def _status_bar(w, clock, bg):
    """Строка состояния iOS: время, сеть, Wi-Fi, батарея; посередине Dynamic Island."""
    from PIL import Image, ImageDraw, ImageFont
    k = SS
    im = Image.new('RGB', (w, STATUS_H * k), bg)
    d = ImageDraw.Draw(im)
    ink = (20, 20, 20)
    f = ImageFont.truetype(FONT, 17 * k)
    tw = d.textlength(clock, font=f)
    d.text((62 * k - tw / 2, 31 * k), clock, font=f, fill=ink, anchor='lm')
    x, y = 294 * k, 31 * k                                # сеть: четыре столбика
    for i, hgt in enumerate((4, 6.5, 9, 11.5)):
        d.rounded_rectangle([x + i * 5 * k, y + (5.5 - hgt) * k, x + i * 5 * k + 3.2 * k, y + 5.5 * k], k, fill=ink)
    cx, cy = 325 * k, 36.5 * k                            # Wi-Fi: три дуги
    for rr, wd in ((12, 2.6), (7.8, 2.6)):
        d.arc([cx - rr * k, cy - rr * k, cx + rr * k, cy + rr * k], 225, 315, fill=ink, width=int(wd * k))
    d.pieslice([cx - 3.6 * k, cy - 3.6 * k, cx + 3.6 * k, cy + 3.6 * k], 225, 315, fill=ink)
    bx, by = 343 * k, 25.5 * k                            # батарея
    d.rounded_rectangle([bx, by, bx + 25 * k, by + 12 * k], 3.8 * k, outline=(150, 150, 150), width=int(1.1 * k))
    d.rounded_rectangle([bx + 2 * k, by + 2 * k, bx + 19 * k, by + 10 * k], 2 * k, fill=ink)
    d.rounded_rectangle([bx + 26.3 * k, by + 4 * k, bx + 27.8 * k, by + 8 * k], k, fill=(150, 150, 150))
    return im


def phone_frame(screen, clock, bottom_bg):
    """Кадр 390 × (844 − строка состояния − полоска) → PNG телефона с рамкой и тенью на прозрачном фоне."""
    from PIL import Image, ImageDraw
    k = SS
    W, H = PHONE_W * k, PHONE_H * k
    scr = Image.new('RGB', (W, H), (255, 255, 255))
    top_bg = screen.getpixel((4, 4))[:3]
    scr.paste(_status_bar(W, clock, top_bg), (0, 0))
    scr.paste(screen.convert('RGB'), (0, STATUS_H * k))
    dh = ImageDraw.Draw(scr)
    dh.rectangle([0, H - HOME_H * k, W, H], fill=bottom_bg)
    dh.rounded_rectangle([W / 2 - 67 * k, H - 13 * k, W / 2 + 67 * k, H - 8 * k], 3 * k, fill=(20, 20, 20))
    bez, rim, rs, pad = 13 * k, 3 * k, 55 * k, 70 * k     # рамка, кант, радиус экрана, поле под тень
    fw, fh = W + 2 * (bez + rim), H + 2 * (bez + rim)
    cw, ch = fw + 2 * pad, fh + 2 * pad
    ox, oy = pad, pad
    body = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    for side, ys in ((-1, ((120, 152), (178, 238), (252, 312))), (1, ((200, 300),))):   # кнопки по бокам
        for y0, y1 in ys:
            x0 = ox - 2.5 * k if side < 0 else ox + fw - 1 * k
            bm = _mask((cw, ch), [x0, oy + y0 * k, x0 + 3.5 * k, oy + y1 * k], 1.5 * k)
            body.paste((58, 58, 62, 255), (0, 0), bm)
    R = rs + bez + rim
    body.paste((72, 72, 78, 255), (0, 0), _mask((cw, ch), [ox, oy, ox + fw, oy + fh], R))          # кант
    body.paste((30, 30, 33, 255), (0, 0), _mask((cw, ch), [ox + k, oy + k, ox + fw - k, oy + fh - k], R - k))
    body.paste((8, 8, 8, 255), (0, 0), _mask((cw, ch), [ox + rim, oy + rim, ox + fw - rim, oy + fh - rim], R - rim))
    sx, sy = ox + rim + bez, oy + rim + bez
    body.paste(scr, (sx, sy), _mask((cw, ch), [sx, sy, sx + W, sy + H], rs).crop((sx, sy, sx + W, sy + H)))
    isl = [cw / 2 - 62 * k, sy + 11 * k, cw / 2 + 62 * k, sy + 48 * k]                          # Dynamic Island
    body.paste((0, 0, 0, 255), (0, 0), _mask((cw, ch), isl, 18.5 * k))
    alpha = _mask((cw, ch), [ox, oy, ox + fw, oy + fh], R)
    out = Image.alpha_composite(_shadow(alpha, 24 * k, 0.42, 16 * k), body)
    return out


async def _phone_shot(pg, name, scroll_to=None, clock=None):
    from PIL import Image
    import io
    if scroll_to:
        await pg.evaluate("""s => { const el = document.querySelector(s), hb = document.querySelector('.ebar');
            window.scrollTo(0, el.getBoundingClientRect().top + scrollY - hb.offsetHeight - 12); }""", scroll_to)
    await pg.wait_for_timeout(500)
    for _ in range(40):                                   # карта: ждём, пока догрузятся все тайлы
        if await pg.evaluate("document.querySelectorAll('.leaflet-tile:not(.leaflet-tile-loaded)').length") == 0:
            break
        await pg.wait_for_timeout(250)
    await pg.wait_for_timeout(400)
    clock = clock or await pg.evaluate(CLOCK_JS)
    shot = Image.open(io.BytesIO(await pg.screenshot()))
    phone_frame(shot, clock, shot.getpixel((6, shot.height - 3))[:3])   # полоска «домой» — цветом низа экрана.save(os.path.join(OUT, name), optimize=True)
    print('phone', name, clock)


CLOCK_JS = "(document.getElementById('clock') || {}).value || '10:00'"   # время демо-часов на экране


async def engineer_phone(b):
    """Четыре состояния экрана инженера на телефоне: начало дня, на адресе, что взять утром, отметки."""
    vp = {'width': PHONE_W, 'height': PHONE_H - STATUS_H - HOME_H}
    ctx = await b.new_context(viewport=vp, device_scale_factor=SS, is_mobile=True, has_touch=True)
    pg = await ctx.new_page()
    await pg.goto(URL + 'engineer.html'); await pg.evaluate('localStorage.clear()')
    await pg.goto(URL + 'engineer.html#d=vostok&b=' + 'Бригада%202'); await pg.reload()
    await pg.wait_for_selector('.stop')
    await pg.add_style_tag(content='::-webkit-scrollbar{display:none}html{scrollbar-width:none}')
    await pg.wait_for_timeout(1500)
    await _phone_shot(pg, 'eng_phone_1.png', None, '10:04')                            # начало дня, «Дальше · Приехал»
    await _phone_shot(pg, 'eng_phone_2.png', '#kit', '09:52')                    # что взять утром и карта
    t_arr = await pg.evaluate(CLOCK_JS)
    await pg.click('#next-btn'); await pg.wait_for_timeout(400)           # приехал на первый адрес
    await _phone_shot(pg, 'eng_phone_3.png', '.ehead-out', t_arr)                # карточка адреса, «Закончил»
    await pg.click('#next-btn'); await pg.wait_for_timeout(400)           # закончил первый
    await pg.evaluate("""() => { const c = document.getElementById('clock'), [h, m] = c.value.split(':').map(Number),
        t = h * 60 + m + 17; c.value = String(t / 60 | 0).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0');
        c.dispatchEvent(new Event('change')); }""")                     # на второй адрес — на 17 минут позже плана
    await pg.wait_for_timeout(300)
    t_arr = await pg.evaluate(CLOCK_JS)
    await pg.click('#next-btn'); await pg.wait_for_timeout(400)           # приехал на второй
    await _phone_shot(pg, 'eng_phone_4.png', '#fact', t_arr)                     # отметки и выполненный адрес
    await pg.evaluate('localStorage.clear()')
    # маршрутный лист: печать в PDF A4, растр 300 dpi, лист бумаги с тенью
    await pg.set_viewport_size({'width': 1280, 'height': 900}); await pg.reload(); await pg.wait_for_selector('.stop')
    await pg.wait_for_timeout(800)
    pdf = os.path.join(OUT, '_sheet.pdf')
    await pg.pdf(path=pdf, prefer_css_page_size=True, print_background=True)
    await ctx.close()
    paper_sheet(pdf)
    os.remove(pdf)


def paper_sheet(pdf, dpi=300):
    """Страницы PDF → лист A4 (и край второго листа под ним) с мягкой тенью на прозрачном фоне."""
    from PIL import Image, ImageDraw
    import glob
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(['pdftoppm', '-r', str(dpi), '-png', pdf, os.path.join(tmp, 'p')], check=True)
        pages = [Image.open(f).convert('RGB') for f in sorted(glob.glob(os.path.join(tmp, 'p*.png')))]
    pw, ph = pages[0].size
    pad = int(pw * 0.09)
    cw, ch = pw + 2 * pad, ph + 2 * pad
    canvas = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))

    def sheet(img, angle, dx, dy):
        lay = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
        lay.paste(img.convert('RGBA'), (pad + dx, pad + dy))
        ImageDraw.Draw(lay).rectangle([pad + dx, pad + dy, pad + dx + pw - 1, pad + dy + ph - 1],
                                      outline=(222, 222, 222, 255), width=max(2, pw // 900))   # край листа
        a = Image.new('L', (cw, ch), 0); a.paste(255, (pad + dx, pad + dy, pad + dx + pw, pad + dy + ph))
        if angle:
            lay = lay.rotate(angle, Image.BICUBIC, center=(cw / 2, ch / 2))
            a = a.rotate(angle, Image.BICUBIC, center=(cw / 2, ch / 2))
        lay.putalpha(a)
        sh = Image.alpha_composite(_shadow(a, pw * 0.022, 0.45, int(pw * 0.016)), _shadow(a, pw * 0.004, 0.3, 3))
        return Image.alpha_composite(sh, lay)
    under = pages[1] if len(pages) > 1 else Image.new('RGB', (pw, ph), (250, 250, 250))
    canvas = Image.alpha_composite(canvas, sheet(under, -2.6, int(pw * 0.012), int(pw * 0.008)))   # лист под ним
    canvas = Image.alpha_composite(canvas, sheet(pages[0], 0, 0, 0))
    canvas = canvas.crop(canvas.getbbox())
    canvas.save(os.path.join(OUT, 'eng_sheet_paper.png'), optimize=True)
    print('paper eng_sheet_paper.png', canvas.size, 'pages', len(pages))
    # верх листа крупно: шапка, что взять, обзвон и первые адреса; низ растворяется
    top = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
    top = Image.alpha_composite(top, sheet(pages[0], 0, 0, 0))
    cut = pad + int(ph * 0.46)
    top = top.crop((0, 0, cw, cut))
    fade = int(ph * 0.08)
    a = top.getchannel('A')
    ramp = Image.linear_gradient('L').resize((cw, fade)).point(lambda v: 255 - v)
    a.paste(Image.composite(Image.new('L', (cw, fade), 0), a.crop((0, cut - fade, cw, cut)),
                            Image.eval(ramp, lambda v: 255 - v)), (0, cut - fade))
    top.putalpha(a)
    top = top.crop(top.getbbox())
    top.save(os.path.join(OUT, 'eng_sheet_paper_top.png'), optimize=True)
    print('paper eng_sheet_paper_top.png', top.size)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
        only = sys.argv[1:]                              # shots.py phone — только телефон и лист
        if not only:
            await dispatcher(b)
            await engineer(b)
        await engineer_phone(b)
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
