#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机实测：两个 agent（不同端口 + peers）验证发现 / 收发 / 下线感知。"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent

# 用高位端口避免与其他服务冲突
PORT_A, PORT_B = 19000, 19001


def main():
    received_a = []
    received_b = []

    a = Agent("agent_a", port=PORT_A, peers={"agent_b": ("127.0.0.1", PORT_B)},
              heartbeat_interval=1.0, stale_timeout=3.0)
    b = Agent("agent_b", port=PORT_B, peers={"agent_a": ("127.0.0.1", PORT_A)},
              heartbeat_interval=1.0, stale_timeout=3.0)

    a.on_message(lambda src, payload: received_a.append((src, payload)))
    b.on_message(lambda src, payload: received_b.append((src, payload)))

    a.start()
    b.start()
    time.sleep(1.0)  # 等发现完成

    # ① 发现
    agents_a = a.list_agents()
    agents_b = b.list_agents()
    print("① 发现：")
    print(f"   a 视角在线表: {list(agents_a.keys())}")
    print(f"   b 视角在线表: {list(agents_b.keys())}")
    assert "agent_b" in agents_a, "a 未发现 b"
    assert "agent_a" in agents_b, "b 未发现 a"
    print("   ✅ 互相发现成功")

    # ② 点对点 + ACK
    r = a.send("agent_b", {"text": "hello from a"})
    print(f"\n② 点对点：a->b 结果 = {r}")
    assert r.get("ok"), f"a->b 发送失败: {r}"
    time.sleep(0.3)
    assert received_b, "b 没收到消息"
    print(f"   b 收到: {received_b[-1]}")
    print("   ✅ 点对点 + ACK 成功")

    # ③ 广播
    rs = a.broadcast_msg({"text": "broadcast"})
    print(f"\n③ 广播：结果 = {rs}")
    assert rs.get("agent_b", {}).get("ok"), "广播到 b 失败"
    time.sleep(0.3)
    print(f"   b 收到（累计 {len(received_b)} 条）")
    print("   ✅ 广播成功")

    # ④ 下线感知（主动 bye）
    b.stop()
    time.sleep(0.5)
    agents_a = a.list_agents()
    print(f"\n④ b 下线后，a 视角在线表: {list(agents_a.keys())}")
    assert "agent_b" not in agents_a, "b 下线后 a 仍认为 b 在线"
    print("   ✅ 主动下线（bye）感知成功")

    # ⑤ 心跳超时下线（短超时参数）
    print("\n⑤ 心跳超时下线（stale=3s, heartbeat=1s）...")
    c = Agent("agent_c", port=19002, peers={"agent_a": ("127.0.0.1", PORT_A)},
              heartbeat_interval=1.0, stale_timeout=3.0)
    c.start()
    time.sleep(1.5)
    assert "agent_c" in a.list_agents(), "a 未发现 c"
    print("   a 发现 c ✅")
    # 异常终止 c（不调 stop，模拟进程被杀）
    c._running = False
    c._sock.close()
    time.sleep(4.0)  # 等 a 心跳超时（3s）剔除 c
    agents_a = a.list_agents()
    print(f"   c 异常退出后，a 视角在线表: {list(agents_a.keys())}")
    assert "agent_c" not in agents_a, "c 异常退出后 a 未超时剔除"
    print("   ✅ 心跳超时下线感知成功")

    a.stop()
    print("\n🎉 全部测试通过")


if __name__ == "__main__":
    main()
