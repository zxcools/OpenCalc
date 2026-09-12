# -*- coding: utf-8 -*-
"""侧栏图标改彩色(CSS 变量, 激活态自动切换白色) + 版本号与 README 对齐 (v50.35)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
P = r'D:\Workbuddy\开仓计算器\main.py'
s = open(P, encoding='utf-8').read()

# ---------- 1. 三个侧栏 tab 图标: currentColor → 品牌配色 CSS 变量 ----------
LINES = {
    'data-tab="calc"': (
        '    <div class="maintab active" data-tab="calc"><span class="mi">'
        '<svg viewBox="0 0 100 100" aria-hidden="true">'
        '<rect x="16" y="74" width="68" height="8" rx="4" fill="var(--mk-base)"/>'
        '<rect x="26" y="10" width="48" height="56" rx="10" fill="none" stroke="var(--mk-main)" stroke-width="8"/>'
        '<rect x="35" y="18" width="30" height="11" rx="3" fill="var(--mk-acc)"/>'
        '<rect x="34" y="34" width="13" height="13" rx="1.5" fill="var(--mk-main)"/>'
        '<rect x="53" y="34" width="13" height="13" rx="1.5" fill="var(--mk-main)"/>'
        '<rect x="34" y="49" width="13" height="13" rx="1.5" fill="var(--mk-main)"/>'
        '<rect x="53" y="49" width="13" height="13" rx="1.5" fill="var(--mk-main)"/>'
        '</svg></span><span class="mt">开仓计算</span><small>期货 · 期权</small></div>'),
    'data-tab="trades"': (
        '    <div class="maintab" data-tab="trades"><span class="mi">'
        '<svg viewBox="0 0 100 100" aria-hidden="true">'
        '<rect x="16" y="74" width="68" height="8" rx="4" fill="var(--mk-base)"/>'
        '<rect x="26" y="36" width="48" height="11" rx="3" fill="var(--mk-main)"/>'
        '<rect x="26" y="53" width="30" height="11" rx="3" fill="var(--mk-acc)"/>'
        '</svg></span><span class="mt">交易记录</span><small>abe 期权</small></div>'),
    'data-tab="funds"': (
        '    <div class="maintab" data-tab="funds"><span class="mi">'
        '<svg viewBox="0 0 100 100" aria-hidden="true">'
        '<rect x="16" y="74" width="68" height="8" rx="4" fill="var(--mk-base)"/>'
        '<path d="M25 62 L42 48 L57 57 L74 31" fill="none" stroke="var(--mk-main)" '
        'stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="75" cy="30" r="7" fill="var(--mk-acc)"/>'
        '</svg></span><span class="mt">资金曲线</span><small>abe · 威科夫</small></div>'),
}
lines = s.split('\n')
hit = 0
for i, ln in enumerate(lines):
    for key, new in LINES.items():
        if key in ln and '<span class="mi">' in ln:
            lines[i] = new
            hit += 1
s = '\n'.join(lines)
assert hit == 3, '侧栏 tab 替换数不对: %d' % hit
print('三个侧栏图标已改为品牌配色变量')

# ---------- 2. CSS: 配色变量(非激活=品牌彩色, 激活绿底=白色+提亮琥珀) ----------
anchor = '.maintab .mi svg{width:100%;height:100%;display:block}'
assert anchor in s, '找不到 .mi svg CSS'
css_add = (anchor + '\n'
           '/* 侧栏图标配色: 非激活=品牌彩色; 激活(绿底 pill)上改白 + 提亮琥珀, 保证对比度 */\n'
           ':root{--mk-base:var(--text);--mk-main:#7FA8CC;--mk-acc:#E8B255}\n'
           '.maintab.active{--mk-base:#fff;--mk-main:#fff;--mk-acc:#ffe08a}')
s = s.replace(anchor, css_add, 1)
print('CSS 配色变量已加')

# ---------- 3. 版本号与 README 对齐: v50.35 ----------
old_v = 'APP_VERSION = 55'
assert old_v in s
s = s.replace(old_v, 'APP_VERSION = 5035               # 与 README 版本号 v50.35 对齐(数值比较用于单实例接管)', 1)
old_fav = 'favicon.ico?v=55'
assert old_fav in s
s = s.replace(old_fav, 'favicon.ico?v=50.35', 1)
print('APP_VERSION = 5035, favicon v=50.35')

open(P, 'w', encoding='utf-8').write(s)
import py_compile
py_compile.compile(P, doraise=True)
print('PY OK')
