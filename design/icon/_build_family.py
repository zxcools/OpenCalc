# -*- coding: utf-8 -*-
"""定稿家族页: 主图标(阶梯止盈) + 三模块, 含暗色侧栏实况 / 多尺寸 / 单色 / 色彩规范"""
import colorsys
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
OUT = r'D:\Workbuddy\开仓计算器\design\icon\family-final.html'

COLOR = {'structural': '#0E1620', 'primary': '#6F9BC2', 'accent': '#E3A93F', 'panel': '#F3F0E8'}
DARK = {'structural': '#E8EDF2', 'primary': '#7FA8CC', 'accent': '#E8B255', 'panel': '#0E1620'}
MONO = {'structural': '#0E1620', 'primary': '#0E1620', 'accent': '#0E1620', 'panel': '#F3F0E8'}

B = ('rect', 16, 74, 68, 8, 4, 'structural')
STAIRS = [('rect', 26, 62, 13, 12, 2, 'primary'), ('rect', 39, 50, 13, 24, 2, 'primary'),
          ('rect', 52, 38, 13, 36, 2, 'primary'), ('rect', 65, 22, 13, 52, 2, 'accent')]
MARKS = {
    'main': ('主图标 · 阶梯止盈', [B] + STAIRS),
    'calc': ('模块一 · 开仓计算', [B] + STAIRS),
    'trades': ('模块二 · 交易记录', [B,
        ('rect', 26, 36, 48, 11, 3, 'primary'), ('rect', 26, 53, 30, 11, 3, 'accent')]),
    'funds': ('模块三 · 资金曲线', [B,
        ('line', 25, 62, 42, 48, None, 'primary', 11), ('line', 42, 48, 57, 57, None, 'primary', 11),
        ('line', 57, 57, 74, 31, None, 'primary', 11), ('circle', 75, 30, 7, 'accent')]),
}
SIZES = [16, 24, 32, 48, 64]


def svg(prims, size, pal, plate=None):
    out = []
    if plate:
        out.append('<rect x="0" y="0" width="100" height="100" rx="22" fill="%s"/>' % plate)
    for p in prims:
        t = p[0]
        if t == 'rect':
            _, x, y, w, h, r, f = p
            out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s"/>' % (x, y, w, h, r, pal[f]))
        elif t == 'line':
            _, x1, y1, x2, y2, _u, s, w = p
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s" stroke-linecap="round"/>'
                       % (x1, y1, x2, y2, pal[s], w))
        elif t == 'circle':
            _, cx, cy, r, f = p
            out.append('<circle cx="%s" cy="%s" r="%s" fill="%s"/>' % (cx, cy, r, pal[f]))
    return '<svg viewBox="0 0 100 100" width="%d" height="%d" style="display:block">%s</svg>' % (size, size, ''.join(out))


def strip(key, pal, label):
    prims = MARKS[key][1]
    return ('<div class="strip"><span class="sl">%s</span>%s</div>'
            % (label, ''.join('<div class="c"><div class="b">%s</div><span>%d</span></div>'
                              % (svg(prims, s, pal), s) for s in SIZES)))


def appicon_row():
    return ''.join('<div class="ico"><div class="plate ivory">%s</div><span>象牙底</span></div>'
                   '<div class="ico"><div class="plate ink">%s</div><span>暗底盘</span></div>'
                   % (svg(MARKS['main'][1], 92, COLOR), svg(MARKS['main'][1], 92, DARK, plate='#0E1620'))
                   for _ in [0])


sidebar = ''.join(
    '<div class="mtab%s"><span class="mico">%s</span><span class="mtxt">%s<small>%s</small></span></div>'
    % (' active' if k == 'calc' else '', svg(MARKS[k][1], 22, DARK),
       {'calc': '开仓计算', 'trades': '交易记录', 'funds': '资金曲线'}[k],
       {'calc': '期货 · 期权', 'trades': 'abe 期权', 'funds': 'abe · 威科夫'}[k])
    for k in ('calc', 'trades', 'funds'))


