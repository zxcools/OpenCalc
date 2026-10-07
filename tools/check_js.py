# -*- coding: utf-8 -*-
"""抽出 main.py 里内嵌的 <script> 块做 JS 语法校验(node --check)。

为什么需要它: PyInstaller 打包**不校验**内嵌 JS —— 前端语法写错照样能打包成功,
只有用户点开页面才发现白屏。所以每轮改完前端都要先跑一遍这个。

用法: python tools/check_js.py      (全过输出 "JS OK" 并返回 0)
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_node():
    """优先用托管 node(多版本候选), 再退到系统 node。"""
    cands = []
    for pat in (r"C:\Users\Administrator\.workbuddy\binaries\node\versions\*\node.exe",
                r"C:\Program Files\nodejs\node.exe"):
        cands.extend(sorted(glob.glob(pat), reverse=True))
    cands.append("node")
    for c in cands:
        if c == "node" or os.path.isfile(c):
            return c
    raise SystemExit("找不到 node, 无法校验内嵌 JS")


def main():
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    # 只看内嵌脚本(带 src= 的外链不校验)
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)
    if not blocks:
        print("没找到内嵌 <script> 块")
        return 1
    node = find_node()
    tmpdir = tempfile.mkdtemp(prefix="oc_jscheck_")
    ok = True
    for i, b in enumerate(blocks):
        p = os.path.join(tmpdir, "block%d.js" % i)
        with open(p, "w", encoding="utf-8") as f:
            f.write(b)
        r = subprocess.run([node, "--check", p], capture_output=True, text=True)
        if r.returncode == 0:
            print("block %d ok (%d chars)" % (i, len(b)))
        else:
            ok = False
            print("block %d FAIL" % i)
            print(r.stdout)
            print(r.stderr)
    print("JS OK" if ok else "JS FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
