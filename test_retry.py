#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「只管发 + 延迟重发」：sendto 报错不再直接丢弃，而是重试 N 次；活动触发只重试对应 src。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import Agent


class FakeSock:
    """假 socket：sendto 抛 No route to host，记录调用次数。"""
    def __init__(self):
        self.send_count = 0

    def sendto(self, data, addr):
        self.send_count += 1
        raise OSError(65, "No route to host")


# ① sendto 报错 → 重试 ack_retries 次（而非一次就丢弃）
a = Agent("test_retry", port=4599, retry_delay=0)  # retry_delay=0 加速测试
fs = FakeSock()
a._sock = fs
a._agents["peer"] = {"ip": "10.9.9.9", "port": 4567, "last_seen": time.time()}

r = a.send("peer", {"text": "hi"})
assert r["ok"] is False and r["reason"] == "error", f"应返回 error: {r}"
assert fs.send_count == 3, f"sendto 报错应重试 3 次，实际 {fs.send_count}"
print(f"① sendto 报错重试 3 次（不再一次丢弃）✅  send_count={fs.send_count} reason={r['reason']}")


# ② _retry_pending(src) 只重试对应 src 的待回复
import hermes_agent as ha

ha._pending_load = lambda: [
    {"src": "peer_a", "text": "msg_a", "conv_id": "c1", "ts": 0},
    {"src": "peer_b", "text": "msg_b", "conv_id": "c2", "ts": 0},
]
# 清空 reply_queue
while not ha.reply_queue.empty():
    ha.reply_queue.get()

ha._retry_pending("peer_a")

items = []
while not ha.reply_queue.empty():
    items.append(ha.reply_queue.get())

assert len(items) == 1, f"应只重试 peer_a 的 1 条，实际 {items}"
assert items[0][0] == "peer_a" and items[0][1] == "msg_a", f"应重试 peer_a 的 msg_a: {items}"
print("② 活动触发重发只重试对应 src ✅")

print("🎉 延迟重发 + 活动触发重发 验证全部通过")
