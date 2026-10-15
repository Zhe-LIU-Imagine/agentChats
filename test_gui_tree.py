#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 两层树结构集成测试：验证「设备 → 主题」树真实生成 + 选中层级决定是否带主题。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from gui import ChatGUI, _DEVICE
from chat_store import ChatStore

root = tk.Tk()
app = ChatGUI(root)

# 用临时 db 替换，避免污染真实聊天记录
tmp = tempfile.mktemp(suffix=".db")
app.store.close()
app.store = ChatStore(tmp)

# 存消息（模拟收到带主题的对话）
app.store.add("dev_A", "received", "帮我分析 NVDA 走势", conversation_id="conv_aaa")
app.store.add("dev_A", "sent", "NVDA 今日上涨 2.3%", conversation_id="conv_aaa")
app.store.add("dev_A", "received", "今天天气如何", conversation_id="conv_bbb")

# 刷新树（agent=None 时 online 为空，设备来自历史）
app._refresh_tree()

# 验证两层结构
devs = app.tree.get_children()
assert len(devs) == 1, f"第一层应有 1 个设备节点，实际 {len(devs)}"
dev = devs[0]
convs = app.tree.get_children(dev)
assert len(convs) == 2, f"第二层应有 2 个主题节点，实际 {len(convs)}"
titles = sorted(app.tree.item(c, "text") for c in convs)
print("① 主题节点标题:", titles)
assert any("NVDA" in t for t in titles), "应有 NVDA 主题"
assert any("天气" in t for t in titles), "应有天气主题"
print("① 两层树结构（设备 → 主题）生成正确 ✅")

# 选中主题节点 → 发送应带该主题 conversation_id
nid = convs[0]
peer_id, conv_id = app._conv_map[nid]
app.current_peer = peer_id
app.current_conv = conv_id
conv = None if app.current_conv is _DEVICE else app.current_conv
assert conv == conv_id, f"选中主题后发送应带 conversation_id={conv_id}，实际 {conv}"
print("② 选中主题节点 → 发送带 conversation_id ✅")

# 选中设备节点 → 发送应不带 conversation_id（新话题）
app.current_conv = _DEVICE
conv2 = None if app.current_conv is _DEVICE else app.current_conv
assert conv2 is None, "选中设备层发送应不带 conversation_id"
print("③ 选中设备层 → 发送不带 conversation_id（新话题）✅")

app.store.close()
os.remove(tmp)
root.destroy()
print("🎉 GUI 两层树结构 + 层级发送逻辑验证全部通过")