def to_all(h):
    h = h.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    rr, gg, bb = r / 255, g / 255, b / 255
    hh, ll, ss = colorsys.rgb_to_hls(rr, gg, bb)
    k = 1 - max(rr, gg, bb)
    c = m = y = 0
    if k < 1:
        c, m, y = ((1 - rr - k) / (1 - k), (1 - gg - k) / (1 - k), (1 - bb - k) / (1 - k))
    return dict(hex=h.upper(), rgb='%d, %d, %d' % (r, g, b),
                cmyk='%d / %d / %d / %d' % (round(c * 100), round(m * 100), round(y * 100), round(k * 100)),
                hsl='%d°, %d%%, %d%%' % (round(hh * 360), round(ss * 100), round(ll * 100)))


pal_rows = ''.join(
    '<tr><td><span class="sw" style="background:%s"></span>%s</td><td class="mono">%s</td><td class="mono">%s</td>'
    '<td class="mono">%s</td><td class="mono">%s</td><td>%s</td></tr>'
    % (COLOR[role], role, to_all(COLOR[role])['hex'], to_all(COLOR[role])['rgb'],
       to_all(COLOR[role])['cmyk'], to_all(COLOR[role])['hsl'], zh)
    for role, zh in [('structural', '深蓝黑 · 夜景环境 / 角色: 结构色 (基线、深色块面)'),
                     ('primary', '钢青蓝 · 屏幕内容 / 角色: 主色 (阶梯主体、折线)'),
                     ('accent', '暖琥珀 · 台灯光晕 / 角色: 点缀 (最高一档、当前权益)'),
                     ('panel', '象牙白 · 编辑面板底 (方法论固定值)')])

HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>OpenCalc 图标家族 · 定稿</title><style>
*{box-sizing:border-box}
body{margin:0;background:#EFEBE2;color:#1B2430;font-family:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.page{max-width:1060px;margin:0 auto;padding:54px 40px 90px}
h1{font-size:29px;margin:0 0 8px;letter-spacing:-.4px}
.sub{color:#6A7686;font-size:14px;margin:0 0 38px;line-height:1.75}
h2{font-size:17px;margin:50px 0 16px;padding-bottom:9px;border-bottom:1px solid #D8D2C6}
.card{background:#FBF9F4;border:1px solid #E2DCD0;border-radius:13px;padding:24px 26px}
.icogrid{display:flex;gap:34px;flex-wrap:wrap}
.ico{display:flex;flex-direction:column;align-items:center;gap:9px;font-size:11.5px;color:#8A96A4}
.plate{width:128px;height:128px;border-radius:28px;display:flex;align-items:center;justify-content:center}
.plate.ivory{background:#F3F0E8;box-shadow:0 7px 20px rgba(20,30,45,.14),0 0 0 1px #E2DCD0 inset}
.plate.ink{background:#0E1620;box-shadow:0 7px 20px rgba(20,30,45,.28)}
.sidebar{width:236px;background:#0E1620;border-radius:13px;padding:14px 12px;display:flex;flex-direction:column;gap:7px}
.mtab{display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:9px}
.mtab.active{background:rgba(111,155,194,.17)}
.mtxt{color:#DCE4EC;font-size:13.5px;display:flex;flex-direction:column;line-height:1.35}
.mtxt small{color:#7C8A99;font-size:10.5px}
.strip{display:flex;align-items:flex-end;gap:24px;flex-wrap:wrap;padding:13px 0;border-top:1px dashed #E2DCD0}
.strip:first-child{border-top:0}
.sl{width:132px;flex:none;font-size:12.5px;color:#5C6A7A;font-weight:600}
.c{display:flex;flex-direction:column;align-items:center;gap:6px}
.c span{font-size:10.5px;color:#98A2AF;font-family:ui-monospace,Consolas,monospace}
.b{display:flex;align-items:flex-end;justify-content:center;min-height:66px}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #E2DCD0;vertical-align:top}
th{color:#6A7686;font-weight:600;font-size:11.5px}
.sw{display:inline-block;width:13px;height:13px;border-radius:3px;margin-right:8px;vertical-align:-2px;
 box-shadow:0 0 0 1px rgba(0,0,0,.14) inset}
ul.rules{margin:8px 0 0;padding-left:20px;font-size:13px;line-height:1.85;color:#3B4757}
.note{font-size:13px;color:#6A7686;line-height:1.75;margin:12px 0 0}
</style></head><body><div class="page">

<h1>OpenCalc 图标家族 · 定稿</h1>
<p class="sub">主图标已定为<b>阶梯止盈</b>。三个模块图标此前设计过一版，但那版是挂在<b>已废弃的旧主图标</b>上的，已于本轮按新主图标重做。</p>

<h2>一、主图标（应用图标）</h2>
<div class="card"><div class="icogrid">__APPICON__</div>
<p class="note">以止损价差为 1R，逐级给出 2R / 3R / 4R / 5R 止盈价 —— 这是本工具独有、别的行情软件没有的输出。
四级等宽、递增、相邻无缝的台阶立在账户基线上，最高一级用台灯琥珀色标出「当前要挂的那一档」。</p></div>

<h2>二、三个模块图标（暗色主题侧栏实况）</h2>
<div class="icogrid">
  <div class="sidebar">__SIDEBAR__</div>
  <div class="card" style="flex:1;min-width:330px">
  <ul class="rules">
    <li><b>开仓计算 = 主图标本身</b>：应用图标 = 计算模块，心智直接对齐（该 tab 正是测算 + 阶梯止盈所在）。</li>
    <li><b>交易记录 = 两条横条</b>：左对齐、右端参差，读作账目行；琥珀那条是被高亮的那笔平仓盈亏。</li>
    <li><b>资金曲线 = 一笔折线</b>：刻意不用柱状 —— 主图标已经是「块状递增阶梯」，这里再用递增柱会在 20px 侧栏里糊成同一形状，所以改成带一次回撤的折线 + 末端圆点（当前权益）。</li>
    <li><b>不靠颜色区分</b>：阶梯是<b>粗块竖向递增</b>、记录是<b>横向参差行</b>、曲线是<b>细折线带端点</b> —— 轮廓完全不同，单色 / 色盲 / 丝印下同样成立。</li>
  </ul>
  </div>
</div>

<h2>三、家族连接组织</h2>
<div class="card">
<p class="note" style="margin-top:0">四个标记共用<b>同一根账户基线</b>（画布坐标 <span class="mono">16, 74, 68, 8</span>，圆角 4）与同一三色调色板。
家族感来自这根线，不来自颜色 —— 所以哪怕全部转成单色，四个标记仍属同一套。</p></div>

<h2>四、多尺寸可辨测试</h2>
<div class="card">
__STRIPS_COLOR__
<p class="note">彩色版最小 24px 可用；单色版 16px 仍可辨。</p>
</div>
<div class="card" style="margin-top:14px">
__STRIPS_MONO__
<p class="note">单色把色彩合并为剪影，四者的<b>轮廓差异</b>（阶梯 / 横排 / 折线）承担全部识别功能。</p>
</div>

<h2>五、色彩规范</h2>
<div class="card"><table>
<tr><th>角色</th><th>HEX</th><th>RGB</th><th>CMYK</th><th>HSL</th><th>来源</th></tr>
__PAL__
</table>
<p class="note">纪律：1 主色 + 1 深色结构色 + 1 点缀色，三色全部提取自参考视觉，未新增任何颜色。</p></div>

<h2>六、禁用项</h2>
<div class="card"><ul class="rules">
<li>禁止渐变、阴影、描边、外发光、旋转、非等比拉伸、改色、加字、加轮廓。</li>
<li>禁止依赖颜色区分模块 —— 任何需要区分模块的场合用形状与数量。</li>
<li>模块图标在侧栏的推荐尺寸 20–22px；小于 18px 时交易记录的两条横条会并成一条，改用 22px。</li>
</ul></div>

</div></body></html>
"""

HTML = (HTML.replace('__APPICON__', appicon_row())
        .replace('__SIDEBAR__', sidebar)
        .replace('__STRIPS_COLOR__', ''.join(strip(k, COLOR, MARKS[k][0]) for k in ('main', 'trades', 'funds')))
        .replace('__STRIPS_MONO__', ''.join(strip(k, MONO, MARKS[k][0]) for k in ('main', 'trades', 'funds')))
        .replace('__PAL__', pal_rows))
open(OUT, 'w', encoding='utf-8').write(HTML)
print('已生成:', OUT, os.path.getsize(OUT), 'bytes')
