#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 冒烟测试：启动界面 → 自动截图 → 自动关闭，验证能正常渲染。"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from gui import ChatGUI

root = tk.Tk()
app = ChatGUI(root)

# 2 秒后截图，2.5 秒后关闭
def snap():
    # screencapture 是 macOS 专属命令，其他平台跳过截图（只验证 GUI 能渲染）
    if sys.platform == "darwin":
        subprocess.run(["screencapture", "-x", "/tmp/udp_gui.png"], check=False)
        print("已截图 /tmp/udp_gui.png")
    else:
        print("非 macOS 平台，跳过截图（仅验证 GUI 渲染）")

root.after(2000, snap)
root.after(2500, root.destroy)

print("GUI 启动中...")
root.mainloop()
print("✅ GUI 启动 + 渲染 + 关闭 无异常")
