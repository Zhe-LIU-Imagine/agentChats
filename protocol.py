#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UDP 多 Agent 通讯协议定义。

消息分两类：
  1. 发现/保活类（广播到 <broadcast>:port，所有 agent 监听同一端口接收）：
     - announce  上线广播
     - heartbeat 心跳保活
     - bye       下线广播
  2. 点对点消息类（直连目标 agent 的 ip:port）：
     - msg       业务消息（带 msg_id，接收方回 ACK）
     - ack       确认（幂等去重 + 超时重发的基础）

所有消息均为 JSON 字符串（UTF-8）。
"""

import json

# ---- 消息类型 ----
TYPE_ANNOUNCE = "announce"    # 上线广播
TYPE_HEARTBEAT = "heartbeat"  # 心跳
TYPE_BYE = "bye"              # 下线广播
TYPE_MSG = "msg"              # 点对点业务消息
TYPE_ACK = "ack"              # ACK 确认
TYPE_PING = "ping"            # 主动探测（判断在线）
TYPE_PONG = "pong"            # 探测响应

# 发现/保活类型集合（用于路由判断）
DISCOVERY_TYPES = {TYPE_ANNOUNCE, TYPE_HEARTBEAT, TYPE_BYE}


def encode(obj: dict) -> bytes:
    """dict -> UTF-8 JSON bytes。"""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def decode(data: bytes) -> dict:
    """UTF-8 JSON bytes -> dict。"""
    return json.loads(data.decode("utf-8"))


def make_announce(agent_id: str, port: int, extra: dict = None) -> dict:
    d = {"type": TYPE_ANNOUNCE, "agent_id": agent_id, "port": port}
    if extra:
        d["extra"] = extra
    return d


def make_heartbeat(agent_id: str, port: int, extra: dict = None) -> dict:
    d = {"type": TYPE_HEARTBEAT, "agent_id": agent_id, "port": port}
    if extra:
        d["extra"] = extra
    return d


def make_bye(agent_id: str) -> dict:
    return {"type": TYPE_BYE, "agent_id": agent_id}


def make_msg(msg_id: str, from_id: str, to_id: str, payload) -> dict:
    return {
        "type": TYPE_MSG,
        "msg_id": msg_id,
        "from": from_id,
        "to": to_id,
        "payload": payload,
    }


def make_ack(msg_id: str, from_id: str, to_id: str) -> dict:
    return {"type": TYPE_ACK, "msg_id": msg_id, "from": from_id, "to": to_id}


def make_ping(msg_id: str, from_id: str, to_id: str) -> dict:
    return {"type": TYPE_PING, "msg_id": msg_id, "from": from_id, "to": to_id}


def make_pong(msg_id: str, from_id: str, to_id: str) -> dict:
    return {"type": TYPE_PONG, "msg_id": msg_id, "from": from_id, "to": to_id}
