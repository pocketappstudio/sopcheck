"""Render a text SOP into an IMAGE-ONLY PDF (no text layer) to simulate a scanned document.
Usage: python make_scanned_pdf.py input.txt output.pdf"""
import random
import sys
import textwrap
from PIL import Image, ImageDraw, ImageFilter, ImageFont

src, dst = sys.argv[1], sys.argv[2]
random.seed(7)
DPI, W, H, M = 200, 1700, 2200, 130            # US Letter @ 200 dpi
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
lines = []
for raw in open(src, encoding="utf-8").read().splitlines():
    wrapped = textwrap.wrap(raw, 78) or [""]
    lines += [(w, raw.isupper() or raw[:2].strip().isdigit() and raw.split(" ", 1)[1:2] and raw.split(" ", 1)[1].isupper()) for w in wrapped]
pages, y, page = [], M, None
for text, is_head in lines:
    if page is None or y > H - M:
        page = Image.new("L", (W, H), 250); pages.append(page); y = M
    ImageDraw.Draw(page).text((M, y), text, fill=25, font=bold if is_head else font)
    y += 48
out = []
for p in pages:
    noise = Image.effect_noise((W, H), 18).point(lambda v: 255 if v > 120 else 230)
    p = Image.blend(p.convert("L"), noise, 0.08)                      # paper speckle
    p = p.rotate(0.6, resample=Image.BICUBIC, fillcolor=250)           # slight skew like a scan
    p = p.filter(ImageFilter.GaussianBlur(0.6))
    out.append(p.convert("RGB"))
out[0].save(dst, "PDF", resolution=DPI, save_all=True, append_images=out[1:])
print(f"wrote {dst}: {len(out)} page(s), image-only")
