#!/usr/bin/env python3
"""验证：对方下线后，send 用记住的 IP 回发统一端口（不返回 offline）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import Agent

a = Agent("receiver", port=20001, unified_port=20001)
a.start()
time.sleep(0.5)

# 模拟：收到过 peer 的消息（记住 ip），但 peer 已下线（在线表里没有）
a._ip_map["peer"] = "127.0.0.1"
a._agents.pop("peer", None)

r = a.send("peer", {"text": "hi"}, timeout=0.5)
print("[1] 对方已下线但有 ip 映射，send 结果:", r)
assert r.get("reason") != "offline", f"不应返回 offline：{r}"
print("[1] ✅ 不再返回 offline，而是回发统一端口（最终 no_ack/error 是因为本机 20001 无对端监听）")

# 完全未知的 peer（无 ip 映射）→ 应仍返回 offline
r2 = a.send("unknown_peer", {"text": "hi"}, timeout=0.5)
print("[2] 完全未知 peer，send 结果:", r2)
assert r2.get("reason") == "offline", f"无 ip 映射应返回 offline：{r2}"
print("[2] ✅ 完全未知 peer 仍返回 offline")

a.stop()
print("🎉 ip_map 回发逻辑验证通过")
