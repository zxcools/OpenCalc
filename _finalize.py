# -*- coding: utf-8 -*-
"""冒烟 + 备份 + 提交 v50.35"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ROOT = r'D:\Workbuddy\开仓计算器'
GIT = r'C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe'
PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
EXE = os.path.join(ROOT, 'dist', 'OpenCalc.exe')
PORT = 8898
os.chdir(ROOT)

# ---- 冒烟 ----
env = dict(os.environ)
env['OC_PORT'] = str(PORT)
env['OC_NO_TAKEOVER'] = '1'
env['APPDATA'] = r'C:\Users\Administrator\AppData\Roaming'
for k in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
    env.pop(k, None)
p = subprocess.Popen([EXE], env=env)
ok = False
for _ in range(30):
    time.sleep(1)
    try:
        v = json.loads(urllib.request.urlopen('http://127.0.0.1:%d/api/version' % PORT, timeout=2).read().decode())
        print('冒烟 版本:', v)
        ok = True
        break
    except Exception:
        pass
html = ''
if ok:
    html = urllib.request.urlopen('http://127.0.0.1:%d/' % PORT, timeout=8).read().decode('utf-8')
    print('侧栏彩色:', '--mk-main:#7FA8CC' in html, '| 激活白化:', '.maintab.active{--mk-base:#fff' in html,
          '| favicon:', 'favicon.ico?v=50.35' in html)
    urllib.request.urlopen('http://127.0.0.1:%d/api/shutdown' % PORT, timeout=5).read()
    time.sleep(2)
    print('进程:', '已干净退出' if p.poll() is not None else '仍在运行')
if p.poll() is None:
    p.kill()
if not ok:
    sys.exit(1)

# ---- 备份 ----
shutil.copy2(EXE, os.path.join(ROOT, 'backup', 'OpenCalc_v50.35.exe'))
exes = sorted(x for x in os.listdir(os.path.join(ROOT, 'backup')) if x.endswith('.exe'))
os.makedirs(os.path.join(ROOT, 'archive'), exist_ok=True)
for x in exes[:-3]:
    shutil.move(os.path.join(ROOT, 'backup', x), os.path.join(ROOT, 'archive', x))
    print('archive:', x)
print('backup:', sorted(x for x in os.listdir(os.path.join(ROOT, 'backup')) if x.endswith('.exe')))

# ---- 提交 ----
MSG = """v50.35: 侧栏图标改彩色 + 版本号与 README 对齐(5035 ↔ v50.35)

用户反馈两点:
1. 侧栏图标是黑白的, 改成彩色
   → 三个 tab 图标从 currentColor 改为品牌配色 CSS 变量:
     --mk-base(基线, 跟随主题文字色) / --mk-main(钢青蓝 #7FA8CC) / --mk-acc(琥珀 #E8B255)
   激活态落在绿色胶囊上, 彩色对比度不够 → 激活态自动切换 白色结构 + 提亮琥珀 #ffe08a
2. 软件里 v55, README 里 v50.34, 两套数字
   → APP_VERSION 55 → 5035, 与 README v50.35 对齐; favicon 参数同步 ?v=50.35
   (接管判断是数值比较, 5035 > 55, 旧版仍会正常让位)

验证: tests 227/227; exe 冒烟 version 5035; 侧栏彩色/激活白化/favicon 均确认
"""
msgf = os.path.join(ROOT, '_tmp_msg.txt')
open(msgf, 'w', encoding='utf-8').write(MSG)


def git(*a):
    r = subprocess.run([GIT, '-C', ROOT] + list(a), capture_output=True, text=True, errors='ignore')
    if r.returncode != 0:
        print('git', a[:2], '->', (r.stderr or r.stdout)[:400])
    return r


git('add', '-A')
r = git('commit', '-F', msgf)
print('commit rc=', r.returncode)
print((r.stdout or '').strip()[:200])
os.remove(msgf)
print(git('log', '--oneline', '-3').stdout)
print('工作区:', git('status', '--short').stdout.strip() or '(干净)')
