# -*- coding: utf-8 -*-
"""在桌面创建「期货开仓计算器」快捷方式 (不依赖 COM)"""
import os
import pylnk3

desktop = os.path.join(os.path.expanduser("~"), "Desktop")
if not os.path.isdir(desktop):
    import ctypes
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0000, None, 0, buf)  # CSIDL_DESKTOP
    desktop = buf.value

exe = r"D:\Workbuddy\开仓计算器\dist\OpenCalc.exe"
lnk = os.path.join(desktop, "期货开仓计算器.lnk")

l = pylnk3.create(lnk)
l.target = exe
l.working_directory = os.path.dirname(exe)
l.icon_location = exe
l.description = "期货/期权开仓计算器 - 风控仓位测算"
l.save()

print("SHORTCUT:", lnk, "| exists:", os.path.exists(lnk))
