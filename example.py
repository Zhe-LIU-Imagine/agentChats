#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UDP 多 Agent 通讯示例：启动一个 agent，展示发现 + 收发。

用法（两个终端各跑一个，互相发现）：

  终端1:  python example.py --id agent_a --port 19000 --peers agent_b=127.0.0.1:19001
  终端2:  python example.py --id agent_b --port 19001 --peers agent_a=127.0.0.1:19000

然后在终端1输入：send agent_b 你好
"""

import argparse
import sys

from agent import Agent


def parse_peers(s: str) -> dict:
    """解析 'a=1.2.3.4:9001,b=1.2.3.5:9002' -> {a:(ip,port), ...}"""
    peers = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        aid, addr = part.split("=", 1)
        ip, port = addr.rsplit(":", 1)
        peers[aid] = (ip, int(port))
    return peers


def main():
    ap = argparse.ArgumentParser(description="UDP 多 Agent 通讯示例")
    ap.add_argument("--id", required=True, help="本 agent 唯一 ID")
    ap.add_argument("--port", type=int, default=19000, help="监听端口")
    ap.add_argument("--peers", default="", help="静态种子，如 a=1.2.3.4:9001,b=...")
    ap.add_argument("--broadcast", default="255.255.255.255", help="广播地址")
    args = ap.parse_args()

    agent = Agent(
        agent_id=args.id,
        port=args.port,
        broadcast_addr=args.broadcast,
        peers=parse_peers(args.peers),
    )

    agent.on_message(lambda src, payload: print(f"\n📩 收到来自 {src} 的消息: {payload}"))
    agent.start()

    print(f"✅ agent '{args.id}' 已上线，监听端口 {args.port}")
    print("命令：list | send <agent_id> <文本> | broadcast <文本> | quit\n")

    try:
        while True:
            line = input("> ").strip()
            if not line:
                continue
            parts = line.split(maxsplit=2)
            cmd = parts[0].lower()

            if cmd == "quit":
                break
            elif cmd == "list":
                agents = agent.list_agents()
                if not agents:
                    print("（在线表为空）")
                for aid, info in agents.items():
                    print(f"  {aid}  @ {info['ip']}:{info['port']}  (last_seen {info['last_seen']:.0f})")
            elif cmd == "send" and len(parts) >= 3:
                target, text = parts[1], parts[2]
                r = agent.send(target, {"text": text})
                print(f"  send -> {target}: {r}")
            elif cmd == "broadcast" and len(parts) >= 2:
                text = parts[1]
                rs = agent.broadcast_msg({"text": text})
                print(f"  broadcast: {rs}")
            else:
                print("  用法：list / send <id> <文本> / broadcast <文本> / quit")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        agent.stop()
        print(f"👋 agent '{args.id}' 已下线")


if __name__ == "__main__":
    main()
