#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 msg_id 用递增计数器：0 起递增，到 MAX_MSG_ID(=10000) 回绕到 0。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as agent_mod
from agent import Agent
from protocol import decode


# ① _next_msg_id 递增 + 回绕
a = Agent("test_msgid", port=4603)
ids = [a._next_msg_id() for _ in range(3)]
assert ids == ["0", "1", "2"], f"应从 0 起递增，实际 {ids}"

a._msg_seq = agent_mod.MAX_MSG_ID - 1
assert a._next_msg_id() == str(agent_mod.MAX_MSG_ID - 1), "最后一格应为 9999"
assert a._next_msg_id() == "0", f"到 {agent_mod.MAX_MSG_ID} 应回绕到 0"
print(f"① msg_id 0 起递增 + 到 {agent_mod.MAX_MSG_ID} 回绕到 0 ✅")


# ② send() 实际用计数器 msg_id（非时间戳/uuid）
class CaptureSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data)


b = Agent("test_msgid_send", port=4604, ack_timeout=0.05, ack_retries=1, retry_delay=0)
cs = CaptureSock()
b._sock = cs
b._agents["peer"] = {"ip": "10.9.9.9", "port": 4567, "last_seen": time.time()}

b.send("peer", {"text": "hi"})    # 等 ACK 超时（快），msg_id 应为 "0"
b.send("peer", {"text": "hi2"})   # msg_id 应为 "1"

mids = []
for data in cs.sent:
    pkt = decode(data)
    if pkt.get("type") == "msg":
        mids.append(pkt["msg_id"])
unique = list(dict.fromkeys(mids))          # 保序去重
assert unique == ["0", "1"], f"send() 的 msg_id 应为计数器 0、1，实际 {unique}"
print(f"② send() 用计数器 msg_id（非 uuid）✅  {unique}")

print("🎉 msg_id 计数器（递增 + 回绕）验证全部通过")
