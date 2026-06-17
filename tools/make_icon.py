"""ソースPNGからプレート(角丸四角)だけをきれいに切り抜き、Windows用 .ico を生成する。

白背景＋ドロップシャドウを、角丸矩形マスクで一括除去してクリーンな縁にする。
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "ChatGPT Image Jun 16, 2026, 12_50_23 AM.png")
ASSETS = os.path.join(ROOT, "assets")
ICO = os.path.join(ASSETS, "icon.ico")
PNG = os.path.join(ASSETS, "icon.png")

im = Image.open(SRC).convert("RGBA")
w, h = im.size
arr = np.array(im).astype(int)

# プレート(濃い色/シアン)= RGB合計が小さい画素。白(765)や薄い影は除外。
solid = arr[:, :, :3].sum(axis=2) < 560
ys, xs = np.where(solid)
x0, x1 = xs.min(), xs.max()
y0, y1 = ys.min(), ys.max()
# 影の縁を確実に除くため内側に少しだけ詰める
inset = max(1, int(min(x1 - x0, y1 - y0) * 0.004))
x0 += inset; y0 += inset; x1 -= inset; y1 -= inset
radius = int(min(x1 - x0, y1 - y0) * 0.17)

# 角丸矩形マスクを 4倍で描いて縮小 (アンチエイリアス)
S = 4
big = Image.new("L", (w * S, h * S), 0)
ImageDraw.Draw(big).rounded_rectangle(
    [x0 * S, y0 * S, x1 * S, y1 * S], radius=radius * S, fill=255)
mask = big.resize((w, h), Image.LANCZOS)

im.putalpha(mask)
im = im.crop((x0, y0, x1, y1))

side = max(im.size)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)

os.makedirs(ASSETS, exist_ok=True)
canvas.save(PNG)
canvas.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                        (64, 64), (128, 128), (256, 256)])
print("OK ->", ICO, "base", canvas.size, "bytes", os.path.getsize(ICO))
