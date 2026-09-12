# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico / icon.png

标记 = 定稿的「阶梯止盈」(与 design/icon/mark-f-main.json 同一几何)
配色 = 品牌暗底盘变体: 墨黑底盘 + 钢青蓝台阶 + 暖琥珀最高级
⚠ 几何与 design/icon/ 里的 mark 必须保持一致, 改标记时同步这里
"""
from PIL import Image, ImageDraw

SCALE = 4                      # 4 倍超采样再缩, 边缘干净
SIZE = 256                     # 最终尺寸
W = SIZE * SCALE

INK = (14, 22, 32, 255)              # #0E1620  底盘
STEEL = (127, 168, 204, 255)         # #7FA8CC  台阶(暗底盘变体)
AMBER = (232, 178, 85, 255)          # #E8B255  最高一级(暗底盘变体)
BASE_LIGHT = (232, 237, 242, 255)    # #E8EDF2  账户基线(暗底盘上必须提亮)

# 标记几何 (0-100 画布) —— 与 design/icon/mark-f-main.json 一致
BASE = (16, 74, 68, 8, 4)                                     # x, y, w, h, r
STEPS = [((26, 62, 13, 12, 2), STEEL), ((39, 50, 13, 24, 2), STEEL),
         ((52, 38, 13, 36, 2), STEEL), ((65, 22, 13, 52, 2), AMBER)]

S = 1.05           # 标记相对画布放大系数
CX, CY = 50, 52    # 标记内容重心 → 映射到画布中心


def tf(x, y):
    """标记坐标 → 画布坐标(居中并放大)"""
    return ((x - CX) * S + 50, (y - CY) * S + 50)


def px(v):
    return v / 100.0 * W


def rect(d, box, fill):
    x, y, w, h, r = box
    x1, y1 = tf(x, y)
    x2, y2 = tf(x + w, y + h)
    d.rounded_rectangle([px(x1), px(y1), px(x2), px(y2)], radius=px(r * S), fill=fill)


img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 底盘: 墨黑圆角方 (平涂, 不用渐变 —— 渐变色在小尺寸与单色工艺下必然丢失)
d.rounded_rectangle([0, 0, W - 1, W - 1], radius=int(W * 0.22), fill=INK)

rect(d, BASE, BASE_LIGHT)          # 账户基线
for box, color in STEPS:           # 四级递增台阶
    rect(d, box, color)

img = img.resize((SIZE, SIZE), Image.LANCZOS)

img.save("icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
img.save("icon.png")
print("icon 生成完成:", SIZE, "px 母版 + 7 档尺寸")
