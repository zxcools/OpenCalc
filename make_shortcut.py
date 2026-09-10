# -*- coding: utf-8 -*-
"""在桌面创建/更新「开仓计算器」快捷方式 (用 pylnk3, 不依赖 COM)

用法:
    python make_shortcut.py [exe路径] [快捷方式名]
默认: exe = <本目录>/dist/OpenCalc.exe, 快捷方式名 = 开仓计算器及资金曲线.lnk

⚠ pylnk3 的 API: 旧写法 `l.target = ...` / `l.working_directory = ...` 在老版本上才有;
   当前版本必须用 `pylnk3.for_file(target, lnk_name=..., description=..., icon_file=..., work_dir=...)`
   然后 `l.save(lnk路径)` —— 直接给 Lnk(path) 赋 path/work_dir 会抛 AttributeError(只读)
"""
import os
import sys

import pylnk3

here = os.path.dirname(os.path.abspath(__file__))
exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "dist", "OpenCalc.exe")
name = sys.argv[2] if len(sys.argv) > 2 else "开仓计算器及资金曲线.lnk"

desktop = os.path.join(os.path.expanduser("~"), "Desktop")
if not os.path.isdir(desktop):
    import ctypes
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0000, None, 0, buf)  # CSIDL_DESKTOP
    desktop = buf.value

exe = os.path.abspath(exe)
if not os.path.exists(exe):
    raise SystemExit("找不到 exe: %s" % exe)

lnk = os.path.join(desktop, name)
l = pylnk3.for_file(
    exe,
    lnk_name=lnk,
    description="期货/期权开仓计算器 - 风控仓位测算",
    icon_file=exe,
    icon_index=0,
    work_dir=os.path.dirname(exe),
)
l.save(lnk)

v = pylnk3.parse(lnk)
print("SHORTCUT :", lnk)
print("  target :", v.path)
print("  workdir:", v.working_dir)
print("  exists :", os.path.exists(v.path))
