#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 hermes_agent 看门狗：启动状态下检查存活、下线则拉起；停止状态下终止循环。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from gui import ChatGUI, HERMES_WATCHDOG_INTERVAL_MS
from chat_store import ChatStore

root = tk.Tk()
app = ChatGUI(root)
tmp = tempfile.mktemp(suffix=".db")
app.store.close()
app.store = ChatStore(tmp)

# mock _ensure_hermes_agent，记录是否被调用
calls = []
app._ensure_hermes_agent = lambda: calls.append(True)

# 测试 1：未启动状态 → 终止循环，不检查
app._watchdog_active = False
app._watchdog_scheduled = True
app._hermes_watchdog()
assert not calls, "未启动状态不应检查 hermes_agent"
assert app._watchdog_scheduled is False, "未启动状态应终止循环"
print("① 停止状态下看门狗终止循环、不检查 ✅")

# 测试 2：启动状态 → 检查存活（调用 _ensure）
app._watchdog_active = True
app._watchdog_scheduled = True
app._hermes_watchdog()
assert len(calls) == 1, f"启动状态应检查一次，实际 {len(calls)}"
print("② 启动状态下看门狗检查存活（调用 _ensure_hermes_agent）✅")

# 测试 3：间隔常量 = 5 分钟
assert HERMES_WATCHDOG_INTERVAL_MS == 5 * 60 * 1000, "间隔应为 5 分钟"
print("③ 看门狗间隔 = 5 分钟 ✅")

app.store.close()
os.remove(tmp)
root.destroy()
print("🎉 hermes_agent 看门狗逻辑验证全部通过")
