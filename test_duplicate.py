#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证同名冲突检测：相同 agent_id 的远程 announce/heartbeat 触发告警，本机广播不误报。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import Agent

MY_IP = "192.168.1.100"

# 不 start()，手动调 _handle 模拟收到远端/本机的 announce/heartbeat
a = Agent("mac_123", port=4599)
a._local_ips = {MY_IP}

calls = []
a.on_duplicate(lambda ip, port, aid: calls.append((ip, port, aid)))

# 1) 远程同 ID 的 announce → 触发同名冲突
a._handle({"type": "announce", "agent_id": "mac_123", "port": 4567}, ("192.168.1.200", 4567))
assert calls == [("192.168.1.200", 4567, "mac_123")], f"应触发同名冲突: {calls}"
print("[1] 远程同 ID announce 触发告警 ✅")

# 2) 本机自己的广播回环 → 不误报
a._handle({"type": "heartbeat", "agent_id": "mac_123", "port": 4599}, (MY_IP, 4599))
assert len(calls) == 1, f"本机广播不应触发: {calls}"
print("[2] 本机广播不误报 ✅")

# 3) 不同 ID → 不触发，正常记入在线表
a._handle({"type": "announce", "agent_id": "mac_456", "port": 4567}, ("192.168.1.201", 4567))
assert len(calls) == 1, f"不同 ID 不应触发: {calls}"
assert "mac_456" in a.list_agents(), "不同 ID 应记入在线表"
print("[3] 不同 ID 正常入在线表、不触发 ✅")

# 4) 限频：同一 ip:port 60 秒内只告警一次
a._dup_warned.clear()
a._notify_duplicate("10.0.0.9", 4567, "mac_123")
assert len(calls) == 2, f"第一次告警应触发: {calls}"
a._notify_duplicate("10.0.0.9", 4567, "mac_123")
assert len(calls) == 2, f"60 秒内应限频不重复: {calls}"
print("[4] 同名告警限频（60 秒内不重复）✅")

print("🎉 同名冲突检测验证全部通过")
