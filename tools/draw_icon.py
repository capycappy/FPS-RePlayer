"""タスクバーで視認しやすい明るいアイコンを生成する (ダーク青プレート+大きなシアン照準)。"""
import os

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
os.makedirs(ASSETS, exist_ok=True)

S = 2048                      # スーパーサンプリング (最後に1024へ縮小)
m = int(S * 0.05)             # プレート余白 (小さめ=大きく見える)
R = int(S * 0.20)             # 角丸半径
CYAN = (54, 214, 248, 255)
CYAN_SOFT = (120, 232, 255, 255)

# --- 青系グラデーションのプレート -----------------------------------
top = np.array([38, 102, 140])     # 明るめの青
bot = np.array([14, 34, 52])
grad = np.zeros((S, S, 3), np.uint8)
for y in range(S):
    t = y / (S - 1)
    grad[y, :] = (top * (1 - t) + bot * t).astype(np.uint8)
plate = Image.fromarray(grad, "RGB").convert("RGBA")
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=R, fill=255)
plate.putalpha(mask)

canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
canvas = Image.alpha_composite(canvas, plate)
d = ImageDraw.Draw(canvas)

# --- 明るいシアンの縁取り (暗いタスクバーで際立つ) ------------------
d.rounded_rectangle([m, m, S - m, S - m], radius=R,
                    outline=CYAN, width=int(S * 0.022))

# --- 大きな照準 (スコープ) ------------------------------------------
cx = cy = S // 2
ro = int(S * 0.30)            # 外リング半径
rt = int(S * 0.050)           # 線の太さ
tick = int(S * 0.44)          # ティックの長さ
gap = int(S * 0.075)          # 中心の隙間

# 4方向のティック (中心に隙間)
for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
    d.line([cx + dx * gap, cy + dy * gap, cx + dx * tick, cy + dy * tick],
           fill=CYAN, width=rt)
# 外リング
d.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], outline=CYAN, width=rt)
# 中心ドット
dot = int(S * 0.035)
d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=CYAN_SOFT)

# --- 1024へ縮小して保存 ---------------------------------------------
icon = canvas.resize((1024, 1024), Image.LANCZOS)
icon.save(os.path.join(ASSETS, "icon.png"))
icon.save(os.path.join(ASSETS, "icon.ico"),
          sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                 (64, 64), (128, 128), (256, 256)])
print("OK -> assets/icon.ico")
