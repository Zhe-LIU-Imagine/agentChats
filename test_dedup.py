#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证同 message ID 重复消息去重：只触发一次业务回调；去重表超容量清理最旧。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as agent_mod
from agent import Agent
from protocol import make_msg


class QuietSock:
    """假 socket：sendto 不抛错（ACK 正常回），记录发送次数。"""
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


# ① 同一 msg_id 重复收到 → 业务回调只触发一次（ACK 每次都回，幂等）
a = Agent("test_dedup", port=4601)
a._sock = QuietSock()
received = []
a.on_message(lambda from_id, payload: received.append((from_id, payload)))

pkt = make_msg("msg-123", "peer", "test_dedup", {"text": "hi"})
addr = ("10.9.9.9", 4567)
a._handle(pkt, addr)
a._handle(pkt, addr)          # 重复消息（重发/广播造成）
a._handle(pkt, addr)          # 再重复

assert len(received) == 1, f"同一 msg_id 只应回调 1 次，实际 {len(received)}"
assert received[0] == ("peer", {"text": "hi"}), f"回调内容不符: {received}"
# ACK 对每次重复都应回（3 次收到 → 3 次 ACK），否则对端会一直重发
assert len(a._sock.sent) == 3, f"重复消息每次都应回 ACK，实际回 {len(a._sock.sent)} 次"
print(f"① 同 msg_id 重复消息只回调 1 次 ✅  收到 3 次 / 回调 1 次 / ACK 回 3 次")


# ② 不同 msg_id 正常触发（去重不会误伤新消息）
pkt2 = make_msg("msg-456", "peer", "test_dedup", {"text": "second"})
a._handle(pkt2, addr)
assert len(received) == 2, f"不同 msg_id 应正常回调，实际 {len(received)}"
print("② 不同 msg_id 正常触发，互不误伤 ✅")


# ③ 去重表容量上限：超限清理最旧 msg_id，防内存无限增长
agent_mod.MAX_SEEN_MSG_IDS = 3
b = Agent("test_dedup_cap", port=4602)
b._sock = QuietSock()
b._seen_msg_ids = {"m1": 1, "m2": 2, "m3": 3}   # 已满
pkt3 = make_msg("m4", "peer", "test_dedup_cap", {"text": "x"})
b._handle(pkt3, ("10.9.9.9", 4567))
assert "m1" not in b._seen_msg_ids, "最旧的 m1 应被清理"
assert "m2" in b._seen_msg_ids and "m3" in b._seen_msg_ids and "m4" in b._seen_msg_ids
assert len(b._seen_msg_ids) == 3, f"去重表应维持在容量上限内，实际 {len(b._seen_msg_ids)}"
print(f"③ 去重表超容量清理最旧 ✅  当前 {list(b._seen_msg_ids.keys())}")

print("🎉 消息去重（重复 msg_id + 容量上限）验证全部通过")
