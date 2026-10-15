#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 peer 归属修正：hermes sync 的「对话对象是自己」归一为 hermes_agent + 方向反转。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from gui import ChatGUI, HERMES_AGENT_ID
from chat_store import ChatStore

root = tk.Tk()
app = ChatGUI(root)
tmp = tempfile.mktemp(suffix=".db")
app.store.close()
app.store = ChatStore(tmp)

my_id = app.device_id


def drain():
    out = []
    while True:
        try:
            out.append(app.msg_queue.get_nowait())
        except Exception:
            return out


# 场景 C：GUI 发消息给本机 hermes_agent，hermes sync 回来（对话对象=自己）
app._on_message("x", {"sync": True, "peer": my_id, "who": "peer", "text": "你好", "conversation_id": "conv_aaa"})
app._on_message("x", {"sync": True, "peer": my_id, "who": "hermes", "text": "你好呀", "conversation_id": "conv_aaa"})
rows = drain()
assert len(rows) == 2
p1, t1, d1, c1 = rows[0]
p2, t2, d2, c2 = rows[1]
assert p1 == HERMES_AGENT_ID and d1 == "sent" and c1 == "conv_aaa", f"自己发的应归 hermes_agent+sent，实际 {rows[0]}"
assert p2 == HERMES_AGENT_ID and d2 == "received" and c2 == "conv_aaa", f"hermes 回复应归 hermes_agent+received，实际 {rows[1]}"
print("① 对话对象=自己 → 归一为 hermes_agent + 方向反转 ✅")

# 场景 B：其他 agent 发消息给本机 hermes，hermes sync 回来（对话对象=其他 agent）
app._on_message("x", {"sync": True, "peer": "other_agent", "who": "peer", "text": "你好", "conversation_id": "conv_bbb"})
app._on_message("x", {"sync": True, "peer": "other_agent", "who": "hermes", "text": "回复", "conversation_id": "conv_bbb"})
rows = drain()
assert rows[0][0] == "other_agent" and rows[0][2] == "received", f"其他 agent 说的应 received，实际 {rows[0]}"
assert rows[1][0] == "other_agent" and rows[1][2] == "sent", f"hermes 回复应 sent，实际 {rows[1]}"
print("② 对话对象≠自己 → peer 保持 + 方向正常 ✅")

# 非 sync 消息：直接来自其他 agent，提取 conversation_id
app._on_message("other_agent", {"text": "回复", "conversation_id": "conv_ccc"})
rows = drain()
assert rows[0][0] == "other_agent" and rows[0][3] == "conv_ccc", f"非 sync 应提取 conv，实际 {rows[0]}"
print("③ 非 sync 消息提取 conversation_id ✅")

# 非 sync 无 dict（纯字符串 payload）
app._on_message("other_agent", "纯文本消息")
rows = drain()
assert rows[0][0] == "other_agent" and rows[0][3] is None, f"纯文本应 conv=None，实际 {rows[0]}"
print("④ 非 sync 纯文本消息 conv=None ✅")

app.store.close()
os.remove(tmp)
root.destroy()
print("🎉 peer 归属修正逻辑验证全部通过")
