#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：消息送达确认 + 下线判断（ping / is_online / send 送达状态）。"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent

PORT_A, PORT_B, PORT_C = 19100, 19101, 19102


def main():
    a = Agent("agent_a", port=PORT_A, peers={"agent_b": ("127.0.0.1", PORT_B)},
              heartbeat_interval=1.0, stale_timeout=3.0)
    b = Agent("agent_b", port=PORT_B, peers={"agent_a": ("127.0.0.1", PORT_A)},
              heartbeat_interval=1.0, stale_timeout=3.0)

    a.start()
    b.start()
    time.sleep(1.0)

    # ① 送达确认：send 成功返回 delivered=True
    r = a.send("agent_b", {"text": "你好"})
    print(f"① 送达确认：a->b 结果 = {r}")
    assert r.get("ok") and r.get("delivered") is True, f"送达确认失败: {r}"
    print("   ✅ delivered=True（收到 ACK = 已送达）")

    # ② is_online 判断（在线）
    print(f"\n② is_online（b 在线）: {a.is_online('agent_b')}")
    assert a.is_online("agent_b") is True
    print("   ✅ 在线判断正确")

    # ③ ping 探测（在线）
    pr = a.ping("agent_b")
    print(f"\n③ ping 探测（b 在线）: {pr}")
    assert pr.get("ok") and pr.get("online") is True, f"ping 失败: {pr}"
    print("   ✅ ping 返回 online=True（实时探测成功）")

    # ④ 主动下线后：send 应明确报 offline
    b.stop()
    time.sleep(0.5)
    r = a.send("agent_b", {"text": "还在吗"})
    print(f"\n④ b 主动下线后 send：{r}")
    assert r.get("reason") == "offline", f"应报 offline: {r}"
    print("   ✅ send 明确返回 reason=offline（已下线）")

    # ⑤ 下线后 ping / is_online
    print(f"\n⑤ b 下线后：is_online={a.is_online('agent_b')}, ping={a.ping('agent_b')}")
    assert a.is_online("agent_b") is False
    print("   ✅ is_online=False，ping 返回 offline")

    # ⑥ 异常下线（在线表还有但实际不响应）→ no_ack 判断
    print("\n⑥ 异常下线场景（在线表还有、实际已死）...")
    c = Agent("agent_c", port=PORT_C, peers={"agent_a": ("127.0.0.1", PORT_A)},
              heartbeat_interval=1.0, stale_timeout=3.0)
    c.start()
    time.sleep(1.2)
    assert "agent_c" in a.list_agents(), "a 未发现 c"
    # c 异常死掉（不发 bye）
    c._running = False
    c._sock.close()
    # 立刻 send（c 还在 a 的在线表里，但已经不响应）→ no_ack
    r = a.send("agent_c", {"text": "test"}, timeout=1.0)
    print(f"    send -> c（已死但在线表还在）: {r}")
    assert r.get("reason") == "no_ack" and r.get("delivered") is False, f"应报 no_ack: {r}"
    print("   ✅ 返回 reason=no_ack（未送达，对方可能已下线）")

    # ⑦ ping 已死的 c → no_ack
    pr = a.ping("agent_c", timeout=1.0)
    print(f"    ping -> c: {pr}")
    assert pr.get("online") is False, f"ping 应判断离线: {pr}"
    print("   ✅ ping 也判断 online=False")

    a.stop()
    print("\n🎉 送达确认 + 下线判断 全部通过")


if __name__ == "__main__":
    main()
