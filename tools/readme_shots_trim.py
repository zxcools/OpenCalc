# -*- coding: utf-8 -*-
"""README 截图后处理: 按截图脚本记录的边界裁切 + 自动裁空白 + 统一缩放。"""
import glob
import json
import os
from PIL import Image

SRC = 'docs/screenshots'
TARGET_W = 1380          # 输出宽度(px)
META = '_tmp_shot_meta.json'
meta = {}
if os.path.exists(META):
    for m in json.load(open(META, encoding='utf-8')):
        meta[m['file']] = m


def is_bg_row(px, y, x0, x1, bg, step=5):
    return all(px[x, y] == bg for x in range(x0, x1, step))


def is_bg_col(px, x, y0, y1, bg, step=7):
    return all(px[x, y] == bg for y in range(y0, y1, step))


def process(path):
    im = Image.open(path).convert('RGB')
    W, H = im.size
    m = meta.get(os.path.basename(path), {})
    # 脚本记录的边界是 CSS px, 图是 2 倍
    x0 = int(m.get('left', 0) * 2)
    y0 = int(m.get('top', 0) * 2)
    # 右侧/底部自动检测空白
    px = im.load()
    bg = im.getpixel((min(x0 + 6, W - 1), H - 6))
    right = W
    for x in range(W - 1, max(W // 2, x0 + 200), -1):
        if not is_bg_col(px, x, y0, H, bg):
            right = min(x + 48, W)
            break
    bottom = H
    for y in range(H - 1, max(H // 3, y0 + 200), -1):
        if not is_bg_row(px, y, x0, right, bg):
            bottom = min(y + 36, H)
            break

    im2 = im.crop((x0, y0, right, bottom))
    h2 = int(im2.height * TARGET_W / im2.width)
    im2 = im2.resize((TARGET_W, h2), Image.LANCZOS)
    im2.save(path, optimize=True)
    return im.size, im2.size, os.path.getsize(path) // 1024


print('%-22s %-16s %-14s %s' % ('文件', '原始(2x)', '输出', '大小'))
total = 0
for f in sorted(glob.glob(os.path.join(SRC, '*.png'))):
    o, n, kb = process(f)
    total += kb
    print('%-22s %-16s %-14s %dKB' % (os.path.basename(f), '%dx%d' % o, '%dx%d' % n, kb))
print('合计 %dKB' % total)
if os.path.exists(META):
    os.remove(META)
