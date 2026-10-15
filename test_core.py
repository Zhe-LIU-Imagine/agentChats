#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元测试：身份持久化 + 聊天记录延续（模拟 IP 变化场景）。"""

import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用隔离临时目录测试，避免污染真实 data/
TMP = tempfile.mkdtemp(prefix="udp_test_")
os.environ.setdefault("UDP_TEST_DIR", TMP)


def test_identity():
    from identity import get_device_id
    # 首次自动生成「系统_3位数」，之后读同一个文件，身份稳定（不依赖网卡 MAC）
    did1 = get_device_id(TMP)
    did2 = get_device_id(TMP)
    assert did1 == did2, f"device_id 不一致: {did1} vs {did2}"
    assert re.match(r"^\w+_\d{3}$", did1), f"身份格式应为 系统_3位数: {did1}"
    print(f"① 身份持久化（系统_3位数）✅  device_id={did1}")
    return did1


def test_set_device_id():
    from identity import get_device_id, set_device_id
    assert set_device_id("mac_777", TMP), "set_device_id 应成功"
    assert get_device_id(TMP) == "mac_777", "改后应持久化新 ID"
    assert set_device_id("", TMP) is False, "空 ID 应拒绝"
    assert set_device_id("has space", TMP) is False, "含空格应拒绝"
    assert set_device_id("x" * 40, TMP) is False, "超长应拒绝"
    print(f"④ 可配置 ID ✅  set_device_id 改 ID 生效 + 非法值拒绝")


def test_chat_history_continuity():
    from chat_store import ChatStore
    db = os.path.join(TMP, "chat.db")
    store = ChatStore(db)

    # 模拟：peer 是同一个设备（device_id 固定），但 IP 变了
    peer_id = "dev_abc123"
    store.add(peer_id, "received", "你好（IP=192.168.3.10 时收到）")
    store.add(peer_id, "sent", "你好呀")
    # —— 模拟 IP 变化后重新上线，继续聊 ——
    store.add(peer_id, "received", "我换 IP 了（现在 192.168.3.99）")

    hist = store.get_history(peer_id)
    assert len(hist) == 3, f"历史条数不对: {len(hist)}"
    assert hist[0][1] == "你好（IP=192.168.3.10 时收到）"
    assert hist[2][1] == "我换 IP 了（现在 192.168.3.99）"

    peers = store.list_peers()
    assert peer_id in peers, f"list_peers 没返回 {peer_id}"

    print(f"② 聊天记录延续 ✅  {len(hist)} 条消息跨 IP 变化完整保留")
    print(f"   历史对话序列：")
    for direction, text, ts in hist:
        who = "我" if direction == "sent" else peer_id
        print(f"     {who}: {text}")
    store.close()


def test_multi_peer():
    from chat_store import ChatStore
    db = os.path.join(TMP, "chat2.db")
    store = ChatStore(db)
    store.add("dev_A", "received", "A 的消息1")
    store.add("dev_B", "received", "B 的消息1")
    store.add("dev_A", "sent", "回 A")
    store.add("dev_B", "sent", "回 B")
    peers = store.list_peers()
    assert set(peers) == {"dev_A", "dev_B"}, f"多设备分组不对: {peers}"
    assert len(store.get_history("dev_A")) == 2
    assert len(store.get_history("dev_B")) == 2
    print(f"③ 多设备分组 ✅  两个设备 {peers} 各自记录互不混淆")
    store.close()


if __name__ == "__main__":
    test_identity()
    test_set_device_id()
    test_chat_history_continuity()
    test_multi_peer()
    print("\n🎉 核心逻辑全部通过")
    shutil.rmtree(TMP, ignore_errors=True)
