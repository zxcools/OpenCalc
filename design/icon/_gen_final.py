# -*- coding: utf-8 -*-
"""定稿版: 主图标 = 阶梯止盈; 三个模块图标按同一基线体系重做
统一连接组织: 基线 rect(16,74,68,8,r4,structural) —— 四个标记完全一致"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ROOT = r'D:\Workbuddy\开仓计算器\design\icon'
PAL = {"primary": "#6F9BC2", "structural": "#0E1620", "accent": "#E3A93F", "panel": "#F3F0E8"}
BASE = {"type": "rect", "x": 16, "y": 74, "w": 68, "h": 8, "r": 4, "fill": "structural"}

FILES = {
    "mark-f-main.json": {
        "name_zh": "开仓计算器", "en": "OpenCalc",
        "notes": "定稿主图标「阶梯止盈」—— 以止损价差为 1R, 逐级给出 2R/3R/4R/5R 止盈价, 这是本工具独有、别的行情软件没有的输出。"
                 "四级等宽、递增、相邻无缝的台阶立在账户基线上, 最高一级用台灯琥珀色 = 当前要挂的那一档。"
                 "单色下就是一架向上的阶梯, 不依赖颜色; 16px 仍可辨轮廓。基线 16,74,68,8 与三个模块图标完全一致。",
        "motif": [BASE,
                  {"type": "rect", "x": 26, "y": 62, "w": 13, "h": 12, "r": 2, "fill": "primary"},
                  {"type": "rect", "x": 39, "y": 50, "w": 13, "h": 24, "r": 2, "fill": "primary"},
                  {"type": "rect", "x": 52, "y": 38, "w": 13, "h": 36, "r": 2, "fill": "primary"},
                  {"type": "rect", "x": 65, "y": 22, "w": 13, "h": 52, "r": 2, "fill": "accent"}]},
    "mark-f-calc.json": {
        "name_zh": "开仓计算", "en": "OpenCalc",
        "notes": "模块一「开仓计算」—— 与主图标完全同一标记。应用图标 = 计算模块, 心智直接对齐。"
                 "该 tab 承载期货/期权测算 + 阶梯止盈, 也正是主图标要表达的内容。",
        "motif": [BASE,
                  {"type": "rect", "x": 26, "y": 62, "w": 13, "h": 12, "r": 2, "fill": "primary"},
                  {"type": "rect", "x": 39, "y": 50, "w": 13, "h": 24, "r": 2, "fill": "primary"},
                  {"type": "rect", "x": 52, "y": 38, "w": 13, "h": 36, "r": 2, "fill": "primary"},
                  {"type": "rect", "x": 65, "y": 22, "w": 13, "h": 52, "r": 2, "fill": "accent"}]},
    "mark-f-trades.json": {
        "name_zh": "交易记录", "en": "OpenCalc",
        "notes": "模块二「交易记录」—— 两条不等长的横条左对齐、右端参差, 读作账目行(开仓一笔 + 平仓一笔), 琥珀那条 = 被高亮的那笔平仓盈亏。"
                 "刻意与「阶梯」正交: 阶梯是竖向递增的块, 这里是横向参差的行 —— 单色下靠方向区分, 不靠颜色。",
        "motif": [BASE,
                  {"type": "rect", "x": 26, "y": 36, "w": 48, "h": 11, "r": 3, "fill": "primary"},
                  {"type": "rect", "x": 26, "y": 53, "w": 30, "h": 11, "r": 3, "fill": "accent"}]},
    "mark-f-funds.json": {
        "name_zh": "资金曲线", "en": "OpenCalc",
        "notes": "模块三「资金曲线」—— 用折线而非柱状: 主图标已经是「块状递增阶梯」, 若这里再用递增柱会在 20px 侧栏里糊成同一个形状。"
                 "改成一笔带一次回撤的三段折线 + 末端圆点(当前权益), 圆点用台灯琥珀。单色下「细折线 + 端点」与阶梯的「粗块阶梯」轮廓完全不同。",
        "motif": [BASE,
                  {"type": "line", "x1": 25, "y1": 62, "x2": 42, "y2": 48,
                   "stroke": "primary", "width": 11, "cap": "round"},
                  {"type": "line", "x1": 42, "y1": 48, "x2": 57, "y2": 57,
                   "stroke": "primary", "width": 11, "cap": "round"},
                  {"type": "line", "x1": 57, "y1": 57, "x2": 74, "y2": 31,
                   "stroke": "primary", "width": 11, "cap": "round"},
                  {"type": "circle", "cx": 75, "cy": 30, "r": 7, "fill": "accent"}]},
}

for fn, cfg in FILES.items():
    out = {"brand": {"name_zh": cfg["name_zh"], "name_en": cfg["en"]},
           "palette": PAL, "canvas": 100, "min_size_px": 16,
           "motif": cfg["motif"], "notes": cfg["notes"]}
    open(os.path.join(ROOT, fn), 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False, indent=2))
    print('已写入', fn, '| 图元数', len(cfg["motif"]))
