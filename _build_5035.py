# -*- coding: utf-8 -*-
"""打包 v50.35 (侧栏彩色 + 版本号对齐)"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ROOT = r'D:\Workbuddy\开仓计算器'
PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
os.chdir(ROOT)

target = os.path.join(ROOT, 'dist', 'OpenCalc.exe')
try:
    with open(target, 'ab'):
        print('dist/OpenCalc.exe 可写')
except OSError:
    moved = os.path.join(ROOT, 'dist', 'OpenCalc_v55_running.exe')
    os.rename(target, moved)
    print('旧 exe 改名为:', os.path.basename(moved))

if os.path.isdir('build'):
    dst = os.path.join('archive', 'build_%s' % time.strftime('%H%M%S'))
    os.makedirs('archive', exist_ok=True)
    if not os.path.exists(dst):
        shutil.move('build', dst)

env = dict(os.environ)
for k in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
    env.pop(k, None)
r = subprocess.run([PY, '-m', 'PyInstaller', '--onefile', '--windowed', '--name', 'OpenCalc',
                    '--icon', 'icon.ico', '--add-data', 'icon.ico;.', '--add-data', 'chart.min.js;.',
                    '--add-data', 'assets/qrcode-wechat.png;assets',
                    '--add-data', 'assets/qrcode-alipay.png;assets', '--noconfirm', 'main.py'],
                   env=env, capture_output=True, text=True, errors='ignore')
print('打包 rc =', r.returncode)
for l in [x for x in (r.stdout or '').splitlines() if 'Build complete' in x or 'ERROR' in x]:
    print('  ', l[:160])
exe = os.path.join(ROOT, 'dist', 'OpenCalc.exe')
print('产物: %.2f MB hash=%s' % (os.path.getsize(exe) / 1048576,
                                 hashlib.sha256(open(exe, 'rb').read()).hexdigest()[:16]))
