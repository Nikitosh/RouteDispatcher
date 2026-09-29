import glob, sys
from PIL import Image, ImageDraw
fs = sorted(glob.glob('preview/slide-*.jpg'))
per = int(sys.argv[1]) if len(sys.argv) > 1 else 4
out = '/private/tmp/claude-501/-Users-nikitosh-Downloads-lct/02562917-2413-4ca8-aed2-1112209116b6/scratchpad/'
for k in range(0, len(fs), per):
    ims = [Image.open(f) for f in fs[k:k + per]]; w, h = ims[0].size
    g = Image.new('RGB', (w * 2 + 10, ((len(ims) + 1) // 2) * (h + 10)), 'black')
    for i, im in enumerate(ims): g.paste(im, ((i % 2) * (w + 10), (i // 2) * (h + 10)))
    g.save(out + f'qa_{k // per + 1:02d}.jpg', quality=85)
    print(out + f'qa_{k // per + 1:02d}.jpg', fs[k], fs[min(k + per, len(fs)) - 1])
