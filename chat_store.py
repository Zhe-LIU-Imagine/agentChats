#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊天记录持久化（SQLite）。

按对方 device_id + 主题（conversation_id）两级组织消息。
IP 变化不影响（因为身份是 device_id 不是 IP）。
"""

import sqlite3
import time

DEFAULT_DB_PATH = "chats/chat.db"   # 聊天记录单独目录（复制项目时可单独排除）
LEGACY_DB_PATH = "data/chat.db"     # 旧路径，首次运行自动迁移

# get_history 的 conversation_id 默认哨兵：未指定 = 该设备全部消息（兼容旧行为）
_UNSPECIFIED = object()


class ChatStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        import os
        # 旧路径一次性迁移：仅当使用默认路径时，data/chat.db → chats/chat.db
        if (db_path == DEFAULT_DB_PATH and os.path.exists(LEGACY_DB_PATH)
                and not os.path.exists(db_path)):
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            import shutil
            shutil.move(LEGACY_DB_PATH, db_path)
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id TEXT NOT NULL,
                conversation_id TEXT,        -- 主题（话题线程）；NULL = 未分类
                direction TEXT NOT NULL,     -- 'sent' 我发的 / 'received' 我收的
                text TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        # 迁移：旧表缺 conversation_id 列，补上（幂等）
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(messages)")]
        if "conversation_id" not in cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN conversation_id TEXT")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_peer_ts ON messages(peer_id, ts)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_peer_conv_ts "
            "ON messages(peer_id, conversation_id, ts)"
        )
        self.conn.commit()

    def add(self, peer_id: str, direction: str, text: str, ts: float = None,
            conversation_id: str = None):
        """记一条消息。direction: 'sent' | 'received'。conversation_id=None 表示未分类。"""
        ts = ts or time.time()
        self.conn.execute(
            "INSERT INTO messages(peer_id, conversation_id, direction, text, ts) "
            "VALUES(?,?,?,?,?)",
            (peer_id, conversation_id, direction, str(text), ts),
        )
        self.conn.commit()

    def get_history(self, peer_id: str, conversation_id=_UNSPECIFIED, limit: int = 500):
        """返回聊天记录 [(direction, text, ts), ...]，按时间升序。

        conversation_id 语义：
          - 不传（默认哨兵）：该设备全部消息（旧行为，向后兼容）
          - None：该设备的「未分类」消息
          - 字符串：该设备某主题的消息
        """
        if conversation_id is _UNSPECIFIED:
            cur = self.conn.execute(
                "SELECT direction, text, ts FROM messages WHERE peer_id=? "
                "ORDER BY ts ASC, id ASC LIMIT ?",
                (peer_id, limit),
            )
        elif conversation_id is None:
            cur = self.conn.execute(
                "SELECT direction, text, ts FROM messages WHERE peer_id=? "
                "AND conversation_id IS NULL ORDER BY ts ASC, id ASC LIMIT ?",
                (peer_id, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT direction, text, ts FROM messages WHERE peer_id=? "
                "AND conversation_id=? ORDER BY ts ASC, id ASC LIMIT ?",
                (peer_id, conversation_id, limit),
            )
        return cur.fetchall()

    def list_conversations(self, peer_id: str):
        """某设备下的主题列表 [(conversation_id, 标题, 最后消息时间), ...]。

        按最后消息时间倒序；conversation_id=None 表示「未分类」（旧数据 / 未归类消息）。
        标题 = 该主题第一条消息文本（可读的「主题名」）。
        """
        cur = self.conn.execute(
            "SELECT conversation_id, MAX(ts) AS last_ts FROM messages "
            "WHERE peer_id=? GROUP BY conversation_id ORDER BY last_ts DESC",
            (peer_id,),
        )
        result = []
        for conv_id, last_ts in cur.fetchall():
            if conv_id is None:
                tcur = self.conn.execute(
                    "SELECT text FROM messages WHERE peer_id=? "
                    "AND conversation_id IS NULL ORDER BY ts ASC, id ASC LIMIT 1",
                    (peer_id,),
                )
            else:
                tcur = self.conn.execute(
                    "SELECT text FROM messages WHERE peer_id=? "
                    "AND conversation_id=? ORDER BY ts ASC, id ASC LIMIT 1",
                    (peer_id, conv_id),
                )
            row = tcur.fetchone()
            result.append((conv_id, row[0] if row else "", last_ts))
        return result

    def list_peers(self):
        """返回所有聊过天的 peer_id，按最后一条消息时间倒序。"""
        cur = self.conn.execute(
            "SELECT peer_id, MAX(ts) AS last_ts FROM messages "
            "GROUP BY peer_id ORDER BY last_ts DESC"
        )
        return [row[0] for row in cur.fetchall()]

    def close(self):
        self.conn.close()
