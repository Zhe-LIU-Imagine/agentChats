#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试聊天记录的「设备 → 主题」两层结构：conversation_id 分组 + 过滤 + 旧表迁移。"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chat_store import ChatStore


def test_two_level():
    tmp = tempfile.mktemp(suffix=".db")
    s = ChatStore(tmp)

    # 1. 带 conversation_id 存消息
    s.add("dev_A", "received", "帮我分析 NVDA 走势", conversation_id="conv_aaa")
    s.add("dev_A", "sent", "好的", conversation_id="conv_aaa")
    s.add("dev_A", "received", "今天天气如何", conversation_id="conv_bbb")
    s.add("dev_A", "received", "一条未分类的消息")  # conversation_id=None

    # 2. list_conversations 按主题分组，标题=首条消息
    convs = s.list_conversations("dev_A")
    assert len(convs) == 3, f"期望 3 个主题，实际 {len(convs)}"
    titles = {c: t for c, t, _ts in convs}
    assert titles["conv_aaa"] == "帮我分析 NVDA 走势", titles
    assert titles["conv_bbb"] == "今天天气如何", titles
    assert titles[None] == "一条未分类的消息", titles
    print("① list_conversations 按主题分组 + 标题=首条消息 ✅")

    # 3. get_history 按主题过滤
    assert len(s.get_history("dev_A", "conv_aaa")) == 2, "conv_aaa 应有 2 条"
    assert len(s.get_history("dev_A", "conv_bbb")) == 1, "conv_bbb 应有 1 条"
    assert len(s.get_history("dev_A", None)) == 1, "未分类应有 1 条"
    assert len(s.get_history("dev_A")) == 4, "全部应有 4 条"
    print("② get_history 按主题过滤（指定 / 未分类 / 全部）✅")

    # 4. 不同设备主题互不混淆
    s.add("dev_B", "received", "B 的消息", conversation_id="conv_aaa")
    assert len(s.get_history("dev_B", "conv_aaa")) == 1
    assert len(s.get_history("dev_A", "conv_aaa")) == 2, "dev_A 的 conv_aaa 不受 dev_B 影响"
    print("③ 多设备主题互不混淆 ✅")

    s.close()
    os.remove(tmp)


def test_migration():
    tmp = tempfile.mktemp(suffix=".db")
    # 建旧表（无 conversation_id 列）
    conn = sqlite3.connect(tmp)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "peer_id TEXT NOT NULL, direction TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO messages(peer_id, direction, text, ts) "
        "VALUES('dev_A','received','旧消息',1.0)"
    )
    conn.commit()
    conn.close()

    s = ChatStore(tmp)  # 打开即触发迁移
    cols = [r[1] for r in s.conn.execute("PRAGMA table_info(messages)")]
    assert "conversation_id" in cols, f"迁移后应有 conversation_id 列，实际 {cols}"
    assert len(s.get_history("dev_A")) == 1, "旧数据应仍可读"
    assert s.list_conversations("dev_A")[0][0] is None, "旧数据应归为未分类主题"
    s.close()
    os.remove(tmp)
    print("④ 旧表迁移（自动加 conversation_id 列 + 旧数据归未分类）✅")


if __name__ == "__main__":
    test_two_level()
    test_migration()
    print("🎉 「设备 → 主题」两层数据逻辑验证全部通过")
