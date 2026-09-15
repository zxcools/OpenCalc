# -*- coding: utf-8 -*-
"""运行 mark_spec.py 生成 4 个标记的规范包"""
import io
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
SCRIPT = r'C:\Users\Administrator\.workbuddy\plugins\cache\experts\logo-editorial-designer\1.1.0\skills\mark-to-spec\scripts\mark_spec.py'
ROOT = r'D:\Workbuddy\开仓计算器\design\icon'

jobs = [
    ('mark-main.json', 'out-main'),
    ('mark-module-calc.json', 'out-module-calc'),
    ('mark-module-trades.json', 'out-module-trades'),
    ('mark-module-funds.json', 'out-module-funds'),
]

for cfg, outdir in jobs:
    cfgp = os.path.join(ROOT, cfg)
    out = os.path.join(ROOT, outdir)
    r = subprocess.run([PY, SCRIPT, '--config', cfgp, '--outdir', out, '--check-mono'],
                       capture_output=True, text=True, errors='ignore')
    print('=== %s -> %s (rc=%d)' % (cfg, outdir, r.returncode))
    tail = [l for l in (r.stdout or '').splitlines() if l.strip()]
    for l in tail[-6:]:
        print('   ', l[:160])
    if r.returncode != 0:
        print('   STDERR:', (r.stderr or '')[-600:])
    if os.path.isdir(out):
        print('    产物:', sorted(os.listdir(out)))
