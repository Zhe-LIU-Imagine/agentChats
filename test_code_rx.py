#!/usr/bin/env python3
"""验证代码分发「接收重组」：乱序分片 → 按 chunk 序号重组 → 写暂存区。"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hermes_agent as ha

# 隔离到临时目录，避免污染真实 data/incoming/
tmp = tempfile.mkdtemp(prefix="agentchat_rx_test_")
ha.INCOMING_DIR = os.path.join(tmp, "incoming")

ha.code_rx_cache.clear()
ha.code_rx_accepting = True   # 模拟「本机已请求代码」，允许接收重组

# 20 字符，分 4 片（每片 5 字符）
content = "0123456789abcdefghij"
chunks = [content[i:i + 5] for i in range(0, len(content), 5)]
assert "".join(chunks) == content

# 乱序发送：3 → 1 → 0 → 2
order = [3, 1, 0, 2]
for idx in order:
    ha._handle_code_sync_receive(
        {"code_sync": True, "file": "test_rx.txt", "chunk": idx,
         "total": 4, "content": chunks[idx]}, "sender")

# 验证重组写暂存区
path = os.path.join(ha.INCOMING_DIR, "test_rx.txt")
with open(path, encoding="utf-8") as f:
    result = f.read()
print(f"[1] 乱序分片重组结果: {result!r}")
assert result == content, f"重组错误: {result!r} != {content!r}"
assert "test_rx.txt" not in ha.code_rx_cache, "重组完成后应清空缓存"
print("[1] 乱序分片按 chunk 序号重组正确（写暂存区）✅")

# 测试「不完整分片不写文件」
ha.code_rx_cache.clear()
ha._handle_code_sync_receive(
    {"code_sync": True, "file": "test_partial.txt", "chunk": 0,
     "total": 3, "content": "AAA"}, "sender")
assert not os.path.exists(os.path.join(ha.INCOMING_DIR, "test_partial.txt")), "未到齐不应写文件"
assert "test_partial.txt" in ha.code_rx_cache, "未到齐应保留缓存"
print("[2] 未到齐不写文件、保留缓存 ✅")

# 清理
shutil.rmtree(tmp, ignore_errors=True)
ha.code_rx_cache.clear()
print("🎉 代码分发「接收重组」验证全部通过")
