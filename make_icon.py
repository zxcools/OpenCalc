# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico (多尺寸)"""
from PIL import Image, ImageDraw

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角背景 (深蓝渐变)
def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

top = (91, 140, 255)
bottom = (30, 54, 130)
for y in range(SIZE):
    t = y / SIZE
    d.line([(0, y), (SIZE, y)], fill=lerp(top, bottom, t))

# 圆角遮罩
mask = Image.new("L", (SIZE, SIZE), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=56, fill=255)
img.putalpha(mask)

# 金色菱形 (代表"开仓/目标")
cx, cy = SIZE // 2, SIZE // 2
r = 62
gold = (245, 199, 107, 255)
d.polygon([(cx, cy - r), (cx + r * 0.72, cy), (cx, cy + r), (cx - r * 0.72, cy)], fill=gold)
# 内部高光
d.polygon([(cx, cy - r + 22), (cx + r * 0.72 - 18, cy), (cx, cy + r - 22), (cx - r * 0.72 + 18, cy)],
          fill=(255, 224, 148, 255))

# 中心文字: 仓
from PIL import ImageFont
try:
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 110)
except Exception:
    font = ImageFont.load_default()
text = "仓"
bbox = d.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
tx = cx - tw / 2 - bbox[0]
ty = cy + r * 0.58 - th / 2 - bbox[1]
d.text((tx, ty), text, font=font, fill=(30, 40, 90, 255))

img.save("icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
img.save("icon.png")
print("icon 生成完成")
